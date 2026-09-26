"""Job worker / chat-history correctness (batch 3 regressions).

- handler-reported failure/cancel must not be recorded as completed
- terminal writes are status-guarded (watchdog-stalled rows stay stalled)
- user cancel actually stops a running handler
- a store error during dispatch doesn't kill the worker loop
- resumed sessions load the NEWEST messages
- run_autonomous surfaces LLM errors
"""
from __future__ import annotations

import asyncio
import os
import uuid

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")

import pytest

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


def _register(job_type, fn, timeout=60):
    HANDLERS[job_type] = Handler(job_type=job_type, fn=fn, default_timeout_seconds=timeout)


def _claim(store, job_type, **kw):
    store.create(job_type=job_type, project_id="lifecycle-test", **kw)
    job = store.claim_next()
    assert job and job["job_type"] == job_type
    return job


@pytest.mark.asyncio
async def test_handler_reported_failure_is_failed(store, fast_supervise):
    jt = f"t-fail-{uuid.uuid4().hex[:6]}"

    async def h(ctx):
        return {"status": "failed", "error": "required MCP tools offline"}
    _register(jt, h)
    job = _claim(store, jt)
    await JobWorker(store)._dispatch(job)
    row = store.get(job["id"])
    assert row["status"] == JobStatus.FAILED
    assert "MCP tools offline" in row["error"]


@pytest.mark.asyncio
async def test_handler_reported_cancel_is_cancelled(store, fast_supervise):
    jt = f"t-cancel-ret-{uuid.uuid4().hex[:6]}"

    async def h(ctx):
        return {"status": "cancelled"}
    _register(jt, h)
    job = _claim(store, jt)
    await JobWorker(store)._dispatch(job)
    assert store.get(job["id"])["status"] == JobStatus.CANCELLED


@pytest.mark.asyncio
async def test_ok_status_completes(store, fast_supervise):
    jt = f"t-ok-{uuid.uuid4().hex[:6]}"

    async def h(ctx):
        return {"status": "ok", "value": 1}
    _register(jt, h)
    job = _claim(store, jt)
    await JobWorker(store)._dispatch(job)
    row = store.get(job["id"])
    assert row["status"] == JobStatus.COMPLETED
    assert row["result"]["value"] == 1


def test_complete_does_not_overwrite_stalled(store):
    j = store.create(job_type="x", project_id="lifecycle-test")
    store.claim_next()
    store.mark_stalled(j["id"], error="watchdog")
    assert store.complete(j["id"], result={"late": True}) is False
    assert store.fail(j["id"], error="late") is False
    assert store.get(j["id"])["status"] == JobStatus.STALLED


@pytest.mark.asyncio
async def test_user_cancel_stops_running_handler(store, fast_supervise):
    jt = f"t-cancel-{uuid.uuid4().hex[:6]}"
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def h(ctx):
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            stopped.set()
        return {"status": "ok"}
    _register(jt, h)
    job = _claim(store, jt)
    dispatch = asyncio.create_task(JobWorker(store)._dispatch(job))
    await asyncio.wait_for(started.wait(), 2)
    store.cancel(job["id"])
    await asyncio.wait_for(dispatch, 5)
    assert stopped.is_set()
    assert store.get(job["id"])["status"] == JobStatus.CANCELLED


@pytest.mark.asyncio
async def test_timeout_cancels_handler(store, fast_supervise):
    jt = f"t-timeout-{uuid.uuid4().hex[:6]}"
    stopped = asyncio.Event()

    async def h(ctx):
        try:
            await asyncio.sleep(60)
        finally:
            stopped.set()
    _register(jt, h)
    job = _claim(store, jt, timeout_seconds=0.2)
    await asyncio.wait_for(JobWorker(store)._dispatch(job), 5)
    assert stopped.is_set()
    row = store.get(job["id"])
    assert row["status"] == JobStatus.FAILED
    assert "timed out" in row["error"]


@pytest.mark.asyncio
async def test_watchdog_stall_stops_handler(store, fast_supervise):
    jt = f"t-stall-{uuid.uuid4().hex[:6]}"
    started = asyncio.Event()
    stopped = asyncio.Event()

    async def h(ctx):
        started.set()
        try:
            await asyncio.sleep(60)
        finally:
            stopped.set()
        return {"status": "ok"}
    _register(jt, h)
    job = _claim(store, jt)
    dispatch = asyncio.create_task(JobWorker(store)._dispatch(job))
    await asyncio.wait_for(started.wait(), 2)
    store.mark_stalled(job["id"], error="no heartbeat")
    await asyncio.wait_for(dispatch, 5)
    assert stopped.is_set()
    assert store.get(job["id"])["status"] == JobStatus.STALLED


@pytest.mark.asyncio
async def test_worker_loop_survives_store_error(store, fast_supervise, monkeypatch):
    jt = f"t-loop-{uuid.uuid4().hex[:6]}"
    ran = []

    async def h(ctx):
        ran.append(ctx.job_id)
        return {"status": "ok"}
    _register(jt, h)
    monkeypatch.setattr(worker_mod, "POLL_INTERVAL_SECONDS", 0.01)
    store.create(job_type=jt, project_id="lifecycle-test")
    store.create(job_type=jt, project_id="lifecycle-test")

    real_complete = store.complete
    calls = {"n": 0}

    def flaky_complete(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        return real_complete(*a, **kw)
    monkeypatch.setattr(store, "complete", flaky_complete)

    w = JobWorker(store)
    w.start()
    try:
        for _ in range(200):
            if len(ran) >= 2 and calls["n"] >= 2:
                break
            await asyncio.sleep(0.02)
    finally:
        await w.stop()
    assert len(ran) == 2, "worker loop died after the first store error"


# ── Chat history ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_history_returns_newest_in_order(tmp_path, monkeypatch):
    from memory.episodic import EpisodicMemory
    ep = EpisodicMemory(db_path=str(tmp_path / "ep.db"))
    sid = f"hist-{uuid.uuid4().hex[:8]}"
    for i in range(10):
        await ep.save_message(session_id=sid, project_id="p", role="user", content=f"m{i}")
    hist = await ep.get_history(session_id=sid, limit=3)
    assert [m["content"] for m in hist] == ["m7", "m8", "m9"]


# ── run_autonomous ───────────────────────────────────────────────────────────

class _ErrProvider:
    async def chat_complete(self, messages, tools=None, **kw):
        raise RuntimeError("LLM endpoint 503")


@pytest.mark.asyncio
async def test_run_autonomous_raises_on_llm_error():
    from agent.core import AgentCore
    agent = AgentCore(provider=_ErrProvider())
    with pytest.raises(RuntimeError, match="503"):
        await agent.run_autonomous("do the thing")


# ── Skill scan gate ──────────────────────────────────────────────────────────

def test_failed_scan_skill_is_blocked_unless_overridden():
    from skills.models import LoadedSkill, ScanResult, SkillManifest
    manifest = SkillManifest(name="shady", description="d")
    manifest.security_scan = ScanResult(passed=False, risk_score=0.9, findings=[])
    sk = LoadedSkill(manifest=manifest, is_bundled=False)
    assert sk.scan_blocked and not sk.is_enabled_for("default")
    sk.scan_override = True
    assert not sk.scan_blocked and sk.is_enabled_for("default")
    bundled = LoadedSkill(manifest=manifest, is_bundled=True)
    assert bundled.is_enabled_for("default")
