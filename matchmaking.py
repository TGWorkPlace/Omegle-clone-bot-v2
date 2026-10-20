"""
In-memory random matchmaking engine.

Nothing here touches Telegram or MongoDB. All state lives in this process and
is intentionally lost on restart:

    _wait_list / _wait_pos   waiting users (list + index map => O(1) add,
                              O(1) remove, O(1) random pick)
    _sessions                session_id -> Session
    _user_to_session         user_id -> session_id
    _banned                  in-memory mirror of banned IDs (fast, race-free
                              ban enforcement; MongoDB stays the source of truth)

Every mutation happens inside one asyncio.Lock and contains no awaits, so the
critical sections are tiny and atomic. Slow Telegram calls never run under the
lock. Sessions are always ended *by session ID*, so a stale reference to an old
session can never tear down (or deliver into) a newer one.
"""
from __future__ import annotations

import asyncio
import logging
import random
import secrets
import time
from dataclasses import dataclass
from enum import Enum
from typing import Dict, Iterable, List, Optional, Set, Tuple

from logutil import log_event

logger = logging.getLogger(__name__)


class JoinStatus(Enum):
    WAITING = "waiting"
    MATCHED = "matched"
    ALREADY_CHATTING = "already_chatting"
    ALREADY_WAITING = "already_waiting"
    BANNED = "banned"


@dataclass(frozen=True)
class Session:
    session_id: str
    user_a: int
    user_b: int
    created_at: float

    @property
    def users(self) -> Tuple[int, int]:
        return (self.user_a, self.user_b)

    def partner_of(self, user_id: int) -> Optional[int]:
        if user_id == self.user_a:
            return self.user_b
        if user_id == self.user_b:
            return self.user_a
        return None


@dataclass(frozen=True)
class JoinResult:
    status: JoinStatus
    session: Optional[Session] = None
    partner_id: Optional[int] = None


@dataclass(frozen=True)
class LeaveResult:
    was_waiting: bool = False
    session: Optional[Session] = None
    partner_id: Optional[int] = None


