"""MCP follow-ups, part 2: subscriptions/listen (list_changed and task
notifications), durable task records with resumption after a restart, and
resources + prompts served to the model as synthetic tools."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from mcp_client import task_store, tasks as tk  # noqa: E402
from mcp_client.client import MCPError, tool_function_name  # noqa: E402
from tests.integration.test_mcp_dual_era import FakeServer, make_client, serve  # noqa: E402
from tests.integration.test_mcp_tasks import DONE, TaskServer  # noqa: E402


class _Events(httpx.AsyncByteStream):
    """An SSE body: each item is (delay seconds, message dict)."""

    def __init__(self, items):
        self.items = items

    async def __aiter__(self):
        for delay, msg in self.items:
            if delay:
                await asyncio.sleep(delay)
            yield f"data: {json.dumps(msg)}\n\n".encode()


class CapServer(FakeServer):
    """A modern FakeServer whose server/discover advertises ``caps``."""

    def __init__(self, caps, **kw):
        super().__init__("modern", **kw)
        self.caps = caps
        self.listens: list[dict] = []

    def _modern(self, method, headers, body, meta, rid):
        if method == "server/discover":
            resp = super()._modern(method, headers, body, meta, rid)
            data = json.loads(resp.content)
            data["result"]["capabilities"] = self.caps
            return httpx.Response(200, json=data)
        if method == "subscriptions/listen":
            flt = (body.get("params") or {}).get("notifications") or {}
            self.listens.append(flt)
            assert headers.get("mcp-method") == "subscriptions/listen"
            return self.listen_stream(rid, flt)
        return super()._modern(method, headers, body, meta, rid)

    def listen_stream(self, rid, flt):
        return httpx.Response(404, json={"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "nope"}})


# ── Subscriptions ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_listener_relists_tools_on_list_changed_and_stops_on_close():
    srv = CapServer({"tools": {"listChanged": True}, "prompts": {}})

    def stream(rid, flt):
        ack = {"jsonrpc": "2.0", "method": "notifications/subscriptions/acknowledged",
               "params": {"_meta": {"io.modelcontextprotocol/subscriptionId": rid}, "notifications": flt}}
        changed = {"jsonrpc": "2.0", "method": "notifications/tools/list_changed",
                   "params": {"_meta": {"io.modelcontextprotocol/subscriptionId": rid}}}
        graceful = {"jsonrpc": "2.0", "id": rid, "result": {"resultType": "complete"}}
        return httpx.Response(200, stream=_Events([(0, ack), (0.05, changed), (0.05, graceful)]),
                              headers={"content-type": "text/event-stream"})
    srv.listen_stream = stream
    c = make_client()
    with serve(srv):
        await c.discover_tools()
        before = srv.list_calls
        assert c.start_listener() is True and c.start_listener() is False
        for _ in range(40):
            await asyncio.sleep(0.05)
            if srv.list_calls > before:
                break
        assert srv.list_calls == before + 1
        assert srv.listens[0] == {"toolsListChanged": True}
        await c.close()
    assert c._listener is None
    # No listChanged advertised: nothing to listen for. Legacy: never.
    quiet = CapServer({"tools": {}})
    c2 = make_client()
    with serve(quiet):
        await c2.discover_tools()
        assert c2.start_listener() is False


@pytest.mark.asyncio
async def test_listener_gives_up_on_servers_without_subscriptions():
    srv = CapServer({"tools": {"listChanged": True}})   # listen_stream answers -32601
    c = make_client()
    with serve(srv):
        await c.discover_tools()
        assert c.start_listener()
        for _ in range(40):
            await asyncio.sleep(0.05)
            if c._listener is None:
                break
        assert c._listener is None and len(srv.listens) == 1


class NotifyingTaskServer(TaskServer):
    """Advertises the tasks extension and pushes the task's completion on a
    subscriptions/listen stream, so the client need not poll."""

    def __init__(self, **kw):
        super().__init__([DONE], poll_ms=5000, **kw)
        self.listens = []

    def _modern(self, method, headers, body, meta, rid):
        if method == "server/discover":
            resp = super()._modern(method, headers, body, meta, rid)
            data = json.loads(resp.content)
            data["result"]["capabilities"] = {"tools": {}, "extensions": {"io.modelcontextprotocol/tasks": {}}}
            return httpx.Response(200, json=data)
        if method == "subscriptions/listen":
            flt = (body.get("params") or {}).get("notifications") or {}
            self.listens.append((flt, headers.get("mcp-name")))
            done = {"jsonrpc": "2.0", "method": "notifications/tasks",
                    "params": {"_meta": {"io.modelcontextprotocol/subscriptionId": rid},
                               **self._base(**DONE)}}
            return httpx.Response(200, stream=_Events([(0.05, done)]), headers={"content-type": "text/event-stream"})
        return super()._modern(method, headers, body, meta, rid)


@pytest.mark.asyncio
async def test_task_notification_completes_without_polling():
    srv = NotifyingTaskServer()
    c = make_client()
    with serve(srv):
        await c.initialize()
        res = await asyncio.wait_for(c.call_tool("slow", {}), timeout=3)
    assert res["text"] == "finished" and srv.gets == 0
    assert srv.listens == [({"taskIds": ["t-1"]}, None)]


# ── Durable task records + resumption ────────────────────────────────────────

@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    settings = SimpleNamespace(data_dir=tmp_path, mcp_task_max_wait_seconds=1800)
    monkeypatch.setattr("config.get_settings", lambda: settings)
    return tmp_path


def test_task_store_roundtrip(data_dir):
    assert task_store.pending() == []
    task_store.remember("c", "slow", "t-9", {"session_id": "s", "project_id": "p", "tool": "mcp_c_slow"})
    assert (data_dir / task_store.FILENAME).exists()
    assert [r["task_id"] for r in task_store.pending()] == ["t-9"]
    task_store.forget("t-9")
    task_store.forget("missing")
    assert task_store.pending() == []


@pytest.mark.asyncio
async def test_run_task_records_and_forgets_with_a_call_context(data_dir, monkeypatch):
    monkeypatch.setattr(tk, "MIN_POLL_MS", 1)
    srv = TaskServer([{"status": "working"}, DONE])
    seen = []
    orig_forget = task_store.forget

    def spy_forget(tid):
        seen.append(("forget", tid, [r["task_id"] for r in task_store.pending()]))
        orig_forget(tid)
    monkeypatch.setattr(task_store, "forget", spy_forget)
    c = make_client()
    token = task_store.CALL_CONTEXT.set({"session_id": "s1", "project_id": "p1", "tool": "mcp_t_slow"})
    try:
        with serve(srv):
            res = await c.call_tool("slow", {})
    finally:
        task_store.CALL_CONTEXT.reset(token)
    assert res["text"] == "finished"
    assert seen == [("forget", "t-1", ["t-1"])] and task_store.pending() == []
    # Without a context (a job, an adapter) nothing is recorded.
    srv2 = TaskServer([DONE])
    with serve(srv2):
        await make_client().call_tool("slow", {})
    assert task_store.pending() == []


@pytest.mark.asyncio
async def test_resume_tasks_posts_the_result_to_the_conversation(data_dir, monkeypatch):
    monkeypatch.setattr(tk, "MIN_POLL_MS", 1)
    from mcp_client.manager import MCPManager
    task_store.remember("t", "slow", "t-1", {"session_id": "s1", "project_id": "p1", "tool": "mcp_t_slow"})
    task_store.remember("gone", "slow", "t-2", {"session_id": "s1"})
    srv = TaskServer([{"status": "working"}, DONE])
    mgr = MCPManager.__new__(MCPManager)
    mgr._configs, mgr._clients, mgr._save_configs = [], {}, lambda: None
    saved = []

    async def save_message(**kw):
        saved.append(kw)
    with serve(srv):
        client = make_client()
        await client.initialize()
        mgr._clients["t"] = client
        with patch("memory.episodic.EpisodicMemory") as ep:
            ep.return_value.save_message = save_message
            await mgr.resume_tasks()
            for _ in range(60):
                await asyncio.sleep(0.05)
                if saved:
                    break
    assert task_store.pending() == []        # t-2 forgotten (no connection), t-1 done
    assert len(saved) == 1 and saved[0]["session_id"] == "s1" and saved[0]["project_id"] == "p1"
    assert saved[0]["role"] == "assistant" and "finished" in saved[0]["content"]
    assert "While Pantheon was restarting" in saved[0]["content"] and saved[0]["metadata"] == {"mcp_task": "t-1"}


# ── Resources and prompts as tools ───────────────────────────────────────────

class ResourceServer(CapServer):
    def __init__(self):
        super().__init__({"tools": {}, "resources": {}, "prompts": {}})
        self.reads: list[dict] = []
        self.methods["resources/list"] = lambda p, h: {"resultType": "complete", "resources": [
            {"uri": "file:///notes/a.md", "name": "a.md", "title": "Notes A", "mimeType": "text/markdown",
             "description": "Project notes"},
            {"uri": "db://users", "name": "users"}]}
        self.methods["resources/templates/list"] = lambda p, h: {"resultType": "complete", "resourceTemplates": [
            {"uriTemplate": "file:///notes/{name}", "name": "Any note"}]}
        self.methods["prompts/list"] = lambda p, h: {"resultType": "complete", "prompts": [
            {"name": "review", "description": "Review code", "arguments": [{"name": "code", "required": True}]}]}
        self.methods["resources/read"] = self._read
        self.methods["prompts/get"] = self._get

    def _read(self, params, headers):
        assert headers.get("mcp-name") == params["uri"], headers.get("mcp-name")
        self.reads.append(params)
        if params["uri"] == "secret://x" and "inputResponses" not in params:
            return {"resultType": "input_required", "requestState": "r1", "inputRequests": {"ok": {
                "method": "elicitation/create", "params": {"mode": "form", "message": "Confirm?",
                                                           "requestedSchema": {"type": "object", "properties": {"yes": {"type": "boolean"}}}}}}}
        if params["uri"] == "bin://x":
            return {"resultType": "complete", "contents": [{"uri": "bin://x", "mimeType": "image/png", "blob": "AAAA"}]}
        return {"resultType": "complete", "contents": [
            {"uri": params["uri"], "mimeType": "text/markdown", "text": "# Hello\nbody"}]}

    def _get(self, params, headers):
        assert headers.get("mcp-name") == params["name"]
        return {"resultType": "complete", "description": "Code review prompt", "messages": [
            {"role": "user", "content": {"type": "text", "text": f"Review: {params.get('arguments', {}).get('code')}"}},
            {"role": "assistant", "content": [{"type": "text", "text": "Sure."},
                                              {"type": "resource_link", "uri": "file:///notes/a.md"}]}]}


@pytest.mark.asyncio
async def test_resources_and_prompts_become_tools():
    from mcp_client.manager import MCPManager
    srv = ResourceServer()
    mgr = MCPManager.__new__(MCPManager)
    mgr._configs = [{"name": "t", "url": "http://mcp.test/mcp", "excluded_tools": ["list_prompts"]}]
    mgr._clients, mgr._save_configs = {}, lambda: None
    with serve(srv):
        c = make_client()
        await c.discover_tools()
        mgr._clients["t"] = c
        assert [r["uri"] for r in c.resources] == ["file:///notes/a.md", "db://users"]
        assert [t["uriTemplate"] for t in c.resource_templates] == ["file:///notes/{name}"]
        assert [p["name"] for p in c.prompts] == ["review"]
        names = [s["function"]["name"] for s in mgr.get_all_tool_schemas()]
        assert "mcp_t_read_resource" in names and "mcp_t_list_resources" in names and "mcp_t_get_prompt" in names
        assert "mcp_t_list_prompts" not in names   # excluded, like any tool
        desc = next(s for s in mgr.get_all_tool_schemas() if s["function"]["name"] == "mcp_t_read_resource")["function"]["description"]
        assert "file:///notes/a.md — Notes A: Project notes" in desc and "template file:///notes/{name}" in desc
        assert mgr.resolve_tool_call("mcp_t_read_resource") == (c, "read_resource")
        assert mgr.resolve_tool_call("mcp_t_list_prompts") is None
        # Run them through the manager like the agent does.
        with patch("mcp_client.budget.record"):
            out = await mgr.execute_tool("mcp_t_read_resource", {"uri": "file:///notes/a.md"})
            assert out.startswith("=== file:///notes/a.md (text/markdown) ===\n# Hello\nbody")
            out = await mgr.execute_tool("mcp_t_read_resource", {"uri": "bin://x"})
            assert "[binary content, 3 bytes base64-encoded; not shown]" in out
            out = await mgr.execute_tool("mcp_t_list_resources", {})
            assert "db://users" in out and "template file:///notes/{name}" in out
            out = await mgr.execute_tool("mcp_t_get_prompt", {"name": "review", "arguments": {"code": "x=1"}})
            assert "(Code review prompt)" in out and "user: Review: x=1" in out
            assert "assistant: Sure.\n[resource file:///notes/a.md: read it with read_resource]" in out
            # A read that asks for input goes through the elicitor (MRTR).
            async def elicit(prompt):
                assert prompt.tool == "resources/read" and prompt.requests[0].message == "Confirm?"
                return {"ok": {"action": "accept", "content": {"yes": True}}}
            out = await mgr.execute_tool("mcp_t_read_resource", {"uri": "secret://x"}, elicit=elicit)
            assert out.startswith("[The user filled in the server's form (yes).]\n=== secret://x")
            assert srv.reads[-1]["inputResponses"] == {"ok": {"action": "accept", "content": {"yes": True}}} \
                and srv.reads[-1]["requestState"] == "r1"
            out = await mgr.execute_tool("mcp_t_read_resource", {"uri": "secret://x"})
            assert "needs user input" in out and "web chat" in out
        discovered = mgr.get_discovered_tools()
        synth = [d for d in discovered if d.get("synthetic")]
        assert {d["name"] for d in synth} == {"read_resource", "list_resources", "get_prompt", "list_prompts"}
        assert next(d for d in synth if d["name"] == "list_prompts")["excluded"] is True
        listed = mgr.list_connections()[0]
        assert listed["resources_count"] == 3 and listed["prompts_count"] == 1


@pytest.mark.asyncio
async def test_a_real_tool_named_like_a_synthetic_one_wins_and_servers_without_caps_get_none():
    srv = ResourceServer()
    srv.tools.append({"name": "read_resource", "description": "mine", "inputSchema": {"type": "object", "properties": {}}})
    c = make_client()
    with serve(srv):
        await c.discover_tools()
    assert "read_resource" not in c.synthetic_tools() and "list_resources" in c.synthetic_tools()
    plain = FakeServer("modern")
    c2 = make_client()
    with serve(plain):
        await c2.discover_tools()
    assert c2.synthetic_tools() == {} and c2.resources == [] and c2.prompts == []
