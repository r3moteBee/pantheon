"""Tool calls written as text (local models without a tool-call parser),
and image editing with a source image."""
from __future__ import annotations

import base64
import json
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from agent.text_tool_calls import might_be_tool_call, recover  # noqa: E402

TOOLS = {"generate_image", "recall", "web_search"}

# Verbatim shape from a Gemma reply: action_input is a string with
# unescaped quotes, so the whole thing is not valid JSON.
SCREENSHOT = ('{ "action": "generate_image", "action_input": "{ "prompt": "A surreal, close-up '
              'fantasy shot of a basketball that is actually the moon.", "n": 1, "path": '
              '"images/generated/2026-09-27/", "size": "1024x1024" }" }')


def _one(text):
    got = recover(text, TOOLS)
    assert got and len(got) == 1, text
    return got[0]["name"], got[0]["args"]


def test_formats_recovered():
    assert _one(SCREENSHOT) == ("generate_image", {
        "prompt": "A surreal, close-up fantasy shot of a basketball that is actually the moon.",
        "n": 1, "path": "images/generated/2026-09-27/", "size": "1024x1024"})
    assert _one('{"action": "recall", "action_input": {"query": "nvidia"}}') == ("recall", {"query": "nvidia"})
    assert _one('{"name": "recall", "arguments": "{\\"query\\": \\"x\\"}"}') == ("recall", {"query": "x"})
    assert _one('```json\n{"name": "web_search", "parameters": {"query": "q"}}\n```') == ("web_search", {"query": "q"})
    assert _one('<tool_call>\n{"name": "recall", "arguments": {"query": "a"}}\n</tool_call>') == ("recall", {"query": "a"})
    assert _one('```tool_code\nrecall(query="gpus", limit=3)\n```') == ("recall", {"query": "gpus", "limit": 3})
    assert _one('```tool_code\nprint(recall(query="gpus"))\n```') == ("recall", {"query": "gpus"})
    two = recover('[{"name": "recall", "arguments": {}}, {"name": "web_search", "arguments": {"query": "q"}}]', TOOLS)
    assert [c["name"] for c in two] == ["recall", "web_search"]


def test_not_recovered():
    for text in (
        '{"name": "delete_everything", "arguments": {}}',                 # unknown tool
        'Here is the call: {"name": "recall", "arguments": {}}',          # prose around it
        '{"name": "Alice", "age": 30}',                                   # data, not a call
        '```python\nprint("hello")\n```',                                  # code answer
        '<tool_call>{"name": "recall", "arguments": {}}</tool_call> and some prose',
        "",
    ):
        assert recover(text, TOOLS) is None, text


def test_might_be_tool_call():
    assert might_be_tool_call("  ") is None
    assert might_be_tool_call("`") is None and might_be_tool_call("<tool") is None
    assert might_be_tool_call(" {") is True and might_be_tool_call("```json") is True
    assert might_be_tool_call("Sure") is False and might_be_tool_call("<b>") is False


class _Prov:
    """Round 1 streams a textual tool call; round 2 answers normally."""
    model = "gemma4"

    def __init__(self, first_chunks):
        self.rounds = [first_chunks, ["Done ", "— here it is."]]
        self.seen = []

    async def chat(self, messages, tools=None, stream=True):
        self.seen.append(messages)
        for c in self.rounds.pop(0):
            yield {"type": "text_delta", "content": c}
        yield {"type": "done"}


async def _run(chunks):
    from agent.core import AgentCore
    prov = _Prov(chunks)
    agent = AgentCore(provider=prov, memory_manager=None, project_id="p", session_id="s")
    events = []
    with patch("agent.core.execute_tool", AsyncMock(return_value="[DISPLAY:artifact://a1]")) as ex, \
         patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.get_all_tool_schemas", return_value=[
             {"type": "function", "function": {"name": n, "parameters": {}}} for n in TOOLS]):
        async for ev in agent.chat("draw it", stream=True):
            events.append(ev)
    return events, ex, prov


@pytest.mark.asyncio
async def test_agent_runs_a_textual_tool_call_without_showing_it():
    events, ex, prov = await _run(['{ "action": "generate_image", ', '"action_input": {"prompt": "moon-ball"} }'])
    assert ex.await_args.kwargs["tool_name"] == "generate_image"
    assert ex.await_args.kwargs["tool_args"] == {"prompt": "moon-ball"}
    text = "".join(e["content"] for e in events if e["type"] == "text_delta")
    assert "action_input" not in text and text == "Done — here it is."
    # The model sees a proper tool call + result in round 2.
    round2 = prov.seen[1]
    assert round2[-2]["tool_calls"][0]["function"]["name"] == "generate_image"
    assert round2[-1]["role"] == "tool"


@pytest.mark.asyncio
async def test_plain_text_streams_immediately_and_json_answers_are_kept():
    events, ex, _ = await _run(["Hello", " there"])
    assert [e["content"] for e in events if e["type"] == "text_delta"][:2] == ["Hello", " there"]
    ex.assert_not_awaited()
    events, ex, _ = await _run(['{"answer": 42}'])
    assert '{"answer": 42}' in "".join(e["content"] for e in events if e["type"] == "text_delta")
    ex.assert_not_awaited()


# ── Image editing ────────────────────────────────────────────────────────────

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _prov(model):
    from models.provider import ModelProvider
    return ModelProvider(base_url=f"https://{model}.test/v1", api_key="k", model=model)


