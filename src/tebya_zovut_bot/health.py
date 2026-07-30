from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from contextlib import suppress
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class RuntimeHealth:
    """Heartbeat plus an out-of-loop watchdog for deadlock recovery."""

    def __init__(
        self,
        *,
        path: Path,
        interval_seconds: float,
        watchdog_timeout_seconds: float,
    ) -> None:
        self._path = path
        self._interval_seconds = interval_seconds
        self._watchdog_timeout_seconds = watchdog_timeout_seconds
        self._last_tick = time.monotonic()
        self._stop_thread = threading.Event()
        self._async_task: asyncio.Task[None] | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._async_task is not None:
            raise RuntimeError("Runtime health monitor is already running")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._touch()
        self._async_task = asyncio.create_task(
            self._heartbeat_loop(),
            name="runtime-heartbeat",
        )
        self._thread = threading.Thread(
            target=self._watchdog_loop,
            name="event-loop-watchdog",
            daemon=True,
        )
        self._thread.start()

    async def stop(self) -> None:
        task = self._async_task
        self._async_task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        self._stop_thread.set()
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.0)
        with suppress(OSError):
            self._path.unlink()

    def _touch(self) -> None:
        self._last_tick = time.monotonic()
        try:
            self._path.touch()
        except OSError:
            LOGGER.exception("Cannot update healthcheck heartbeat path=%s", self._path)

    async def _heartbeat_loop(self) -> None:
        while True:
            self._touch()
            await asyncio.sleep(self._interval_seconds)

    def _watchdog_loop(self) -> None:
        check_interval = max(min(self._watchdog_timeout_seconds / 4, 15.0), 1.0)
        while not self._stop_thread.wait(check_interval):
            stalled_for = time.monotonic() - self._last_tick
            if stalled_for <= self._watchdog_timeout_seconds:
                continue
            LOGGER.critical(
                "Event loop watchdog timed out after %.1fs; terminating for restart",
                stalled_for,
            )
            # A wedged event loop cannot shut down cleanly. Docker's restart policy
            # will replace the process and the SQLite outbox will resume delivery.
            os._exit(70)
