from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

import service
import texts


@Client.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    if message.from_user is None:
        return
    user = message.from_user
    gate = await service.check_access(client, user)  # registers the user + verifies subscription
    if gate is service.Gate.OK:
        await service.send_welcome(client, user.id)
    else:
        await service.deny(client, user.id, gate)


@Client.on_message(filters.command("help") & filters.private)
async def help_handler(client: Client, message: Message):
    await service.send_text(client, message.chat.id, texts.HELP)


@Client.on_callback_query(filters.regex(r"^check_sub$"))
async def check_subscription_callback(client: Client, query: CallbackQuery):
    user = query.from_user
    gate = await service.check_access(client, user)  # always a fresh server-side check

    async def answer(text=None, alert=False):
        try:
            await query.answer(text, show_alert=alert)
        except Exception:
            pass  # expired callback ids are harmless

    if gate is service.Gate.OK:
        await answer("✅ Verified!")
        try:
            await query.message.edit_text(texts.SUB_VERIFIED)
        except Exception:
            pass  # message may be too old / unchanged
        await service.send_welcome(client, user.id)
    elif gate is service.Gate.NOT_SUBSCRIBED:
        await answer(texts.SUB_NOT_JOINED + " Join it, then tap the button again.", alert=True)
    elif gate is service.Gate.BANNED:
        await answer(texts.BANNED, alert=True)
    else:
        await answer(texts.UNAVAILABLE, alert=True)
