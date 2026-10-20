"""
Glue between Telegram (Pyrogram) and the in-memory matchmaking engine.

Everything the plugins need lives here so command handlers, button callbacks and
the relay all share exactly the same session logic.

NOTE ON FORWARDING: the anonymous relay in this module only ever creates NEW bot
messages via send_* methods (by file_id). It never calls forward/copy. The sole
copy in the project is the moderation copy in moderation.py (private log channel).
"""
import asyncio
import inspect
import logging
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import Enum
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

from pyrogram import enums, errors
from pyrogram.errors import FloodWait
from pyrogram.types import (
    InlineKeyboardMarkup,
    InputMediaAudio,
    InputMediaDocument,
    InputMediaPhoto,
    InputMediaVideo,
    ReplyKeyboardMarkup,
)

try:  # newer Pyrogram forks (kurigram) use ReplyParameters instead of reply_to_message_id
    from pyrogram.types import ReplyParameters
except ImportError:  # pragma: no cover
    ReplyParameters = None

import moderation
import subscription
import texts
from config import (
    ALBUM_WAIT_SECONDS,
    AUTH_CHANNELS,
    LOG_CHANNEL,
    MAX_MATCH_ATTEMPTS,
    ONLINE_WINDOW_SECONDS,
    REPLY_LINK_LIMIT,
    REPORT_COOLDOWN_SECONDS,
    REPORT_REASON_MAX,
)
from database import db
from logutil import log_event
from matchmaking import JoinStatus, LeaveResult, mm
from pyrogram.enums import ParseMode
logger = logging.getLogger(__name__)
PLAIN = enums.ParseMode.HTML


# =========================================================== delivery helpers
class Delivery(Enum):
    OK = "ok"
    UNREACHABLE = "unreachable"  # user blocked the bot / deleted account / chat not found
    FAILED = "failed"            # anything else (transient Telegram error, bad media, ...)


def _collect(names) -> tuple:
    found = []
    for name in names:
        exc = getattr(errors, name, None)
        if isinstance(exc, type) and issubclass(exc, BaseException):
            found.append(exc)
    return tuple(found)


UNREACHABLE_ERRORS = _collect([
    "UserIsBlocked", "InputUserDeactivated", "UserDeactivated", "UserDeactivatedBan",
    "PeerIdInvalid", "ChatWriteForbidden", "UserIdInvalid",
])


async def deliver_result(send: Callable[[], Awaitable]) -> Tuple[Delivery, Any]:
    """Run one Telegram send; classify the outcome; never raise. Returns (outcome, sent message(s))."""
    for attempt in range(2):
        try:
            return Delivery.OK, await send()
        except FloodWait as exc:
            wait = getattr(exc, "value", 0) or 0
            if attempt == 0 and wait <= 30:
                await asyncio.sleep(wait + 1)
                continue
            return Delivery.FAILED, None
        except UNREACHABLE_ERRORS as exc:
            log_event(logger, logging.INFO, "recipient_unreachable", error=type(exc).__name__)
            return Delivery.UNREACHABLE, None
        except Exception as exc:  # includes RPCError; never log content
            log_event(logger, logging.WARNING, "delivery_failed", error=type(exc).__name__)
            return Delivery.FAILED, None
    return Delivery.FAILED, None


async def deliver(send: Callable[[], Awaitable]) -> Delivery:
    """Same as deliver_result() when the sent message is not needed."""
    return (await deliver_result(send))[0]


async def send_text(client, user_id: int, text: str, **kwargs) -> Delivery:
    kwargs.setdefault("parse_mode", PLAIN)
    return await deliver(lambda: client.send_message(user_id, text, **kwargs))


# ============================================================== access control
class Gate(Enum):
    OK = "ok"
    BANNED = "banned"
    NOT_SUBSCRIBED = "not_subscribed"
    UNAVAILABLE = "unavailable"


