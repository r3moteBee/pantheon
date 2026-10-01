"""Time-sensitive questions: the first round must call a tool."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from agent.freshness import needs_fresh_facts  # noqa: E402


@pytest.mark.parametrize("msg", [
    "Who is the current Pope?", "Who is the prime minister of Japan?", "Who's the CEO of Intel?",
    "What's the euro to dollar exchange rate today?", "Who won the most recent Formula 1 Grand Prix?",
    "Any news about the Artemis launch?", "What's the latest version of Python?", "Is Biden still president?",
])
def test_time_sensitive(msg):
    assert needs_fresh_facts(msg)


@pytest.mark.parametrize("msg", [
    "hi", "What's the capital of France?", "Write a haiku about autumn", "Is 7919 a prime number?",
    "Who wrote Pride and Prejudice?", "Explain how photosynthesis works",
])
def test_not_time_sensitive(msg):
    assert not needs_fresh_facts(msg)


class _Prov:
    """round 1 answers from memory (no tool), or calls `first_tool`; round 2 answers."""
    model, task_class = "m", "agent"

    def __init__(self, first_tool=None):
        self.first_tool, self.seen = first_tool, []

    async def chat(self, messages, tools=None, stream=True, **kw):
        self.seen.append(messages)
        if len(self.seen) == 1:
            if self.first_tool:
                yield {"type": "tool_call", "id": "m1", "name": self.first_tool, "args": {"query": "Pope Francis"}}
            else:
                yield {"type": "text_delta", "content": "The current Pope is Pope Francis."}
        else:
            yield {"type": "text_delta", "content": "The current Pope is Leo XIV."}
        yield {"type": "done"}


async def _run(msg, first_tool=None, force=True):
    from agent.core import AgentCore
    from config import get_settings
    prov = _Prov(first_tool)
    agent = AgentCore(provider=prov, memory_manager=None, project_id="p", session_id="s")
    tools = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ("web_search", "recall")]
    calls = []

    async def fake_exec(**kw):
        calls.append((kw["tool_name"], kw["tool_args"]))
        return "results: Leo XIV elected 2025"
    with patch.object(get_settings(), "agent_force_search", force), patch.object(get_settings(), "agent_thinking", False), \
         patch("agent.core.get_all_tool_schemas", return_value=tools), patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.execute_tool", fake_exec):
        events = [e async for e in agent.chat(msg)]
    text = "".join(e["content"] for e in events if e["type"] == "text_delta")
    return text, calls, prov


@pytest.mark.asyncio
async def test_unsearched_answer_is_dropped_and_the_question_is_searched():
    text, calls, prov = await _run("Who is the current Pope?")
    assert "Francis" not in text and text == "The current Pope is Leo XIV."      # stale round never shown
    assert calls == [("web_search", {"query": "Who is the current Pope?"})]      # the user's words, not a guess
    assert prov.seen[1][-1]["role"] == "tool"


@pytest.mark.asyncio
async def test_a_memory_lookup_alone_is_not_enough():
    text, calls, _ = await _run("Who is the current Pope?", first_tool="recall")
    assert [c[0] for c in calls] == ["recall", "web_search"]


@pytest.mark.asyncio
async def test_a_round_that_searched_is_left_alone():
    _, calls, _ = await _run("Who is the current Pope?", first_tool="web_search")
    assert calls == [("web_search", {"query": "Pope Francis"})]


@pytest.mark.asyncio
async def test_other_questions_and_the_setting_are_untouched():
    text, calls, _ = await _run("Write a haiku about autumn")
    assert calls == [] and "Francis" in text
    text, calls, _ = await _run("Who is the current Pope?", force=False)
    assert calls == [] and "Francis" in text
