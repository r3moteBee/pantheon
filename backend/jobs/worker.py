"""Single in-process polling worker.

Started as an asyncio task during FastAPI lifespan startup. Loops:
  1. claim_next() — atomic queued→running transition
  2. resolve handler from registry
  3. run handler.fn(ctx) as a task and supervise it: every
     SUPERVISE_INTERVAL it checks the timeout, the user's cancel flag and
     whether the watchdog stalled the row — any of those cancels the task
  4. on success → store.complete, unless the handler returned
     status=failed/error (→ store.fail) or status=cancelled
     on timeout → store.fail; on user cancel → store.mark_cancelled
     on Exception → store.fail with the exception text
  5. sleep poll_interval, repeat

All terminal writes are guarded on status='running', so a row the
watchdog already stalled is never flipped back to completed.

Concurrency: up to JOB_WORKER_CONCURRENCY jobs at once (default 2), so a
long batch ingest doesn't hold up a scheduled digest. Jobs that work in the
project's repo checkout (REPO_JOB_TYPES) never run two at a time in the
same project. Set JOB_WORKER_CONCURRENCY=1 for strictly sequential.
"""
from __future__ import annotations

import asyncio
import logging
import os
import traceback
from typing import Any

from jobs.context import JobContext
from jobs.handlers import get_handler, HANDLERS
from jobs.store import JobStore, JobStatus, get_store

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = float(os.getenv("JOB_WORKER_POLL_SECONDS", "1.0"))
SUPERVISE_INTERVAL_SECONDS = float(os.getenv("JOB_WORKER_SUPERVISE_SECONDS", "2.0"))
CONCURRENCY = max(1, int(os.getenv("JOB_WORKER_CONCURRENCY", "2")))
# Share the project's git checkout, so at most one per project at a time.
REPO_JOB_TYPES = ("coding_task", "iteration_loop")
# How long to wait for a cancelled handler to unwind before moving on.
CANCEL_GRACE_SECONDS = 30.0

_FAILED_STATUSES = {"failed", "error"}


