"""Keeping a turn's tool loop inside the model's context window (agent/context_fit.py)."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from agent import context_fit as cf  # noqa: E402

OVERFLOW = ("LLM API error 400: request (40012 tokens) exceeds the available context size "
            "(32768 tokens), try increasing it")


def _turn(n_results: int, size: int) -> list[dict]:
    msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "research this"}]
    for i in range(n_results):
        msgs.append({"role": "assistant", "content": "", "tool_calls": [
            {"id": f"c{i}", "type": "function", "function": {"name": "web_fetch", "arguments": "{}"}}]})
        msgs.append({"role": "tool", "tool_call_id": f"c{i}", "content": f"page {i} " + "x" * size})
    return msgs


def test_under_budget_changes_nothing():
    msgs = _turn(2, 1000)
    before = [dict(m) for m in msgs]
    st = cf.fit_messages(msgs, 32768)
    assert st["trimmed"] == 0 and msgs == before


def test_unknown_window_changes_nothing():
    msgs = _turn(5, 30000)
    assert cf.fit_messages(msgs, None)["trimmed"] == 0


def test_older_results_shrink_first_and_nothing_is_removed():
    msgs = _turn(6, 20000)                   # ~40K estimated tokens of tool results
    ids = [m.get("tool_call_id") for m in msgs]
    st = cf.fit_messages(msgs, 16384)
    assert st["after"] <= st["budget"]
    assert len(msgs) == len(ids) and [m.get("tool_call_id") for m in msgs] == ids   # pairs intact
    tools = [m for m in msgs if m["role"] == "tool"]
    assert len(tools[-1]["content"]) > len(tools[0]["content"])                       # newest kept bigger
    assert any(t["content"].startswith(cf.STUB_MARK) for t in tools[:-2])             # oldest stubbed


def test_newest_results_are_never_stubbed():
    msgs = _turn(3, 60000)
    cf.fit_messages(msgs, 4096, reserve=512)  # far too small: everything shrinks to the floor
    tools = [m for m in msgs if m["role"] == "tool"]
    assert not tools[-1]["content"].startswith(cf.STUB_MARK)
    assert not tools[-2]["content"].startswith(cf.STUB_MARK)
    assert tools[-1]["content"].startswith("page 2 ")                                 # head kept


def test_messages_are_replaced_not_edited():
    msgs = _turn(5, 30000)
    first_tool = msgs[3]
    original = first_tool["content"]
    cf.fit_messages(msgs, 16384)
    assert first_tool["content"] == original and msgs[3] is not first_tool


def test_overflow_message_is_parsed():
    assert cf.context_overflow(OVERFLOW) == (40012, 32768)
    assert cf.context_overflow("LLM API error 500: boom") is None
    assert cf.context_overflow(None) is None


def test_calibration_tightens_the_fit():
    a, b = _turn(6, 20000), _turn(6, 20000)
    loose = cf.fit_messages(a, 32768)
    tight = cf.fit_messages(b, 32768, calibration=2.0)
    assert tight["trimmed"] > loose["trimmed"]


# ---- the agent loop ------------------------------------------------------------------------

TOOLS = [{"type": "function", "function": {"name": "web_fetch", "parameters": {}}}]


class _Prov:
    """Asks for `fetches` tool calls, then answers. Records each round's estimated prompt.
    `overflow_rounds` makes those (1-based) calls fail the way llama.cpp does."""
    model, task_class = "m", "agent"

    def __init__(self, fetches=4, overflow_rounds=(), stream=True):
        self.fetches, self.overflow_rounds, self.calls, self.sizes = fetches, set(overflow_rounds), 0, []

    def _round(self, messages, tools):
        self.calls += 1
        self.sizes.append(cf.estimate(messages, tools))
        if self.calls in self.overflow_rounds:
            return "overflow"
        done = sum(1 for m in messages if m.get("role") == "tool")
        return "call" if done < self.fetches else "answer"

    async def chat(self, messages, tools=None, stream=True, **kw):
        kind = self._round(messages, tools)
        if kind == "overflow":
            yield {"type": "error", "message": OVERFLOW, "status": 400}
            return
        if kind == "call":
            yield {"type": "tool_call", "id": f"t{self.calls}", "name": "web_fetch", "args": {"url": "u"}}
        else:
            yield {"type": "text_delta", "content": "the answer"}
        yield {"type": "done"}

    async def chat_complete(self, messages, tools=None, **kw):
        kind = self._round(messages, tools)
        if kind == "overflow":
            from models.provider import LLMHTTPError
            raise LLMHTTPError(400, OVERFLOW)
        if kind == "call":
            return {"content": "", "tool_calls": [{"id": f"t{self.calls}", "name": "web_fetch", "args": {"url": "u"}}]}
        return {"content": "the answer", "tool_calls": []}


async def _run(prov, window, stream=True):
    from agent.core import AgentCore
    from config import get_settings
    agent = AgentCore(provider=prov, memory_manager=None, project_id="p", session_id="s")
    big_page = "p" * 30000                  # ~10K estimated tokens per fetch
    with patch.object(get_settings(), "agent_force_search", False), \
         patch.object(get_settings(), "agent_thinking", False), \
         patch("agent.core.get_all_tool_schemas", return_value=TOOLS), \
         patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.execute_tool", return_value=big_page), \
         patch("agent.context_fit.context_window", return_value=window):
        return [e async for e in agent.chat("research this", stream=stream)]


def _text(events):
    return "".join(e.get("content", "") for e in events if e["type"] == "text_delta")


@pytest.mark.asyncio
async def test_long_tool_loop_stays_inside_the_window():
    prov = _Prov(fetches=4)
    events = await _run(prov, window=16384)
    budget = 16384 - cf.REPLY_RESERVE - cf.SAFETY_MARGIN
    assert "the answer" in _text(events)
    assert max(prov.sizes) <= budget            # 4 x 10K tokens of pages, every round under budget
    assert not [e for e in events if e["type"] == "error"]


@pytest.mark.asyncio
async def test_server_overflow_is_retried_once_and_not_shown():
    prov = _Prov(fetches=2, overflow_rounds={2})
    events = await _run(prov, window=None)      # unknown window: only the server's count helps
    assert "the answer" in _text(events)
    assert not [e for e in events if e["type"] == "error"]
    assert prov.sizes[2] < prov.sizes[1]        # the retry was re-fitted smaller


@pytest.mark.asyncio
async def test_second_overflow_is_reported_not_looped():
    prov = _Prov(fetches=3, overflow_rounds={2, 3})
    events = await _run(prov, window=None)
    assert [e for e in events if e["type"] == "error"]
    assert prov.calls <= 4


@pytest.mark.asyncio
async def test_non_streaming_overflow_is_retried():
    prov = _Prov(fetches=2, overflow_rounds={2})
    events = await _run(prov, window=None, stream=False)
    assert "the answer" in _text(events)


# ── Long background jobs: dropping whole old rounds ──────────────────────────
# A research job (2026-10-08) made 125 calls in 46 rounds; with 123 results already stubbed the calls
# themselves still overflowed a 32K window, and the job died with "exceeds the available context size".

def _job(rounds: int, calls_per_round: int = 3) -> list[dict]:
    msgs = [{"role": "system", "content": "sys " * 3000}, {"role": "user", "content": "You are running a task"}]
    for r in range(rounds):
        calls = [{"id": f"c{r}-{k}", "type": "function", "function": {
            "name": "web_search" if k else "ingest_source", "arguments": '{"query": "' + "q" * 300 + '"}'}}
            for k in range(calls_per_round)]
        msgs.append({"role": "assistant", "content": "Next I will look up more sources. " * 10, "tool_calls": calls})
        msgs += [{"role": "tool", "tool_call_id": c["id"], "content": "result " * 40} for c in calls]
    return msgs


def _pairs_intact(msgs):
    open_ids = set()
    for m in msgs:
        if m["role"] == "assistant":
            assert not open_ids, "a tool call lost its result"
            open_ids = {c["id"] for c in m.get("tool_calls") or []}
        elif m["role"] == "tool":
            assert m["tool_call_id"] in open_ids, "a tool result lost its call"
            open_ids.discard(m["tool_call_id"])
    return True


def test_long_job_drops_old_rounds_when_stubs_are_not_enough():
    msgs = _job(46)
    st = cf.fit_messages(msgs, 32768)
    assert st["after"] <= st["budget"] and st["dropped"] > 0
    assert msgs[0]["role"] == "system" and msgs[1]["content"] == "You are running a task"
    notes = [m for m in msgs if m["role"] == "assistant" and m["content"].startswith(cf.DROPPED_MARK)]
    assert len(notes) == 1
    assert f"web_search x{st['dropped'] * 2}" in notes[0]["content"]
    assert f"ingest_source x{st['dropped']}" in notes[0]["content"]
    assert msgs[-1]["tool_call_id"] == "c45-2"                                        # newest round kept
    assert _pairs_intact(msgs)


def test_later_fits_keep_one_note_and_keep_counting():
    msgs = _job(46)
    first = cf.fit_messages(msgs, 32768)["dropped"]
    for r in range(46, 70):                                                              # the job goes on
        msgs += _job(r + 1)[-4:]
    second = cf.fit_messages(msgs, 32768)["dropped"]
    notes = [m for m in msgs if m["role"] == "assistant" and m["content"].startswith(cf.DROPPED_MARK)]
    assert len(notes) == 1 and f"ingest_source x{first + second}" in notes[0]["content"]
    assert _pairs_intact(msgs)


def test_the_newest_rounds_are_never_dropped():
    msgs = _job(cf.KEEP_ROUNDS)
    st = cf.fit_messages(msgs, 4096, reserve=512)
    assert not st.get("dropped") and sum(1 for m in msgs if m.get("tool_calls")) == cf.KEEP_ROUNDS
