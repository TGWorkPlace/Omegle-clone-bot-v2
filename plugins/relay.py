from pyrogram import Client, filters
from pyrogram.types import Message

import service
import texts

KNOWN_COMMANDS = ["start", "help", "chat", "next", "stop", "report", "ban", "unban", "stats", "broadcast"]


async def _command_like(_, __, message: Message) -> bool:
    text = message.text or message.caption or ""
    return text.startswith("/")


command_like = filters.create(_command_like)


# Every non-command private message is relayed to the sender's current partner.
@Client.on_message(filters.private & ~filters.service & ~command_like)
async def relay_handler(client: Client, message: Message):
    await service.relay_message(client, message)


# Unknown "/something" is never relayed to the stranger.
@Client.on_message(filters.private & command_like & ~filters.command(KNOWN_COMMANDS))
async def unknown_command_handler(client: Client, message: Message):
    await service.send_text(client, message.chat.id, texts.UNKNOWN_COMMAND)