class Matchmaker:
    def __init__(self) -> None:
        self._lock: Optional[asyncio.Lock] = None  # created lazily inside the running loop
        self._wait_list: List[int] = []
        self._wait_pos: Dict[int, int] = {}
        self._sessions: Dict[str, Session] = {}
        self._user_to_session: Dict[int, str] = {}
        self._banned: Set[int] = set()
        self._last_active: Dict[int, float] = {}
        self.sessions_created = 0

    # ------------------------------------------------------------------ utils
    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _remove_waiting(self, user_id: int) -> bool:
        idx = self._wait_pos.pop(user_id, None)
        if idx is None:
            return False
        last = self._wait_list.pop()
        if idx < len(self._wait_list):  # removed element was not the tail
            self._wait_list[idx] = last
            self._wait_pos[last] = idx
        return True

    def _enqueue(self, user_id: int) -> None:
        self._wait_pos[user_id] = len(self._wait_list)
        self._wait_list.append(user_id)

    def _pick_partner(self, exclude: int) -> Optional[int]:
        """Pop a uniformly random *eligible* waiting user (defensive filtering)."""
        while self._wait_list:
            candidate = self._wait_list[random.randrange(len(self._wait_list))]
            self._remove_waiting(candidate)
            if candidate == exclude or candidate in self._banned or candidate in self._user_to_session:
                log_event(logger, logging.WARNING, "queue_inconsistency_dropped")
                continue
            return candidate
        return None

    def _create_session(self, user_a: int, user_b: int) -> Session:
        assert user_a != user_b
        assert user_a not in self._user_to_session and user_b not in self._user_to_session
        session_id = secrets.token_hex(8)
        while session_id in self._sessions:  # practically impossible, but cheap
            session_id = secrets.token_hex(8)
        session = Session(session_id, user_a, user_b, time.time())
        self._sessions[session_id] = session
        self._user_to_session[user_a] = session_id
        self._user_to_session[user_b] = session_id
        self.sessions_created += 1
        log_event(logger, logging.INFO, "session_created", sid=session_id)
        return session

    def _drop_session(self, session_id: str) -> Optional[Session]:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return None
        for uid in session.users:
            if self._user_to_session.get(uid) == session_id:
                del self._user_to_session[uid]
        log_event(logger, logging.INFO, "session_ended", sid=session_id)
        return session

    def _leave(self, user_id: int) -> LeaveResult:
        was_waiting = self._remove_waiting(user_id)
        session = None
        partner = None
        sid = self._user_to_session.get(user_id)
        if sid is not None:
            session = self._drop_session(sid)
            if session is not None:
                partner = session.partner_of(user_id)
        return LeaveResult(was_waiting, session, partner)

    # ------------------------------------------------------------ mutations
    async def join(self, user_id: int) -> JoinResult:
        """Match with a random waiting user, or enter the queue. Atomic."""
        async with self._get_lock():
            if user_id in self._banned:
                return JoinResult(JoinStatus.BANNED)
            if user_id in self._user_to_session:
                return JoinResult(JoinStatus.ALREADY_CHATTING)
            if user_id in self._wait_pos:
                return JoinResult(JoinStatus.ALREADY_WAITING)
            partner = self._pick_partner(exclude=user_id)
            if partner is None:
                self._enqueue(user_id)
                return JoinResult(JoinStatus.WAITING)
            session = self._create_session(partner, user_id)
            return JoinResult(JoinStatus.MATCHED, session, partner)

    async def leave(self, user_id: int) -> LeaveResult:
        """Remove from queue and/or end the user's active session."""
        async with self._get_lock():
            return self._leave(user_id)

    async def end_session(self, session_id: str) -> Optional[Session]:
        """End a specific session. No-op (None) if it is already gone/replaced."""
        async with self._get_lock():
            return self._drop_session(session_id)

    async def ban(self, user_id: int) -> LeaveResult:
        """Block the user in memory and tear down any queue/session state."""
        async with self._get_lock():
            self._banned.add(user_id)
            return self._leave(user_id)

    async def clear(self) -> Tuple[int, int]:
        """Discard every waiting user and session (shutdown / restart)."""
        async with self._get_lock():
            waiting, sessions = len(self._wait_list), len(self._sessions)
            self._wait_list.clear()
            self._wait_pos.clear()
            self._sessions.clear()
            self._user_to_session.clear()
            return waiting, sessions

    # ------------------------------------------------------- ban mirror (sync)
    def mark_banned(self, user_id: int) -> None:
        """Add only. The DB sync must never un-ban (it could race with /ban)."""
        self._banned.add(user_id)

    def mark_banned_many(self, user_ids: Iterable[int]) -> None:
        self._banned.update(user_ids)

    def unban(self, user_id: int) -> None:
        self._banned.discard(user_id)

    def is_banned(self, user_id: int) -> bool:
        return user_id in self._banned

    # --------------------------------------------------------------- queries
    def resolve(self, user_id: int) -> Optional[Tuple[str, int]]:
        """(session_id, partner_id) for the user's *current* session, else None."""
        sid = self._user_to_session.get(user_id)
        if sid is None:
            return None
        session = self._sessions.get(sid)
        if session is None:
            return None
        partner = session.partner_of(user_id)
        if partner is None:
            return None
        return sid, partner

    def get_session(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def in_session(self, session_id: str, user_id: int) -> bool:
        session = self._sessions.get(session_id)
        return session is not None and user_id in session.users

    def is_waiting(self, user_id: int) -> bool:
        return user_id in self._wait_pos

    def is_chatting(self, user_id: int) -> bool:
        return user_id in self._user_to_session

    def snapshot_active_users(self) -> Set[int]:
        return set(self._wait_list) | set(self._user_to_session)

    # -------------------------------------------------------------- activity
    def touch(self, user_id: int) -> None:
        self._last_active[user_id] = time.monotonic()

    def online_count(self, window: float) -> int:
        cutoff = time.monotonic() - window
        return sum(1 for t in self._last_active.values() if t >= cutoff)

    def prune_activity(self, window: float) -> int:
        cutoff = time.monotonic() - window
        stale = [u for u, t in self._last_active.items() if t < cutoff]
        for u in stale:
            del self._last_active[u]
        return len(stale)

    # ----------------------------------------------------------------- stats
    def stats(self, online_window: float) -> Dict[str, int]:
        sessions = len(self._sessions)
        return {
            "waiting": len(self._wait_list),
            "sessions": sessions,
            "chatting": sessions * 2,  # every session has exactly two distinct users
            "created": self.sessions_created,
            "online": self.online_count(online_window),
            "banned_in_memory": len(self._banned),
        }

    def check_invariants(self) -> None:
        """Raise AssertionError if internal state is inconsistent (tests/debug)."""
        assert len(self._wait_list) == len(self._wait_pos) == len(set(self._wait_list))
        for uid, idx in self._wait_pos.items():
            assert self._wait_list[idx] == uid
        seen: Set[int] = set()
        for sid, s in self._sessions.items():
            assert s.session_id == sid and s.user_a != s.user_b
            for uid in s.users:
                assert uid not in seen, "user in two sessions"
                seen.add(uid)
                assert self._user_to_session.get(uid) == sid
        assert set(self._user_to_session) == seen
        assert not (seen & set(self._wait_list)), "user both waiting and chatting"


# Single process-wide instance used by the handlers.
mm = Matchmaker()
