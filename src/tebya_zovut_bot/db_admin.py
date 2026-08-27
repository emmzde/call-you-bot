from __future__ import annotations

import argparse
import os
import sqlite3
import time
from contextlib import suppress
from pathlib import Path


def check_database(path: Path) -> None:
    connection = sqlite3.connect(path)
    try:
        result = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()
    if result is None or result[0] != "ok":
        raise RuntimeError(f"SQLite quick_check failed: {result!r}")


def backup_database(source: Path, target: Path) -> None:
    source_connection = sqlite3.connect(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()
    check_database(target)


def restore_database(source: Path, target: Path) -> None:
    """Atomically replace a stopped bot database with a verified backup."""
    source = source.resolve()
    target = target.resolve()
    if source == target:
        raise ValueError("Backup source and database target must be different")
    check_database(source)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.restore-{os.getpid()}.tmp")
    with suppress(FileNotFoundError):
        temporary.unlink()

    try:
        backup_database(source, temporary)
        for suffix in ("-wal", "-shm"):
            with suppress(FileNotFoundError):
                Path(f"{target}{suffix}").unlink()
        temporary.replace(target)
        check_database(target)
    finally:
        with suppress(FileNotFoundError):
            temporary.unlink()


def prune_backups(directory: Path, *, days: int) -> int:
    if days < 1:
        raise ValueError("days must be positive")
    if not directory.exists():
        return 0

    cutoff = time.time() - days * 86400
    removed = 0
    for path in directory.glob("pre-update-*.sqlite3"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink()
            removed += 1
    return removed


def main() -> None:
    parser = argparse.ArgumentParser(description="Bot database maintenance")
    subparsers = parser.add_subparsers(dest="command", required=True)
    check_parser = subparsers.add_parser("check", help="Run SQLite quick_check")
    check_parser.add_argument("path", nargs="?", type=Path)
    backup_parser = subparsers.add_parser(
        "backup",
        help="Create a transactionally consistent SQLite backup",
    )
    backup_parser.add_argument("target", type=Path)
    restore_parser = subparsers.add_parser(
        "restore",
        help="Atomically restore a verified backup while the bot is stopped",
    )
    restore_parser.add_argument("source", type=Path)
    prune_parser = subparsers.add_parser(
        "prune-backups",
        help="Remove expired automatic pre-update backups",
    )
    prune_parser.add_argument("directory", type=Path)
    prune_parser.add_argument("--days", type=int, default=14)
    args = parser.parse_args()

    source = Path(os.getenv("DATABASE_PATH", "data/bot.sqlite3")).expanduser()
    if args.command == "check":
        checked_path = args.path or source
        check_database(checked_path)
        print(f"database: ok ({checked_path})")
    elif args.command == "backup":
        backup_database(source, args.target)
        print(f"backup: {args.target}")
    elif args.command == "restore":
        restore_database(args.source, source)
        print(f"restored: {args.source} -> {source}")
    else:
        removed = prune_backups(args.directory, days=args.days)
        print(f"expired backups removed: {removed}")


if __name__ == "__main__":
    main()
