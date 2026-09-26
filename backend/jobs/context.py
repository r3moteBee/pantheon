"""JobContext — what handlers receive when a job is dispatched."""
from __future__ import annotations

import asyncio
import logging
import time
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
    # monotonic time of the handler's last real progress (see touch()).
    last_progress: float = field(default_factory=time.monotonic)

    def touch(self) -> None:
        """Record real progress (an agent step, a tool result). pinger_for
        with max_quiet stops keeping the job alive once this goes stale."""
        self.last_progress = time.monotonic()

    async def heartbeat(self, progress: str | None = None) -> None:
        """Tell the watchdog this job is still alive. Optionally update
        the human-readable progress string."""
        self.touch()
        await self._write_heartbeat(progress)

    async def _write_heartbeat(self, progress: str | None = None) -> None:
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
                    await ctx._write_heartbeat()
                except Exception:
                    pass
        return _loop


# Default for agent-driven handlers: if the agent reports no progress for
# this long, stop masking it — the watchdog stalls the row and the worker
# cancels the handler.
AGENT_MAX_QUIET_SECONDS = 900.0


class pinger_for:
    """Async context manager that heartbeats every `interval` seconds for
    the duration of the with-block. Use around any single awaitable that
    might exceed the stall watchdog window:

        async with pinger_for(ctx, 30.0, max_quiet=AGENT_MAX_QUIET_SECONDS):
            result = await agent.run_autonomous(prompt)

    With ``max_quiet`` set, the pinger only vouches for the job while
    ``ctx.touch()``/``ctx.heartbeat()`` has been called within that many
    seconds. Without it, a genuinely hung await (the pinger keeps beating
    regardless) could never be detected.
    """
    def __init__(self, ctx: JobContext, interval: float = 30.0,
                 max_quiet: float | None = None):
        self.ctx = ctx
        self.interval = interval
        self.max_quiet = max_quiet
        self._task: asyncio.Task | None = None

    async def __aenter__(self):
        from utils.progress import set_reporter
        self.ctx.touch()
        # Code awaited inside the with-block reports progress via
        # utils.progress.report_progress() -> ctx.touch().
        self._reporter_token = set_reporter(self.ctx.touch)

        async def loop():
            warned = False
            try:
                while True:
                    await asyncio.sleep(self.interval)
                    quiet = time.monotonic() - self.ctx.last_progress
                    if self.max_quiet is not None and quiet > self.max_quiet:
                        if not warned:
                            logger.warning(
                                "Job %s: no progress for %.0fs — no longer heartbeating "
                                "(watchdog will stall it)", self.ctx.job_id[:8], quiet,
                            )
                            warned = True
                        continue
                    warned = False
                    await self.ctx._write_heartbeat()
            except asyncio.CancelledError:
                return
        self._task = asyncio.create_task(loop())
        return self

    async def __aexit__(self, exc_type, exc, tb):
        from utils.progress import reset_reporter
        try:
            reset_reporter(self._reporter_token)
        except (ValueError, AttributeError):
            pass
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (Exception, asyncio.CancelledError):
                pass