async def check_access(client, user) -> Gate:
    """
    Register/refresh the user, then require: not banned, subscribed. Fails closed:
    a database or subscription-check failure never grants access.
    """
    uid = user.id
    mm.touch(uid)
    try:
        record = await db.register_user(uid)
    except Exception as exc:
        log_event(logger, logging.ERROR, "db_unavailable", op="register_user", error=type(exc).__name__)
        return Gate.UNAVAILABLE
    if record.is_new:
        moderation.spawn(moderation.log_new_user(client, user))
    if record.is_banned:
        mm.mark_banned(uid)
        return Gate.BANNED
    if mm.is_banned(uid):  # ban issued while this request was in flight
        return Gate.BANNED
    subscribed = await subscription.is_subscribed(client, uid)
    if subscribed is None:
        return Gate.UNAVAILABLE
    if not subscribed:
        return Gate.NOT_SUBSCRIBED
    return Gate.OK


async def send_subscription_prompt(client, user_id: int) -> None:
    markup = await subscription.prompt_markup(client, user_id)
    has_link = any(getattr(b, "url", None) for row in markup.inline_keyboard for b in row)
    text = texts.SUB_REQUIRED if has_link else texts.SUB_REQUIRED_NO_LINK
    result = await send_text(client, user_id, text, reply_markup=markup)
    if result is Delivery.FAILED and has_link:  # e.g. rejected button URL
        fallback = await subscription.prompt_markup(client, user_id, with_join_button=False)
        await send_text(client, user_id, texts.SUB_REQUIRED_NO_LINK, reply_markup=fallback)


async def deny(client, user_id: int, gate: Gate) -> None:
    if gate is Gate.BANNED:
        await send_text(client, user_id, texts.BANNED)
    elif gate is Gate.NOT_SUBSCRIBED:
        await send_subscription_prompt(client, user_id)
    else:
        await send_text(client, user_id, texts.UNAVAILABLE)


def reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup([["/chat", "/next"], ["/stop", "/help"]], resize_keyboard=True)


async def send_welcome(client, user_id: int) -> None:
    await send_text(client, user_id, texts.WELCOME, reply_markup=reply_keyboard())


# ============================================================ commands: chat
async def start_search(client, user_id: int) -> None:
    """Match with a waiting stranger or join the queue (caller already passed the gate)."""
    for _ in range(MAX_MATCH_ATTEMPTS):
        result = await mm.join(user_id)
        status = result.status
        if status is JoinStatus.BANNED:
            await send_text(client, user_id, texts.BANNED)
            return
        if status is JoinStatus.ALREADY_CHATTING:
            await send_text(client, user_id, texts.ALREADY_CHATTING)
            return
        if status is JoinStatus.ALREADY_WAITING:
            await send_text(client, user_id, texts.ALREADY_WAITING)
            return
        if status is JoinStatus.WAITING:
            await send_text(client, user_id, texts.SEARCHING)
            return

        # MATCHED - the session already exists atomically; now tell both people.
        session, partner_id = result.session, result.partner_id
        if await send_text(client, partner_id, texts.CONNECTED) is not Delivery.OK:
            # The waiting user is gone (blocked the bot, ...). Drop that session
            # (by ID) and try the next waiting user for the requester.
            await mm.end_session(session.session_id)
            continue
        if await send_text(client, user_id, texts.CONNECTED) is not Delivery.OK:
            if await mm.end_session(session.session_id) is not None:
                await send_text(client, partner_id, texts.PARTNER_LEFT)
            return
        asyncio.ensure_future(_bump_total_chats(user_id, partner_id))
        return
    await send_text(client, user_id, texts.UNAVAILABLE)


async def _bump_total_chats(*user_ids: int) -> None:
    try:
        await db.increment_total_chats(user_ids)
    except Exception as exc:
        log_event(logger, logging.WARNING, "total_chats_update_failed", error=type(exc).__name__)


