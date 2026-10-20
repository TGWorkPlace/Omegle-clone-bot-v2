# Anonymous Random Chat Bot (Omegle-style)

A Telegram bot that pairs strangers for anonymous 1-to-1 chats. Built on the
existing plugin skeleton: [Pyrogram](https://docs.pyrogram.org/) (kurigram fork),
Motor/MongoDB, and the aiohttp health-check server for Koyeb.

Many chats run at once and are fully independent: every message is routed through a
per-user session lookup, never through a global "current partner".

## Structure

```
bot.py            Client + startup/shutdown (DB init, janitor, clears state on stop)
config.py         Env-based configuration + startup validation
database.py       Motor access: users collection only (no sessions, no transcripts)
matchmaking.py    In-memory queue + sessions (asyncio lock, O(1) random match)
service.py        Gate checks, /chat /next /stop logic, relay, admin actions, stats
subscription.py   Force-subscription checks (server-side get_chat_member)
moderation.py     LOG_CHANNEL logging (separate from routing)
texts.py          User-facing strings
logutil.py        Structured "event=… key=value" logging helper
plugins/          Thin handlers, auto-loaded by Pyrogram
  start.py        /start, /help, "Check Subscription" button
  chat.py         /chat, /next, /stop, /report
  admin.py        /ban, /unban, /stats (ADMIN_IDS only)
  relay.py        Anonymous message relay + unknown-command reply
```

## Configuration

Everything comes from environment variables (a local `.env` is also read if present;
see `.env.example`). The bot refuses to start and lists the *names* of missing or
invalid values — never the values.

| Variable | Required | Description |
|---|---|---|
| `API_ID`, `API_HASH` | yes | From https://my.telegram.org (needed by Pyrogram) |
| `BOT_TOKEN` | yes | From @BotFather |
| `MONGO_URI` | yes | MongoDB connection string (MongoDB **4.2+**) |
| `DB_NAME` | no | Database name (default `anonymous_chat_bot`) |
| `AUTH_CHANNELS` | yes | 1–5 numeric channel IDs, comma-separated (e.g. `-1001111,-1002222`). Users must join **all** of them. The old single `AUTH_CHANNEL` still works |
| `AUTH_CHANNEL_URLS` | no* | Join links, comma-separated, same order as `AUTH_CHANNELS` (empty entry = auto). *Required for private channels* |
| `LOG_CHANNEL` | yes | Numeric ID of the moderation channel |
| `LOG_MEDIA_TYPES` | no | Media types copied to the log channel (default: photo, video, video_note, animation, voice, audio, document) |
| `ADMIN_IDS` | yes | One or more numeric Telegram user IDs, comma-separated (e.g. `111,222,333`). Every listed user can run the admin commands. The old `ADMIN_ID` still works (it may also hold a list) |

### MongoDB
Create a free cluster (e.g. MongoDB Atlas), create a database user, allow your host's
IP (or `0.0.0.0/0` on Koyeb), and put the connection string in `MONGO_URI`. The
database and `users` collection plus indexes are created automatically on first start.
Only user records and ban state are stored: `_id`, `first_seen`, `last_seen`,
`is_banned`, `ban_reason`, `total_chats` (and `banned_at`).

### Force subscription (`AUTH_CHANNELS`)
1. Create/choose up to 5 channels and add the bot as an **administrator** of each (needed for reliable `get_chat_member` results, especially for larger channels).
2. Get each numeric ID (forward a channel post to a bot like @RawDataBot, or use a Telegram client that shows IDs). It starts with `-100`.
3. Public channels: the join button is built from the channel's `@username`. Private channels: set the matching entry in `AUTH_CHANNEL_URLS` to an invite link — the bot never invents one.

The subscription prompt shows one join button per channel the user is still missing, plus "Check Subscription". Non-members of any channel cannot join the queue, get matched, or relay messages. Membership is
checked on the server on `/start`, `/chat`, `/next` and the button; while chatting, a
positive result is reused for 2 minutes. If the check itself fails, access is denied.

### Moderation channel (`LOG_CHANNEL`)
Create a **private** channel, add the bot as an admin with permission to post, and set
its numeric ID. Logged: `NEW USER` events, a copy of selected media (sender's
Telegram ID in the caption, album ID for albums) and `USER REPORT`s (see `/report`). Chat text is never logged, except a message that a user explicitly reports. Restrict
channel access to trusted moderators.

### Administrators (`ADMIN_IDS`)
Numeric Telegram user IDs of everyone who may moderate (e.g. from @userinfobot), separated by commas: `ADMIN_IDS=111111111,222222222`. An administrator cannot be banned with `/ban`.

## Commands

| Command | Who | Effect |
|---|---|---|
| `/start`, `/help`, `/chat`, `/next`, `/stop` | everyone | as described in `/help` |
| `/report [reason]` | everyone | Swipe-reply to a message from your partner with `/report`. The reported user's ID, name and username (if available), the reporter, the optional reason and the message itself are sent to `LOG_CHANNEL` |
| `/ban USER_ID [reason]` | admin | Bans, ends their chat/queue entry, notifies the partner. Works for IDs that never started the bot |
| `/unban USER_ID` | admin | Clears the ban (they must `/chat` again) |
| `/stats` | admin | Registered / online (active in last 5 min) / waiting / sessions / chatting / banned / sessions since restart |

Non-admins who try admin commands get the same reply as for any unknown command.

## Run locally

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in real values
python bot.py
```

## Deploy (Koyeb / any Docker host)

The unchanged `Dockerfile` works. Set the variables above as secrets/env vars; port
`8080` serves the health check (`/health`). Polling is preserved. On SIGTERM the bot
tells active users their chat ended, clears all in-memory state, and closes MongoDB.

## Troubleshooting

- **"Invalid configuration" at start** – the listed variable names are missing/non-numeric.
- **Everyone sees "Service temporarily unavailable"** – MongoDB unreachable (check `MONGO_URI` / IP allow-list) *or* the subscription check fails (bot not admin in an `AUTH_CHANNELS` channel, wrong ID). Look for `event=db_unavailable` / `event=subscription_check_failed` in the logs.
- **`channel_unreachable` warning** – wrong channel ID or the bot isn't a member/admin. If Telegram says the peer is unknown, post any message in the channel once so the bot sees it.
- **No join button** – private channel without a matching `AUTH_CHANNEL_URLS` entry.
- **Nothing appears in LOG_CHANNEL** – bot lacks post permission (`event=log_media_failed`). Chats continue to work.
- **Pairs vanish after a redeploy** – by design: sessions live only in memory.

## Limitations (Telegram Bot API)

- Relayed messages use `protect_content`, which blocks forwarding/saving in Telegram clients. Nothing can stop screenshots, screen recording, manual copying of text, or a client that ignores the flag.
- This is **not end-to-end encrypted**: the bot server sees all traffic and moderators see logged media (stated in `/help`).
- Albums are re-sent as albums after a ~1 s collection window (max 10 items). Polls, dice, games and other types are rejected with a notice. Edits and deletions are not mirrored. Swipe-replies are preserved (the partner sees a real reply) for messages relayed since the last restart; quotes of part of a message are not.
- Location, contact and venue messages contain whatever the sender chooses to share.
- A user who leaves any `AUTH_CHANNELS` channel mid-chat is removed at their next message (within the cache window).
- Single process only: sessions are not shared between multiple instances.