class JobWorker:
    """Polling worker. Lifecycle: start() → run forever → stop() to cancel."""

    def __init__(self, store: JobStore | None = None, concurrency: int | None = None):
        self.store = store or get_store()
        self.concurrency = max(1, concurrency or CONCURRENCY)
        self._task: asyncio.Task | None = None
        self._stopping = False
        # in-flight dispatch task -> (project_id, job_type)
        self._running: dict[asyncio.Task, tuple[str, str]] = {}

    def start(self) -> None:
        if self._task and not self._task.done():
            logger.debug("worker already running")
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="job-worker")
        logger.info(
            "Job worker started (concurrency %d); %d handler(s) registered: %s",
            self.concurrency, len(HANDLERS), sorted(HANDLERS.keys()),
        )

    async def stop(self) -> None:
        self._stopping = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass

    async def _loop(self) -> None:
        try:
            while not self._stopping:
                if len(self._running) >= self.concurrency:
                    done, _ = await asyncio.wait(set(self._running), return_when=asyncio.FIRST_COMPLETED)
                    for t in done:
                        self._running.pop(t, None)
                    continue
                busy = {p for p, t in self._running.values() if t in REPO_JOB_TYPES}
                try:
                    job = self.store.claim_next(exclusive_types=REPO_JOB_TYPES, busy_projects=busy)
                except Exception as e:
                    logger.exception("claim_next failed: %s", e)
                    job = None
                if not job:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue
                task = asyncio.create_task(self._dispatch_safely(job), name=f"dispatch-{job['id'][:8]}")
                self._running[task] = (job.get("project_id") or "", job.get("job_type") or "")
                task.add_done_callback(lambda t: self._running.pop(t, None))
        except asyncio.CancelledError:
            logger.info("Job worker stopping")
            # Leave rows 'running': startup orphan recovery re-queues them.
            for t in list(self._running):
                t.cancel()
            if self._running:
                await asyncio.wait(set(self._running), timeout=CANCEL_GRACE_SECONDS)
            raise

    async def _dispatch_safely(self, job: dict[str, Any]) -> None:
        try:
            await self._dispatch(job)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A store error (e.g. "database is locked") must not kill the
            # worker — nothing would run until restart.
            logger.exception("dispatch of job %s crashed; continuing", job.get("id"))

    async def _dispatch(self, job: dict[str, Any]) -> None:
        handler = get_handler(job["job_type"])
        if not handler:
            err = f"No handler registered for job_type={job['job_type']!r}"
            logger.warning("%s (job %s)", err, job["id"])
            self.store.fail(job["id"], error=err)
            return

        timeout = job.get("timeout_seconds") or handler.default_timeout_seconds
        ctx = JobContext(
            job_id=job["id"],
            job_type=job["job_type"],
            project_id=job["project_id"],
            payload=job.get("payload") or {},
            store=self.store,
            title=job.get("title") or "",
            description=job.get("description") or "",
        )

        logger.info(
            "Dispatching job %s type=%s project=%s timeout=%ss",
            job["id"], job["job_type"], job["project_id"], timeout,
        )

        jid = job["id"]
        task = asyncio.create_task(handler.fn(ctx), name=f"job-{jid[:8]}")
        try:
            stop_reason = await self._supervise(jid, task, timeout)
        except asyncio.CancelledError:
            # Worker shutting down. Stop the handler but leave the row
            # 'running': startup orphan recovery re-queues it cleanly.
            task.cancel()
            await asyncio.wait({task}, timeout=CANCEL_GRACE_SECONDS)
            raise

        session_id = (ctx.partial_result or {}).get("session_id")
        if stop_reason == "timeout":
            err = f"Handler timed out after {timeout}s"
            logger.warning("Job %s: %s", jid, err)
            self.store.fail(jid, error=err, session_id=session_id)
            return
        if stop_reason == "cancelled":
            logger.info("Job %s cancelled by user", jid)
            self.store.mark_cancelled(jid, session_id=session_id)
            return
        if stop_reason == "stalled":
            logger.warning("Job %s was marked stalled; handler stopped", jid)
            return

        try:
            result = task.result()
        except asyncio.CancelledError:
            self.store.fail(jid, error="Handler was cancelled", session_id=session_id)
            return
        except Exception as e:
            tb = "".join(traceback.format_exception(e))
            logger.error("Job %s failed: %s", jid, e, exc_info=e)
            self.store.fail(
                jid,
                error=f"{type(e).__name__}: {e}\n\n{tb[-1500:]}",
                session_id=session_id,
            )
            return

        # Merge any partial_result the handler accumulated, then overlay
        # whatever the handler returned explicitly.
        merged = {**(ctx.partial_result or {}), **(result or {})}
        status = str(merged.get("status") or "").lower()
        if status in _FAILED_STATUSES:
            err = str(merged.get("error") or merged.get("reason") or "Handler reported failure")
            logger.warning("Job %s reported failure: %s", jid, err[:200])
            self.store.fail(jid, error=err, session_id=merged.get("session_id"), result=merged)
        elif status == "cancelled":
            self.store.mark_cancelled(jid, session_id=merged.get("session_id"), result=merged)
        else:
            self.store.complete(
                jid,
                result=merged,
                session_id=merged.get("session_id"),
                artifact_id=merged.get("artifact_id"),
                pr_url=merged.get("pr_url"),
            )
            logger.info("Job %s completed", jid)

    async def _supervise(self, job_id: str, task: asyncio.Task, timeout: float) -> str | None:
        """Wait for ``task``; cancel it on timeout, user cancel, or when the
        watchdog has stalled the row. Returns the stop reason, or None if
        the handler finished on its own."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + float(timeout)
        reason: str | None = None
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                reason = "timeout"
                break
            done, _ = await asyncio.wait({task}, timeout=min(SUPERVISE_INTERVAL_SECONDS, remaining))
            if done:
                return None
            try:
                if self.store.get_status(job_id) != JobStatus.RUNNING:
                    reason = "stalled"
                    break
                if self.store.is_cancel_requested(job_id):
                    reason = "cancelled"
                    break
            except Exception:
                logger.debug("supervise status check failed", exc_info=True)
        task.cancel()
        done, _ = await asyncio.wait({task}, timeout=CANCEL_GRACE_SECONDS)
        if not done:
            logger.error("Job %s handler ignored cancellation for %ss", job_id, CANCEL_GRACE_SECONDS)
        elif not task.cancelled() and task.exception():
            logger.debug("Job %s raised while cancelling: %s", job_id, task.exception())
        return reason

_INSTANCE: JobWorker | None = None

def get_worker() -> JobWorker:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = JobWorker()
    return _INSTANCE
