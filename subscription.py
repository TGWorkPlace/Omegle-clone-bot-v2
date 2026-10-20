"""
Force-subscription helpers (1-5 required channels). Membership is always verified
server-side via the Bot API (get_chat_member); nothing the client sends is trusted.
A user must be a member of EVERY channel in AUTH_CHANNELS.

For reliable results the bot should be an administrator of each channel.
"""
import asyncio
import logging
import time
from typing import Dict, List, Optional

from pyrogram import enums
from pyrogram.errors import UserNotParticipant
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import AUTH_CHANNEL_URLS, AUTH_CHANNELS, SUBSCRIPTION_CACHE_TTL
from logutil import log_event

logger = logging.getLogger(__name__)

_MEMBER_STATUSES = {
    enums.ChatMemberStatus.OWNER,
    enums.ChatMemberStatus.ADMINISTRATOR,
    enums.ChatMemberStatus.MEMBER,
}

_positive_cache: Dict[int, float] = {}   # user_id -> expiry (monotonic); "member of all" only
_missing: Dict[int, List[int]] = {}      # user_id -> channels they were last found NOT in
_channel_info: Dict[int, dict] = {}      # channel_id -> {"url": ..., "title": ...} (resolved lazily)


async def check_channel(client, channel_id: int, user_id: int) -> Optional[bool]:
    """True / False for one channel, or None when it could not be verified."""
    try:
        member = await client.get_chat_member(channel_id, user_id)
    except UserNotParticipant:
        return False
    except Exception as exc:
        log_event(logger, logging.ERROR, "subscription_check_failed", error=type(exc).__name__)
        return None
    status = member.status
    if status in _MEMBER_STATUSES:
        return True
    if status == enums.ChatMemberStatus.RESTRICTED:
        return bool(getattr(member, "is_member", False))
    return False  # LEFT, BANNED, anything unknown


async def check_membership(client, user_id: int) -> Optional[bool]:
    """
    True only if the user is in ALL channels. False if definitely missing from at
    least one. None if nothing is definitely missing but a check failed (never grant).
    """
    results = await asyncio.gather(*(check_channel(client, ch, user_id) for ch in AUTH_CHANNELS))
    missing = [ch for ch, ok in zip(AUTH_CHANNELS, results) if ok is False]
    _missing[user_id] = missing
    if missing:
        return False
    if any(ok is None for ok in results):
        return None
    return True


async def is_subscribed(client, user_id: int, use_cache: bool = False) -> Optional[bool]:
    """
    use_cache=False -> always ask Telegram (used for /start, /chat, /next, the button).
    use_cache=True  -> reuse a recent "member of all" result (used per relayed message).
    """
    if use_cache:
        expiry = _positive_cache.get(user_id)
        if expiry is not None and expiry > time.monotonic():
            return True
    result = await check_membership(client, user_id)
    if result:
        _positive_cache[user_id] = time.monotonic() + SUBSCRIPTION_CACHE_TTL
    else:
        _positive_cache.pop(user_id, None)
    return result


def forget(user_id: int) -> None:
    _positive_cache.pop(user_id, None)
    _missing.pop(user_id, None)


def prune_cache() -> None:
    now = time.monotonic()
    for uid in [u for u, exp in _positive_cache.items() if exp <= now]:
        del _positive_cache[uid]
    for uid in [u for u in _missing if u not in _positive_cache and len(_missing) > 5000]:
        del _missing[uid]


async def get_channel_info(client, channel_id: int) -> dict:
    """
    Join URL + title for one channel. A configured URL wins; otherwise a public
    @username link or an invite link Telegram already reports. url is None for a
    private channel with no link - a username is never invented.
    """
    cached = _channel_info.get(channel_id)
    if cached and cached.get("url"):
        return cached
    idx = AUTH_CHANNELS.index(channel_id)
    configured = AUTH_CHANNEL_URLS[idx] if idx < len(AUTH_CHANNEL_URLS) else ""
    info = {"url": configured or None, "title": (cached or {}).get("title")}
    try:
        chat = await client.get_chat(channel_id)
    except Exception as exc:
        log_event(logger, logging.WARNING, "channel_lookup_failed", error=type(exc).__name__)
    else:
        info["title"] = getattr(chat, "title", None) or info["title"]
        if not info["url"]:
            username = getattr(chat, "username", None)
            invite = getattr(chat, "invite_link", None)
            if username:
                info["url"] = f"https://t.me/{username}"
            elif invite:
                info["url"] = invite
    _channel_info[channel_id] = info
    return info


def _label(info: dict, position: int, total: int) -> str:
    title = (info.get("title") or "").strip()
    if title:
        return f"📢 Join {title}"[:60]
    return "📢 Join Channel" if total == 1 else f"📢 Join Channel {position}"


async def prompt_markup(client, user_id: Optional[int] = None, with_join_button: bool = True) -> InlineKeyboardMarkup:
    """Join buttons for the channels the user is still missing (all, if unknown) + a re-check button."""
    rows = []
    if with_join_button:
        targets = _missing.get(user_id) or list(AUTH_CHANNELS)
        infos = await asyncio.gather(*(get_channel_info(client, ch) for ch in targets))
        for ch, info in zip(targets, infos):
            if info.get("url"):
                rows.append([InlineKeyboardButton(_label(info, AUTH_CHANNELS.index(ch) + 1, len(AUTH_CHANNELS)), url=info["url"])])
    rows.append([InlineKeyboardButton("✅ Check Subscription", callback_data="check_sub")])
    return InlineKeyboardMarkup(rows)
