from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def normalize_username(username: str | None) -> str | None:
    if not username:
        return None
    normalized = username.strip().removeprefix("@").casefold()
    return normalized or None


class Storage:
    """Small persistent registry of Telegram users and sent notifications."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._create_schema()

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
                    PRIMARY KEY (chat_id, message_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS notifications_user_idx
                    ON notifications (user_id, created_at);
                """
            )

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
                """,
                (
                    user_id,
                    username.removeprefix("@") if username else None,
                    username_key,
                    first_name or "Пользователь",
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

    def claim_notification(
        self, *, chat_id: int, message_id: int, user_id: int
    ) -> bool:
        """Return true only for the first attempt for this message and user."""
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
                SET status = ?, error = ?, updated_at = ?
                WHERE chat_id = ? AND message_id = ? AND user_id = ?
                """,
                (
                    "sent" if sent else "failed",
                    error,
                    _now(),
                    chat_id,
                    message_id,
                    user_id,
                ),
            )

    def close(self) -> None:
        self._connection.close()
