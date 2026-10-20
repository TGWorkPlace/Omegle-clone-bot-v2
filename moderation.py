"""
Moderation logging to LOG_CHANNEL. Deliberately independent from matchmaking
and relaying: it only ever sends to LOG_CHANNEL, runs in the background, and
swallows its own failures.

This is the ONLY place that copies a user's message (to the private log channel).
The anonymous relay never copies or forwards. /report re-sends the reported message
by file_id (relayed messages are protect_content, so Telegram would refuse a copy).
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


# ---------------------------------------------------------------- /report
_TEXT_LIMIT = 4096


def _full_name(user) -> str:
    if user is None:
        return ""
    return " ".join(p for p in (getattr(user, "first_name", None), getattr(user, "last_name", None)) if p)


def _person_lines(label: str, user_id: int, user) -> list:
    username = getattr(user, "username", None) if user is not None else None
    return [
        f"{label} ID: {user_id}",
        f"{label} name: {_full_name(user) or '-'}",
        f"{label} username: @{username}" if username else f"{label} username: -",
    ]


async def _resend(client, chat_id: int, m, kind: str, caption: Optional[str] = None):
    """Send the content of `m` to `chat_id` by file_id (no copy/forward, so protect_content is irrelevant)."""
    kw = {"caption": caption, "parse_mode": enums.ParseMode.DISABLED} if caption is not None else {}
    if kind == "photo":
        return await client.send_photo(chat_id, m.photo.file_id, **kw)
    if kind == "video":
        return await client.send_video(chat_id, m.video.file_id, **kw)
    if kind == "animation":
        return await client.send_animation(chat_id, m.animation.file_id, **kw)
    if kind == "voice":
        return await client.send_voice(chat_id, m.voice.file_id, **kw)
    if kind == "audio":
        return await client.send_audio(chat_id, m.audio.file_id, **kw)
    if kind == "document":
        return await client.send_document(chat_id, m.document.file_id, **kw)
    if kind == "video_note":
        return await client.send_video_note(chat_id, m.video_note.file_id)
    if kind == "sticker":
        return await client.send_sticker(chat_id, m.sticker.file_id)
    if kind == "location":
        return await client.send_location(chat_id, m.location.latitude, m.location.longitude)
    if kind == "venue":
        v = m.venue
        return await client.send_venue(chat_id, v.location.latitude, v.location.longitude, v.title, v.address)
    if kind == "contact":
        c = m.contact
        return await client.send_contact(
            chat_id, c.phone_number, c.first_name, last_name=c.last_name or "", vcard=c.vcard or "")
    raise ValueError("unsupported kind")


async def log_report(client, reporter, reported_id: int, reported_user, message, kind: Optional[str],
                     reason: Optional[str] = None) -> bool:
    """
    Send a user report to LOG_CHANNEL: reported user's ID / name / username (if any),
    the reporter, the optional reason and the reported message itself.
    Returns True when the report reached the channel.
    """
    lines = ["🚩 USER REPORT", ""]
    lines += _person_lines("Reported user", reported_id, reported_user)
    lines.append("")
    lines += _person_lines("Reporter", reporter.id, reporter)
    lines.append(f"Message type: {_LABELS.get(kind, kind) if kind else 'unavailable'}")
    if reason:
        lines.append(f"Reason: {reason}")
    lines.append(f"Time: {_timestamp()}")
    header = "\n".join(lines)
    off = enums.ParseMode.DISABLED

    try:
        if kind == "text":
            text = getattr(message, "text", None) or ""
            body = f"{header}\n\nMessage:\n{text}"
            await client.send_message(LOG_CHANNEL, body[:_TEXT_LIMIT], parse_mode=off)
        elif kind in _CAPTIONABLE:
            original: Optional[str] = getattr(message, "caption", None)
            caption = f"{header}\n\nCaption: {original}" if original else header
            await _resend(client, LOG_CHANNEL, message, kind, caption[:_CAPTION_LIMIT])
        elif kind is not None:
            # Stickers / video notes / location etc. cannot carry a caption.
            await _resend(client, LOG_CHANNEL, message, kind)
            await client.send_message(LOG_CHANNEL, header, parse_mode=off)
        else:
            await client.send_message(LOG_CHANNEL, f"{header}\n\n(Message content unavailable)", parse_mode=off)
        return True
    except Exception as exc:
        log_event(logger, logging.WARNING, "log_report_failed", kind=kind or "none", error=type(exc).__name__)
        return False
