"""
MongoDB access (Motor). Persistent data only: user records + ban state.

Active sessions, the waiting queue and chat content are NEVER stored here.
Requires MongoDB 4.2+ (aggregation-pipeline upserts).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Iterable, List, Optional

from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ASCENDING, ReturnDocument

from config import DB_NAME, MONGO_URI
from logutil import log_event

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserRecord:
    is_new: bool
    is_banned: bool


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Database:
    def __init__(self) -> None:
        self._client = None
        self.users = None
        self._ready = False

    async def connect(self) -> bool:
        """Create the client and indexes. Never raises; returns readiness."""
        try:
            # Created inside the running loop (Motor binds to the current loop).
            self._client = AsyncIOMotorClient(MONGO_URI, serverSelectionTimeoutMS=8000)
            self.users = self._client[DB_NAME]["users"]
        except Exception as exc:
            log_event(logger, logging.ERROR, "mongo_client_failed", error=type(exc).__name__)
            return False
        return await self.ensure_ready()

    async def ensure_ready(self) -> bool:
        if self._ready:
            return True
        if self.users is None:
            return False
        try:
            await self.users.create_index([("is_banned", ASCENDING)])
            await self.users.create_index([("last_seen", ASCENDING)])
            await self.users.create_index([("first_seen", ASCENDING)])
        except Exception as exc:
            log_event(logger, logging.ERROR, "mongo_index_failed", error=type(exc).__name__)
            return False
        self._ready = True
        log_event(logger, logging.INFO, "mongo_ready")
        return True

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()

    # ---------------------------------------------------------------- users
    async def register_user(self, user_id: int) -> UserRecord:
        """
        Atomic upsert: creates the record on first contact, preserves
        first_seen/ban fields afterwards, always refreshes last_seen.
        Raises if MongoDB is unreachable (callers fail closed).
        """
        now = _now()
        pipeline = [{
            "$set": {
                "first_seen": {"$ifNull": ["$first_seen", now]},
                "last_seen": now,
                "is_banned": {"$ifNull": ["$is_banned", False]},
                "ban_reason": {"$ifNull": ["$ban_reason", None]},
                "total_chats": {"$ifNull": ["$total_chats", 0]},
            }
        }]
        before = await self.users.find_one_and_update(
            {"_id": user_id},
            pipeline,
            upsert=True,
            return_document=ReturnDocument.BEFORE,
            projection={"is_banned": 1, "first_seen": 1},
        )
        if before is None:
            return UserRecord(is_new=True, is_banned=False)
        # A record created by /ban for a never-started user has first_seen=None.
        return UserRecord(
            is_new=before.get("first_seen") is None,
            is_banned=bool(before.get("is_banned", False)),
        )

    async def set_banned(self, user_id: int, reason: Optional[str]) -> None:
        await self.users.update_one(
            {"_id": user_id},
            {
                "$set": {"is_banned": True, "ban_reason": reason, "banned_at": _now()},
                "$setOnInsert": {"first_seen": None, "last_seen": None, "total_chats": 0},
            },
            upsert=True,
        )

    async def unban(self, user_id: int) -> bool:
        """Clear the ban. Returns True if the user was banned before."""
        before = await self.users.find_one_and_update(
            {"_id": user_id},
            {"$set": {"is_banned": False, "ban_reason": None}},
            return_document=ReturnDocument.BEFORE,
            projection={"is_banned": 1},
        )
        return bool(before and before.get("is_banned"))

    async def list_banned_ids(self) -> List[int]:
        ids: List[int] = []
        async for doc in self.users.find({"is_banned": True}, {"_id": 1}):
            ids.append(doc["_id"])
        return ids

    async def list_broadcast_ids(self) -> List[int]:
        """IDs of users who started the bot (ban-only stubs have first_seen=None) and aren't banned."""
        ids: List[int] = []
        query = {"first_seen": {"$ne": None}, "is_banned": {"$ne": True}}
        async for doc in self.users.find(query, {"_id": 1}):
            ids.append(doc["_id"])
        return ids

    async def increment_total_chats(self, user_ids: Iterable[int]) -> None:
        await self.users.update_many(
            {"_id": {"$in": list(user_ids)}},
            {"$inc": {"total_chats": 1}},
        )

    async def counts(self) -> Dict[str, int]:
        return {
            # Users who actually started the bot (ban-only stubs have first_seen=None).
            "registered": await self.users.count_documents({"first_seen": {"$ne": None}}),
            "banned": await self.users.count_documents({"is_banned": True}),
        }


db = Database()
