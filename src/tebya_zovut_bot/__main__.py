from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from .config import Config
from .handlers import create_router
from .storage import Storage


async def run() -> None:
    config = Config.from_env()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    storage = Storage(config.database_path)
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(link_preview_is_disabled=True),
    )
    dispatcher = Dispatcher()

    try:
        bot_user = await bot.get_me()
        if not bot_user.username:
            raise RuntimeError("Telegram did not return a username for the bot")

        dispatcher.include_router(
            create_router(
                storage=storage,
                bot_id=bot_user.id,
                bot_username=bot_user.username,
            )
        )
        await bot.delete_webhook(drop_pending_updates=config.drop_pending_updates)
        logging.getLogger(__name__).info(
            "Starting @%s using long polling", bot_user.username
        )
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
        )
    finally:
        storage.close()
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        pass


if __name__ == "__main__":
    main()
