import os
import sqlite3
import time
from pathlib import Path

from tebya_zovut_bot.db_admin import (
    backup_database,
    check_database,
    prune_backups,
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
