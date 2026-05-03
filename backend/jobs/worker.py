"""Single in-process polling worker.

Started as an asyncio task during FastAPI lifespan startup. Loops:
  1. claim_next() — atomic queued→running transition
  2. resolve handler from registry
  3. wait_for(handler.fn(ctx), timeout=job.timeout or handler default)
  4. on success → store.complete with the partial_result
     on TimeoutError → store.fail with 'timeout after Ns'
     on Exception → store.fail with the exception text
  5. sleep poll_interval, repeat

Concurrency: one in-flight job. Bump WORKER_CONCURRENCY env var if it
becomes painful; for now sequential is fine for single-user pantheon.
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


class JobWorker:
    """Polling worker. Lifecycle: start() → run forever → stop() to cancel."""

    def __init__(self, store: JobStore | None = None):
        self.store = store or get_store()
        self._task: asyncio.Task | None = None
        self._stopping = False

    def start(self) -> None:
        if self._task and not self._task.done():
            logger.debug("worker already running")
            return
        self._stopping = False
        self._task = asyncio.create_task(self._loop(), name="job-worker")
        logger.info(
            "Job worker started; %d handler(s) registered: %s",
            len(HANDLERS), sorted(HANDLERS.keys()),
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
                try:
                    job = self.store.claim_next()
                except Exception as e:
                    logger.exception("claim_next failed: %s", e)
                    job = None
                if not job:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                    continue
                await self._dispatch(job)
        except asyncio.CancelledError:
            logger.info("Job worker stopping")
            raise

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

        try:
            result = await asyncio.wait_for(handler.fn(ctx), timeout=timeout)
            # Merge any partial_result the handler accumulated, then overlay
            # whatever the handler returned explicitly.
            merged = {**(ctx.partial_result or {}), **(result or {})}
            self.store.complete(
                job["id"],
                result=merged,
                session_id=merged.get("session_id"),
                artifact_id=merged.get("artifact_id"),
                pr_url=merged.get("pr_url"),
            )
            logger.info("Job %s completed", job["id"])
        except asyncio.TimeoutError:
            err = f"Handler timed out after {timeout}s"
            logger.warning("Job %s: %s", job["id"], err)
            self.store.fail(job["id"], error=err,
                            session_id=(ctx.partial_result or {}).get("session_id"))
        except asyncio.CancelledError:
            # Worker is stopping; mark the job as cancelled so it can be
            # re-queued cleanly on next startup.
            self.store.fail(job["id"], error="Worker stopped before completion")
            raise
        except Exception as e:
            tb = traceback.format_exc()
            logger.exception("Job %s failed", job["id"])
            self.store.fail(
                job["id"],
                error=f"{type(e).__name__}: {e}\n\n{tb[-1500:]}",
                session_id=(ctx.partial_result or {}).get("session_id"),
            )


_INSTANCE: JobWorker | None = None

def get_worker() -> JobWorker:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = JobWorker()
    return _INSTANCE
