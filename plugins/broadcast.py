import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional

from pyrogram import Client, filters
from pyrogram.errors import FloodWait
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import service
import texts
from config import ADMIN_IDS
from database import db
from logutil import log_event

logger = logging.getLogger(__name__)

PROGRESS_INTERVAL = 7      # seconds between progress edits
BATCH_SIZE = 20            # concurrent sends per round (keeps well under Telegram's ~30 msg/s)
BATCH_PAUSE = 0.8          # seconds between rounds

CANCEL_MARKUP = InlineKeyboardMarkup(
    [[InlineKeyboardButton("🛑 Cancel Broadcast", callback_data="bc_cancel")]]
)


@dataclass
class _Broadcast:
    total: int
    started: float = field(default_factory=time.monotonic)
    sent: int = 0
    blocked: int = 0
    failed: int = 0
    cancel: asyncio.Event = field(default_factory=asyncio.Event)
    task: Optional[asyncio.Task] = None

    @property
    def done(self) -> int:
        return self.sent + self.blocked + self.failed


_current: Optional[_Broadcast] = None


def _fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    if m:
        return f"{m}m {s:02d}s"
    return f"{s}s"


def _render(b: _Broadcast, title: str) -> str:
    percent = int(b.done * 100 / b.total) if b.total else 100
    return (
        f"{title}\n\n"
        f"👥 <b>Total users:</b> {b.total}\n"
        f"✅ <b>Broadcast send:</b> {b.sent}\n"
        f"🚫 <b>Blocked users:</b> {b.blocked}\n"
        f"⚠️ <b>Failed:</b> {b.failed}\n"
        f"⏱ <b>Time taken:</b> {_fmt_time(time.monotonic() - b.started)}\n"
        f"📊 <b>Progress:</b> {percent}% ({b.done}/{b.total})"
    )


async def _safe_edit(status: Message, text: str, markup=None) -> None:
    try:
        await status.edit_text(text, reply_markup=markup)
    except FloodWait:
        pass  # skip this tick; the next one catches up
    except Exception as exc:  # e.g. MessageNotModified
        log_event(logger, logging.DEBUG, "broadcast_edit_skipped", error=type(exc).__name__)


async def _send_one(client: Client, uid: int, from_chat: int, msg_id: int) -> str:
    """Copy the admin's message to one user -> 'ok' | 'blocked' | 'failed'."""
    for attempt in range(2):
        try:
            await client.copy_message(uid, from_chat, msg_id)
            return "ok"
        except FloodWait as exc:
            if attempt == 0:
                await asyncio.sleep(min(getattr(exc, "value", 0) or 0, 60) + 1)
                continue
            return "failed"
        except service.UNREACHABLE_ERRORS:
            return "blocked"
        except Exception as exc:  # never log content
            log_event(logger, logging.WARNING, "broadcast_send_failed", error=type(exc).__name__)
            return "failed"
    return "failed"


async def _ticker(b: _Broadcast, status: Message) -> None:
    while True:
        await asyncio.sleep(PROGRESS_INTERVAL)
        await _safe_edit(status, _render(b, "📢 <b>Broadcast in progress...</b>"), CANCEL_MARKUP)


async def _run(client: Client, b: _Broadcast, status: Message, ids: list, from_chat: int, msg_id: int) -> None:
    global _current
    ticker = asyncio.ensure_future(_ticker(b, status))
    try:
        for i in range(0, len(ids), BATCH_SIZE):
            if b.cancel.is_set():
                break
            batch = ids[i:i + BATCH_SIZE]
            results = await asyncio.gather(*(_send_one(client, uid, from_chat, msg_id) for uid in batch))
            for r in results:
                if r == "ok":
                    b.sent += 1
                elif r == "blocked":
                    b.blocked += 1
                else:
                    b.failed += 1
            await asyncio.sleep(BATCH_PAUSE)
        title = "🛑 <b>Broadcast cancelled</b>" if b.cancel.is_set() else "✅ <b>Broadcast completed</b>"
    except Exception as exc:
        log_event(logger, logging.ERROR, "broadcast_error", error=type(exc).__name__)
        title = f"⚠️ <b>Broadcast stopped ({type(exc).__name__})</b>"
    finally:
        ticker.cancel()
        _current = None
    await _safe_edit(status, _render(b, title))  # no markup -> cancel button removed
    log_event(logger, logging.INFO, "broadcast_finished", total=b.total, sent=b.sent, blocked=b.blocked, failed=b.failed)


def _is_admin(user) -> bool:
    return user is not None and user.id in ADMIN_IDS


@Client.on_message(filters.command("broadcast") & filters.private)
async def broadcast_handler(client: Client, message: Message):
    global _current
    user = message.from_user
    if not _is_admin(user):
        if user is not None:  # answer like any unknown command
            await service.send_text(client, user.id, texts.UNKNOWN_COMMAND)
        return

    source = message.reply_to_message
    if source is None:
        await service.send_text(
            client, message.chat.id,
            "Usage: reply to the message you want to send with /broadcast")
        return
    if _current is not None:
        await service.send_text(client, message.chat.id, "⚠️ A broadcast is already running. Cancel it or wait for it to finish.")
        return

    try:
        ids = await db.list_broadcast_ids()
    except Exception as exc:
        log_event(logger, logging.ERROR, "db_unavailable", op="list_broadcast_ids", error=type(exc).__name__)
        await service.send_text(client, message.chat.id, "⚠️ Database unavailable. Try again shortly.")
        return
    if not ids:
        await service.send_text(client, message.chat.id, "ℹ️ There are no users to broadcast to.")
        return

    b = _Broadcast(total=len(ids))
    _current = b  # claimed before any further await
    status = await message.reply_text(_render(b, "📢 <b>Broadcast started...</b>"), reply_markup=CANCEL_MARKUP)
    b.task = asyncio.ensure_future(_run(client, b, status, ids, source.chat.id, source.id))


@Client.on_callback_query(filters.regex(r"^bc_cancel$"))
async def broadcast_cancel_callback(client: Client, query: CallbackQuery):
    if not _is_admin(query.from_user):
        await query.answer("Not allowed.", show_alert=True)
        return
    if _current is None or _current.cancel.is_set():
        await query.answer("No broadcast is running.")
        return
    _current.cancel.set()
    await query.answer("Cancelling…")
