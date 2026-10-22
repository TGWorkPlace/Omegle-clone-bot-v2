"""
Configuration - reads from environment variables.

A local ``.env`` file is loaded automatically if python-dotenv is installed
(handy for local runs). On hosting platforms, set real environment variables.
"""
import os
import re

try:  # optional convenience for local development
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # pragma: no cover
    pass

_problems = []


def _str_env(name: str, required: bool = True, default: str = "") -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        if required:
            _problems.append(f"{name} is missing")
        return default
    return value


def _int_env(name: str, required: bool = True) -> int:
    """Parse an integer (negative values such as -100… channel IDs allowed)."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        if required:
            _problems.append(f"{name} is missing")
        return 0
    try:
        return int(raw)
    except ValueError:
        # Never echo the value: it may be a credential typed into the wrong field.
        _problems.append(f"{name} must be a numeric Telegram ID")
        return 0


# --- Telegram client credentials (required by Pyrogram) -------------------
API_ID = _int_env("API_ID")
API_HASH = _str_env("API_HASH")
BOT_TOKEN = _str_env("BOT_TOKEN")

# --- Database -------------------------------------------------------------
MONGO_URI = _str_env("MONGO_URI")
DB_NAME = _str_env("DB_NAME", required=False, default="anonymous_chat_bot")

# --- Channels / admin -----------------------------------------------------
MAX_AUTH_CHANNELS = 5


def _parse_channels() -> list:
    """
    OPTIONAL. AUTH_CHANNELS="-100111,-100222" (1-5 numeric IDs). Falls back to the legacy
    single AUTH_CHANNEL. Unset/empty = force-subscription disabled (everyone passes the gate).
    """
    raw = os.environ.get("AUTH_CHANNELS", "").strip() or os.environ.get("AUTH_CHANNEL", "").strip()
    if not raw:
        return []
    ids = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            _problems.append("AUTH_CHANNELS must be comma-separated numeric Telegram IDs")
            return []
    if len(ids) > MAX_AUTH_CHANNELS:
        _problems.append(f"AUTH_CHANNELS supports at most {MAX_AUTH_CHANNELS} channels")
    elif len(set(ids)) != len(ids):
        _problems.append("AUTH_CHANNELS contains duplicate IDs")
    return ids


AUTH_CHANNELS = _parse_channels()          # force-subscription channel IDs (optional; all required when set)
LOG_CHANNEL = _int_env("LOG_CHANNEL")     # moderation log channel ID


def _parse_admins() -> frozenset:
    """ADMIN_IDS="111,222,333" (one or more positive user IDs; commas and/or spaces).

    Falls back to the legacy ADMIN_ID variable, which may also hold a list.
    """
    raw = os.environ.get("ADMIN_IDS", "").strip() or os.environ.get("ADMIN_ID", "").strip()
    if not raw:
        _problems.append("ADMIN_IDS is missing")
        return frozenset()
    ids = set()
    for part in re.split(r"[,\s]+", raw):
        if not part:
            continue
        if not part.isdigit() or int(part) <= 0:  # user IDs are positive integers
            # Never echo the value: it may be a credential typed into the wrong field.
            _problems.append("ADMIN_IDS must be comma-separated positive numeric Telegram user IDs")
            return frozenset()
        ids.add(int(part))
    if not ids:
        _problems.append("ADMIN_IDS is missing")
    return frozenset(ids)


ADMIN_IDS = _parse_admins()               # every user allowed to run /ban, /unban, /stats
# Optional join links (only meaningful with AUTH_CHANNELS), comma-separated, in the same order as AUTH_CHANNELS (leave an
# entry empty to auto-resolve it: "https://t.me/+abc,,https://t.me/+xyz"). Needed for
# private channels; a public @username is resolved automatically.
_urls_raw = os.environ.get("AUTH_CHANNEL_URLS", "").strip() or os.environ.get("AUTH_CHANNEL_URL", "").strip()
AUTH_CHANNEL_URLS = [u.strip() for u in _urls_raw.split(",")] if _urls_raw else []

# --- Moderation -----------------------------------------------------------
KNOWN_MEDIA_TYPES = {
    "photo", "video", "video_note", "animation", "voice",
    "audio", "document", "sticker", "location", "contact", "venue",
}
_raw_types = _str_env(
    "LOG_MEDIA_TYPES",
    required=False,
    default="photo,video,video_note,animation,voice,audio,document",
)
LOG_MEDIA_TYPES = {t.strip().lower() for t in _raw_types.split(",") if t.strip()}

# --- Behaviour tunables (constants, not environment-driven) ---------------
ONLINE_WINDOW_SECONDS = 300        # "online" = interacted in the last 5 minutes
SUBSCRIPTION_CACHE_TTL = 120       # seconds a positive membership result is reused while relaying
ALBUM_WAIT_SECONDS = 1.2           # how long to collect album items before sending
MAX_MATCH_ATTEMPTS = 20            # retries when waiting partners turn out to be unreachable
REPLY_LINK_LIMIT = 40000           # message-ID links kept in memory (2 per relayed message, oldest dropped first)
REPORT_COOLDOWN_SECONDS = 15       # minimum gap between two /report commands from one user
REPORT_REASON_MAX = 300            # characters of the optional reason kept in the log


def validate_config() -> list:
    """Return a list of human-readable problems (names only, never values)."""
    problems = list(_problems)
    unknown = LOG_MEDIA_TYPES - KNOWN_MEDIA_TYPES
    if unknown:
        problems.append("LOG_MEDIA_TYPES contains unknown type names: " + ", ".join(sorted(unknown)))
    if len(AUTH_CHANNEL_URLS) > len(AUTH_CHANNELS):
        problems.append("AUTH_CHANNEL_URLS has more entries than AUTH_CHANNELS")
    if any(u and not u.startswith(("https://", "http://")) for u in AUTH_CHANNEL_URLS):
        problems.append("AUTH_CHANNEL_URLS entries must start with https://")
    return problems
