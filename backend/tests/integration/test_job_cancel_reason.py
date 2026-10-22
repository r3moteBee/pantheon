"""Cancelled jobs record why (UI review 2026-10-21 #6/#32), and the job
detail API returns the project's display name."""
from __future__ import annotations

import asyncio
import os
import sqlite3
import uuid

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")

import pytest

import api.jobs as jobs_api
import jobs.worker as worker_mod
from jobs.handlers import HANDLERS, Handler
from jobs.store import JobStatus, JobStore
from jobs.worker import JobWorker


@pytest.fixture
def store(tmp_path):
    return JobStore(db_path=str(tmp_path / "jobs.db"))


@pytest.fixture
def fast_supervise(monkeypatch):
    monkeypatch.setattr(worker_mod, "SUPERVISE_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(worker_mod, "CANCEL_GRACE_SECONDS", 2.0)


def test_cancel_queued_job_stores_reason(store):
    j = store.create(job_type="x", project_id="p1")
    assert store.cancel(j["id"], reason="Its project was deleted")
    row = store.get(j["id"])
    assert row["status"] == JobStatus.CANCELLED
    assert row["cancel_reason"] == "Its project was deleted"
    # error carries it too, for the agent's job-ledger context
    assert row["error"] == "Its project was deleted"


def test_cancel_without_reason_gets_default(store):
    j = store.create(job_type="x", project_id="p1")
    store.cancel(j["id"])
    assert store.get(j["id"])["cancel_reason"]


@pytest.mark.asyncio
async def test_running_cancel_keeps_requested_reason(store, fast_supervise):
    jt = f"t-cr-{uuid.uuid4().hex[:6]}"
    started = asyncio.Event()

    async def h(ctx):
        started.set()
        await asyncio.sleep(60)
    HANDLERS[jt] = Handler(job_type=jt, fn=h, default_timeout_seconds=60)
    store.create(job_type=jt, project_id="p1")
    job = store.claim_next()
    dispatch = asyncio.create_task(JobWorker(store)._dispatch(job))
    await asyncio.wait_for(started.wait(), 2)
    store.cancel(job["id"], reason="Cancelled by the user")
    assert store.get(job["id"])["status"] == JobStatus.RUNNING  # cooperative
    await asyncio.wait_for(dispatch, 5)
    row = store.get(job["id"])
    assert row["status"] == JobStatus.CANCELLED
    assert row["cancel_reason"] == "Cancelled by the user"
    assert row["error"] == "Cancelled by the user"


@pytest.mark.asyncio
async def test_handler_reported_cancel_gets_a_reason(store, fast_supervise):
    jt = f"t-hr-{uuid.uuid4().hex[:6]}"

    async def h(ctx):
        return {"status": "cancelled", "reason": "required repo binding missing"}
    HANDLERS[jt] = Handler(job_type=jt, fn=h, default_timeout_seconds=60)
    store.create(job_type=jt, project_id="p1")
    job = store.claim_next()
    await JobWorker(store)._dispatch(job)
    row = store.get(job["id"])
    assert row["status"] == JobStatus.CANCELLED
    assert row["cancel_reason"] == "required repo binding missing"


def test_old_database_gets_cancel_reason_column(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute("""CREATE TABLE jobs (id TEXT PRIMARY KEY, job_type TEXT NOT NULL,
        project_id TEXT NOT NULL DEFAULT 'default', status TEXT NOT NULL DEFAULT 'queued',
        title TEXT, description TEXT, payload TEXT NOT NULL DEFAULT '{}',
        result TEXT, error TEXT, progress TEXT, attempts INTEGER NOT NULL DEFAULT 0,
        max_attempts INTEGER NOT NULL DEFAULT 1, scheduled_for TEXT, timeout_seconds INTEGER,
        parent_job_id TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT, last_heartbeat_at TEXT,
        schedule_id TEXT, session_id TEXT, artifact_id TEXT, pr_url TEXT)""")
    conn.commit(); conn.close()
    s = JobStore(db_path=str(db))
    j = s.create(job_type="x", project_id="p1")
    s.cancel(j["id"], reason="r")
    assert s.get(j["id"])["cancel_reason"] == "r"


@pytest.mark.asyncio
async def test_api_cancel_and_detail(store, monkeypatch):
    monkeypatch.setattr(jobs_api, "get_store", lambda: store)
    import api.projects as projects_mod
    monkeypatch.setattr(projects_mod, "_load_projects",
                        lambda: {"p1": {"id": "p1", "name": "Marble Maze Game"}})
    j = store.create(job_type="x", project_id="p1")
    await jobs_api.cancel_job(j["id"], None)
    detail = await jobs_api.get_job(j["id"])
    assert detail["cancel_reason"] == "Cancelled by the user"
    assert detail["project_name"] == "Marble Maze Game"

    j2 = store.create(job_type="x", project_id="gone")
    await jobs_api.cancel_job(j2["id"], jobs_api.CancelJobRequest(reason="  "))
    detail2 = await jobs_api.get_job(j2["id"])
    assert detail2["cancel_reason"] == "Cancelled by the user"
    assert detail2["project_name"] is None
