"""Output cap, truncation reporting, streamed usage, route-cache freshness and
the artifact tool fixes found by the iOS bake-off (2026-10-18): four hosted
models writing a Swift app through the agent tools, where a 4,096-token cap
cut files mid-call and the run failed with "not valid JSON" or nothing at all."""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))


# ── Provider: cap, usage, truncation ─────────────────────────────────────────

async def _stream(chunks):
    from models.provider import ModelProvider
    seen = []

    def handler(req):
        seen.append(json.loads(req.content))
        sse = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n"
        return httpx.Response(200, text=sse, headers={"content-type": "text/event-stream"})

    p = ModelProvider(base_url="https://m.test/v1", api_key="k", model="m")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with patch("utils.http.shared_client", return_value=client):
        events = [e async for e in p.chat([{"role": "user", "content": "x"}], stream=True)]
    await client.aclose()
    return seen[0], events


@pytest.mark.asyncio
async def test_stream_sends_configured_cap_and_asks_for_usage():
    from config import get_settings
    with patch.object(get_settings(), "llm_max_tokens", 1234):
        body, events = await _stream([
            {"choices": [{"delta": {"content": "hi"}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 1}},
        ])
    assert body["max_tokens"] == 1234
    assert body["stream_options"] == {"include_usage": True}
    assert events[-1] == {"type": "done", "content": "hi", "reasoning": "", "finish_reason": "stop",
                          "usage": {"prompt_tokens": 9, "completion_tokens": 1}}


@pytest.mark.asyncio
async def test_cut_off_tool_call_mislabelled_tool_calls_is_reported_as_length():
    # DeepSeek/Opus via RouteLLM: finish "tool_calls", arguments cut mid-JSON,
    # completion_tokens == max_tokens.
    from config import get_settings
    with patch.object(get_settings(), "llm_max_tokens", 50):
        _, events = await _stream([
            {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {
                "name": "save_to_artifact", "arguments": '{"path": "A.swift", "content": "imp'}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
            {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 50}},
        ])
    call = next(e for e in events if e["type"] == "tool_call")
    assert "not valid JSON" in call["args_error"]
    assert events[-1]["finish_reason"] == "length"


@pytest.mark.asyncio
async def test_stream_with_nothing_in_it_is_reported_as_empty():
    # Gemini via RouteLLM: no text, no tool call, no finish reason, no usage.
    _, events = await _stream([{"choices": [{"delta": {}}]}])
    assert events[-1]["finish_reason"] == "empty"
    assert "usage" not in events[-1]


@pytest.mark.asyncio
async def test_routed_stream_records_usage():
    from models.provider import RoutedProvider

    class _P:
        model = "m"

        async def chat(self, *a, **kw):
            yield {"type": "done", "content": "", "finish_reason": "stop",
                   "usage": {"prompt_tokens": 7, "completion_tokens": 3}}

    with patch("llm_config.usage.record") as rec:
        _ = [e async for e in RoutedProvider("code", [(_P(), "ep")]).chat([], stream=True)]
    kw = rec.call_args.kwargs
    assert (kw["operation"], kw["prompt_tokens"], kw["completion_tokens"]) == ("stream", 7, 3)


def test_route_change_outside_the_api_rebuilds_the_cached_provider():
    import models.provider as mp
    from config import get_settings
    from llm_config.store import ResolvedRole

    def role(model):
        return [ResolvedRole(base_url="https://r.test/v1", api_key="k", model=model,
                             api_type="openai", endpoint_name="routellm")]

    mp.reset_provider()
    current = {"model": "deepseek"}
    with patch("llm_config.store.resolve_route", side_effect=lambda tc: role(current["model"])):
        with patch.object(get_settings(), "llm_route_recheck_seconds", 1e-9):
            assert mp.get_provider_for("code").model == "deepseek"
            current["model"] = "opus"       # e.g. another process wrote the vault
            assert mp.get_provider_for("code").model == "opus"
        with patch.object(get_settings(), "llm_route_recheck_seconds", 0):
            current["model"] = "gemini"     # rechecks off: cached until reset_provider()
            assert mp.get_provider_for("code").model == "opus"
    mp.reset_provider()


# ── Agent loop: what the model and the user are told ─────────────────────────

class _Rounds:
    """Streams scripted rounds of events; records the messages of each call."""
    model = "m"
    task_class = "agent"

    def __init__(self, rounds):
        self.rounds = rounds
        self.calls = []

    async def chat(self, messages, tools=None, stream=True, **kw):
        self.calls.append([dict(m) for m in messages])
        for ev in (self.rounds.pop(0) if self.rounds else [{"type": "done", "finish_reason": "stop"}]):
            yield ev

    async def chat_complete(self, *a, **kw):
        return {"content": "", "tool_calls": []}


def _call(name="recall", args=None, **extra):
    return {"type": "tool_call", "id": "c", "name": name, "args": args or {}, **extra}


async def _agent_run(prov, **kw):
    from agent.core import AgentCore
    agent = AgentCore(provider=prov, memory_manager=None, project_id="p", session_id="s")
    events = []
    with patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.get_all_tool_schemas", return_value=[]), \
         patch("agent.core.execute_tool", new_callable=AsyncMock, return_value="ok") as ex:
        async for ev in agent.chat("build the app", stream=True, **kw):
            events.append(ev)
    return events, ex


@pytest.mark.asyncio
async def test_cut_off_tool_arguments_tell_the_model_to_write_smaller_calls():
    prov = _Rounds([
        [_call("save_to_artifact", args_error="arguments were not valid JSON: {\"path\""),
         {"type": "done", "finish_reason": "length"}],
        [{"type": "text_delta", "content": "Saved in parts."}, {"type": "done", "finish_reason": "stop"}],
    ])
    events, ex = await _agent_run(prov)
    result = next(e for e in events if e["type"] == "tool_result")["result"]
    assert "hit the output limit" in result and "smaller" in result
    ex.assert_not_called()


@pytest.mark.asyncio
async def test_empty_reply_mid_task_gets_one_nudge():
    from agent.core import EMPTY_REPLY_NOTICE
    prov = _Rounds([
        [_call(), {"type": "done", "finish_reason": "tool_calls"}],
        [{"type": "done", "finish_reason": "empty"}],
        [{"type": "text_delta", "content": "All files saved."}, {"type": "done", "finish_reason": "stop"}],
    ])
    events, _ = await _agent_run(prov)
    assert prov.calls[2][-1] == {"role": "user", "content": EMPTY_REPLY_NOTICE}
    assert events[-1]["full_response"] == "All files saved."


@pytest.mark.asyncio
async def test_empty_reply_is_shown_not_silent():
    from agent.core import EMPTY_REPLY_NOTE
    prov = _Rounds([[{"type": "done", "finish_reason": "empty"}]])
    events, _ = await _agent_run(prov)
    assert events[-1]["full_response"] == EMPTY_REPLY_NOTE
    assert len(prov.calls) == 1


@pytest.mark.asyncio
async def test_step_limit_is_visible_in_the_reply():
    prov = _Rounds([[_call(), {"type": "done", "finish_reason": "tool_calls"}] for _ in range(3)])
    events, _ = await _agent_run(prov, max_iterations=3)
    done = events[-1]
    assert done["truncated"] is True
    assert "Stopped after 3 steps" in done["full_response"]
    assert any(e["type"] == "text_delta" and "Stopped after 3 steps" in e["content"] for e in events)


# ── Artifact tools ───────────────────────────────────────────────────────────

class _Mem:
    def __init__(self):
        self.indexed = []

    async def index_artifact(self, aid, **kw):
        self.indexed.append(aid)


@pytest.fixture
def tools(tmp_path):
    from artifacts.store import ArtifactStore
    from agent.tools import execute_tool
    store = ArtifactStore(db_path=str(tmp_path / "a.db"), blobs_dir=tmp_path / "blobs")
    mem = _Mem()

    async def run(name, **args):
        return await execute_tool(name, args, mem, project_id="ios", session_id="s")

    with patch("artifacts.store.get_store", return_value=store), \
         patch("artifacts.embedder.schedule_embed"), \
         patch("artifacts.embedder.drop_for_artifact", new_callable=AsyncMock) as drop:
        yield run, store, mem, drop


@pytest.mark.asyncio
async def test_save_collision_says_a_separate_copy_was_made(tools):
    run, store, _, _ = tools
    await run("save_to_artifact", path="App/Game.swift", content="v1")
    out = await run("save_to_artifact", path="App/Game.swift", content="v2")
    assert "SEPARATE copy" in out and "ios/App/Game-1.swift" in out and "overwrite=true" in out
    assert store.get_by_path("ios", "ios/App/Game.swift")["content"] == "v1"


@pytest.mark.asyncio
async def test_save_with_overwrite_replaces_in_place(tools):
    run, store, _, _ = tools
    await run("save_to_artifact", path="App/Game.swift", content="v1")
    out = await run("save_to_artifact", path="App/Game.swift", content="v2", overwrite=True)
    assert out.startswith("Overwrote artifact ios/App/Game.swift") and "now v2" in out
    assert store.get_by_path("ios", "ios/App/Game.swift")["content"] == "v2"
    assert store.get_by_path("ios", "ios/App/Game-1.swift") is None
    # overwrite of a new path is a plain save
    assert (await run("save_to_artifact", path="App/New.swift", content="x", overwrite=True)).startswith("Saved")


@pytest.mark.asyncio
async def test_code_gets_its_type_and_skips_memory_extraction(tools):
    run, store, mem, _ = tools
    await run("save_to_artifact", path="App/Game.swift", content="struct G {}", content_type="text/markdown")
    await run("save_to_artifact", path="notes/plan.md", content="# Plan")
    await run("save_to_artifact", path="data.json", content="{}")
    code = store.get_by_path("ios", "ios/App/Game.swift")
    assert code["content_type"] == "text/x-swift"
    assert store.get_by_path("ios", "ios/data.json")["content_type"] == "application/json"
    plan = store.get_by_path("ios", "ios/notes/plan.md")
    assert plan["content_type"] == "text/markdown"
    assert code["id"] not in mem.indexed and plan["id"] in mem.indexed
    assert (await run("read_artifact", path="App/Game.swift")).endswith("struct G {}")


@pytest.mark.asyncio
async def test_delete_artifact_by_path_and_id(tools):
    run, store, _, drop = tools
    await run("save_to_artifact", path="App/Game.swift", content="v1")
    await run("save_to_artifact", path="App/Game.swift", content="v2")
    out = await run("delete_artifact", path="App/Game-1.swift")
    assert out.startswith("Deleted artifact ios/App/Game-1.swift")
    assert store.get_by_path("ios", "ios/App/Game-1.swift") is None
    drop.assert_awaited_once()
    other = store.create(project_id="elsewhere", path="elsewhere/x.md", content="x", content_type="text/markdown")
    assert (await run("delete_artifact", id=other["id"])).startswith("Artifact not found")
    assert store.get(other["id"]) is not None
    keep = store.get_by_path("ios", "ios/App/Game.swift")
    assert (await run("delete_artifact", id=keep["id"])).startswith("Deleted")
    # the path is free again for a plain save
    assert "SEPARATE" not in await run("save_to_artifact", path="App/Game.swift", content="v3")
