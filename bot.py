import asyncio
import logging
import sys

from pyrogram import Client

import service
from config import API_ID, API_HASH, BOT_TOKEN, validate_config
from webserver import run_webserver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


class BroadcastBot(Client):
    def __init__(self):
        super().__init__(
            name="broadcast_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            # Auto-discovers and imports every module in plugins/ on start,
            # which is what actually registers any @Client.on_message handlers.
            plugins=dict(root="plugins"),
        )

    async def start(self, *args, **kwargs):
        # MongoDB + ban list first, so handlers never run against an uninitialised DB.
        # Matchmaking state starts empty on every boot by design.
        await service.startup()
        await super().start(*args, **kwargs)
        me = await self.get_me()
        logger.info(f"Bot started: @{me.username}")

        await service.warmup(self)
        self._janitor = asyncio.ensure_future(service.janitor())
        self._web_runner = await run_webserver()

    async def stop(self, *args, **kwargs):
        if hasattr(self, "_janitor"):
            self._janitor.cancel()
        try:
            await service.shutdown(self)  # clears sessions/queue and notifies users
        except Exception:
            logger.exception("shutdown cleanup failed")
        if hasattr(self, "_web_runner"):
            await self._web_runner.cleanup()
        await service.db.close()
        await super().stop(*args, **kwargs)
        logger.info("Bot stopped.")


app = BroadcastBot()


if __name__ == "__main__":
    problems = validate_config()
    if problems:
        logger.error("Invalid configuration:")
        for problem in problems:
            logger.error(" - %s", problem)
        logger.error("See .env.example for the full list of settings.")
        sys.exit(1)
    app.run()
