from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _bounded_error(error: str | None) -> str | None:
    if error is None:
        return None
    return error.replace("\x00", "")[:1000]


def normalize_username(username: str | None) -> str | None:
    if not username:
        return None
    normalized = username.strip().removeprefix("@").casefold()
    return normalized or None


@dataclass(frozen=True, slots=True)
class NotificationJob:
    chat_id: int
    message_id: int
    user_id: int
    body_text: str
    button_text: str
    message_link: str
    attempt_count: int


@dataclass(frozen=True, slots=True)
class QueueStats:
    queued: int
    sent: int
    permanently_failed: int


class Storage:
    """Persistent user registry and durable notification outbox."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        new_database = not self.path.exists() or self.path.stat().st_size == 0
        self._connection = sqlite3.connect(self.path, timeout=5.0)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA busy_timeout=5000")
        if new_database:
            self._connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA temp_store=MEMORY")
        self._connection.execute("PRAGMA wal_autocheckpoint=1000")
        self._create_schema()
        self._migrate_schema()
        self._enable_incremental_vacuum()

    def _create_schema(self) -> None:
        with self._connection:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    username_key TEXT UNIQUE,
                    first_name TEXT NOT NULL,
                    dm_allowed INTEGER NOT NULL DEFAULT 0
                        CHECK (dm_allowed IN (0, 1)),
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS notifications (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    status TEXT NOT NULL
                        CHECK (status IN ('pending', 'sent', 'failed')),
                    error TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    body_text TEXT,
                    button_text TEXT,
                    message_link TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT,
                    retryable INTEGER NOT NULL DEFAULT 1
                        CHECK (retryable IN (0, 1)),
                    PRIMARY KEY (chat_id, message_id, user_id)
                );
                """
            )

    def _migrate_schema(self) -> None:
        columns = {
            str(row["name"])
            for row in self._connection.execute(
                "PRAGMA table_info(notifications)"
            ).fetchall()
        }
        additions = {
            "body_text": "TEXT",
            "button_text": "TEXT",
            "message_link": "TEXT",
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "next_attempt_at": "TEXT",
            "retryable": "INTEGER NOT NULL DEFAULT 1 CHECK (retryable IN (0, 1))",
        }

        with self._connection:
            for name, declaration in additions.items():
                if name not in columns:
                    self._connection.execute(
                        f"ALTER TABLE notifications ADD COLUMN {name} {declaration}"
                    )
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS notifications_ready_idx
                ON notifications (
                    COALESCE(next_attempt_at, created_at), created_at
                )
                WHERE
                    retryable = 1
                    AND body_text IS NOT NULL
                    AND button_text IS NOT NULL
                    AND message_link IS NOT NULL
                """
            )
            self._connection.execute("DROP INDEX IF EXISTS notifications_due_idx")
            self._connection.execute(
                """
                CREATE INDEX IF NOT EXISTS notifications_cleanup_idx
                ON notifications (status, retryable, updated_at)
                """
            )
            self._connection.execute("DROP INDEX IF EXISTS notifications_user_idx")

    def _enable_incremental_vacuum(self) -> None:
        mode = int(self._connection.execute("PRAGMA auto_vacuum").fetchone()[0])
        if mode == 2:
            return
        LOGGER.info("Migrating SQLite to incremental auto-vacuum")
        self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self._connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self._connection.execute("VACUUM")

    def remember_user(
        self,
        *,
        user_id: int,
        username: str | None,
        first_name: str,
        dm_allowed: bool = False,
    ) -> None:
        username_key = normalize_username(username)
        timestamp = _now()

        with self._connection:
            if username_key is not None:
                # Telegram usernames can move from one account to another.
                self._connection.execute(
                    """
                    UPDATE users
                    SET username = NULL, username_key = NULL, updated_at = ?
                    WHERE username_key = ? AND user_id <> ?
                    """,
                    (timestamp, username_key, user_id),
                )

            current = self._connection.execute(
                "SELECT dm_allowed FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
            allowed = int(dm_allowed or bool(current and current["dm_allowed"]))

            self._connection.execute(
                """
                INSERT INTO users (
                    user_id, username, username_key, first_name,
                    dm_allowed, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username = excluded.username,
                    username_key = excluded.username_key,
                    first_name = excluded.first_name,
                    dm_allowed = excluded.dm_allowed,
                    updated_at = excluded.updated_at
                WHERE
                    users.username IS NOT excluded.username
                    OR users.username_key IS NOT excluded.username_key
                    OR users.first_name IS NOT excluded.first_name
                    OR users.dm_allowed IS NOT excluded.dm_allowed
                """,
                (
                    user_id,
                    username.removeprefix("@") if username else None,
                    username_key,
                    (first_name or "Пользователь")[:128],
                    allowed,
                    timestamp,
                ),
            )

    def resolve_username(self, username: str) -> int | None:
        username_key = normalize_username(username)
        if username_key is None:
            return None
        row = self._connection.execute(
            "SELECT user_id FROM users WHERE username_key = ?",
            (username_key,),
        ).fetchone()
        return int(row["user_id"]) if row else None

    def filter_dm_allowed(self, user_ids: Iterable[int]) -> set[int]:
        unique_ids = sorted(set(user_ids))
        allowed: set[int] = set()
        # json_each keeps the SQL static and avoids SQLite's host-variable limit.
        for offset in range(0, len(unique_ids), 5000):
            chunk = unique_ids[offset : offset + 5000]
            rows = self._connection.execute(
                """
                SELECT user_id
                FROM users
                WHERE
                    dm_allowed = 1
                    AND user_id IN (
                        SELECT CAST(value AS INTEGER) FROM json_each(?)
                    )
                """,
                (json.dumps(chunk, separators=(",", ":")),),
            ).fetchall()
            allowed.update(int(row["user_id"]) for row in rows)
        return allowed

    def set_dm_allowed(self, user_id: int, allowed: bool) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE users
                SET dm_allowed = ?, updated_at = ?
                WHERE user_id = ?
                """,
                (int(allowed), _now(), user_id),
            )

    def enqueue_notifications(
        self,
        *,
        chat_id: int,
        message_id: int,
        user_ids: Iterable[int],
        body_text: str,
        button_text: str,
        message_link: str,
    ) -> int:
        timestamp = _now()
        inserted = 0
        bounded_body = body_text[:4096]
        bounded_button = button_text[:128]
        bounded_link = message_link[:2048]

        with self._connection:
            for user_id in sorted(set(user_ids)):
                cursor = self._connection.execute(
                    """
                    INSERT OR IGNORE INTO notifications (
                        chat_id, message_id, user_id, status, error,
                        created_at, updated_at, body_text, button_text,
                        message_link, attempt_count, next_attempt_at, retryable
                    )
                    VALUES (
                        ?, ?, ?, 'pending', NULL, ?, ?, ?, ?, ?, 0, ?, 1
                    )
                    """,
                    (
                        chat_id,
                        message_id,
                        user_id,
                        timestamp,
                        timestamp,
                        bounded_body,
                        bounded_button,
                        bounded_link,
                        timestamp,
                    ),
                )
                inserted += int(cursor.rowcount == 1)
        return inserted

    def next_due_notification(self) -> NotificationJob | None:
        row = self._connection.execute(
            """
            SELECT
                chat_id, message_id, user_id, body_text,
                button_text, message_link, attempt_count
            FROM notifications
            WHERE
                retryable = 1
                AND body_text IS NOT NULL
                AND button_text IS NOT NULL
                AND message_link IS NOT NULL
                AND COALESCE(next_attempt_at, created_at) <= ?
            ORDER BY COALESCE(next_attempt_at, created_at), created_at
            LIMIT 1
            """,
            (_now(),),
        ).fetchone()
        if row is None:
            return None
        return NotificationJob(
            chat_id=int(row["chat_id"]),
            message_id=int(row["message_id"]),
            user_id=int(row["user_id"]),
            body_text=str(row["body_text"]),
            button_text=str(row["button_text"]),
            message_link=str(row["message_link"]),
            attempt_count=int(row["attempt_count"]),
        )

    def seconds_until_next_attempt(self) -> float | None:
        row = self._connection.execute(
            """
            SELECT MIN(COALESCE(next_attempt_at, created_at)) AS due_at
            FROM notifications
            WHERE
                retryable = 1
                AND body_text IS NOT NULL
                AND button_text IS NOT NULL
                AND message_link IS NOT NULL
            """
        ).fetchone()
        if row is None or row["due_at"] is None:
            return None
        due_at = datetime.fromisoformat(str(row["due_at"]))
        return max((due_at - datetime.now(UTC)).total_seconds(), 0.0)

    def mark_notification_sent(self, job: NotificationJob) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE notifications
                SET
                    status = 'sent',
                    error = NULL,
                    retryable = 0,
                    next_attempt_at = NULL,
                    body_text = NULL,
                    button_text = NULL,
                    message_link = NULL,
                    updated_at = ?
                WHERE chat_id = ? AND message_id = ? AND user_id = ?
                """,
                (_now(), job.chat_id, job.message_id, job.user_id),
            )

    def mark_notification_failed(
        self,
        job: NotificationJob,
        *,
        error: str,
        retryable: bool,
        retry_after_seconds: float | None = None,
    ) -> None:
        next_attempt_at: str | None = None
        if retryable:
            next_attempt_at = (
                datetime.now(UTC) + timedelta(seconds=max(retry_after_seconds or 0, 0))
            ).isoformat(timespec="seconds")

        with self._connection:
            self._connection.execute(
                """
                UPDATE notifications
                SET
                    status = 'failed',
                    error = ?,
                    retryable = ?,
                    attempt_count = attempt_count + 1,
                    next_attempt_at = ?,
                    body_text = CASE WHEN ? = 1 THEN body_text ELSE NULL END,
                    button_text = CASE WHEN ? = 1 THEN button_text ELSE NULL END,
                    message_link = CASE WHEN ? = 1 THEN message_link ELSE NULL END,
                    updated_at = ?
                WHERE chat_id = ? AND message_id = ? AND user_id = ?
                """,
                (
                    _bounded_error(error),
                    int(retryable),
                    next_attempt_at,
                    int(retryable),
                    int(retryable),
                    int(retryable),
                    _now(),
                    job.chat_id,
                    job.message_id,
                    job.user_id,
                ),
            )

    def queue_stats(self) -> QueueStats:
        row = self._connection.execute(
            """
            SELECT
                SUM(
                    CASE
                        WHEN status = 'pending'
                            OR (status = 'failed' AND retryable = 1)
                        THEN 1 ELSE 0
                    END
                ) AS queued,
                SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) AS sent,
                SUM(
                    CASE
                        WHEN status = 'failed' AND retryable = 0
                        THEN 1 ELSE 0
                    END
                ) AS permanently_failed
            FROM notifications
            """
        ).fetchone()
        return QueueStats(
            queued=int(row["queued"] or 0),
            sent=int(row["sent"] or 0),
            permanently_failed=int(row["permanently_failed"] or 0),
        )

    def cleanup_notifications(
        self,
        *,
        retention_days: int,
        batch_size: int = 5000,
    ) -> int:
        cutoff = (datetime.now(UTC) - timedelta(days=retention_days)).isoformat(
            timespec="seconds"
        )
        with self._connection:
            cursor = self._connection.execute(
                """
                DELETE FROM notifications
                WHERE rowid IN (
                    SELECT rowid
                    FROM notifications
                    WHERE
                        updated_at < ?
                        AND (
                            status = 'sent'
                            OR (status = 'failed' AND retryable = 0)
                            OR body_text IS NULL
                        )
                    LIMIT ?
                )
                """,
                (cutoff, batch_size),
            )
        return cursor.rowcount

    def maintain(self, *, retention_days: int) -> int:
        removed = 0
        for _ in range(20):
            batch_removed = self.cleanup_notifications(retention_days=retention_days)
            removed += batch_removed
            if batch_removed < 5000:
                break
        self._connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
        self._connection.execute("PRAGMA incremental_vacuum(5000)")
        self._connection.execute("PRAGMA optimize")
        return removed

    def healthcheck(self) -> None:
        self._connection.execute("SELECT 1").fetchone()

    # Compatibility helpers retained for small integrations and older tests.
    def claim_notification(
        self, *, chat_id: int, message_id: int, user_id: int
    ) -> bool:
        timestamp = _now()
        with self._connection:
            cursor = self._connection.execute(
                """
                INSERT OR IGNORE INTO notifications (
                    chat_id, message_id, user_id, status,
                    error, created_at, updated_at
                )
                VALUES (?, ?, ?, 'pending', NULL, ?, ?)
                """,
                (chat_id, message_id, user_id, timestamp, timestamp),
            )
        return cursor.rowcount == 1

    def finish_notification(
        self,
        *,
        chat_id: int,
        message_id: int,
        user_id: int,
        sent: bool,
        error: str | None = None,
    ) -> None:
        with self._connection:
            self._connection.execute(
                """
                UPDATE notifications
                SET status = ?, error = ?, retryable = 0, updated_at = ?
                WHERE chat_id = ? AND message_id = ? AND user_id = ?
                """,
                (
                    "sent" if sent else "failed",
                    _bounded_error(error),
                    _now(),
                    chat_id,
                    message_id,
                    user_id,
                ),
            )

    def close(self) -> None:
        try:
            self._connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._connection.execute("PRAGMA optimize")
        finally:
            self._connection.close()
