from __future__ import annotations

import asyncio
import logging
import os
import secrets
from contextlib import suppress
from typing import Any

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from .storage import NotificationJob, Storage

LOGGER = logging.getLogger(__name__)
TEMPORARY_TELEGRAM_ERRORS = (
    TelegramNetworkError,
    TelegramServerError,
    TelegramRetryAfter,
)
JITTER_RANDOM = secrets.SystemRandom()


class OutboundRateLimiter:
    """Serialize Bot API sends below Telegram's free broadcast limit."""

    def __init__(self, rate_per_second: float) -> None:
        if rate_per_second < 0:
            raise ValueError("rate_per_second cannot be negative")
        self._interval = 0.0 if rate_per_second == 0 else 1.0 / rate_per_second
        self._lock = asyncio.Lock()
        self._next_slot = 0.0

    async def wait(self) -> None:
        if self._interval == 0:
            return

        async with self._lock:
            loop = asyncio.get_running_loop()
            delay = self._next_slot - loop.time()
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_slot = max(self._next_slot, loop.time()) + self._interval


async def _send_message_with_retry(
    bot: Bot,
    *,
    attempts: int = 4,
    **kwargs: Any,
) -> Message:
    if attempts < 1:
        raise ValueError("attempts must be positive")

    for attempt in range(1, attempts + 1):
        try:
            return await bot.send_message(**kwargs)
        except TelegramRetryAfter as error:
            if attempt == attempts:
                raise
            delay = max(float(error.retry_after), 0.0) + 0.1
            LOGGER.warning(
                "Telegram flood control; retrying send in %.1fs attempt=%s/%s",
                delay,
                attempt,
                attempts,
            )
            await asyncio.sleep(delay)
        except (TelegramNetworkError, TelegramServerError):
            if attempt == attempts:
                raise
            delay = min(0.5 * (2 ** (attempt - 1)), 4.0)
            LOGGER.warning(
                "Temporary Telegram send failure; retrying in %.1fs attempt=%s/%s",
                delay,
                attempt,
                attempts,
                exc_info=True,
            )
            await asyncio.sleep(delay)

    raise RuntimeError("unreachable")


