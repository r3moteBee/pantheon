"""Opt-in thinking for the agent class, and the finalize round that recovers a
reply a thinking model left inside its reasoning (settings.agent_thinking)."""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

import httpx
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))


class _Prov:
    """Streams scripted rounds; records every call's kwargs."""
    model = "m"

    def __init__(self, rounds, task_class="agent", final="Rufus."):
        self.task_class = task_class
        self.rounds = rounds
        self.final = final
        self.stream_calls, self.complete_calls = [], []

    async def chat(self, messages, tools=None, stream=True, **kw):
        self.stream_calls.append(kw)
        text, reasoning = self.rounds.pop(0)
        if text:
            yield {"type": "text_delta", "content": text}
        yield {"type": "done", "reasoning": reasoning}

    async def chat_complete(self, messages, tools=None, **kw):
        self.complete_calls.append(dict(messages=messages, tools=tools, **kw))
        return {"content": self.final, "tool_calls": []}


async def _run(prov, thinking):
    from agent.core import AgentCore
    from config import get_settings
    agent = AgentCore(provider=prov, memory_manager=None, project_id="p", session_id="s")
    events = []
    with patch.object(get_settings(), "agent_thinking", thinking), \
         patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.get_all_tool_schemas", return_value=[]):
        async for ev in agent.chat("what is my dog called?", stream=True):
            events.append(ev)
    return "".join(e["content"] for e in events if e["type"] == "text_delta")


@pytest.mark.asyncio
async def test_off_by_default_sends_nothing_extra():
    prov = _Prov([("Rufus.", "")])
    assert await _run(prov, thinking=False) == "Rufus."
    assert prov.stream_calls == [{}]


@pytest.mark.asyncio
async def test_on_asks_the_agent_class_to_think():
    prov = _Prov([("Rufus.", "the user said Rufus")])
    await _run(prov, thinking=True)
    assert prov.stream_calls == [{"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}}]
    assert prov.complete_calls == []            # a normal reply needs no finalize round


@pytest.mark.asyncio
async def test_other_task_classes_are_left_alone():
    prov = _Prov([("ok", "")], task_class="code")
    await _run(prov, thinking=True)
    assert prov.stream_calls == [{}]


@pytest.mark.asyncio
async def test_answer_left_in_reasoning_is_finalized():
    prov = _Prov([("", "The memory says the dog is Rufus. Answer: Rufus.")], final="Your dog is Rufus.")
    assert await _run(prov, thinking=True) == "Your dog is Rufus."
    (call,) = prov.complete_calls
    assert call["tools"] is None                                      # this turn had none
    assert call["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}}
    assert "Answer: Rufus." in call["messages"][-2]["content"]       # its own notes handed back
    assert call["messages"][-1]["role"] == "user"


@pytest.mark.asyncio
async def test_empty_reply_without_reasoning_is_not_finalized():
    prov = _Prov([("", "")])
    assert await _run(prov, thinking=True) == ""
    assert prov.complete_calls == []


@pytest.mark.asyncio
async def test_provider_sends_extra_body_and_returns_reasoning():
    from models.provider import ModelProvider
    seen = []

    def handler(req):
        body = json.loads(req.content)
        seen.append(body)
        if body.get("stream"):
            sse = "".join("data: " + json.dumps(c) + "\n\n" for c in (
                {"choices": [{"delta": {"reasoning_content": "think "}}]},
                {"choices": [{"delta": {"reasoning_content": "more"}}]},
                {"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]},
            )) + "data: [DONE]\n\n"
            return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})
        return httpx.Response(200, json={"choices": [{"message": {
            "content": "", "reasoning_content": "all in here"}, "finish_reason": "stop"}]})

    p = ModelProvider(base_url="https://m.test/v1", api_key="k", model="m")
    extra = {"chat_template_kwargs": {"enable_thinking": True}}
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("utils.http.shared_client", return_value=client):
        events = [e async for e in p.chat([{"role": "user", "content": "x"}], stream=True, extra_body=extra)]
        r = await p.chat_complete([{"role": "user", "content": "x"}], extra_body=extra)
        await p.chat_complete([{"role": "user", "content": "x"}])
    await client.aclose()
    assert events[-1] == {"type": "done", "content": "hi", "reasoning": "think more"}
    assert r["reasoning"] == "all in here"
    assert seen[0]["chat_template_kwargs"] == {"enable_thinking": True}
    assert seen[1]["chat_template_kwargs"] == {"enable_thinking": True}
    assert "chat_template_kwargs" not in seen[2]


@pytest.mark.asyncio
async def test_finalize_keeps_the_tools_but_forbids_calls():
    """Tools render at the top of the prompt: resending them keeps the KV cache."""
    from agent.core import AgentCore
    from config import get_settings
    tools = [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]
    prov = _Prov([("", "search says 3.14. Answer: 3.14")], final="3.14")
    agent = AgentCore(provider=prov, memory_manager=None, project_id="p", session_id="s")
    with patch.object(get_settings(), "agent_thinking", True), \
         patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.get_all_tool_schemas", return_value=tools):
        [e async for e in agent.chat("latest python?", stream=True)]
    (call,) = prov.complete_calls
    assert call["tools"] == tools
    assert call["extra_body"] == {"chat_template_kwargs": {"enable_thinking": False}, "tool_choice": "none"}


def test_extras_override_options_but_not_the_request():
    from models.provider import _apply_request_extras
    p = {"model": "m", "messages": [1], "stream": True, "tools": [2], "tool_choice": "auto"}
    _apply_request_extras(p, {"tool_choice": "none", "model": "x", "messages": [], "tools": None, "stream": False})
    assert p == {"model": "m", "messages": [1], "stream": True, "tools": [2], "tool_choice": "none"}
