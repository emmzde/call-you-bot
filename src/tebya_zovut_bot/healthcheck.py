from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path


def _require_fresh(path: Path, *, max_age: int, label: str) -> None:
    age = time.time() - path.stat().st_mtime
    if age < 0 or age > max_age:
        raise RuntimeError(f"stale {label} heartbeat: {age:.1f}s")


def check_runtime() -> None:
    heartbeat_path = Path(
        os.getenv("HEALTHCHECK_PATH", "data/bot.heartbeat")
    ).expanduser()
    database_path = Path(os.getenv("DATABASE_PATH", "data/bot.sqlite3")).expanduser()
    interval = int(os.getenv("HEALTHCHECK_INTERVAL_SECONDS", "15"))
    max_age = max(interval * 4, 30)

    _require_fresh(heartbeat_path, max_age=max_age, label="runtime")
    connection = sqlite3.connect(
        f"{database_path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=2.0,
    )
    try:
        connection.execute("SELECT 1").fetchone()
    finally:
        connection.close()


def check_telegram() -> None:
    telegram_path = Path(
        os.getenv("TELEGRAM_HEALTHCHECK_PATH", "data/telegram.heartbeat")
    ).expanduser()
    telegram_max_age = int(os.getenv("TELEGRAM_HEALTHCHECK_MAX_AGE_SECONDS", "300"))
    _require_fresh(telegram_path, max_age=telegram_max_age, label="Telegram")


def main() -> None:
    telegram_only = sys.argv[1:] == ["--telegram-only"]
    if sys.argv[1:] and not telegram_only:
        print(
            "usage: python -m tebya_zovut_bot.healthcheck [--telegram-only]",
            file=sys.stderr,
        )
        raise SystemExit(2)

    try:
        if telegram_only:
            check_telegram()
        else:
            check_runtime()
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as error:
        print(f"unhealthy: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print("telegram: healthy" if telegram_only else "runtime: healthy")


if __name__ == "__main__":
    main()