async def handle_chat(client, user) -> None:
    gate = await check_access(client, user)
    if gate is not Gate.OK:
        await deny(client, user.id, gate)
        return
    await start_search(client, user.id)


async def handle_next(client, user) -> None:
    gate = await check_access(client, user)
    if gate is not Gate.OK:
        await deny(client, user.id, gate)
        return
    left = await mm.leave(user.id)  # leaves queue and/or ends the old session
    if left.partner_id is not None:
        await send_text(client, left.partner_id, texts.PARTNER_LEFT)
    await start_search(client, user.id)


async def handle_stop(client, user) -> None:
    mm.touch(user.id)
    left = await mm.leave(user.id)  # always allowed, even if banned/unsubscribed
    if left.session is not None:
        await send_text(client, user.id, texts.CHAT_ENDED)
        await send_text(client, left.partner_id, texts.PARTNER_LEFT)
    elif left.was_waiting:
        await send_text(client, user.id, texts.SEARCH_CANCELLED)
    else:
        await send_text(client, user.id, texts.NOT_ACTIVE)


# ================================================================ admin tools
async def ban_user(client, target_id: int, reason: Optional[str]):
    """Returns (db_ok, LeaveResult). Memory ban takes effect first, instantly."""
    left = await mm.ban(target_id)
    subscription.forget(target_id)
    db_ok = True
    try:
        await db.set_banned(target_id, reason)
    except Exception as exc:
        db_ok = False
        log_event(logger, logging.ERROR, "db_unavailable", op="set_banned", error=type(exc).__name__)
    if left.partner_id is not None:
        await send_text(client, left.partner_id, texts.PARTNER_LEFT)
    await send_text(client, target_id, texts.BANNED)  # best effort (may never have started the bot)
    return db_ok, left


async def unban_user(target_id: int) -> Optional[bool]:
    """True = was banned, False = was not banned, None = database unavailable."""
    try:
        was_banned = await db.unban(target_id)
    except Exception as exc:
        log_event(logger, logging.ERROR, "db_unavailable", op="unban", error=type(exc).__name__)
        return None
    mm.unban(target_id)
    return was_banned


async def collect_stats() -> str:
    s = mm.stats(ONLINE_WINDOW_SECONDS)
    registered = banned = "n/a"
    try:
        counts = await db.counts()
        registered, banned = counts["registered"], counts["banned"]
    except Exception as exc:
        log_event(logger, logging.ERROR, "db_unavailable", op="counts", error=type(exc).__name__)
    return (
        "BOT STATISTICS\n\n"
        f"👥 Registered users: {registered}\n"
        f"🟢 Online users: {s['online']} (active in the last {ONLINE_WINDOW_SECONDS // 60} min)\n"
        f"🔎 Waiting for match: {s['waiting']}\n"
        f"💬 Active chat sessions: {s['sessions']}\n"
        f"👤 Users currently chatting: {s['chatting']}\n"
        f"🚫 Banned users: {banned}\n"
        f"📈 Sessions created since restart: {s['created']}"
    )


# ========================================================== message relaying
def classify(m) -> Optional[str]:
    """Name of the (single) content kind of an incoming message, or None if unsupported."""
    if getattr(m, "text", None):
        return "text"
    for kind, attr in (
        ("photo", "photo"), ("video", "video"), ("video_note", "video_note"),
        ("voice", "voice"), ("audio", "audio"), ("animation", "animation"),
        ("document", "document"), ("sticker", "sticker"), ("venue", "venue"),
        ("location", "location"), ("contact", "contact"),
    ):
        if getattr(m, attr, None):
            return kind
    return None


ALBUM_KINDS = {"photo", "video", "audio", "document"}


