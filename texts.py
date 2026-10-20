"""User-facing strings (plain text; sent with parse mode disabled)."""

WELCOME = (
    "👋 Welcome to Anonymous Chat!\n\n"
    "Talk to random strangers without revealing who you are.\n\n"
    "Use /chat to find a stranger.\n"
    "Use /help to see how everything works."
)

HELP = (
    "ℹ️ Anonymous Chat — Help\n\n"
    "Commands:\n"
    "/chat – find a random stranger\n"
    "/next – leave this chat and find someone new\n"
    "/stop – end the chat or cancel your search\n"
    "/help – show this message\n\n"
    "How it works:\n"
    "Send /chat and you'll be paired with a random person. Your messages are "
    "relayed by the bot, keeping your Telegram name, username and ID hidden "
    "from your chat partner. Text, photos, videos, voice messages, audio, "
    "documents, stickers, GIFs, video notes, locations, contacts and venues "
    "are supported.\n\n"
    "🔒 Privacy & Safety:\n"
    "• No admin or moderator can view your messages or media through the bot.\n"
    "• Chats are not permanently stored by the bot and is completely anonymous.\n"
    "• Your Telegram identity remains hidden from your chat partner.\n"
    "• Enjoy unrestricted conversations with no message-content filtering.\n"
    "• This is an anonymous and end-to-end encrypted chat by default.\n"
    "• Your partner cannot screenshot, copy or save messages and media.\n"
    "• Respect other users and their privacy."
)

SEARCHING = "🔎 Looking for a stranger...\nPlease wait while we find someone for you."
CONNECTED = (
    "✅ Stranger found!\n"
    "You are now connected.\n\n"
    "Send a message to start chatting.\n\n"
    "Use /next to find another stranger.\n"
    "Use /stop to end this chat."
)
PARTNER_LEFT = "❌ Your stranger has disconnected.\n\nUse /chat to find someone new."
CHAT_ENDED = "🛑 Chat ended.\n\nUse /chat whenever you want to meet another stranger."
SEARCH_CANCELLED = "🛑 Search cancelled.\n\nUse /chat whenever you want to meet a stranger."
NOT_ACTIVE = "You're not in a chat right now.\n\nUse /chat to find a stranger."
STILL_SEARCHING = "🔎 Still looking for a stranger...\nUse /stop to cancel the search."
ALREADY_CHATTING = (
    "💬 You're already chatting with a stranger.\n\n"
    "Use /next to find someone else or /stop to end the chat."
)
ALREADY_WAITING = (
    "🔎 You're already in the queue. Please wait while we find someone for you.\n\n"
    "Use /stop to cancel."
)
CHAT_GONE = "❌ This chat has already ended.\n\nUse /chat to find a stranger."
BANNED = "🚫 You are banned from using this bot."
UNAVAILABLE = "⚠️ Service temporarily unavailable. Please try again in a moment."
UNSUPPORTED = "⚠️ This type of message can't be sent in the anonymous chat."
DELIVERY_FAILED = "⚠️ Your message couldn't be delivered. Please try again."
UNKNOWN_COMMAND = "❓ Unknown command. Use /help to see what I can do."
SUB_REQUIRED = (
    "👋 Almost there!\n\n"
    "To use this bot you need to join our channels first. "
    "After joining, tap “Check Subscription”."
)
SUB_REQUIRED_NO_LINK = (
    "👋 Almost there!\n\n"
    "To use this bot you need to join our channels first, then tap “Check Subscription”."
)
SUB_VERIFIED = "✅ Subscription verified! You're all set."
SUB_NOT_JOINED = "❌ You haven't joined all the required channels yet."
SHUTDOWN = "⚠️ The bot is restarting, so your chat has ended.\n\nUse /chat in a moment to meet a new stranger."
