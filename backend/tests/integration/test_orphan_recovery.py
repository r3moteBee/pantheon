"""Startup recovery of restart-orphaned jobs.

A backend restart leaves in-flight jobs marked 'running'; recovery must
stall them and auto-requeue autonomous_task jobs (ledger-resumable by
design) with a capped retry counter. Regression for a real incident: an
H6 deploy killed a merge task mid-run and it sat stalled until manually
rerun.
"""
import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")

from jobs.recovery import recover_orphaned_jobs, AUTO_REQUEUE_MAX
from jobs.store import get_store, JobStatus


def _make_running(job_type="autonomous_task", payload=None):
    store = get_store()
    j = store.create(job_type=job_type, project_id="orphan-test",
                     title="t", description="d", payload=payload or {"task_name": "x"},
                     timeout_seconds=7200)
    with store._connect() as conn:
        conn.execute("UPDATE jobs SET status = ? WHERE id = ?",
                     (JobStatus.RUNNING, j["id"]))
    return j["id"]


def test_orphaned_autonomous_task_is_requeued():
    store = get_store()
    jid = _make_running()
    result = recover_orphaned_jobs()
    requeued = dict(result["requeued"])
    assert jid in requeued, "orphan was not requeued"
    old = store.get(jid)
    assert old["status"] == JobStatus.STALLED
    assert "auto-requeued" in (old["error"] or "")
    new = store.get(requeued[jid])
    assert new["status"] == JobStatus.QUEUED
    assert new["parent_job_id"] == jid
    assert new["payload"]["auto_requeue_count"] == 1
    assert new["payload"]["task_name"] == "x"  # original payload preserved
    assert new["timeout_seconds"] == 7200
    # Clean up the queued copy so the live worker in other contexts
    # doesn't pick up a synthetic test job.
    with store._connect() as conn:
        conn.execute("DELETE FROM jobs WHERE id IN (?, ?)", (jid, new["id"]))


def test_requeue_budget_exhausted():
    store = get_store()
    jid = _make_running(payload={"task_name": "x",
                                 "auto_requeue_count": AUTO_REQUEUE_MAX})
    result = recover_orphaned_jobs()
    assert jid in result["stalled"]
    assert jid not in dict(result["requeued"])
    old = store.get(jid)
    assert old["status"] == JobStatus.STALLED
    assert "budget exhausted" in (old["error"] or "")
    with store._connect() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (jid,))


def test_non_requeueable_type_only_stalled():
    store = get_store()
    jid = _make_running(job_type="extraction")
    result = recover_orphaned_jobs()
    assert jid in result["stalled"]
    old = store.get(jid)
    assert old["status"] == JobStatus.STALLED
    assert "not auto-requeued" in (old["error"] or "")
    with store._connect() as conn:
        conn.execute("DELETE FROM jobs WHERE id = ?", (jid,))


def test_noop_with_no_orphans():
    result = recover_orphaned_jobs()
    assert result["orphans"] == len(result["requeued"]) + len(result["stalled"])


def test_recovery_runs_before_worker_start():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2] / "main.py").read_text(encoding="utf-8")
    assert "recover_orphaned_jobs" in src
    assert src.index("recover_orphaned_jobs()") < src.index("get_worker().start()")