# ---- reply threading --------------------------------------------------------
# The relay re-sends every message as a NEW bot message, so Telegram has no idea which
# message is a reply to which. We keep a tiny in-memory map of message IDs (never any
# content) so a swipe-reply on one side becomes a real reply on the other side:
#   (chat, message_id) -> the same message's ID in the partner's chat + who wrote it.
# It is also what /report uses to know who wrote the message that was replied to.
@dataclass
class _Link:
    session_id: str
    author: int        # user who originally wrote the message
    peer: int          # the other chat the message was relayed to / came from
    peer_msg: int      # message ID of the counterpart in that other chat
    reported: bool = False


class _LinkStore:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._items: "OrderedDict[Tuple[int, int], _Link]" = OrderedDict()

    def put(self, chat_id: int, message_id: int, link: _Link) -> None:
        key = (chat_id, message_id)
        self._items[key] = link
        self._items.move_to_end(key)
        while len(self._items) > self._limit:
            self._items.popitem(last=False)

    def get(self, chat_id: int, message_id: int) -> Optional[_Link]:
        return self._items.get((chat_id, message_id))

    def clear(self) -> None:
        self._items.clear()


links = _LinkStore(REPLY_LINK_LIMIT)


def _remember(sender_id: int, sender_msg_id: int, partner_id: int, partner_msg_id: int, session_id: str) -> None:
    """Link a relayed message in both directions (sender's copy <-> partner's copy)."""
    if not sender_msg_id or not partner_msg_id:
        return
    links.put(sender_id, sender_msg_id, _Link(session_id, sender_id, partner_id, partner_msg_id))
    links.put(partner_id, partner_msg_id, _Link(session_id, sender_id, sender_id, sender_msg_id))


def _reply_target(sender_id: int, m, session_id: str, partner_id: int) -> Optional[int]:
    """If `m` swipe-replies to a message of THIS chat, return the matching message ID in the partner's chat."""
    reply_id = getattr(m, "reply_to_message_id", None)
    if not reply_id:
        return None
    link = links.get(sender_id, reply_id)
    if link is None or link.session_id != session_id or link.peer != partner_id:
        return None  # unknown, or belongs to an older chat: send as a normal message
    return link.peer_msg


_reply_style: Optional[str] = None


def _reply_kwargs(client, message_id: Optional[int]) -> dict:
    """Reply argument understood by the installed Pyrogram fork (it differs between versions)."""
    global _reply_style
    if not message_id:
        return {}
    if _reply_style is None:
        try:
            params = inspect.signature(client.send_message).parameters
        except (TypeError, ValueError):
            params = {}
        if "reply_parameters" in params and ReplyParameters is not None:
            _reply_style = "parameters"
        elif "reply_to_message_id" in params:
            _reply_style = "id"
        else:
            _reply_style = "none"
    if _reply_style == "parameters":
        return {"reply_parameters": ReplyParameters(message_id=message_id)}
    if _reply_style == "id":
        return {"reply_to_message_id": message_id}
    return {}


async def _send_raw(client, to: int, m, kind: str, reply_to: Optional[int]):
    """Create a NEW bot message for the recipient. Never forward/copy; no sender info."""
    extra = {"protect_content": True, **_reply_kwargs(client, reply_to)}
    caption = {
        "caption": getattr(m, "caption", None) or "",
        "caption_entities": getattr(m, "caption_entities", None),
        "parse_mode": PLAIN,
    }
    if kind == "text":
        return await client.send_message(
            to, m.text, entities=getattr(m, "entities", None), parse_mode=PLAIN, **extra)
    if kind == "photo":
        return await client.send_photo(to, m.photo.file_id, **caption, **extra)
    if kind == "video":
        return await client.send_video(to, m.video.file_id, **caption, **extra)
    if kind == "animation":
        return await client.send_animation(to, m.animation.file_id, **caption, **extra)
    if kind == "voice":
        return await client.send_voice(to, m.voice.file_id, **caption, **extra)
    if kind == "audio":
        return await client.send_audio(to, m.audio.file_id, **caption, **extra)
    if kind == "document":
        return await client.send_document(to, m.document.file_id, **caption, **extra)
    if kind == "video_note":
        return await client.send_video_note(to, m.video_note.file_id, **extra)
    if kind == "sticker":
        return await client.send_sticker(to, m.sticker.file_id, **extra)
    if kind == "location":
        return await client.send_location(to, m.location.latitude, m.location.longitude, **extra)
    if kind == "venue":
        v = m.venue
        return await client.send_venue(
            to, v.location.latitude, v.location.longitude, v.title, v.address, **extra)
    if kind == "contact":
        c = m.contact
        return await client.send_contact(
            to, c.phone_number, c.first_name, last_name=c.last_name or "",
            vcard=c.vcard or "", **extra)
    raise ValueError("unsupported kind")  # caught by deliver(); never falls back to copy/forward


