from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TypeVar

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import (
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.utils.backoff import BackoffConfig

from .config import Config
from .handlers import create_router
from .health import RuntimeHealth
from .logging_config import configure_logging
from .notifier import NotificationWorker
from .storage import Storage

LOGGER = logging.getLogger(__name__)
ResultT = TypeVar("ResultT")


async def _telegram_startup_call(
    name: str,
    operation: Callable[[], Awaitable[ResultT]],
) -> ResultT:
    delay = 1.0
    while True:
        try:
            return await operation()
        except TelegramRetryAfter as error:
            wait_seconds = max(float(error.retry_after), 1.0)
        except (TelegramNetworkError, TelegramServerError):
            wait_seconds = delay
            delay = min(delay * 1.7, 30.0)
        LOGGER.warning(
            "Telegram unavailable during %s; retrying in %.1fs",
            name,
            wait_seconds,
            exc_info=True,
        )
        await asyncio.sleep(wait_seconds)


async def run() -> None:
    config = Config.from_env()
    configure_logging(level=config.log_level, log_format=config.log_format)

    storage = Storage(config.database_path)
    bot = Bot(
        token=config.bot_token,
        default=DefaultBotProperties(link_preview_is_disabled=True),
    )
    dispatcher = Dispatcher()
    worker = NotificationWorker(
        bot=bot,
        storage=storage,
        send_rate_per_second=config.send_rate_per_second,
        retry_base_seconds=config.notification_retry_base_seconds,
        retry_max_seconds=config.notification_retry_max_seconds,
        max_attempts=config.notification_max_attempts,
        retention_days=config.notification_retention_days,
        maintenance_interval_seconds=config.maintenance_interval_seconds,
    )
    health = RuntimeHealth(
        path=config.healthcheck_path,
        interval_seconds=config.healthcheck_interval_seconds,
        watchdog_timeout_seconds=config.watchdog_timeout_seconds,
    )

    try:
        bot_user = await _telegram_startup_call("getMe", bot.get_me)
        if not bot_user.username:
            raise RuntimeError("Telegram did not return a username for the bot")

        dispatcher.include_router(
            create_router(
                storage=storage,
                bot_id=bot_user.id,
                bot_username=bot_user.username,
                notification_worker=worker,
                admin_user_ids=config.admin_user_ids,
            )
        )
        await _telegram_startup_call(
            "deleteWebhook",
            lambda: bot.delete_webhook(
                drop_pending_updates=config.drop_pending_updates
            ),
        )

        worker.start()
        health.start()
        LOGGER.info(
            "Bot ready username=@%s bot_id=%s polling_timeout=%ss send_rate=%.1f/s",
            bot_user.username,
            bot_user.id,
            config.polling_timeout_seconds,
            config.send_rate_per_second,
        )
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            polling_timeout=config.polling_timeout_seconds,
            handle_as_tasks=False,
            handle_signals=True,
            close_bot_session=False,
            backoff_config=BackoffConfig(
                min_delay=1.0,
                max_delay=30.0,
                factor=1.5,
                jitter=0.1,
            ),
        )
    finally:
        await health.stop()
        await worker.stop(grace_seconds=config.shutdown_grace_seconds)
        storage.close()
        await bot.session.close()
        LOGGER.info("Bot stopped cleanly")


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception:
        if not logging.getLogger().handlers:
            logging.basicConfig(
                level=logging.ERROR,
                format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            )
        logging.getLogger(__name__).critical(
            "Bot terminated unexpectedly", exc_info=True
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
