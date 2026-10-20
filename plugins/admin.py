import functools
import logging

from pyrogram import Client, filters
from pyrogram.types import Message

import service
import texts
from config import ADMIN_ID
from logutil import log_event

logger = logging.getLogger(__name__)


async def _authorized(client: Client, message: Message) -> bool:
    user = message.from_user
    if user is not None and user.id == ADMIN_ID:
        return True
    # Don't reveal that admin commands exist: answer like any unknown command.
    if user is not None:
        log_event(logger, logging.WARNING, "admin_command_rejected", user=user.id)
        await service.send_text(client, user.id, texts.UNKNOWN_COMMAND)
    return False


async def _reply(client: Client, message: Message, text: str) -> None:
    await service.send_text(client, message.chat.id, text)


def _guarded(handler):
    """Log (and tell the admin about) unexpected errors instead of failing silently."""
    @functools.wraps(handler)
    async def wrapper(client: Client, message: Message):
        try:
            await handler(client, message)
        except Exception as exc:
            log_event(logger, logging.ERROR, "admin_handler_error", handler=handler.__name__, error=type(exc).__name__)
            try:
                await _reply(client, message, f"⚠️ Command failed ({type(exc).__name__}). Check the bot logs.")
            except Exception:
                pass
    return wrapper


def _parse_user_id(message: Message):
    """Return (user_id, rest_of_args) or (None, None) when missing/invalid."""
    args = message.command[1:] if message.command else []
    if not args:
        return None, None
    raw = args[0]
    if not raw.isdigit():  # also rejects negatives: user IDs are positive
        return None, None
    user_id = int(raw)
    if user_id <= 0:
        return None, None
    return user_id, " ".join(args[1:]).strip() or None


@Client.on_message(filters.command("ban") & filters.private)
@_guarded
async def ban_handler(client: Client, message: Message):
    if not await _authorized(client, message):
        return
    target, reason = _parse_user_id(message)
    if target is None:
        await _reply(client, message, "Usage: /ban USER_ID [reason]\nExample: /ban 123456789")
        return
    if target == ADMIN_ID:
        await _reply(client, message, "⚠️ You can't ban the administrator account.")
        return
    db_ok, left = await service.ban_user(client, target, reason)
    lines = [f"✅ User {target} has been banned."]
    if left.session is not None:
        lines.append("Their active chat was ended and the partner was notified.")
    elif left.was_waiting:
        lines.append("They were removed from the waiting queue.")
    if not db_ok:
        lines.append(
            "⚠️ The database write failed: the ban is active in memory only and will be "
            "lost on restart. Run /ban again once MongoDB is reachable."
        )
    await _reply(client, message, "\n".join(lines))


@Client.on_message(filters.command("unban") & filters.private)
@_guarded
async def unban_handler(client: Client, message: Message):
    if not await _authorized(client, message):
        return
    target, _ = _parse_user_id(message)
    if target is None:
        await _reply(client, message, "Usage: /unban USER_ID\nExample: /unban 123456789")
        return
    outcome = await service.unban_user(target)
    if outcome is None:
        text = "⚠️ Database unavailable. The user was NOT unbanned; try again shortly."
    elif outcome:
        text = f"✅ User {target} has been unbanned."
    else:
        text = f"ℹ️ User {target} was not banned."
    await _reply(client, message, text)
    if outcome:
        await service.send_text(client, target, "✅ You have been unbanned. Use /chat to start again.")


@Client.on_message(filters.command("stats") & filters.private)
@_guarded
async def stats_handler(client: Client, message: Message):
    if not await _authorized(client, message):
        return
    await _reply(client, message, await service.collect_stats())
