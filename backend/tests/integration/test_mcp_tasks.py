"""MCP tasks extension (io.modelcontextprotocol/tasks): a modern server may
answer tools/call with a task handle; the client polls tasks/get to the end,
answers task-side input through the elicitor with tasks/update, and sends
tasks/cancel when the user cancels or when it has to give up. Also the
chat-side progress/cancel plumbing (broker, AgentCore, WebSocket)."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from mcp_client import elicitation as el  # noqa: E402
from mcp_client import tasks as tk  # noqa: E402
from mcp_client.client import MCPError  # noqa: E402
from tests.integration.test_mcp_dual_era import FakeServer, _decode, make_client, serve  # noqa: E402

NOW = "2026-10-24T00:00:00Z"   # in the past: a 60 s ttl from here is long gone
TASKS_CAP = {"extensions": {"io.modelcontextprotocol/tasks": {}}}


@pytest.fixture(autouse=True)
def _fast_polls(monkeypatch):
    monkeypatch.setattr(tk, "MIN_POLL_MS", 1)


class TaskServer(FakeServer):
    """A modern server whose ``slow`` tool returns a task that walks through
    ``script`` (one DetailedTask per tasks/get) and records every tasks/*
    request with its Mcp-Name header."""

    def __init__(self, script, *, task_id="t-1", poll_ms=1, ttl_ms=60000, created=None, **kw):
        super().__init__("modern", **kw)
        self.script = list(script)
        self.task_id, self.poll_ms, self.ttl_ms = task_id, poll_ms, ttl_ms
        self.created = created or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        self.updates: list[dict] = []
        self.cancels = 0
        self.gets = 0
        self.names: list[str] = []
        self.tools.append({"name": "slow", "description": "slow", "inputSchema": {"type": "object", "properties": {}}})
        self.handlers["slow"] = self._create
        self.methods["tasks/get"] = self._get
        self.methods["tasks/update"] = self._update
        self.methods["tasks/cancel"] = self._cancel

    def _base(self, **extra):
        t = {"taskId": self.task_id, "status": "working", "createdAt": self.created, "lastUpdatedAt": self.created,
             "ttlMs": self.ttl_ms, "pollIntervalMs": self.poll_ms}
        t.update(extra)
        return t

    def _create(self, args, body):
        caps = body["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"]
        assert caps.get("extensions") == {"io.modelcontextprotocol/tasks": {}}
        first = self.script.pop(0) if self.script and self.script[0].get("_initial") else {"status": "working"}
        first = {k: v for k, v in first.items() if k != "_initial"}
        return {"resultType": "task", **self._base(**first)}

    def _check(self, params, headers):
        self.names.append(_decode(headers.get("mcp-name", "")))
        if params.get("taskId") != self.task_id:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": 0,
                                             "error": {"code": -32602, "message": "Task not found"}})
        return None

    def _get(self, params, headers):
        bad = self._check(params, headers)
        if bad is not None:
            return bad
        self.gets += 1
        step = self.script.pop(0) if self.script else {"status": "working"}
        return {"resultType": "complete", **self._base(**step)}

    def _update(self, params, headers):
        bad = self._check(params, headers)
        if bad is not None:
            return bad
        self.updates.append(params["inputResponses"])
        return {"resultType": "complete"}

    def _cancel(self, params, headers):
        bad = self._check(params, headers)
        if bad is not None:
            return bad
        self.cancels += 1
        return {"resultType": "complete"}


DONE = {"status": "completed", "result": {"resultType": "complete",
                                          "content": [{"type": "text", "text": "finished"}],
                                          "structuredContent": {"n": 3}, "isError": False}}
FORM = {"method": "elicitation/create", "params": {"mode": "form", "message": "Approve?",
        "requestedSchema": {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}}}


# ── Polling to completion ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_task_is_polled_to_completion_with_progress_and_routing_header():
    srv = TaskServer([{"status": "working", "statusMessage": "step 1 of 3"},
                      {"status": "working", "statusMessage": "step 2 of 3"}, DONE])
    seen = []

    async def notify(status):
        seen.append(status)

    c = make_client()
    with serve(srv):
        res = await c.call_tool("slow", {}, notify=notify)
    assert res == {"text": "finished", "structured": {"n": 3}, "is_error": False}
    assert srv.gets == 3 and srv.cancels == 0
    # Every tasks/* request carried Mcp-Name = taskId and the tasks capability.
    assert srv.names == ["t-1", "t-1", "t-1"]
    for m, h, b in srv.requests:
        if m.startswith("tasks/"):
            assert h["mcp-method"] == m
            assert b["params"]["_meta"]["io.modelcontextprotocol/clientCapabilities"]["extensions"] == TASKS_CAP["extensions"]
    states = [(s["state"], s.get("message")) for s in seen]
    assert states[0] == ("working", None) and ("working", "step 2 of 3") in states and states[-1] == ("completed", None)
    assert all(s["task_id"] == "t-1" and isinstance(s["elapsed_s"], int) for s in seen)


@pytest.mark.asyncio
async def test_completed_tool_error_is_a_result_not_a_failure():
    srv = TaskServer([{"status": "completed", "result": {"content": [{"type": "text", "text": "bad input"}],
                                                        "isError": True}}])
    c = make_client()
    with serve(srv):
        res = await c.call_tool("slow", {})
    assert res["is_error"] and res["text"] == "bad input"


@pytest.mark.asyncio
async def test_failed_task_raises_its_jsonrpc_error():
    srv = TaskServer([{"status": "failed", "statusMessage": "boom",
                       "error": {"code": -32603, "message": "API rate limit exceeded"}}])
    c = make_client()
    with serve(srv):
        with pytest.raises(MCPError) as e:
            await c.call_tool("slow", {})
    assert e.value.code == -32603 and "API rate limit exceeded" in str(e.value)


@pytest.mark.asyncio
async def test_server_side_cancel_is_reported():
    srv = TaskServer([{"status": "cancelled", "statusMessage": "operator stopped it"}])
    c = make_client()
    with serve(srv):
        res = await c.call_tool("slow", {})
    assert res["is_error"] and "cancelled the background task" in res["text"] and "operator stopped it" in res["text"]


@pytest.mark.asyncio
async def test_initial_task_may_already_be_terminal():
    srv = TaskServer([{"_initial": True, **DONE}])
    c = make_client()
    with serve(srv):
        res = await c.call_tool("slow", {})
    assert res["text"] == "finished" and srv.gets == 0


# ── Input during a task ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_input_required_in_task_is_answered_once_via_tasks_update():
    srv = TaskServer([{"status": "input_required", "inputRequests": {"approve": FORM}},
                      {"status": "input_required", "inputRequests": {"approve": FORM}},   # same key again: no re-ask
                      {"status": "working"}, DONE])
    prompts = []

    async def elicit(prompt):
        prompts.append(prompt)
        return {"approve": {"action": "accept", "content": {"ok": True}}}

    c = make_client()
    with serve(srv):
        res = await c.call_tool("slow", {}, elicit=elicit)
    assert len(prompts) == 1 and prompts[0].tool == "slow" and prompts[0].requests[0].key == "approve"
    assert srv.updates == [{"approve": {"action": "accept", "content": {"ok": True}}}]
    assert res["text"] == "[The user filled in the server's form (ok).]\nfinished" and not res["is_error"]


@pytest.mark.asyncio
async def test_input_required_without_elicitor_cancels_the_task():
    srv = TaskServer([{"status": "input_required", "inputRequests": {"approve": FORM}}])
    c = make_client()
    with serve(srv):
        res = await c.call_tool("slow", {})
    assert res["is_error"] and srv.cancels == 1 and srv.updates == []
    assert "fill in ok" in res["text"] and "web chat" in res["text"]


@pytest.mark.asyncio
async def test_unsupported_task_input_request_cancels():
    srv = TaskServer([{"status": "input_required", "inputRequests": {
        "q": {"method": "sampling/createMessage", "params": {"messages": [], "maxTokens": 1}}}}])
    c = make_client()
    with serve(srv):
        res = await c.call_tool("slow", {}, elicit=AsyncMock())
    assert res["is_error"] and srv.cancels == 1 and "sampling/createMessage" in res["text"]


# ── Giving up ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_user_cancel_event_sends_tasks_cancel():
    srv = TaskServer([{"status": "working"}] * 50)
    ev = asyncio.Event()
    seen = []

    async def notify(status):
        seen.append(status["state"])
        if len(seen) == 2:
            ev.set()

    c = make_client()
    with serve(srv):
        res = await c.call_tool("slow", {}, notify=notify, cancel_event=ev)
    assert res["is_error"] and "user cancelled" in res["text"] and srv.cancels == 1
    assert seen[-1] == "cancelled" and srv.gets < 10


@pytest.mark.asyncio
async def test_ttl_backstop_and_max_wait():
    old = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
    srv = TaskServer([{"status": "working"}] * 5, created=old, ttl_ms=5000)
    c = make_client()
    with serve(srv):
        res = await c.call_tool("slow", {})
    assert res["is_error"] and "expire" in res["text"] and srv.cancels == 1
    srv = TaskServer([{"status": "working"}] * 50)
    c = make_client()
    with serve(srv):
        res = await c.call_tool("slow", {}, max_wait_s=0.0)
    assert res["is_error"] and "Gave up waiting" in res["text"] and srv.cancels == 1


@pytest.mark.asyncio
async def test_broken_task_objects_raise():
    srv = TaskServer([{"status": "completed"}])   # completed without a result
    c = make_client()
    with serve(srv):
        with pytest.raises(MCPError, match="invalid tasks/get reply"):
            await c.call_tool("slow", {})
    srv = TaskServer([])
    srv.handlers["slow"] = lambda a, b: {"resultType": "task", "taskId": "", "status": "working"}
    c = make_client()
    with serve(srv):
        with pytest.raises(MCPError, match="invalid task from server"):
            await c.call_tool("slow", {})


def test_helpers():
    assert tk.poll_delay({"pollIntervalMs": 5000}) == 5.0
    assert tk.poll_delay({}) == tk.DEFAULT_POLL_MS / 1000
    assert tk.poll_delay({"pollIntervalMs": 10 ** 9}) == tk.MAX_POLL_MS / 1000
    assert not tk.expired({"ttlMs": None, "createdAt": NOW})
    assert tk.expired({"ttlMs": 1000, "createdAt": NOW})
    assert not tk.expired({"ttlMs": 10 ** 12, "createdAt": NOW})
    assert not tk.expired({"ttlMs": 1000, "createdAt": "garbage"})
    with pytest.raises(tk.InvalidTask):
        tk.parse_task({"taskId": "x", "status": "sleeping"})
    assert tk.public_status({"taskId": "x", "status": "working", "statusMessage": "  hi "}, elapsed_s=3.9) == \
        {"task_id": "x", "state": "working", "elapsed_s": 3, "message": "hi"}


def test_discover_records_server_extensions():
    import asyncio as _a
    srv = FakeServer("modern")
    orig = srv._modern

    def with_ext(method, headers, body, meta, rid):
        resp = orig(method, headers, body, meta, rid)
        if method == "server/discover":
            data = json.loads(resp.content)
            data["result"]["capabilities"]["extensions"] = {"io.modelcontextprotocol/tasks": {}}
            return httpx.Response(200, json=data)
        return resp
    srv._modern = with_ext
    c = make_client()
    with serve(srv):
        info = _a.run(c.test_connection())
    assert c.server_extensions == ["io.modelcontextprotocol/tasks"] and info["extensions"] == c.server_extensions


# ── Chat plumbing ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_broker_forwards_status_and_cancels_by_tool_id():
    sent = []

    async def send(ev):
        sent.append(ev)

    broker = el.ElicitationBroker(send)
    await broker.tool_status("tc1", "mcp_x_slow", {"state": "working", "message": "m", "elapsed_s": 2, "task_id": "t"})
    assert sent[-1] == {"type": "tool_status", "tool_id": "tc1", "name": "mcp_x_slow", "state": "working",
                        "message": "m", "elapsed_s": 2, "task_id": "t"}
    ev = asyncio.Event()
    broker.cancel_events["tc1"] = ev
    assert not await broker.cancel_tool("nope") and sent[-1]["type"] == "warning"
    assert await broker.cancel_tool("tc1") and ev.is_set()
    ev2 = asyncio.Event()
    broker.cancel_events["tc2"] = ev2
    broker.close()
    assert ev2.is_set() and broker.cancel_events == {}


def test_agent_core_gates_task_plumbing_like_the_elicitor():
    from agent.core import AgentCore
    ts, ce = object(), {}
    a = AgentCore(provider=MagicMock(), interactive=True, channel="web", tool_status=ts, cancel_events=ce)
    assert a.tool_status is ts and a.cancel_events is ce
    b = AgentCore(provider=MagicMock(), interactive=True, channel="discord", tool_status=ts, cancel_events=ce)
    assert b.tool_status is None and b.cancel_events is None and b._tool_notifier("x", "y") is None


def _ws_app():
    from fastapi import FastAPI
    from api import chat as chat_mod
    app = FastAPI()
    app.include_router(chat_mod.router)
    app.add_api_websocket_route("/ws/chat", chat_mod.websocket_chat)
    return app


class _FakeAgent:
    """Reports task progress through tool_status and finishes when the
    user's cancel event is set (like a tool waiting on a task would)."""

    def __init__(self, tool_status, cancel_events):
        self.tool_status, self.cancel_events = tool_status, cancel_events
        self.working_memory = []

    async def chat(self, message, stream=True):
        yield {"type": "tool_call", "name": "mcp_x_slow", "args": {}, "id": "tc1"}
        ev = asyncio.Event()
        self.cancel_events["tc1"] = ev
        await self.tool_status("tc1", "mcp_x_slow", {"state": "working", "message": "step 1", "elapsed_s": 0, "task_id": "t"})
        await asyncio.wait_for(ev.wait(), timeout=5)
        self.cancel_events.pop("tc1", None)
        yield {"type": "tool_result", "name": "mcp_x_slow", "tool_id": "tc1", "is_error": True,
               "result": "[The user cancelled the background task]"}
        yield {"type": "done", "full_response": ""}


def test_build_agent_accepts_the_task_plumbing():
    """The WebSocket test below stubs _build_agent; make sure the real one
    takes what the socket passes (a missing kw-arg broke every turn once)."""
    import inspect
    from api import chat as chat_mod
    params = inspect.signature(chat_mod._build_agent).parameters
    assert {"elicit", "tool_status", "cancel_events"} <= set(params)


def test_websocket_forwards_task_status_and_cancel():
    from starlette.testclient import TestClient
    from api import chat as chat_mod

    async def fake_build_agent(*, tool_status=None, cancel_events=None, **kw):
        return _FakeAgent(tool_status, cancel_events)

    memory = MagicMock()
    memory.episodic.save_message = AsyncMock()
    with patch.object(chat_mod, "_build_agent", fake_build_agent), \
         patch.object(chat_mod, "get_provider_for", return_value=MagicMock()), \
         patch.object(chat_mod, "create_memory_manager", return_value=memory), \
         patch.object(chat_mod, "_route_turn", AsyncMock(return_value=None)), \
         patch.object(chat_mod, "_finish_route", return_value=None), \
         patch("api.auth.authorize_websocket", AsyncMock(return_value=True)), \
         patch("utils.chat_settings.skill_discovery", return_value="off"):
        client = TestClient(_ws_app())
        with client.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"message": "go", "session_id": "s1", "project_id": "default"}))
            types = []
            while True:
                ev = ws.receive_json()
                types.append(ev["type"])
                if ev["type"] == "tool_status":
                    assert ev["tool_id"] == "tc1" and ev["state"] == "working" and ev["message"] == "step 1"
                    break
            assert types == ["session_start", "tool_call", "tool_status"]
            ws.send_text(json.dumps({"type": "tool_cancel", "tool_id": "zzz"}))
            assert ws.receive_json()["type"] == "warning"
            ws.send_text(json.dumps({"type": "tool_cancel", "tool_id": "tc1"}))
            res = ws.receive_json()
            assert res["type"] == "tool_result" and "cancelled" in res["result"]
            assert ws.receive_json()["type"] == "done"
