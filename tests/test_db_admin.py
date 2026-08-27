import os
import sqlite3
import threading
import time
from pathlib import Path

import pytest

from tebya_zovut_bot.db_admin import (
    backup_database,
    check_database,
    prune_backups,
    restore_database,
)


def test_backup_is_consistent_and_old_automatic_backups_are_pruned(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("CREATE TABLE sample (value TEXT NOT NULL)")
    connection.execute("INSERT INTO sample VALUES ('kept')")
    connection.commit()
    connection.close()

    current_backup = tmp_path / "pre-update-current.sqlite3"
    old_backup = tmp_path / "pre-update-old.sqlite3"
    backup_database(source, current_backup)
    backup_database(source, old_backup)
    old_timestamp = time.time() - 20 * 86400
    os.utime(old_backup, (old_timestamp, old_timestamp))

    removed = prune_backups(tmp_path, days=14)

    check_database(current_backup)
    assert removed == 1
    assert current_backup.exists()
    assert not old_backup.exists()


def test_restore_atomically_replaces_database(tmp_path: Path) -> None:
    live = tmp_path / "live.sqlite3"
    backup = tmp_path / "backup.sqlite3"
    connection = sqlite3.connect(live)
    connection.execute("CREATE TABLE values_table (value TEXT NOT NULL)")
    connection.execute("INSERT INTO values_table VALUES ('before')")
    connection.commit()
    connection.close()
    backup_database(live, backup)

    connection = sqlite3.connect(live)
    connection.execute("UPDATE values_table SET value = 'after'")
    connection.commit()
    connection.close()

    restore_database(backup, live)

    connection = sqlite3.connect(live)
    try:
        assert connection.execute("SELECT value FROM values_table").fetchone() == (
            "before",
        )
    finally:
        connection.close()


def test_backup_is_consistent_during_concurrent_wal_writes(tmp_path: Path) -> None:
    source = tmp_path / "live.sqlite3"
    backup = tmp_path / "concurrent-backup.sqlite3"
    connection = sqlite3.connect(source)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("CREATE TABLE events (value INTEGER NOT NULL)")
    connection.commit()
    connection.close()

    first_write = threading.Event()

    def write_events() -> None:
        writer = sqlite3.connect(source, timeout=10)
        try:
            for value in range(5_000):
                with writer:
                    writer.execute("INSERT INTO events VALUES (?)", (value,))
                if value == 0:
                    first_write.set()
        finally:
            writer.close()

    thread = threading.Thread(target=write_events)
    thread.start()
    assert first_write.wait(timeout=5)
    backup_database(source, backup)
    thread.join(timeout=15)
    assert not thread.is_alive()

    check_database(backup)
    connection = sqlite3.connect(backup)
    try:
        backed_up_count = int(connection.execute("SELECT COUNT(*) FROM events").fetchone()[0])
    finally:
        connection.close()
    assert 1 <= backed_up_count <= 5_000


def test_corrupt_backup_cannot_replace_live_database(tmp_path: Path) -> None:
    live = tmp_path / "live.sqlite3"
    corrupt = tmp_path / "corrupt.sqlite3"
    connection = sqlite3.connect(live)
    connection.execute("CREATE TABLE values_table (value TEXT NOT NULL)")
    connection.execute("INSERT INTO values_table VALUES ('preserved')")
    connection.commit()
    connection.close()
    corrupt.write_bytes(b"this is not a SQLite database")

    with pytest.raises(sqlite3.DatabaseError):
        restore_database(corrupt, live)

    connection = sqlite3.connect(live)
    try:
        assert connection.execute("SELECT value FROM values_table").fetchone() == (
            "preserved",
        )
    finally:
        connection.close()
