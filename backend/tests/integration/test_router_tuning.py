"""Phase 3 routing: turn outcomes, correction/feedback signals, tuning
recommendations (+apply) and the what-if replay."""
from __future__ import annotations

import os
import sqlite3
import tempfile
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from llm_config.models import EndpointWithKey, RouteEntry  # noqa: E402


@pytest.fixture
def vault(monkeypatch, tmp_path):
    from secrets import vault as _v
    fresh = _v.SecretsVault(db_path=str(tmp_path / "vault.db"), master_key="k")
    monkeypatch.setattr(_v, "_vault_instance", fresh)
    monkeypatch.setattr(_v, "_cache", {})
    fresh.set_secret("llm_config_migrated_v1", "true")
    from models import provider
    provider.reset_provider()
    yield fresh
    provider.reset_provider()


@pytest.fixture
def usage_db(monkeypatch, tmp_path):
    from llm_config import usage
    monkeypatch.setattr(usage, "get_settings", lambda: SimpleNamespace(db_dir=tmp_path))
    return usage


def _setup(**classes):
    from llm_config.store import save_endpoint, set_routes
    save_endpoint(EndpointWithKey(name="ep", base_url="https://ep.test/v1", api_type="openai", api_key="k"))
    routes = {"agent": [RouteEntry(endpoint="ep", model="qwen3-max")]}
    for cls, models in classes.items():
        models = models if isinstance(models, list) else [models]
        routes[cls] = [RouteEntry(endpoint="ep", model=m) for m in models]
    set_routes(routes)


def _sid():
    return str(uuid.uuid4())


class _Agent:
    def __init__(self, events):
        self.provider = None
        self._events = events

    def _get_working_messages(self):
        return []

    async def chat(self, message, stream=True):
        for e in self._events:
            yield e


async def _noop(ev):
    pass


# ── Outcomes + signals ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_turn_outcome_is_recorded(vault, usage_db):
    from api.chat import _stream_turn
    _setup(quick="gpt-5.4-nano")
    agent = _Agent([
        {"type": "tool_call", "name": "web_search", "args": {}, "id": "1"},
        {"type": "tool_result", "name": "web_search", "result": "Error executing web_search: boom", "tool_id": "1"},
        {"type": "tool_call", "name": "recall", "args": {}, "id": "2"},
        {"type": "tool_result", "name": "recall", "result": "3 results", "tool_id": "2"},
        {"type": "done", "full_response": "ok", "iterations": 3, "truncated": False},
    ])
    _, route = await _stream_turn(agent, "thanks!", _sid(), _noop)
    row = usage_db.decision_rows(1)[-1]
    assert row["decision_id"] == route["decision_id"]
    assert (row["tool_calls"], row["tool_errors"], row["iterations"]) == (2, 1, 3)
    assert row["latency_ms"] is not None and row["stream_error"] == 0 and row["corrected"] == 0


@pytest.mark.asyncio
async def test_turn_without_done_counts_as_error(vault, usage_db):
    from api.chat import _stream_turn
    _setup()
    await _stream_turn(_Agent([{"type": "error", "message": "x"}]), "hello", _sid(), _noop)
    assert usage_db.decision_rows(1)[-1]["stream_error"] == 1


@pytest.mark.asyncio
async def test_pushback_marks_previous_turn_corrected(vault, usage_db):
    from api.chat import _stream_turn
    _setup()
    sid = _sid()
    done = [{"type": "done", "full_response": "a", "iterations": 1}]
    _, r1 = await _stream_turn(_Agent(done), "what is 2+2", sid, _noop)
    _, r2 = await _stream_turn(_Agent(done), "No, that's wrong — try again", sid, _noop)
    _, r3 = await _stream_turn(_Agent(done), "thanks", sid, _noop)
    rows = {r["decision_id"]: r for r in usage_db.decision_rows(1)}
    assert rows[r1["decision_id"]]["corrected"] == 1
    assert rows[r2["decision_id"]]["corrected"] == 0
    assert rows[r3["decision_id"]]["corrected"] == 0


@pytest.mark.asyncio
async def test_repin_marks_previous_turn_corrected(vault, usage_db):
    from api.chat import _stream_turn
    from llm_config.router import set_override
    _setup(code="claude-sonnet-4-5")
    sid = _sid()
    _, r1 = await _stream_turn(_Agent([{"type": "done", "full_response": "a"}]), "hello", sid, _noop)
    set_override(sid, "auto")        # no change → not a correction
    set_override(sid, "code")        # user overrode the agent choice
    rows = {r["decision_id"]: r for r in usage_db.decision_rows(1)}
    assert rows[r1["decision_id"]]["corrected"] == 1