class NotificationWorker:
    """Deliver the SQLite outbox and recover queued jobs after restarts."""

    def __init__(
        self,
        *,
        bot: Bot,
        storage: Storage,
        send_rate_per_second: float = 25.0,
        retry_base_seconds: float = 2.0,
        retry_max_seconds: float = 300.0,
        max_attempts: int = 20,
        retention_days: int = 7,
        maintenance_interval_seconds: float = 3600.0,
    ) -> None:
        if retry_base_seconds <= 0 or retry_max_seconds <= 0:
            raise ValueError("retry delays must be positive")
        if retry_base_seconds > retry_max_seconds:
            raise ValueError("retry_base_seconds cannot exceed retry_max_seconds")
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if retention_days < 1:
            raise ValueError("retention_days must be positive")
        if maintenance_interval_seconds <= 0:
            raise ValueError("maintenance interval must be positive")

        self._bot = bot
        self._storage = storage
        self._rate_limiter = OutboundRateLimiter(send_rate_per_second)
        self._retry_base_seconds = retry_base_seconds
        self._retry_max_seconds = retry_max_seconds
        self._max_attempts = max_attempts
        self._retention_days = retention_days
        self._maintenance_interval_seconds = maintenance_interval_seconds
        self._wake_event = asyncio.Event()
        self._stop_requested = False
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("Notification worker is already running")
        self._stop_requested = False
        self._task = asyncio.create_task(
            self._run(),
            name="notification-worker",
        )
        self._task.add_done_callback(self._on_task_done)

    def wake(self) -> None:
        self._wake_event.set()

    def _on_task_done(self, task: asyncio.Task[None]) -> None:
        if self._stop_requested or task.cancelled():
            return
        error = task.exception()
        if error is None:
            LOGGER.critical("Notification worker stopped unexpectedly")
        else:
            LOGGER.critical(
                "Notification worker crashed; terminating for restart",
                exc_info=(type(error), error, error.__traceback__),
            )
        # Delivery is durable in SQLite, so a clean process replacement is safer
        # than continuing to poll while the outbox is no longer being drained.
        os._exit(71)

    async def stop(self, *, grace_seconds: float) -> None:
        task = self._task
        if task is None:
            return
        self._stop_requested = True
        self._wake_event.set()
        try:
            await asyncio.wait_for(task, timeout=max(grace_seconds, 0.1))
        except TimeoutError:
            LOGGER.warning("Notification worker exceeded shutdown grace period")
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        finally:
            self._task = None

    async def process_next(self) -> bool:
        job = self._storage.next_due_notification()
        if job is None:
            return False
        await self._deliver(job)
        return True

    def _retry_delay(self, attempt_number: int) -> float:
        base = min(
            self._retry_base_seconds * (2 ** max(attempt_number - 1, 0)),
            self._retry_max_seconds,
        )
        return base * JITTER_RANDOM.uniform(0.85, 1.15)

    async def _deliver(self, job: NotificationJob) -> None:
        try:
            await self._rate_limiter.wait()
            await _send_message_with_retry(
                self._bot,
                chat_id=job.user_id,
                text=job.body_text,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text=job.button_text,
                                url=job.message_link,
                            )
                        ]
                    ]
                ),
            )
        except TelegramForbiddenError as error:
            self._storage.set_dm_allowed(job.user_id, False)
            self._storage.mark_notification_failed(
                job,
                error=str(error),
                retryable=False,
            )
            LOGGER.warning(
                "Permanent notification failure user_id=%s chat_id=%s message_id=%s",
                job.user_id,
                job.chat_id,
                job.message_id,
            )
        except TelegramBadRequest as error:
            # A bad request can be caused by this specific payload or button.
            # It must not globally unregister a user who still allows DMs.
            self._storage.mark_notification_failed(
                job,
                error=str(error),
                retryable=False,
            )
            LOGGER.error(
                "Invalid notification payload user_id=%s chat_id=%s message_id=%s",
                job.user_id,
                job.chat_id,
                job.message_id,
            )
        except TEMPORARY_TELEGRAM_ERRORS as error:
            self._schedule_retry(job, error)
        except TelegramAPIError as error:
            self._schedule_retry(job, error)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._schedule_retry(job, error, log_exception=True)
        else:
            self._storage.set_dm_allowed(job.user_id, True)
            self._storage.mark_notification_sent(job)
            LOGGER.debug(
                "Notification sent user_id=%s chat_id=%s message_id=%s",
                job.user_id,
                job.chat_id,
                job.message_id,
            )

    def _schedule_retry(
        self,
        job: NotificationJob,
        error: Exception,
        *,
        log_exception: bool = False,
    ) -> None:
        attempt_number = job.attempt_count + 1
        retryable = attempt_number < self._max_attempts
        delay = self._retry_delay(attempt_number) if retryable else None
        self._storage.mark_notification_failed(
            job,
            error=str(error),
            retryable=retryable,
            retry_after_seconds=delay,
        )

        if retryable:
            LOGGER.warning(
                "Notification deferred user_id=%s attempt=%s retry_in=%.1fs",
                job.user_id,
                attempt_number,
                delay,
                exc_info=log_exception,
            )
        else:
            LOGGER.error(
                "Notification abandoned after retries user_id=%s chat_id=%s "
                "message_id=%s attempts=%s",
                job.user_id,
                job.chat_id,
                job.message_id,
                attempt_number,
                exc_info=log_exception,
            )

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        next_maintenance = loop.time()

        while not self._stop_requested:
            if loop.time() >= next_maintenance:
                removed = self._storage.maintain(retention_days=self._retention_days)
                stats = self._storage.queue_stats()
                LOGGER.info(
                    "Outbox maintenance queued=%s sent=%s failed=%s removed=%s",
                    stats.queued,
                    stats.sent,
                    stats.permanently_failed,
                    removed,
                )
                next_maintenance = loop.time() + self._maintenance_interval_seconds

            if await self.process_next():
                continue

            next_due = self._storage.seconds_until_next_attempt()
            until_maintenance = max(next_maintenance - loop.time(), 0.1)
            wait_seconds = until_maintenance
            if next_due is not None:
                wait_seconds = min(wait_seconds, max(next_due, 0.1))

            self._wake_event.clear()
            try:
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=wait_seconds,
                )
            except TimeoutError:
                pass
