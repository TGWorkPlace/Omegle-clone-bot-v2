from pyrogram import Client, filters
from pyrogram.types import Message

import service


@Client.on_message(filters.command("chat") & filters.private)
async def chat_handler(client: Client, message: Message):
    if message.from_user:
        await service.handle_chat(client, message.from_user)


@Client.on_message(filters.command("next") & filters.private)
async def next_handler(client: Client, message: Message):
    if message.from_user:
        await service.handle_next(client, message.from_user)


@Client.on_message(filters.command("stop") & filters.private)
async def stop_handler(client: Client, message: Message):
    if message.from_user:
        await service.handle_stop(client, message.from_user)
