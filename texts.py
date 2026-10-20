"""User-facing strings (Telegram HTML parse mode required)."""

WELCOME = (
    "💗 <b>Welcome to 𝑳𝒐𝒗𝒆 𝑫𝒂𝒕𝒆 𝑩𝒐𝒕</b> 💗\n\n"
    "<blockquote>💕 <i>Every stranger is a new possibility. "
    "Every conversation could be the beginning of something beautiful.</i></blockquote>\n\n"
    "💬 <b>Meet new people</b>\n"
    "❤️ Make friends, flirt, and discover unexpected connections.\n"
    "🎭 Chat anonymously without revealing your Telegram identity to your partner.\n\n"
    "🦋 <i>Your next connection could be just one message away!</i>\n\n"
    "💘 <b>Ready to meet someone special?</b>\n"
    "👉 Use /chat to find your match.\n"
    "ℹ️ Use /help to learn more."
)

HELP = (
    "💗 <b>𝑳𝒐𝒗𝒆 𝑫𝒂𝒕𝒆 𝑩𝒐𝒕 — Help</b> 💗\n\n"
    "💬 <b>Commands</b>\n"
    "▫️ /chat — Meet a random person\n"
    "▫️ /next — Leave your current chat and meet someone new\n"
    "▫️ /stop — End your chat or cancel your search\n"
    "▫️ /help — Show this message\n\n"
    "💕 <b>How It Works</b>\n"
    "<blockquote>Start with /chat and we'll connect you with a random person. "
    "Break the ice, make friends, flirt, or see where the conversation goes!</blockquote>\n\n"
    "🎭 <b>Anonymous Conversations</b>\n"
    "Your Telegram name, username, and ID are not shared with your chat partner "
    "through messages relayed by the bot.\n\n"
    "📸 <b>Supported Messages</b>\n"
    "<i>Text, photos, videos, voice messages, audio, documents, stickers, GIFs, "
    "video notes, locations, contacts, and venues.</i>\n\n"
    "🔒 <b>Privacy &amp; Safety</b>\n"
    "▫️ Your partner won't automatically see your Telegram profile details "
    "through messages relayed by the bot.\n"
    "▫️ Chat data handling depends on the bot's implementation and storage settings.\n"
    "▫️ This bot does not guarantee end-to-end encryption.\n"
    "▫️ Your partner may screenshot, copy, or save messages and media.\n"
    "▫️ Avoid sharing passwords, financial information, or sensitive personal details.\n"
    "▫️ Treat everyone with respect. Abusive users may be banned.\n\n"
    "<blockquote>💗 <i>Be yourself, stay safe, and enjoy meeting new people!</i></blockquote>"
)

SEARCHING = (
    "💘 <b>Looking for someone special...</b>\n\n"
    "<blockquote>🔎 Searching for a stranger to connect with you.\n"
    "💕 Your next interesting conversation might be just around the corner!</blockquote>"
)

CONNECTED = (
    "💗 <b>It's a Match!</b> 💗\n"
    "<i>Well, a conversation at least! 😉</i>\n\n"
    "<blockquote>✨ You've been connected with someone new.\n"
    "💬 Say hello and let the conversation begin!\n"
    "💕 Be friendly, be yourself, and see where things go.</blockquote>\n\n"
    "🔄 <b>/next</b> — Meet someone else\n"
    "🛑 <b>/stop</b> — End this conversation"
)

PARTNER_LEFT = (
    "💔 <b>Your chat partner has left.</b>\n\n"
    "<blockquote>💕 Don't worry! There are more people to meet "
    "and new conversations to discover.</blockquote>\n\n"
    "💘 Use /chat to start a new conversation."
)

CHAT_ENDED = (
    "💗 <b>Conversation Ended</b>\n\n"
    "<blockquote>✨ Every ending makes room for a new connection.</blockquote>\n\n"
    "💕 Use /chat whenever you're ready to meet someone new."
)

SEARCH_CANCELLED = (
    "🛑 <b>Search Cancelled</b>\n\n"
    "<blockquote>💕 Someone new could be waiting for you!</blockquote>\n\n"
    "💗 Use /chat whenever you're ready."
)

