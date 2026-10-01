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
    model, task_class = "m", "agent"

    def __init__(self):
        self.kws = []

    async def chat(self, messages, tools=None, stream=True, **kw):
        self.kws.append(kw)
        if len(self.kws) == 1:
            yield {"type": "tool_call", "id": "1", "name": "web_search", "args": {"query": "q"}}
        else:
            yield {"type": "text_delta", "content": "Leo XIV"}
        yield {"type": "done"}


async def _run(msg, force=True):
    from agent.core import AgentCore
    from config import get_settings
    prov = _Prov()
    agent = AgentCore(provider=prov, memory_manager=None, project_id="p", session_id="s")
    tools = [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]
    with patch.object(get_settings(), "agent_force_search", force), patch.object(get_settings(), "agent_thinking", False), \
         patch("agent.core.get_all_tool_schemas", return_value=tools), patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.execute_tool", return_value="results"):
        [e async for e in agent.chat(msg)]
    return prov.kws


@pytest.mark.asyncio
async def test_first_round_is_forced_to_use_a_tool_and_later_rounds_are_not():
    kws = await _run("Who is the current Pope?")
    assert kws[0] == {"extra_body": {"tool_choice": "required"}}
    assert kws[1] == {}


@pytest.mark.asyncio
async def test_other_questions_and_the_setting_leave_tool_choice_alone():
    assert (await _run("Write a haiku about autumn"))[0] == {}
    assert (await _run("Who is the current Pope?", force=False))[0] == {}
