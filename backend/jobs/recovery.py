"""Startup recovery for restart-orphaned jobs.

A backend restart kills any in-flight job; on the next startup those rows
still say 'running'. In Pantheon's single-process design that's
definitive — no other worker could own them. Mark them stalled, and
auto-requeue autonomous_task jobs: their task-ledger protocol makes a
fresh run RESUME from the ledger instead of redoing work, so requeueing
is safe by design. Other job types stay stalled for manual rerun.

The auto_requeue_count payload counter caps retries so a job that
crashes the backend can't requeue itself forever.

Observed incident this guards against: an H6 deploy restarted the
backend mid-merge-task; the job sat 'running' until the watchdog flagged
it stalled and the user had to notice and rerun it by hand.
"""
from __future__ import annotations

import logging
import os

from jobs.store import get_store

logger = logging.getLogger(__name__)

AUTO_REQUEUE_MAX = int(os.getenv("JOB_AUTO_REQUEUE_MAX", "2"))

# Only job types whose handlers can resume safely. autonomous_task has the
# task-ledger protocol; everything else gets stalled for a human decision.
_REQUEUEABLE_TYPES = {"autonomous_task"}


def recover_orphaned_jobs() -> dict:
    """Run once during FastAPI lifespan startup, BEFORE the worker starts
    (so a requeued job can't race its own orphaned predecessor)."""
    store = get_store()
    orphans = store.list(statuses=["running"], limit=200)
    requeued: list[tuple[str, str]] = []
    stalled_only: list[str] = []

    for job in orphans:
        jid = job["id"]
        payload = job.get("payload") or {}
        count = int(payload.get("auto_requeue_count") or 0)
        try:
            if job["job_type"] in _REQUEUEABLE_TYPES and count < AUTO_REQUEUE_MAX:
                store.mark_stalled(jid, error=(
                    f"Orphaned by backend restart — auto-requeued "
                    f"(attempt {count + 1}/{AUTO_REQUEUE_MAX}). The new run "
                    f"resumes from the task ledger if one exists."))
                new = store.rerun(jid, extra_payload={"auto_requeue_count": count + 1})
                requeued.append((jid, new["id"]))
                logger.warning(
                    "Orphan recovery: requeued %s as %s (attempt %d/%d)",
                    jid[:8], new["id"][:8], count + 1, AUTO_REQUEUE_MAX)
            else:
                reason = ("auto-requeue budget exhausted"
                          if job["job_type"] in _REQUEUEABLE_TYPES
                          else f"{job['job_type']} jobs are not auto-requeued")
                store.mark_stalled(jid, error=(
                    f"Orphaned by backend restart — {reason}; "
                    f"re-queue manually from the Tasks tab if needed."))
                stalled_only.append(jid)
                logger.warning("Orphan recovery: stalled %s (%s)", jid[:8], reason)
        except Exception:
            logger.exception("Orphan recovery failed for job %s", jid)

    if orphans:
        logger.warning(
            "Orphan recovery: %d orphan(s) — %d requeued, %d stalled",
            len(orphans), len(requeued), len(stalled_only))
    return {"orphans": len(orphans),
            "requeued": requeued,
            "stalled": stalled_only}