@pytest.mark.asyncio
async def test_pushback_is_not_sent_to_quick(vault):
    from llm_config.router import decide
    _setup(quick="gpt-5.4-nano")
    assert (await decide("thanks!", session_id=_sid())).task_class == "quick"
    assert (await decide("no, that's wrong", session_id=_sid())).task_class == "agent"


def test_correction_phrases():
    from llm_config.router import looks_like_correction
    for m in ("no", "Nope, not that", "that's not right", "You missed the second file", "try again"):
        assert looks_like_correction(m), m
    for m in ("notes please", "now do the next one", "nothing else, thanks", "know any good books?"):
        assert not looks_like_correction(m), m


@pytest.mark.asyncio
async def test_feedback_api(vault, usage_db):
    from fastapi import HTTPException
    from api.chat import _stream_turn
    from api.llm_endpoints import RouterFeedbackPayload, router_feedback
    _setup()
    _, route = await _stream_turn(_Agent([{"type": "done", "full_response": "a"}]), "hi", _sid(), _noop)
    await router_feedback(RouterFeedbackPayload(decision_id=route["decision_id"], rating=-1))
    assert usage_db.decision_rows(1)[-1]["rating"] == -1
    with pytest.raises(HTTPException):
        await router_feedback(RouterFeedbackPayload(decision_id="nope", rating=1))
    with pytest.raises(HTTPException):
        await router_feedback(RouterFeedbackPayload(decision_id=route["decision_id"], rating=5))


def test_old_decision_table_is_migrated(monkeypatch, tmp_path):
    from llm_config import usage
    conn = sqlite3.connect(tmp_path / "llm_calls.db")
    conn.execute("""CREATE TABLE route_decisions (id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL,
        session_id TEXT, task_class TEXT NOT NULL, rule TEXT NOT NULL, reason TEXT, endpoint TEXT,
        model TEXT, served_model TEXT, message_chars INTEGER, est_tokens INTEGER)""")
    conn.execute("INSERT INTO route_decisions (ts, task_class, rule) VALUES (?, 'agent', 'default')",
                 (datetime.now().timestamp(),))
    conn.commit()
    conn.close()
    monkeypatch.setattr(usage, "get_settings", lambda: SimpleNamespace(db_dir=tmp_path))
    usage._ready_paths.discard(str(tmp_path / "llm_calls.db"))
    rows = usage.decision_rows(1)
    assert rows[0]["corrected"] == 0 and "latency_ms" in rows[0]


# ── Recommendations ────────────────────────────────────────────────────

def _turns(usage, cls, rule, n, latency=1000, **every):
    """n turns; ``every`` maps field -> k (e.g. tool_calls=2 → every 2nd turn)."""
    for i in range(n):
        o = {"latency_ms": latency, "iterations": 1, "tool_calls": 0, "tool_errors": 0,
             "truncated": False, "stream_error": False}
        hit = {f for f, k in every.items() if k and i % k == 0}
        for f in hit - {"corrected"}:
            o[f] = 1
        did = uuid.uuid4().hex
        usage.record_decision(session_id="s", task_class=cls, rule=rule, reason="",
                              endpoint="ep", model=cls, served_model=cls,
                              decision_id=did, outcome=o)
        if "corrected" in hit:
            usage.mark_corrected(did)


def test_little_data_says_so(vault, usage_db):
    from llm_config.tuning import recommendations
    _setup()
    assert [r["id"] for r in recommendations(24)] == ["more-data"]


def test_quick_misfire_recommendation_and_apply(vault, usage_db):
    from llm_config.router import get_config
    from llm_config.tuning import apply_recommendation, recommendations
    _setup(quick="gpt-5.4-nano")
    _turns(usage_db, "quick", "quick", 20, tool_calls=2)       # 50% of quick turns used tools
    rec = next(r for r in recommendations(24) if r["id"] == "quick-misfire")
    assert rec["action"] == {"kind": "router_config", "patch": {"quick_max_chars": 144}}
    apply_recommendation("quick-misfire", 24)
    assert get_config()["quick_max_chars"] == 144
    with pytest.raises(ValueError):
        apply_recommendation("does-not-exist", 24)