async def _send_single(client, to: int, m, kind: str, reply_to: Optional[int] = None):
    """Send as a reply when possible; if Telegram rejects the reply target, send it plainly."""
    if reply_to:
        try:
            return await _send_raw(client, to, m, kind, reply_to)
        except (FloodWait,) + UNREACHABLE_ERRORS:
            raise
        except (errors.RPCError, TypeError) as exc:
            log_event(logger, logging.INFO, "reply_target_rejected", error=type(exc).__name__)
    return await _send_raw(client, to, m, kind, None)


async def _send_album(client, to: int, media: list, reply_to: Optional[int] = None):
    if reply_to:
        try:
            return await client.send_media_group(
                to, media, protect_content=True, **_reply_kwargs(client, reply_to))
        except (FloodWait,) + UNREACHABLE_ERRORS:
            raise
        except (errors.RPCError, TypeError) as exc:
            log_event(logger, logging.INFO, "reply_target_rejected", error=type(exc).__name__)
    return await client.send_media_group(to, media, protect_content=True)


def _album_item(m, kind: str):
    cap = {
        "caption": getattr(m, "caption", None) or "",
        "caption_entities": getattr(m, "caption_entities", None),
        "parse_mode": PLAIN,
    }
    if kind == "photo":
        return InputMediaPhoto(m.photo.file_id, **cap)
    if kind == "video":
        return InputMediaVideo(m.video.file_id, **cap)
    if kind == "audio":
        return InputMediaAudio(m.audio.file_id, **cap)
    return InputMediaDocument(m.document.file_id, **cap)


@dataclass
class _Album:
    kinds: List[str]
    messages: list


_albums: Dict[tuple, _Album] = {}


async def _after_delivery(client, sender_id: int, session_id: str, result: Delivery) -> None:
    if result is Delivery.OK:
        return
    if result is Delivery.UNREACHABLE:
        # Partner is gone. End exactly THIS session (by ID); a newer one is untouched.
        if await mm.end_session(session_id) is not None:
            await send_text(client, sender_id, texts.PARTNER_LEFT)
        return
    await send_text(client, sender_id, texts.DELIVERY_FAILED)


async def _flush_album(client, key: tuple) -> None:
    await asyncio.sleep(ALBUM_WAIT_SECONDS)
    album = _albums.pop(key, None)
    if album is None:
        return
    sender_id, _group_id, session_id = key
    session = mm.get_session(session_id)
    if session is None or sender_id not in session.users:
        return  # session ended meanwhile: drop, never redirect to someone else
    partner_id = session.partner_of(sender_id)
    pairs = sorted(zip(album.messages, album.kinds), key=lambda p: p[0].id)[:10]
    reply_to = None
    for m, _kind in pairs:  # the swipe-reply is attached to one (usually the first) album item
        reply_to = _reply_target(sender_id, m, session_id, partner_id)
        if reply_to:
            break
    if len(pairs) == 1:
        m, kind = pairs[0]
        result, sent = await deliver_result(lambda: _send_single(client, partner_id, m, kind, reply_to))
        sent_list = [sent]
    else:
        media = [_album_item(m, kind) for m, kind in pairs]
        result, sent = await deliver_result(lambda: _send_album(client, partner_id, media, reply_to))
        sent_list = list(sent or [])
    if result is Delivery.OK:
        for (orig, _kind), out in zip(pairs, sent_list):
            _remember(sender_id, orig.id, partner_id, getattr(out, "id", 0), session_id)
    await _after_delivery(client, sender_id, session_id, result)


