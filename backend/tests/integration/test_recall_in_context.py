"""Episodic recall must not spend its slots on messages already in the prompt.

Chat saves the user's message to episodic memory (and indexes it) before the
agent runs, so the best episodic match for every question was the question
itself, and the rest of the current session crowded out older sessions — the
ones recall exists for."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def _manager(ep_hits):
    import models.provider
    from memory.manager import ContextBudget, MemoryManager
    m = MemoryManager.__new__(MemoryManager)
    m.project_id, m.session_id = "p", "now"
    m.context_budget = ContextBudget()
    m.episodic = SimpleNamespace(search_messages=AsyncMock(return_value=ep_hits))
    m._graph_augment = AsyncMock(side_effect=lambda results, **kw: results)
    return m


def _hit(i, content, session, role="user"):
    return {"id": str(i), "content": content, "session_id": session, "role": role,
            "score": 1.0 - i / 100, "timestamp": ""}


@pytest.fixture(autouse=True)
def _no_reranker(monkeypatch):
    import models.provider
    monkeypatch.setattr(models.provider, "get_provider_for", lambda cls: None)


async def test_messages_in_the_prompt_do_not_use_up_episodic_slots():
    question = "Where is our team's Q3 offsite?"
    hits = [_hit(0, question, "now"),                                   # the question itself
            _hit(1, "Where is the offsite again?", "now"),              # earlier in this session
            _hit(2, "I'm planning our Q3 offsite in Lisbon next month.", "old")]
    m = _manager(hits)
    out = await m.recall(question, tiers=["episodic"], limit_per_tier=2,
                         in_context={question, "Where is the offsite again?"})
    contents = [r["content"] for r in out]
    assert contents == ["[user] I'm planning our Q3 offsite in Lisbon next month."]


async def test_without_in_context_nothing_is_dropped():
    hits = [_hit(0, "a", "now"), _hit(1, "b", "old")]
    out = await _manager(hits).recall("q", tiers=["episodic"], limit_per_tier=5)
    assert len(out) == 2


def test_agent_reports_what_it_already_sends():
    from agent.core import AgentCore
    a = AgentCore(provider=SimpleNamespace(model="m"), memory_manager=None, project_id="p", session_id="s")
    a.working_memory = [{"role": "user", "content": " earlier "},
                        {"role": "assistant", "content": "reply"},
                        {"role": "user", "content": [{"type": "text", "text": "img"}]}]
    assert a._in_context_texts(" new question ") == {"new question", "earlier", "reply"}


# ── Relevance floor ──────────────────────────────────────────────────────────

def test_logits_become_probabilities_and_probabilities_are_kept():
    from memory.manager import _as_probabilities
    assert _as_probabilities([0.9, 0.1]) == [0.9, 0.1]
    p = _as_probabilities([3.01, -1.78, -6.27])          # bge-reranker-v2-m3 via llama.cpp
    assert [round(x, 3) for x in p] == [0.953, 0.144, 0.002]


def _semantic_manager(monkeypatch, rerank_scores):
    import models.provider
    m = _manager([])
    docs = [{"id": str(i), "content": f"doc{i}", "score": 0.5, "tier": "semantic"} for i in range(len(rerank_scores))]
    m.semantic = SimpleNamespace(search=AsyncMock(return_value=docs))
    reranker = SimpleNamespace(base_url="http://r.invalid/v1", model="rerank-1", api_key="")
    monkeypatch.setattr(models.provider, "get_provider_for", lambda cls: reranker if cls == "rerank" else None)
    return m


async def test_pre_recall_drops_what_the_reranker_calls_unrelated(monkeypatch):
    m = _semantic_manager(monkeypatch, [3.0, -1.8, -6.3])
    async def rerank(query, results, reranker):
        from memory.manager import _as_probabilities
        out = []
        for r, p in zip(results, _as_probabilities([3.0, -1.8, -6.3])):
            out.append({**r, "score": p, "reranked": True})
        return out
    m._rerank = rerank
    out = await m.recall("dog?", tiers=["semantic"], limit_per_tier=5, context_focus="broad", min_relevance=0.05)
    assert [r["id"] for r in out] == ["0", "1"]              # the weak-but-relevant note survives
    out = await m.recall("dog?", tiers=["semantic"], limit_per_tier=5, context_focus="broad")
    assert len(out) == 3                                      # recall tool: no floor


async def test_no_floor_when_the_rerank_failed(monkeypatch):
    m = _semantic_manager(monkeypatch, [])
    m.semantic = SimpleNamespace(search=AsyncMock(return_value=[
        {"id": "a", "content": "a", "score": 0.01, "tier": "semantic"}]))
    m._rerank = AsyncMock(side_effect=lambda q, results, r: results)   # timed out: original order, no flag
    out = await m.recall("q", tiers=["semantic"], limit_per_tier=5, min_relevance=0.05)
    assert [r["id"] for r in out] == ["a"]


# ── Current-session fallback (turns dropped by the history budget) ──────────

async def test_dropped_turns_of_this_chat_come_back_by_similarity(monkeypatch):
    import models.provider
    monkeypatch.setattr(models.provider, "get_provider_for", lambda cls: None)
    m = _manager([])
    session_hits = [
        {"id": "a", "content": "I'm giving a keynote speech in Denver on Friday", "role": "user", "similarity": 0.61},
        {"id": "b", "content": "Good luck with your keynote in Denver on Friday", "role": "assistant", "similarity": 0.49},
        {"id": "c", "content": "still in the prompt", "role": "user", "similarity": 0.90},
        {"id": "d", "content": "an essay on tides", "role": "assistant", "similarity": 0.25},
    ]
    calls = []

    async def search(query, project_id="default", limit=20, session_id=None):
        calls.append(session_id)
        return session_hits if session_id else []
    m.episodic = SimpleNamespace(search_messages=search)
    out = await m.recall("did I mention a speech?", tiers=["episodic"], limit_per_tier=5, context_focus="broad",
                         in_context={"still in the prompt"}, session_fallback="now", session_min_similarity=0.45)
    assert [r["id"] for r in out] == ["a", "b"]
    assert all(r["metadata"]["earlier_in_session"] for r in out)
    assert "now" in calls


async def test_no_session_fallback_unless_asked(monkeypatch):
    import models.provider
    monkeypatch.setattr(models.provider, "get_provider_for", lambda cls: None)
    m = _manager([])
    m.episodic = SimpleNamespace(search_messages=AsyncMock(return_value=[]))
    await m.recall("q", tiers=["episodic"], limit_per_tier=5)
    assert all(c.kwargs.get("session_id") is None for c in m.episodic.search_messages.await_args_list)


def test_earlier_turns_are_labelled_as_this_conversation():
    from agent.prompts import render_turn_context
    out = render_turn_context([{"tier": "episodic", "content": "[user] keynote in Denver",
                                "metadata": {"earlier_in_session": True}}])
    assert "[earlier in this chat, user said] keynote in Denver" in out
