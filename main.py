from __future__ import annotations

import asyncio
import signal

from pyrogram import Client, idle

from config import config
from db import Database
from handlers import setup_handlers
from queue_manager import QueueManager


async def main() -> None:
    config.local_download_root.mkdir(parents=True, exist_ok=True)
    config.drive_root.mkdir(parents=True, exist_ok=True)

    db = Database(config.db_path)
    db.mark_unfinished_failed_on_startup()

    app = Client(
        "telegram_drive_bot",
        api_id=config.api_id,
        api_hash=config.api_hash,
        bot_token=config.bot_token,
        workdir=".",
    )

    await app.start()
    queue_manager = QueueManager(app, db, config.max_concurrent_downloads)
    setup_handlers(app, db, queue_manager)
    await queue_manager.start()

    print("Bot started")
    try:
        await idle()
    finally:
        await queue_manager.stop()
        await app.stop()


if __name__ == "__main__":
    asyncio.run(main())
