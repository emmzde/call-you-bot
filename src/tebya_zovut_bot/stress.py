from __future__ import annotations

import argparse
import json
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .db_admin import backup_database, check_database, restore_database
from .storage import Storage


@dataclass(frozen=True, slots=True)
class StressResult:
    users: int
    notifications: int
    drained: int
    queued_after_drain: int
    database_bytes: int
    registration_seconds: float
    enqueue_seconds: float
    drain_seconds: float
    backup_restore_seconds: float
    total_seconds: float

    def as_report(self) -> dict[str, int | float]:
        report = asdict(self)
        report["registrations_per_second"] = round(
            self.users / max(self.registration_seconds, 0.000_001),
            1,
        )
        report["enqueues_per_second"] = round(
            self.notifications / max(self.enqueue_seconds, 0.000_001),
            1,
        )
        report["drains_per_second"] = round(
            self.drained / max(self.drain_seconds, 0.000_001),
            1,
        )
        return report


def run_stress(
    *,
    users: int,
    notifications: int,
    drain: int,
    directory: Path,
) -> StressResult:
    if users < 1:
        raise ValueError("users must be positive")
    if notifications < 1:
        raise ValueError("notifications must be positive")
    if not 0 <= drain <= notifications:
        raise ValueError("drain must be between zero and notifications")

    directory.mkdir(parents=True, exist_ok=True)
    database = directory / "stress.sqlite3"
    backup = directory / "stress-backup.sqlite3"
    restored = directory / "stress-restored.sqlite3"
    occupied = [path for path in (database, backup, restored) if path.exists()]
    if occupied:
        names = ", ".join(path.name for path in occupied)
        raise FileExistsError(
            f"stress directory contains existing artifacts: {names}"
        )
    started = time.perf_counter()
    storage = Storage(database)

    registration_started = time.perf_counter()
    for index in range(users):
        storage.remember_user(
            user_id=100_000 + index,
            username=f"stress_user_{index}",
            first_name=f"Stress {index}",
            dm_allowed=True,
        )
    registration_seconds = time.perf_counter() - registration_started

    enqueue_started = time.perf_counter()
    inserted_total = 0
    message_id = 1
    user_cursor = 0
    batch_size = min(users, 250)
    last_user_ids: list[int] = []
    last_message_id = 0
    while inserted_total < notifications:
        current_size = min(batch_size, notifications - inserted_total)
        user_ids = [
            100_000 + ((user_cursor + offset) % users)
            for offset in range(current_size)
        ]
        inserted = storage.enqueue_notifications(
            chat_id=-100_987_654_321,
            message_id=message_id,
            user_ids=user_ids,
            body_text="Тебя зовут! Стресс-проверка очереди.",
            button_text="Нагрузочный чат",
            message_link=f"https://t.me/c/987654321/{message_id}",
        )
        if inserted != current_size:
            raise RuntimeError(
                f"unexpected deduplication: inserted={inserted}, expected={current_size}"
            )
        inserted_total += inserted
        last_user_ids = user_ids
        last_message_id = message_id
        user_cursor = (user_cursor + current_size) % users
        message_id += 1

    duplicate_count = storage.enqueue_notifications(
        chat_id=-100_987_654_321,
        message_id=last_message_id,
        user_ids=last_user_ids,
        body_text="duplicate",
        button_text="duplicate",
        message_link="https://t.me/c/987654321/1",
    )
    if duplicate_count != 0:
        raise RuntimeError("durable outbox accepted duplicate notifications")
    enqueue_seconds = time.perf_counter() - enqueue_started

    initial_stats = storage.queue_stats()
    if initial_stats.queued != notifications:
        raise RuntimeError(
            f"queued={initial_stats.queued}, expected={notifications}"
        )
    storage.close()

    # Reopening here simulates a process/container crash between enqueue and send.
    storage = Storage(database)
    drain_started = time.perf_counter()
    for _ in range(drain):
        job = storage.next_due_notification()
        if job is None:
            raise RuntimeError("durable queue lost a pending notification")
        storage.mark_notification_sent(job)
    drain_seconds = time.perf_counter() - drain_started

    final_stats = storage.queue_stats()
    expected_queued = notifications - drain
    if final_stats.queued != expected_queued or final_stats.sent != drain:
        raise RuntimeError(
            "unexpected outbox state after drain: "
            f"queued={final_stats.queued}, sent={final_stats.sent}"
        )
    storage.close()

    backup_started = time.perf_counter()
    backup_database(database, backup)
    check_database(backup)
    restore_database(backup, restored)
    restored_storage = Storage(restored)
    try:
        restored_stats = restored_storage.queue_stats()
        if restored_stats != final_stats:
            raise RuntimeError(
                f"restored stats differ: {restored_stats!r} != {final_stats!r}"
            )
    finally:
        restored_storage.close()
    backup_restore_seconds = time.perf_counter() - backup_started

    return StressResult(
        users=users,
        notifications=notifications,
        drained=drain,
        queued_after_drain=expected_queued,
        database_bytes=database.stat().st_size,
        registration_seconds=round(registration_seconds, 3),
        enqueue_seconds=round(enqueue_seconds, 3),
        drain_seconds=round(drain_seconds, 3),
        backup_restore_seconds=round(backup_restore_seconds, 3),
        total_seconds=round(time.perf_counter() - started, 3),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stress the durable SQLite outbox without contacting Telegram",
    )
    parser.add_argument("--users", type=int, default=20_000)
    parser.add_argument("--notifications", type=int, default=100_000)
    parser.add_argument("--drain", type=int, default=25_000)
    parser.add_argument(
        "--directory",
        type=Path,
        help="Keep artifacts in this directory instead of a temporary directory",
    )
    args = parser.parse_args()

    if args.directory is not None:
        result = run_stress(
            users=args.users,
            notifications=args.notifications,
            drain=args.drain,
            directory=args.directory.expanduser(),
        )
    else:
        with tempfile.TemporaryDirectory(prefix="call-you-bot-stress-") as temporary:
            result = run_stress(
                users=args.users,
                notifications=args.notifications,
                drain=args.drain,
                directory=Path(temporary),
            )

    print(json.dumps(result.as_report(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
