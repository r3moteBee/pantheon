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


async def _run(msg, first_tool=None, force=True, pre=False):
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
         patch.object(get_settings(), "agent_pre_search", pre), \
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


# ── Names the model may not know ─────────────────────────────────────────────

from agent.freshness import unknown_entities  # noqa: E402


@pytest.mark.parametrize("msg,name", [
    ("What is Jev?", "Jev"),
    ("I want to use Jev to write summaries of my meeting notes.", "Jev"),
    ("Do I need a gaming PC to use the Steam Frame?", "Steam Frame"),
    ("What is the iPhone Duo?", "iPhone Duo"),
    ("Tell me about Fugu Ultra v2.0", "Fugu Ultra v2.0"),
])
def test_names_asked_about_or_to_be_used_are_looked_up(msg, name):
    assert unknown_entities(msg) == [name]


@pytest.mark.parametrize("msg", ["hi", "What is the capital of France?", "Email this to Sarah",
                                 "What is my dog's name?", "Write a haiku about autumn", "What is this?"])
def test_ordinary_messages_have_no_unknown_names(msg):
    assert unknown_entities(msg) == []


def test_pantheon_s_own_tools_and_skills_are_not_unknown():
    assert unknown_entities("How do I use Web-Research?", {"web-research"}) == []
    assert unknown_entities("Tell me about Daily Digest", {"daily_digest"}) == []


@pytest.mark.asyncio
async def test_an_unknown_name_is_searched_by_name_when_the_model_guesses():
    text, calls, _ = await _run("I want to use Jev to write summaries of my meeting notes.")
    assert calls == [("web_search", {"query": "What is Jev?"})]
    assert "Francis" not in text



@pytest.mark.asyncio
async def test_pre_search_runs_before_round_one_and_round_one_answers():
    text, calls, prov = await _run("Who is the current Pope?", pre=True)
    import time as _t
    assert calls == [("web_search", {"query": f"Who is the current Pope {_t.strftime('%B %Y')}"})]   # dated, before any round
    first_round = prov.seen[0]
    assert first_round[-1]["role"] == "tool" and first_round[-2]["tool_calls"][0]["function"]["name"] == "web_search"


@pytest.mark.asyncio
async def test_pre_search_uses_the_name_for_unknown_entities_and_skips_other_turns():
    _, calls, _ = await _run("I want to use Jev to write summaries of my meeting notes.", pre=True)
    assert calls[0] == ("web_search", {"query": "What is Jev?"})
    _, calls, _ = await _run("Write a haiku about autumn", pre=True)
    assert calls == []



@pytest.mark.asyncio
async def test_version_questions_pre_search_without_a_date():
    _, calls, _ = await _run("What's the latest stable version of PostgreSQL?", pre=True)
    assert calls[0] == ("web_search", {"query": "What's the latest stable version of PostgreSQL?"})


# ── Questions about Pantheon itself / the user's own things (2026-10-02 regression) ──

@pytest.mark.parametrize("msg", [
    "describe the current configuration", "describe the current configuration of this agent harness",
    "What is your current configuration?", "What tools do you have?", "What's in my current project?",
    "Summarize my notes from today's meeting", "Show me the results of my last task", "Rate my essay",
    "What model are you running on?", "How is Pantheon configured right now?",
])
def test_questions_about_pantheon_or_the_user_are_not_web_lookups(msg):
    from agent.freshness import needs_fresh_facts, unknown_entities
    assert not needs_fresh_facts(msg) and unknown_entities(msg) == []


@pytest.mark.parametrize("msg", [
    "Can you tell me who the current PM of Japan is?", "What's the current price of bitcoin?",
    "How did the election results turn out?", "What is the ECB's current deposit facility rate?",
])
def test_world_facts_with_current_still_are(msg):
    from agent.freshness import needs_fresh_facts
    assert needs_fresh_facts(msg)


@pytest.mark.asyncio
async def test_describe_the_current_configuration_does_not_search():
    text, calls, _ = await _run("describe the current configuration of this agent harness", pre=True)
    assert calls == []


@pytest.mark.parametrize("msg,want", [
    ("describe the current configuration of this agent harness", True), ("What tools do you have?", True),
    ("How is Pantheon configured right now?", True), ("What model are you running on?", True),
    ("Summarize my notes from today's meeting", False), ("Who is the current Pope?", False), ("Rate my essay", False),
])
def test_self_description_questions(msg, want):
    from agent.freshness import wants_self_description
    assert wants_self_description(msg) is want


@pytest.mark.asyncio
async def test_self_description_reads_pantheon_s_own_docs_first():
    from agent.core import AgentCore
    from config import get_settings
    prov = _Prov()
    agent = AgentCore(provider=prov, memory_manager=None, project_id="p", session_id="s")
    tools = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ("web_search", "get_self_documentation")]
    calls = []

    async def fake_exec(**kw):
        calls.append(kw["tool_name"]); return "Pantheon self-doc: model routes ..."
    with patch.object(get_settings(), "agent_thinking", False), patch("agent.core.get_all_tool_schemas", return_value=tools), \
         patch("agent.core.build_system_prompt", return_value="sys"), patch("agent.core.execute_tool", fake_exec):
        [e async for e in agent.chat("describe the current configuration of this agent harness")]
    assert calls == ["get_self_documentation"]
    assert prov.seen[0][-1]["role"] == "tool"            # round 1 already has Pantheon's own state
