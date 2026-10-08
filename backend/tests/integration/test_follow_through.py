"""Replies that promise a step and end the turn without taking it (agent/follow_through.py)."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from agent.follow_through import MAX_NUDGES, announced_action  # noqa: E402

PROMISES = [
    "Here is the summary of the three pages. I'll now save it as an artifact.",
    "I found the release notes. Next, I'll fetch the changelog for 2.4.",
    "Let me search for the latest benchmark results.",
    "Got it. I will create a daily task for the PR digest.",
    "Okay, now I'm going to check the GitHub issues.",
    "**Let me look that up.**",
    # 2026-10-07: a promise behind a lead-in clause, followed by a numbered plan, then the turn ended
    "I can see the image is still being extracted. Once the extraction is complete, I'll edit it to remove "
    "the car and fill in the background.\n\nI'll use the image editing capability to:\n1. Remove the car\n"
    "2. Fill in the space behind it\n3. Keep the lighting consistent",
    "I'll report back here as soon as I have a list of promising listings!",
]
NOT_PROMISES = [
    "Would you like me to save this as an artifact?",
    "I can fetch the changelog too if you want.",
    "Once you send me the file, I'll convert it.",
    "Let me know if you need anything else.",
    "I'll be happy to help with more.",
    "The release came out on October 2. It fixes the memory leak.",
    "I'll wait for your approval in the Tasks tab.",
    "I'll save it as an artifact if you'd like.",
    "",
    None,
]


@pytest.mark.parametrize("text", PROMISES)
def test_promises_are_spotted(text):
    assert announced_action(text)


@pytest.mark.parametrize("text", NOT_PROMISES)
def test_offers_questions_and_answers_are_not(text):
    assert announced_action(text) is None


def test_only_the_end_of_the_reply_counts():
    text = ("I'll start by reading the page. " + "It covers the install steps in detail. " * 40
            + "That is everything the page says.")
    assert announced_action(text) is None


def test_a_reply_ending_in_a_question_hands_the_turn_back():
    assert announced_action("I'll set up a daily digest at 8 AM.\n\n1. Search releases\n2. Summarise\n\n"
                            "Would you like me to proceed?") is None


# ---- the agent loop ------------------------------------------------------------------------

TOOLS = [{"type": "function", "function": {"name": "web_fetch", "parameters": {}}}]


class _Prov:
    """Scripted rounds: each entry is ("text", str) or ("call", tool name). Records the
    messages of every round so the test can see what the loop sent back."""
    model, task_class = "m", "agent"

    def __init__(self, script):
        self.script, self.calls, self.seen = list(script), 0, []

    def _next(self, messages):
        self.calls += 1
        self.seen.append([dict(m) for m in messages])
        return self.script.pop(0) if self.script else ("text", "done.")

    async def chat(self, messages, tools=None, stream=True, **kw):
        kind, val = self._next(messages)
        if kind == "call":
            yield {"type": "tool_call", "id": f"t{self.calls}", "name": val, "args": {"url": "u"}}
        else:
            yield {"type": "text_delta", "content": val}
        yield {"type": "done"}

    async def chat_complete(self, messages, tools=None, **kw):
        kind, val = self._next(messages)
        if kind == "call":
            return {"content": "", "tool_calls": [{"id": f"t{self.calls}", "name": val, "args": {"url": "u"}}]}
        return {"content": val, "tool_calls": []}


async def _run(prov, stream=True):
    from agent.core import AgentCore
    from config import get_settings
    agent = AgentCore(provider=prov, memory_manager=None, project_id="p", session_id="s")
    calls = []

    async def fake_tool(tool_name, tool_args, **kw):
        calls.append(tool_name)
        return "page text"
    with patch.object(get_settings(), "agent_force_search", False), \
         patch.object(get_settings(), "agent_thinking", False), \
         patch("agent.core.get_all_tool_schemas", return_value=TOOLS), \
         patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.execute_tool", side_effect=fake_tool), \
         patch("agent.context_fit.context_window", return_value=None):
        events = [e async for e in agent.chat("summarise the release notes", stream=stream)]
    return events, calls


def _text(events):
    return "".join(e.get("content", "") for e in events if e["type"] == "text_delta")


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
async def test_announced_step_is_taken(stream):
    prov = _Prov([("text", "Sure. I'll fetch the release notes now."), ("call", "web_fetch"),
                  ("text", "The release fixes the memory leak.")])
    events, calls = await _run(prov, stream)
    assert calls == ["web_fetch"]                                   # the promised step happened
    assert "fixes the memory leak" in _text(events)
    nudge = prov.seen[1][-1]
    assert nudge["role"] == "user" and "I'll fetch the release notes now." in nudge["content"]
    assert "create_task" in nudge["content"]                        # later work goes to the task queue


@pytest.mark.asyncio
async def test_plain_answer_ends_the_turn():
    prov = _Prov([("text", "The release fixes the memory leak. Would you like the full changelog?")])
    events, calls = await _run(prov)
    assert prov.calls == 1 and calls == []


@pytest.mark.asyncio
async def test_nudges_are_capped():
    prov = _Prov([("text", "I'll fetch it now.")] * 6)
    events, calls = await _run(prov)
    assert prov.calls == MAX_NUDGES + 1                             # then the turn ends as before
    assert not [e for e in events if e["type"] == "error"]
