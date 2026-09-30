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
