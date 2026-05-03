"""Stall watchdog — marks running jobs with no recent heartbeat as stalled.

A separate asyncio task from the worker so a stuck handler can't block
the watchdog's ability to mark itself stalled. Runs every 60s by default.
"""
from __future__ import annotations

import asyncio
import logging
import os

from jobs.store import get_store

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = float(os.getenv("JOB_STALL_CHECK_SECONDS", "60"))
STALL_TIMEOUT_SECONDS = int(os.getenv("JOB_STALL_TIMEOUT_SECONDS", "300"))  # 5 minutes


class StallWatchdog:
    def __init__(self):
        self._task: asyncio.Task | None = None
        self._stopping = False

    def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="job-watchdog")
        logger.info(
            "Stall watchdog started: check=%ss timeout=%ss",
            CHECK_INTERVAL_SECONDS, STALL_TIMEOUT_SECONDS,
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try: await self._task
            except (asyncio.CancelledError, Exception): pass

    async def _loop(self) -> None:
        try:
            while not self._stopping:
                try:
                    n = get_store().stall_running(idle_seconds=STALL_TIMEOUT_SECONDS)
                    if n:
                        logger.warning("Watchdog: marked %d job(s) stalled", n)
                except Exception:
                    logger.exception("watchdog tick failed")
                await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            logger.info("Stall watchdog stopping")
            raise


_INSTANCE: StallWatchdog | None = None

def get_watchdog() -> StallWatchdog:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = StallWatchdog()
    return _INSTANCE
