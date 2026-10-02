"""Recalled memory is labelled by provenance, never overrides tools for current
facts, and rides in the user message so the system prompt stays cacheable."""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from agent.prompts import MEMORY_GUIDANCE, render_turn_context  # noqa: E402


def test_nothing_recalled_still_gives_the_time():
    assert render_turn_context(None, now="2026-09-30 12:00 UTC") == \
        "<context>\nCurrent time: 2026-09-30 12:00 UTC\n</context>\n\n"
    assert "Recalled memory" not in render_turn_context([{"tier": "semantic", "content": "  "}])


def test_items_are_labelled_by_provenance():
    out = render_turn_context([
        {"tier": "semantic", "content": "User's dog is called Rufus"},
        {"tier": "episodic", "content": "[user] our Q3 budget is 42,000 euros"},
        {"tier": "episodic", "content": "[assistant] The latest Python is 3.12.7"},
        {"tier": "graph", "content": "[graph:vendor] Acme"},
    ])
    assert "[note] User's dog is called Rufus" in out
    assert "[user said] our Q3 budget is 42,000 euros" in out
    assert "[your earlier reply] The latest Python is 3.12.7" in out
    assert "[graph] [graph:vendor] Acme" in out


def test_guidance_keeps_corpus_authority_but_not_over_tools():
    assert "authoritative" in MEMORY_GUIDANCE                       # corpus-grounded research keeps working
    assert "use your tools even when a memory" in MEMORY_GUIDANCE   # current facts still go to tools
    assert "not the user" in MEMORY_GUIDANCE                        # the block is not the user's words
    assert "primary source" not in MEMORY_GUIDANCE.lower()          # the old blanket instruction is gone


class _Prov:
    model, task_class = "m", "agent"

    def __init__(self):
        self.seen = []

    async def chat(self, messages, tools=None, stream=True, **kw):
        self.seen.append(messages)
        yield {"type": "text_delta", "content": "ok"}
        yield {"type": "done"}


@pytest.mark.asyncio
async def test_per_turn_parts_stay_out_of_the_system_prompt():
    """Two turns with different memories and times: the system prompt and the
    earlier history are byte-identical (a stable prefix for the KV cache)."""
    from agent.core import AgentCore
    mgr = SimpleNamespace(recall=AsyncMock(side_effect=[
        [{"tier": "semantic", "content": "dog is Rufus", "score": 0.9}],
        [{"tier": "episodic", "content": "[user] budget 42k", "score": 0.8}],
    ]))
    prov = _Prov()
    agent = AgentCore(provider=prov, memory_manager=mgr, project_id="p", session_id="s")
    with patch("agent.core.get_all_tool_schemas", return_value=[]):
        with patch("agent.prompts.datetime") as dt:
            dt.now.return_value.strftime.return_value = "2026-09-30 12:00 UTC"
            [e async for e in agent.chat("dog?")]
            dt.now.return_value.strftime.return_value = "2026-09-30 12:07 UTC"
            [e async for e in agent.chat("budget?")]
    t1, t2 = prov.seen
    assert t1[0] == t2[0]                                    # same system prompt
    assert "Rufus" not in t1[0]["content"] and "Current time" not in t1[0]["content"]
    assert t1[-1]["content"].startswith("<context>\nCurrent time: 2026-09-30 12:00 UTC")
    assert "[note] dog is Rufus" in t1[-1]["content"] and t1[-1]["content"].endswith("dog?")
    assert t2[1] == {"role": "user", "content": "dog?"}      # history keeps the plain message
    assert "[user said] budget 42k" in t2[-1]["content"] and "12:07" in t2[-1]["content"]