def _buffer_album(client, sender_id: int, session_id: str, m, kind: str) -> None:
    key = (sender_id, m.media_group_id, session_id)
    album = _albums.get(key)
    if album is None:
        _albums[key] = _Album([kind], [m])
        asyncio.ensure_future(_flush_album(client, key))
    else:
        album.kinds.append(kind)
        album.messages.append(m)


async def relay_message(client, message) -> None:
    """Deliver one incoming message to the sender's CURRENT partner, anonymously."""
    user = message.from_user
    if user is None:
        return
    uid = user.id
    mm.touch(uid)

    if mm.is_banned(uid):
        await send_text(client, uid, texts.BANNED)
        return

    if mm.resolve(uid) is None:
        await send_text(client, uid, texts.STILL_SEARCHING if mm.is_waiting(uid) else texts.NOT_ACTIVE)
        return

    # Membership (recent positive results are cached; failures never grant access).
    subscribed = await subscription.is_subscribed(client, uid, use_cache=True)
    if subscribed is None:
        await send_text(client, uid, texts.UNAVAILABLE)
        return
    if not subscribed:
        left = await mm.leave(uid)
        if left.partner_id is not None:
            await send_text(client, left.partner_id, texts.PARTNER_LEFT)
        await send_subscription_prompt(client, uid)
        return

    kind = classify(message)
    if kind is None:
        await send_text(client, uid, texts.UNSUPPORTED)
        return

    # Resolve the recipient AFTER all awaits above, right before sending, so a
    # /next or /stop that happened in the meantime can never misroute this message.
    current = mm.resolve(uid)
    if current is None:
        await send_text(client, uid, texts.CHAT_GONE)
        return
    session_id, partner_id = current

    if moderation.should_log(kind):
        moderation.spawn(moderation.log_media(client, message, kind, uid))

    if getattr(message, "media_group_id", None) and kind in ALBUM_KINDS:
        _buffer_album(client, uid, session_id, message, kind)
        return

    reply_to = _reply_target(uid, message, session_id, partner_id)
    result, sent = await deliver_result(lambda: _send_single(client, partner_id, message, kind, reply_to))
    if result is Delivery.OK:
        _remember(uid, message.id, partner_id, getattr(sent, "id", 0), session_id)
    await _after_delivery(client, uid, session_id, result)


# ================================================================ /report
_last_report: Dict[int, float] = {}


async def _lookup_user(client, user_id: int):
    """Name/username of the reported user (None if Telegram can't resolve them)."""
    try:
        return await client.get_users(user_id)
    except Exception as exc:
        log_event(logger, logging.INFO, "report_user_lookup_failed", error=type(exc).__name__)
        return None


