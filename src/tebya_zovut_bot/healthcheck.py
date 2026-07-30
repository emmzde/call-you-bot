from __future__ import annotations

import os
import sqlite3
import sys
import time
from pathlib import Path


def main() -> None:
    heartbeat_path = Path(
        os.getenv("HEALTHCHECK_PATH", "data/bot.heartbeat")
    ).expanduser()
    database_path = Path(os.getenv("DATABASE_PATH", "data/bot.sqlite3")).expanduser()
    interval = int(os.getenv("HEALTHCHECK_INTERVAL_SECONDS", "15"))
    max_age = max(interval * 4, 30)

    try:
        age = time.time() - heartbeat_path.stat().st_mtime
        if age < 0 or age > max_age:
            raise RuntimeError(f"stale heartbeat: {age:.1f}s")
        connection = sqlite3.connect(
            f"{database_path.resolve().as_uri()}?mode=ro",
            uri=True,
            timeout=2.0,
        )
        try:
            connection.execute("SELECT 1").fetchone()
        finally:
            connection.close()
    except (OSError, RuntimeError, sqlite3.Error, ValueError) as error:
        print(f"unhealthy: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print("healthy")


if __name__ == "__main__":
    main()
