import asyncio
from pathlib import Path
from typing import Any

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.methods import SendMessage

from tebya_zovut_bot.notifier import NotificationWorker
from tebya_zovut_bot.storage import Storage


class FailingBot:
    async def send_message(self, **kwargs: Any) -> None:
        raise RuntimeError("temporary failure")


class BadPayloadBot:
    async def send_message(self, **kwargs: Any) -> None:
        raise TelegramBadRequest(
            method=SendMessage(chat_id=2, text="Notification"),
            message="BUTTON_URL_INVALID",
        )


class BlockedBot:
    async def send_message(self, **kwargs: Any) -> None:
        raise TelegramForbiddenError(
            method=SendMessage(chat_id=2, text="Notification"),
            message="bot was blocked by the user",
        )


def enqueue_test_job(storage: Storage) -> None:
    storage.enqueue_notifications(
        chat_id=-1001,
        message_id=7,
        user_ids=[2],
        body_text="Notification",
        button_text="Release chat",
        message_link="https://t.me/c/1/7",
    )


def test_worker_persists_transient_failure_for_retry(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "bot.sqlite3")
    try:
        enqueue_test_job(storage)
        worker = NotificationWorker(
            bot=FailingBot(),
            storage=storage,
            send_rate_per_second=0,
            retry_base_seconds=1,
            retry_max_seconds=1,
            max_attempts=2,
        )

        asyncio.run(worker.process_next())

        stats = storage.queue_stats()
        assert stats.queued == 1
        assert stats.permanently_failed == 0
    finally:
        storage.close()


def test_worker_stops_retrying_at_configured_limit(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "bot.sqlite3")
    try:
        enqueue_test_job(storage)
        worker = NotificationWorker(
            bot=FailingBot(),
            storage=storage,
            send_rate_per_second=0,
            retry_base_seconds=1,
            retry_max_seconds=1,
            max_attempts=1,
        )

        asyncio.run(worker.process_next())

        stats = storage.queue_stats()
        assert stats.queued == 0
        assert stats.permanently_failed == 1
    finally:
        storage.close()


def test_bad_payload_does_not_unregister_user(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "bot.sqlite3")
    try:
        storage.remember_user(
            user_id=2,
            username="still_allowed",
            first_name="Allowed",
            dm_allowed=True,
        )
        enqueue_test_job(storage)
        worker = NotificationWorker(
            bot=BadPayloadBot(),
            storage=storage,
            send_rate_per_second=0,
        )

        asyncio.run(worker.process_next())

        assert storage.filter_dm_allowed([2]) == {2}
        assert storage.queue_stats().permanently_failed == 1
    finally:
        storage.close()


def test_blocked_user_is_unregistered(tmp_path: Path) -> None:
    storage = Storage(tmp_path / "bot.sqlite3")
    try:
        storage.remember_user(
            user_id=2,
            username="blocked",
            first_name="Blocked",
            dm_allowed=True,
        )
        enqueue_test_job(storage)
        worker = NotificationWorker(
            bot=BlockedBot(),
            storage=storage,
            send_rate_per_second=0,
        )

        asyncio.run(worker.process_next())

        assert storage.filter_dm_allowed([2]) == set()
        assert storage.queue_stats().permanently_failed == 1
    finally:
        storage.close()
