"""
Glue between Telegram (Pyrogram) and the in-memory matchmaking engine.

Everything the plugins need lives here so command handlers, button callbacks and
the relay all share exactly the same session logic.

NOTE ON FORWARDING: the anonymous relay in this module only ever creates NEW bot
messages via send_* methods (by file_id). It never calls forward/copy. The sole
copy in the project is the moderation copy in moderation.py (private log channel).
"""
import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Dict, List, Optional

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

import moderation
import subscription
import texts
from config import (
    ALBUM_WAIT_SECONDS,
    AUTH_CHANNELS,
    LOG_CHANNEL,
    MAX_MATCH_ATTEMPTS,
    ONLINE_WINDOW_SECONDS,
)
from database import db
from logutil import log_event
from matchmaking import JoinStatus, LeaveResult, mm

logger = logging.getLogger(__name__)
PLAIN = enums.ParseMode.DISABLED


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


async def deliver(send: Callable[[], Awaitable]) -> Delivery:
    """Run one Telegram send; classify the outcome; never raise."""
    for attempt in range(2):
        try:
            await send()
            return Delivery.OK
        except FloodWait as exc:
            wait = getattr(exc, "value", 0) or 0
            if attempt == 0 and wait <= 30:
                await asyncio.sleep(wait + 1)
                continue
            return Delivery.FAILED
        except UNREACHABLE_ERRORS as exc:
            log_event(logger, logging.INFO, "recipient_unreachable", error=type(exc).__name__)
            return Delivery.UNREACHABLE
        except Exception as exc:  # includes RPCError; never log content
            log_event(logger, logging.WARNING, "delivery_failed", error=type(exc).__name__)
            return Delivery.FAILED
    return Delivery.FAILED


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


async def _send_single(client, to: int, m, kind: str):
    """Create a NEW bot message for the recipient. Never forward/copy; no sender info."""
    protect = {"protect_content": True}
    caption = {
        "caption": getattr(m, "caption", None) or "",
        "caption_entities": getattr(m, "caption_entities", None),
        "parse_mode": PLAIN,
    }
    if kind == "text":
        return await client.send_message(
            to, m.text, entities=getattr(m, "entities", None), parse_mode=PLAIN, **protect)
    if kind == "photo":
        return await client.send_photo(to, m.photo.file_id, **caption, **protect)
    if kind == "video":
        return await client.send_video(to, m.video.file_id, **caption, **protect)
    if kind == "animation":
        return await client.send_animation(to, m.animation.file_id, **caption, **protect)
    if kind == "voice":
        return await client.send_voice(to, m.voice.file_id, **caption, **protect)
    if kind == "audio":
        return await client.send_audio(to, m.audio.file_id, **caption, **protect)
    if kind == "document":
        return await client.send_document(to, m.document.file_id, **caption, **protect)
    if kind == "video_note":
        return await client.send_video_note(to, m.video_note.file_id, **protect)
    if kind == "sticker":
        return await client.send_sticker(to, m.sticker.file_id, **protect)
    if kind == "location":
        return await client.send_location(to, m.location.latitude, m.location.longitude, **protect)
    if kind == "venue":
        v = m.venue
        return await client.send_venue(
            to, v.location.latitude, v.location.longitude, v.title, v.address, **protect)
    if kind == "contact":
        c = m.contact
        return await client.send_contact(
            to, c.phone_number, c.first_name, last_name=c.last_name or "",
            vcard=c.vcard or "", **protect)
    raise ValueError("unsupported kind")  # caught by deliver(); never falls back to copy/forward


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
    if len(pairs) == 1:
        m, kind = pairs[0]
        result = await deliver(lambda: _send_single(client, partner_id, m, kind))
    else:
        media = [_album_item(m, kind) for m, kind in pairs]
        result = await deliver(lambda: client.send_media_group(partner_id, media, protect_content=True))
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

    result = await deliver(lambda: _send_single(client, partner_id, message, kind))
    await _after_delivery(client, uid, session_id, result)


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
        except Exception as exc:  # the janitor must never die
            log_event(logger, logging.ERROR, "janitor_error", error=type(exc).__name__)


async def shutdown(client) -> None:
    """Discard all sessions/queue and tell affected users (best effort, bounded)."""
    users = mm.snapshot_active_users()
    waiting, sessions = await mm.clear()
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
