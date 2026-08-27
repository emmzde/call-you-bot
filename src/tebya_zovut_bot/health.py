from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path

LOGGER = logging.getLogger(__name__)


class AsyncServiceProbe:
    """Persist the last successful result of an external async health probe."""

    def __init__(
        self,
        *,
        path: Path,
        interval_seconds: float,
        operation: Callable[[], Awaitable[object]],
        name: str,
    ) -> None:
        if interval_seconds <= 0:
            raise ValueError("probe interval must be positive")
        self._path = path
        self._interval_seconds = interval_seconds
        self._operation = operation
        self._name = name
        self._task: asyncio.Task[None] | None = None

    def start(self, *, initially_healthy: bool = False) -> None:
        if self._task is not None:
            raise RuntimeError(f"{self._name} health probe is already running")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        if initially_healthy:
            self._path.touch()
        self._task = asyncio.create_task(
            self._run(),
            name=f"{self._name}-health-probe",
        )

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is not None:
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        with suppress(OSError):
            self._path.unlink()

    async def _run(self) -> None:
        while True:
            try:
                await self._operation()
            except asyncio.CancelledError:
                raise
            except Exception:
                LOGGER.warning(
                    "%s health probe failed",
                    self._name,
                    exc_info=True,
                )
            else:
                try:
                    self._path.touch()
                except OSError:
                    LOGGER.exception(
                        "Cannot update %s health path=%s",
                        self._name,
                        self._path,
                    )
            await asyncio.sleep(self._interval_seconds)


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