def test_quick_quality_gap_suggests_turning_quick_off(vault, usage_db):
    from llm_config.tuning import recommendations
    _setup(quick="gpt-5.4-nano")
    _turns(usage_db, "agent", "default", 20)
    _turns(usage_db, "quick", "quick", 20, corrected=3)          # ~35% corrected
    recs = {r["id"]: r for r in recommendations(24)}
    assert recs["quick-quality"]["action"]["patch"] == {"quick_max_chars": 0}
    assert "quick-expand" not in recs


def test_quick_expand_when_fast_and_as_good(vault, usage_db):
    from llm_config.tuning import recommendations
    _setup(quick="gpt-5.4-nano")
    _turns(usage_db, "agent", "default", 20, latency=4000)
    _turns(usage_db, "quick", "quick", 20, latency=900)
    recs = {r["id"]: r for r in recommendations(24)}
    assert recs["quick-expand"]["action"]["patch"] == {"quick_max_chars": 360}


def test_unreliable_primary_swaps_with_healthy_fallback(vault, usage_db):
    from llm_config.store import get_routes
    from llm_config.tuning import apply_recommendation, recommendations
    _setup(summarize=["flaky-model", "solid-model"])
    for i in range(25):
        usage_db.record(task_class="summarize", endpoint="ep", model="flaky-model", operation="complete",
                        attempt=0, ok=i % 3 != 0, latency_ms=10, status="503" if i % 3 == 0 else None,
                        error="upstream down" if i % 3 == 0 else None)
    for _ in range(10):
        usage_db.record(task_class="summarize", endpoint="ep", model="solid-model", operation="complete",
                        attempt=1, ok=True, latency_ms=10)
    rec = next(r for r in recommendations(24) if r["id"] == "swap-summarize")
    assert "upstream down" in rec["detail"]
    apply_recommendation("swap-summarize", 24)
    assert [e["model"] for e in get_routes()["summarize"]] == ["solid-model", "flaky-model"]


def test_unreliable_primary_without_fallback_warns(vault, usage_db):
    from llm_config.tuning import recommendations
    _setup()
    for i in range(25):
        usage_db.record(task_class="agent", endpoint="ep", model="qwen3-max", operation="stream",
                        attempt=0, ok=i % 2 == 0, latency_ms=10)
    rec = next(r for r in recommendations(24) if r["id"] == "unreliable-agent")
    assert rec["action"] is None and "no fallback" in rec["detail"]


# ── What-if replay ────────────────────────────────────────────────────────

@pytest.fixture
def episodic(monkeypatch, tmp_path):
    from memory import episodic as ep_mod
    real = ep_mod.EpisodicMemory
    db = str(tmp_path / "episodic.db")
    monkeypatch.setattr(ep_mod, "EpisodicMemory", lambda *a, **k: real(db_path=db))
    mem = real(db_path=db)

    def add(sid, role, content, minutes_ago, metadata="{}"):
        ts = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
        with mem._connect() as conn:
            conn.execute("INSERT INTO messages (id, session_id, role, content, timestamp, metadata) "
                         "VALUES (?, ?, ?, ?, ?, ?)", (uuid.uuid4().hex, sid, role, content, ts, metadata))
            conn.commit()
    return add


@pytest.mark.asyncio
async def test_simulate_reports_moves_without_touching_live_state(vault, episodic):
    from llm_config import router
    from llm_config.tuning import simulate
    _setup(quick="gpt-5.4-nano")
    episodic("chat1", "user", "thanks, that helps a lot", 50)
    episodic("chat1", "assistant", "you're welcome", 49)
    episodic("chat1", "user", "I was thinking about how the market for these accelerator vendors has "
             "shifted over the past year and wondered what you think is driving the consolidation", 40)
    episodic("chat1", "user", "/model quick", 39)
    episodic("job1", "user", "hi", 30, '{"job_id": "j1", "kind": "autonomous_task_prompt"}')
    before = dict(router._sessions)
    res = await simulate({"quick_max_chars": 100}, days=1)
    assert res["messages"] == 2                       # /model and the job prompt are skipped
    assert res["current"] == {"quick": 2}
    assert res["candidate"] == {"quick": 1, "agent": 1}
    assert res["moves"] == {"quick→agent": 1} and res["examples"][0]["text"].startswith("I was thinking")
    assert router._sessions == before
    with pytest.raises(ValueError):
        await simulate({"bogus": 1}, days=1)