NOT_ACTIVE = (
    "💬 <b>You're not chatting with anyone right now.</b>\n\n"
    "<blockquote>💗 Ready to meet someone new?</blockquote>\n\n"
    "💕 Use /chat to find a random person."
)

STILL_SEARCHING = (
    "🔎 <b>Still Looking...</b>\n\n"
    "<blockquote>💕 Please wait while we search for someone "
    "to connect with you.</blockquote>\n\n"
    "🛑 Use /stop if you'd like to cancel."
)

ALREADY_CHATTING = (
    "💞 <b>You're Already Chatting!</b>\n\n"
    "<blockquote>💕 Enjoy your conversation, or meet someone new "
    "whenever you're ready.</blockquote>\n\n"
    "🔄 <b>/next</b> — Find someone else\n"
    "🛑 <b>/stop</b> — End the conversation"
)

ALREADY_WAITING = (
    "🔎 <b>You're Already in the Queue!</b>\n\n"
    "<blockquote>💕 Please wait while we look for someone to connect with you.</blockquote>\n\n"
    "🛑 Use /stop to cancel your search."
)

CHAT_GONE = (
    "💔 <b>This Conversation Has Ended</b>\n\n"
    "<blockquote>💕 There are more people to meet and more stories to discover!</blockquote>\n\n"
    "💗 Use /chat to start a new conversation."
)

BANNED = (
    "🚫 <b>Access Restricted</b>\n\n"
    "<blockquote>You cannot use 𝑳𝒐𝒗𝒆 𝑫𝒂𝒕𝒆 𝑩𝒐𝒕 because your account has been banned.</blockquote>"
)

UNAVAILABLE = (
    "🥺 <b>Something Went Wrong</b>\n\n"
    "<blockquote>💗 We're having a little trouble on our side. "
    "Please try again in a moment!</blockquote>"
)

UNSUPPORTED = (
    "⚠️ <b>Unsupported Message</b>\n\n"
    "<blockquote>💬 This type of message isn't supported in the current chat. "
    "Try sending text or another supported type of media.</blockquote>"
)

DELIVERY_FAILED = (
    "💔 <b>Message Not Delivered</b>\n\n"
    "<blockquote>Your message couldn't be delivered. "
    "Please try again. If the problem continues, your chat may have ended.</blockquote>"
)

UNKNOWN_COMMAND = (
    "❓ <b>Unknown Command</b>\n\n"
    "<blockquote>💗 I'm not familiar with that command. "
    "Use /help to see the available commands.</blockquote>"
)

SUB_REQUIRED = (
    "💗 <b>One Little Step Before You Begin!</b>\n\n"
    "<blockquote>💕 Join our required channels to unlock "
    "𝑳𝒐𝒗𝒆 𝑫𝒂𝒕𝒆 𝑩𝒐𝒕 and start meeting new people!</blockquote>\n\n"
    "✅ Once you've joined, tap <b>Check Subscription</b>.\n\n"
    "💘 Your next connection is waiting!"
)

SUB_REQUIRED_NO_LINK = (
    "💗 <b>Welcome to 𝑳𝒐𝒗𝒆 𝑫𝒂𝒕𝒆 𝑩𝒐𝒕!</b>\n\n"
    "<blockquote>💕 Please join all required channels to continue.</blockquote>\n\n"
    "✅ Then tap <b>Check Subscription</b> to get started."
)

SUB_VERIFIED = (
    "✅ <b>Subscription Verified!</b>\n\n"
    "<blockquote>💗 You're all set! Welcome to 𝑳𝒐𝒗𝒆 𝑫𝒂𝒕𝒆 𝑩𝒐𝒕.\n"
    "💕 New people and new conversations are waiting for you!</blockquote>\n\n"
    "💘 Use /chat to meet someone new."
)

SUB_NOT_JOINED = (
    "❌ <b>Subscription Incomplete</b>\n\n"
    "<blockquote>💕 Please join all the required channels first, "
    "then tap <b>Check Subscription</b> again.</blockquote>"
)

SHUTDOWN = (
    "🔄 <b>𝑳𝒐𝒗𝒆 𝑫𝒂𝒕𝒆 𝑩𝒐𝒕 Is Restarting</b>\n\n"
    "<blockquote>💗 Your current conversation has ended because the bot is restarting.\n"
    "Please come back shortly to meet someone new!</blockquote>"
)