async def handle_report(client, message) -> None:
    """/report as a swipe-reply to a partner's message -> details go to LOG_CHANNEL."""
    user = message.from_user
    if user is None:
        return
    uid = user.id
    mm.touch(uid)
    if mm.is_banned(uid):
        await send_text(client, uid, texts.BANNED)
        return

    reply_id = getattr(message, "reply_to_message_id", None)
    if not reply_id:
        await send_text(client, uid, texts.REPORT_USAGE)
        return
    link = links.get(uid, reply_id)
    if link is None:
        await send_text(client, uid, texts.REPORT_NOT_FOUND)
        return
    if link.author == uid:
        await send_text(client, uid, texts.REPORT_OWN_MESSAGE)
        return
    if link.reported:
        await send_text(client, uid, texts.REPORT_ALREADY)
        return
    now = time.monotonic()
    if now - _last_report.get(uid, -REPORT_COOLDOWN_SECONDS) < REPORT_COOLDOWN_SECONDS:
        await send_text(client, uid, texts.REPORT_COOLDOWN)
        return
    _last_report[uid] = now  # set before any await so rapid repeats can't slip through

    target = getattr(message, "reply_to_message", None)
    if target is None or getattr(target, "empty", False):
        try:
            target = await client.get_messages(message.chat.id, reply_id)
        except Exception as exc:
            log_event(logger, logging.INFO, "report_fetch_failed", error=type(exc).__name__)
            target = None
    if target is not None and getattr(target, "empty", False):
        target = None
    kind = classify(target) if target is not None else None

    args = message.command[1:] if message.command else []
    reason = " ".join(args).strip()[:REPORT_REASON_MAX] or None

    reported_id = link.author
    reported_user = await _lookup_user(client, reported_id)
    sent = await moderation.log_report(
        client,
        reporter=user,
        reported_id=reported_id,
        reported_user=reported_user,
        message=target,
        kind=kind,
        reason=reason,
    )
    if sent:
        link.reported = True
        await send_text(client, uid, texts.REPORT_SENT)
    else:
        _last_report.pop(uid, None)  # let them retry right away
        await send_text(client, uid, texts.REPORT_FAILED)


# ====================================================== lifecycle / background
_banned_synced = False


async def _sync_banned() -> bool:
    global _banned_synced
    try:
        mm.mark_banned_many(await db.list_banned_ids())
    except Exception as exc:
        log_event(logger, logging.ERROR, "db_unavailable", op="load_banned", error=type(exc).__name__)
        return False
    _banned_synced = True
    return True


async def startup() -> None:
    """Connect to MongoDB (never raises) and load bans. Matchmaking starts empty."""
    await db.connect()
    await _sync_banned()


async def warmup(client) -> None:
    """Resolve the configured channels once so misconfiguration shows up in the logs."""
    channels = [(f"AUTH_CHANNELS[{i}]", ch) for i, ch in enumerate(AUTH_CHANNELS)] + [("LOG_CHANNEL", LOG_CHANNEL)]
    for name, chat_id in channels:
        try:
            await client.get_chat(chat_id)
        except Exception as exc:
            log_event(logger, logging.WARNING, "channel_unreachable", channel=name, error=type(exc).__name__)


async def janitor() -> None:
    """Housekeeping: retry DB init/ban sync until it works, prune caches."""
    while True:
        await asyncio.sleep(60)
        try:
            if not _banned_synced:
                await db.ensure_ready()
                await _sync_banned()
            mm.prune_activity(ONLINE_WINDOW_SECONDS)
            subscription.prune_cache()
            cutoff = time.monotonic() - REPORT_COOLDOWN_SECONDS
            for uid in [u for u, t in _last_report.items() if t < cutoff]:
                del _last_report[uid]
        except Exception as exc:  # the janitor must never die
            log_event(logger, logging.ERROR, "janitor_error", error=type(exc).__name__)


async def shutdown(client) -> None:
    """Discard all sessions/queue and tell affected users (best effort, bounded)."""
    users = mm.snapshot_active_users()
    waiting, sessions = await mm.clear()
    links.clear()
    log_event(logger, logging.INFO, "state_cleared", waiting=waiting, sessions=sessions)
    if not users:
        return
    gate = asyncio.Semaphore(20)

    async def notify(uid: int) -> None:
        async with gate:
            await send_text(client, uid, texts.SHUTDOWN)

    try:
        await asyncio.wait_for(asyncio.gather(*(notify(u) for u in users)), timeout=10)
    except asyncio.TimeoutError:
        log_event(logger, logging.WARNING, "shutdown_notify_timeout")
