import asyncio
import os
import sqlite3
from pathlib import Path

import pytest

from tebya_zovut_bot.health import AsyncServiceProbe
from tebya_zovut_bot.healthcheck import check_runtime, check_telegram


def test_async_service_probe_records_success_and_cleans_up(tmp_path: Path) -> None:
    heartbeat = tmp_path / "telegram.heartbeat"

    async def scenario() -> None:
        called = asyncio.Event()

        async def operation() -> object:
            called.set()
            return object()

        probe = AsyncServiceProbe(
            path=heartbeat,
            interval_seconds=60,
            operation=operation,
            name="test",
        )
        probe.start()
        await asyncio.wait_for(called.wait(), timeout=1)
        await asyncio.sleep(0)
        assert heartbeat.exists()
        await probe.stop()
        assert not heartbeat.exists()

    asyncio.run(scenario())


def test_healthchecks_separate_runtime_from_telegram_dependency(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_heartbeat = tmp_path / "runtime.heartbeat"
    telegram_heartbeat = tmp_path / "telegram.heartbeat"
    database = tmp_path / "bot.sqlite3"
    runtime_heartbeat.touch()
    telegram_heartbeat.touch()
    sqlite3.connect(database).close()

    monkeypatch.setenv("HEALTHCHECK_PATH", str(runtime_heartbeat))
    monkeypatch.setenv("TELEGRAM_HEALTHCHECK_PATH", str(telegram_heartbeat))
    monkeypatch.setenv("DATABASE_PATH", str(database))
    monkeypatch.setenv("HEALTHCHECK_INTERVAL_SECONDS", "15")
    monkeypatch.setenv("TELEGRAM_HEALTHCHECK_MAX_AGE_SECONDS", "300")

    check_runtime()
    check_telegram()

    old_timestamp = telegram_heartbeat.stat().st_mtime - 600
    os.utime(telegram_heartbeat, (old_timestamp, old_timestamp))
    check_runtime()
    with pytest.raises(RuntimeError, match="stale Telegram heartbeat"):
        check_telegram()
