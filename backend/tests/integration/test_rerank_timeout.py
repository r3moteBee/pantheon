"""A slow reranker must degrade to "original order", never cost the turn its memories.

Regression (2026-09-30): agent.core caps the whole pre-recall (search + rerank) at
4 s, but _rerank allowed 15 s. A reranker that was still loading (15-20 s cold after
a gateway restart) ran the pre-recall out, the recall was cancelled, and the turn was
answered with NO memory context ("Pre-recall timed out, proceeding without context").
"""
from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

RERANKER = SimpleNamespace(base_url="http://reranker.invalid/v1", model="rerank-1", api_key="k")


def _results():
    return [
        {"id": "a", "content": "alpha", "score": 0.9, "tier": "semantic"},
        {"id": "b", "content": "beta", "score": 0.5, "tier": "semantic"},
    ]


@pytest.fixture
def settings_env(monkeypatch):
    """Set env-backed settings for one test (config.get_settings is lru_cached)."""
    from config import get_settings

    def apply(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
        get_settings.cache_clear()

    yield apply
    get_settings.cache_clear()


class _Client:
    """Stands in for utils.http.pooled_client(): post() waits `delay` s, then answers."""

    def __init__(self, delay: float, payload: dict | None = None):
        self.delay, self.payload = delay, payload or {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, headers=None, json=None):
        await asyncio.sleep(self.delay)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: self.payload)


def _reranker_answers_after(monkeypatch, delay: float, payload: dict | None = None):
    import utils.http
    monkeypatch.setattr(utils.http, "pooled_client", lambda timeout=None: _Client(delay, payload))


def _manager():
    from memory.manager import ContextBudget, MemoryManager
    m = MemoryManager.__new__(MemoryManager)
    m.project_id, m.session_id = "p", "s1"
    m.context_budget = ContextBudget()   # what __init__ sets by default
    return m


def test_rerank_budget_is_capped_below_the_pre_recall_budget(settings_env):
    from memory.manager import rerank_timeout
    settings_env(PRE_RECALL_TIMEOUT_SECONDS=4.0, RERANK_TIMEOUT_SECONDS=15)
    assert rerank_timeout() == pytest.approx(2.5)   # the old 15 s can no longer outlast pre-recall
    settings_env(PRE_RECALL_TIMEOUT_SECONDS=4.0, RERANK_TIMEOUT_SECONDS=1)
    assert rerank_timeout() == pytest.approx(1.0)
    settings_env(PRE_RECALL_TIMEOUT_SECONDS=10, RERANK_TIMEOUT_SECONDS=6)
    assert rerank_timeout() == pytest.approx(6.0)


async def test_slow_reranker_keeps_original_order_within_budget(monkeypatch, settings_env, caplog):
    settings_env(RERANK_TIMEOUT_SECONDS=0.3)
    _reranker_answers_after(monkeypatch, delay=30)
    t0 = time.monotonic()
    with caplog.at_level(logging.WARNING, logger="memory.manager"):
        out = await _manager()._rerank("q", _results(), RERANKER)
    assert time.monotonic() - t0 < 2
    assert [r["id"] for r in out] == ["a", "b"]
    assert not any(r.get("reranked") for r in out)
    assert "Rerank timed out" in caplog.text


async def test_fast_reranker_still_reorders(monkeypatch, settings_env):
    settings_env(RERANK_TIMEOUT_SECONDS=2.5)
    _reranker_answers_after(monkeypatch, delay=0, payload={"results": [
        {"index": 1, "relevance_score": 0.99}, {"index": 0, "relevance_score": 0.10},
    ]})
    out = await _manager()._rerank("q", _results(), RERANKER)
    assert [r["id"] for r in out] == ["b", "a"]
    assert all(r["reranked"] for r in out)


async def test_recall_keeps_memories_when_the_reranker_hangs(monkeypatch, settings_env):
    """The production failure, end to end: same wait_for budget agent.core applies."""
    import models.provider
    budget = 2.0                                           # literal, so the test also runs (and fails) on old code
    settings_env(PRE_RECALL_TIMEOUT_SECONDS=budget)        # rerank cap becomes 0.5 s
    _reranker_answers_after(monkeypatch, delay=30)        # a reranker model still loading
    monkeypatch.setattr(models.provider, "get_provider_for",
                        lambda cls: RERANKER if cls == "rerank" else None)
    m = _manager()
    m.semantic = SimpleNamespace(search=AsyncMock(return_value=_results()))
    m._graph_augment = AsyncMock(side_effect=lambda results, **kw: results)

    results = await asyncio.wait_for(
        m.recall("q", tiers=["semantic"], project_id="p", limit_per_tier=5),
        timeout=budget,
    )
    assert {r["id"] for r in results} == {"a", "b"}      # memories survive, just unranked
