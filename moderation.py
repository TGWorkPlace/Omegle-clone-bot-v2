"""
Moderation logging to LOG_CHANNEL. Deliberately independent from matchmaking
and relaying: it only ever sends to LOG_CHANNEL, runs in the background, and
swallows its own failures.

This is the ONLY place that copies a user's message (to the private log channel).
The anonymous relay never copies or forwards.
"""
import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from pyrogram import enums

from config import LOG_CHANNEL, LOG_MEDIA_TYPES
from logutil import log_event

logger = logging.getLogger(__name__)

_CAPTIONABLE = {"photo", "video", "animation", "voice", "audio", "document"}
_LABELS = {
    "photo": "Photo", "video": "Video", "video_note": "Video note", "animation": "Animation/GIF",
    "voice": "Voice message", "audio": "Audio", "document": "Document", "sticker": "Sticker",
    "location": "Location", "contact": "Contact", "venue": "Venue",
}
_CAPTION_LIMIT = 1024
_tasks = set()


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def spawn(coro) -> None:
    """Run a logging coroutine in the background (keeps a reference until done)."""
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def should_log(kind: str) -> bool:
    return kind in LOG_MEDIA_TYPES


async def log_new_user(client, user) -> None:
    lines = [
        "🆕 NEW USER",
        "",
        f"User ID: {user.id}",
        f"First name: {user.first_name or '-'}",
        f"Username: @{user.username}" if getattr(user, "username", None) else "Username: -",
        f"Time: {_timestamp()}",
    ]
    try:
        await client.send_message(LOG_CHANNEL, "\n".join(lines), parse_mode=enums.ParseMode.DISABLED)
    except Exception as exc:
        log_event(logger, logging.WARNING, "log_new_user_failed", error=type(exc).__name__)


async def log_media(client, message, kind: str, sender_id: int) -> None:
    """Send a moderation copy of `message` to LOG_CHANNEL with the sender's ID."""
    header = [
        "NEW MEDIA MESSAGE",
        "",
        f"Sender ID: {sender_id}",
        f"Type: {_LABELS.get(kind, kind)}",
        f"Time: {_timestamp()}",
    ]
    if getattr(message, "media_group_id", None):
        header.append(f"Album: {message.media_group_id}")
    header_text = "\n".join(header)
    try:
        if kind in _CAPTIONABLE:
            caption = header_text
            original: Optional[str] = getattr(message, "caption", None)
            if original:
                caption = f"{header_text}\n\nCaption: {original}"
            await message.copy(
                LOG_CHANNEL,
                caption=caption[:_CAPTION_LIMIT],
                parse_mode=enums.ParseMode.DISABLED,
            )
        else:
            # Stickers / video notes / location etc. cannot carry a caption.
            copied = await message.copy(LOG_CHANNEL)
            try:
                await client.send_message(
                    LOG_CHANNEL, header_text,
                    reply_to_message_id=copied.id,
                    parse_mode=enums.ParseMode.DISABLED,
                )
            except TypeError:  # newer Pyrogram forks dropped reply_to_message_id
                await client.send_message(LOG_CHANNEL, header_text, parse_mode=enums.ParseMode.DISABLED)
    except Exception as exc:
        # Never fall back to any other chat; just record that logging failed.
        log_event(logger, logging.WARNING, "log_media_failed", kind=kind, error=type(exc).__name__)