@pytest.mark.asyncio
async def test_chat_mode_edit_sends_source_image():
    from models import provider as pmod
    p = _prov("qwen_image_edit")
    pmod._IMAGE_API_STYLE[(p.base_url, p.model)] = "chat"
    seen = {}

    def handler(req):
        seen.update(json.loads(req.content))
        return httpx.Response(200, json={"choices": [{"message": {"images": [{"image_url": {
            "url": "data:image/png;base64," + base64.b64encode(_PNG).decode()}}]}}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("utils.http.shared_client", return_value=client):
        assert await p.generate_image("make the bunny dribble it", images=[_PNG]) == [_PNG]
    await client.aclose()
    content = seen["messages"][0]["content"]
    assert content[0] == {"type": "text", "text": "make the bunny dribble it"}
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.asyncio
async def test_images_api_edit_uses_edits_endpoint():
    from models import provider as pmod
    p = _prov("gpt-image-1")
    pmod._IMAGE_API_STYLE.pop((p.base_url, p.model), None)
    paths = []

    def handler(req):
        paths.append(req.url.path)
        assert b'name="image"' in req.content and b'name="prompt"' in req.content
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(_PNG).decode()}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("utils.http.shared_client", return_value=client):
        assert await p.generate_image("edit", images=[_PNG]) == [_PNG]
    await client.aclose()
    assert paths == ["/v1/images/edits"]


@pytest.mark.asyncio
async def test_tool_loads_source_image_artifact(tmp_path):
    from agent.tools import execute_tool
    from artifacts.store import get_store
    src = get_store().create(project_id="default", path="default/images/src.png", content=_PNG,
                             content_type="image/png", title="src", tags=[], source={}, edited_by="t")
    fake = SimpleNamespace(model="m", generate_image=AsyncMock(return_value=[_PNG]))
    with patch("models.provider.get_provider_for", return_value=fake):
        res = await execute_tool("generate_image", {"prompt": "fix it", "source_image": src["id"]}, None)
        assert "[DISPLAY:artifact://" in res and "source_image=" in res
        assert fake.generate_image.await_args.kwargs["images"] == [_PNG]
        bad = await execute_tool("generate_image", {"prompt": "x", "source_image": "nope"}, None)
        assert "not found" in bad


QWEN_XML = ("<tool_call>\n<function=save_to_artifact>\n<parameter=path>\nBriefings/heads.md\n</parameter>\n"
            "<parameter=content>\n# Heads of Government\n\n- Canada: Mark Carney\n</parameter>\n</function>\n</tool_call>")


def test_qwen_xml_calls_are_recovered():
    """A whole save_to_artifact call in Qwen3's XML format was shown to the user as the reply (2026-10-03)."""
    got = recover(QWEN_XML, TOOLS | {"save_to_artifact"})
    assert got and got[0]["name"] == "save_to_artifact"
    assert got[0]["args"] == {"path": "Briefings/heads.md", "content": "# Heads of Government\n\n- Canada: Mark Carney"}
    # stopped before the closing tag; typed parameter values
    got = recover("<tool_call>\n<function=recall>\n<parameter=query>\ngpus\n</parameter>\n<parameter=limit>\n3\n"
                  "</parameter>\n</function>", TOOLS)
    assert got[0]["args"] == {"query": "gpus", "limit": 3}
    # prose around it, or an unknown tool: not a call
    assert recover("Here you go: " + QWEN_XML, TOOLS | {"save_to_artifact"}) is None
    assert recover(QWEN_XML, TOOLS) is None


@pytest.mark.asyncio
async def test_xml_call_after_the_web_budget_becomes_an_answer():
    """Past the lookup budget the model wrote the call as text; it must not reach the user as markup."""
    from agent.core import AgentCore
    from config import get_settings

    class P:
        model, task_class = "m", "agent"

        def __init__(self):
            self.n = 0

        async def chat(self, messages, tools=None, stream=True, extra_body=None, **kw):
            self.n += 1
            if self.n == 1:
                yield {"type": "tool_call", "id": "c1", "name": "web_search", "args": {"query": "canada pm"}}
            else:
                yield {"type": "text_delta", "content": QWEN_XML}
            yield {"type": "done"}
    tools = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ("web_search", "save_to_artifact")]
    calls = []

    async def fake_exec(**kw):
        calls.append(kw["tool_name"]); return "Mark Carney is Prime Minister of Canada"

    async def fake_final(self, messages, reasoning, agent_extra, tools):
        yield "Canada's prime minister is Mark Carney."
    agent = AgentCore(provider=P(), memory_manager=None, project_id="p", session_id="s", interactive=True)
    with patch.object(get_settings(), "agent_web_budget", 1), patch.object(get_settings(), "agent_thinking", False), \
         patch.object(get_settings(), "agent_force_search", False), \
         patch("agent.core.get_all_tool_schemas", return_value=tools), patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.execute_tool", fake_exec), patch.object(AgentCore, "_finalize_stream", fake_final):
        events = [e async for e in agent.chat("who leads canada? save it")]
    done = [e for e in events if e["type"] == "done"][0]["full_response"]
    assert "<tool_call>" not in done and done.startswith("Canada's prime minister is Mark Carney.")
    assert calls == ["web_search"]
