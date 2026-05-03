"""JobContext — what handlers receive when a job is dispatched."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from jobs.store import JobStore

logger = logging.getLogger(__name__)


@dataclass
class JobContext:
    """Passed to every handler. Provides heartbeat / cancel / result helpers
    so handlers don't have to import the store directly.
    """
    job_id: str
    job_type: str
    project_id: str
    payload: dict[str, Any]
    store: JobStore
    title: str = ""
    description: str = ""

    # populated as the handler runs — surfaced via update_result()
    partial_result: dict[str, Any] = field(default_factory=dict)

    async def heartbeat(self, progress: str | None = None) -> None:
        """Tell the watchdog this job is still alive. Optionally update
        the human-readable progress string."""
        try:
            self.store.heartbeat(self.job_id, progress=progress)
        except Exception:
            logger.debug("heartbeat write failed", exc_info=True)

    def cancel_requested(self) -> bool:
        """Cooperative cancel poll. Handlers should call this between steps."""
        try:
            return self.store.is_cancel_requested(self.job_id)
        except Exception:
            return False

    def update_result(self, partial: dict[str, Any]) -> None:
        """Merge into the in-memory partial result. The worker writes this
        out on success."""
        self.partial_result.update(partial)

    @staticmethod
    def heartbeat_pinger(ctx: "JobContext", interval: float = 30.0) -> Callable[[], Awaitable[None]]:
        """Return an async coroutine that heartbeats every `interval`
        seconds until cancelled. Use during long single-call awaits where
        the handler can't manually heartbeat between steps:

            async with create_pinger(ctx) as ping:
                result = await long_running_call()

        The async-context-manager wrapper is below in `pinger_for`.
        """
        async def _loop():
            while True:
                await asyncio.sleep(interval)
                try:
                    await ctx.heartbeat()
                except Exception:
                    pass
        return _loop


class pinger_for:
    """Async context manager that runs ctx.heartbeat() every `interval`
    seconds for the duration of the with-block. Use around any single
    awaitable that might exceed the stall watchdog window:

        async with pinger_for(ctx, 30.0):
            result = await llm.long_call()
    """
    def __init__(self, ctx: JobContext, interval: float = 30.0):
        self.ctx = ctx
        self.interval = interval
        self._task: asyncio.Task | None = None

    async def __aenter__(self):
        async def loop():
            try:
                while True:
                    await asyncio.sleep(self.interval)
                    await self.ctx.heartbeat()
            except asyncio.CancelledError:
                return
        self._task = asyncio.create_task(loop())
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._task:
            self._task.cancel()
            try: await self._task
            except Exception: pass
