import asyncio
from pathlib import Path
from typing import Any

from tebya_zovut_bot.notifier import NotificationWorker
from tebya_zovut_bot.storage import Storage


class FailingBot:
    async def send_message(self, **kwargs: Any) -> None:
        raise RuntimeError("temporary failure")


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
