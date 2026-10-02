"""Time-sensitive questions: the first round must call a tool."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import AsyncMock, patch

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
    ("describe the current configuration", True), ("What's the current configuration of nginx on Ubuntu?", False),
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



@pytest.mark.asyncio
async def test_self_description_drops_our_own_earlier_replies_from_recall():
    from agent.core import AgentCore
    from config import get_settings
    from types import SimpleNamespace
    prov = _Prov()
    mgr = SimpleNamespace(recall=AsyncMock(return_value=[
        {"tier": "episodic", "content": "[assistant] This harness follows Microsoft Agent Framework...", "score": 0.9},
        {"tier": "episodic", "content": "[user] I set the agent model to qwen", "score": 0.8}]))
    agent = AgentCore(provider=prov, memory_manager=mgr, project_id="p", session_id="s")
    with patch.object(get_settings(), "agent_thinking", False), patch("agent.core.get_all_tool_schemas", return_value=[]), \
         patch("agent.core.build_system_prompt", return_value="sys"):
        [e async for e in agent.chat("describe the current configuration of this agent harness")]
    sent = prov.seen[0][-1]["content"]
    assert "Microsoft Agent Framework" not in sent and "qwen" in sent


# ── Follow-ups in a conversation about current facts (2026-10-02 election regression) ──

def _hist(*pairs):
    return [{"role": r, "content": c} for r, c in pairs]


SENATE = "Which US Senate races are the most competitive in the 2026 midterms?"


@pytest.mark.parametrize("msg,history,want", [
    ("can you do a similar breakdown for the house?", _hist(("user", SENATE), ("assistant", "...")), True),
    ("and Docker Engine?", _hist(("user", "What's the latest stable version of Kubernetes?"), ("assistant", "v1.37")), True),
    # an answer built on web sources makes the conversation current even without time words
    ("list all 38 districts with the candidates",
     _hist(("user", "Which Texas House seats are competitive?"), ("assistant", "TX-28...\n\nSources:\n- https://x.org/a")), True),
    # stays current through an acknowledgement
    ("and solana?", _hist(("user", "What's the current price of Bitcoin?"), ("assistant", "$61k"), ("user", "ok"),
                         ("assistant", "!")), True),
    ("and of Spain?", _hist(("user", "What's the capital of France?"), ("assistant", "Paris")), False),
    ("ok thanks", _hist(("user", "Who is the current Prime Minister of the UK?"), ("assistant", "...")), False),
    ("cool, thank you!", _hist(("user", "Who is the current Prime Minister of the UK?"), ("assistant", "...")), False),
    ("summarize that in one line", _hist(("user", "What's the current price of Bitcoin?"), ("assistant", "...")), False),
    ("what tools do you have?", _hist(("user", "What's the current price of Bitcoin?"), ("assistant", "...")), False),
    ("and Docker Engine?", [], False),
])
def test_followup_needs_fresh(msg, history, want):
    from agent.freshness import followup_needs_fresh
    assert followup_needs_fresh(msg, history) is want


@pytest.mark.parametrize("msg,want", [
    (SENATE, True), ("any upcoming SpaceX launches?", True), ("who won the Ohio primary election?", True),
    ("What is the primary key of this table?", False), ("what's on my upcoming calendar?", False),
    # plural offices: "current governors" was answered from memory (Youngkin, not Spanberger)
    ("Who are the current governors of all 50 US states?", True), ("List all 100 current US senators", True),
    ("what is the current account balance", False),
])
def test_election_words_need_fresh_facts(msg, want):
    from agent.freshness import needs_fresh_facts
    assert needs_fresh_facts(msg) is want


@pytest.mark.asyncio
async def test_followup_is_searched_as_a_standalone_query():
    from agent.core import AgentCore
    from config import get_settings

    class P(_Prov):
        async def chat_complete(self, messages, tools=None, extra_body=None):
            self.rewrite_prompt = messages[0]["content"]
            return {"content": '"latest stable Docker Engine version"'}
    prov = P()
    agent = AgentCore(provider=prov, memory_manager=None, project_id="p", session_id="s")
    agent.working_memory = _hist(("user", "What's the latest stable version of Kubernetes?"), ("assistant", "v1.37.1"))
    tools = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ("web_search", "recall")]
    calls = []

    async def fake_exec(**kw):
        calls.append((kw["tool_name"], kw["tool_args"])); return "Docker Engine 29.1 released"
    with patch.object(get_settings(), "agent_force_search", True), patch.object(get_settings(), "agent_thinking", False), \
         patch.object(get_settings(), "agent_pre_search", True), \
         patch("agent.core.get_all_tool_schemas", return_value=tools), patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.execute_tool", fake_exec):
        [e async for e in agent.chat("and Docker Engine?")]
    assert "Kubernetes" in prov.rewrite_prompt and "and Docker Engine?" in prov.rewrite_prompt
    # a version query: no month/year (see test_version_questions_pre_search_without_a_date)
    assert calls[0] == ("web_search", {"query": "latest stable Docker Engine version"})


@pytest.mark.asyncio
async def test_followup_rewrite_failure_falls_back_to_previous_question():
    from agent.core import AgentCore

    class P(_Prov):
        async def chat_complete(self, messages, tools=None, extra_body=None):
            raise RuntimeError("down")
    agent = AgentCore(provider=P(), memory_manager=None, project_id="p", session_id="s")
    q = await agent._standalone_query("and AMD?", _hist(("user", "Who is the current CEO of Intel?"), ("assistant", "x")))
    assert q == "Who is the current CEO of Intel - and AMD?"


@pytest.mark.asyncio
async def test_reply_cut_off_at_max_tokens_says_so():
    from agent.core import AgentCore, CUT_OFF_NOTE
    from config import get_settings

    class P(_Prov):
        async def chat(self, messages, tools=None, stream=True, **kw):
            self.seen.append(messages)
            yield {"type": "text_delta", "content": "| OH-01 | Greg Landsman |"}
            yield {"type": "done", "finish_reason": "length"}
    agent = AgentCore(provider=P(), memory_manager=None, project_id="p", session_id="s")
    with patch.object(get_settings(), "agent_thinking", False), patch("agent.core.get_all_tool_schemas", return_value=[]), \
         patch("agent.core.build_system_prompt", return_value="sys"):
        events = [e async for e in agent.chat("give me every Ohio district with both candidates")]
    text = "".join(e["content"] for e in events if e["type"] == "text_delta")
    assert text.endswith(CUT_OFF_NOTE)
    assert [e for e in events if e["type"] == "done"][0]["full_response"].endswith(CUT_OFF_NOTE)


def test_presence_penalty_is_added_only_when_set():
    from agent.core import _with_sampling
    from config import get_settings
    kw = {"extra_body": {"chat_template_kwargs": {"enable_thinking": False}}}
    with patch.object(get_settings(), "agent_presence_penalty", 0.0):
        assert _with_sampling(kw) == kw and _with_sampling({}) == {}
    with patch.object(get_settings(), "agent_presence_penalty", 1.5):
        assert _with_sampling(kw)["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}, "presence_penalty": 1.5}
        assert _with_sampling({}) == {"extra_body": {"presence_penalty": 1.5}}
    assert "presence_penalty" not in kw["extra_body"]          # caller's dict untouched


@pytest.mark.asyncio
async def test_web_budget_stops_the_search_loop_in_chat():
    """48 district-by-district searches for one message (2026-10-02): past the budget the agent must answer."""
    from agent.core import AgentCore, WEB_BUDGET_NOTE
    from config import get_settings

    class P(_Prov):
        async def chat(self, messages, tools=None, stream=True, extra_body=None, **kw):
            self.seen.append((messages, extra_body))
            n = len(self.seen)
            if n <= 10:     # keeps searching, and once more after the notice
                yield {"type": "tool_call", "id": f"c{n}", "name": "web_search", "args": {"query": f"district {n}"}}
            else:
                yield {"type": "text_delta", "content": "never reached"}
            yield {"type": "done"}
    prov = P()
    agent = AgentCore(provider=prov, memory_manager=None, project_id="p", session_id="s", interactive=True)
    tools = [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]
    calls = []

    async def fake_exec(**kw):
        calls.append(kw["tool_args"]["query"]); return "results"

    async def fake_final(self, messages, reasoning, agent_extra, tools):
        yield "Here is what I could verify."
    with patch.object(get_settings(), "agent_web_budget", 3), patch.object(get_settings(), "agent_thinking", False), \
         patch.object(get_settings(), "agent_force_search", False), \
         patch("agent.core.get_all_tool_schemas", return_value=tools), patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.execute_tool", fake_exec), patch.object(AgentCore, "_finalize_stream", fake_final):
        events = [e async for e in agent.chat("give me every Ohio district with both candidates")]
    assert len(calls) == 3                                           # the 4th call was dropped, not run
    msgs, extra = prov.seen[3]
    assert msgs[-1]["content"] == WEB_BUDGET_NOTE.format(n=3) and extra["tool_choice"] == "none"
    assert [e for e in events if e["type"] == "done"][0]["full_response"].startswith("Here is what I could verify.")
