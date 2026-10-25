"""The dual-era MCP client against a fake server that speaks either the
stateless 2026-07-28 revision ("modern") or the 2025-11-25 handshake
("legacy"), or both. Covers era detection and fallback, per-request _meta
and mirrored headers, x-mcp-header validation and encoding, HeaderMismatch
recovery, ttlMs refresh, version negotiation, MRTR/task placeholders, the
LLM schema simplifier, the OAuth additions and the local port scanner."""
from __future__ import annotations

import base64
import json
import os
import tempfile
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

MODERN = "2026-07-28"
LEGACY = "2025-11-25"


def _tool(name, props=None, required=None):
    return {"name": name, "description": f"{name} tool",
            "inputSchema": {"type": "object", "properties": props or {}, "required": required or []}}


DEFAULT_TOOLS = [
    _tool("echo", {"text": {"type": "string"}}),
    _tool("execute_sql", {"region": {"type": "string", "x-mcp-header": "Region"},
                          "limit": {"type": "integer", "x-mcp-header": "Limit"},
                          "dry": {"type": "boolean", "x-mcp-header": "Dry"},
                          "query": {"type": "string"}}, ["query"]),
]


def _decode(value: str) -> str:
    if value.startswith("=?base64?") and value.endswith("?="):
        return base64.b64decode(value[len("=?base64?"):-2]).decode("utf-8")
    return value


class FakeServer:
    """One MCP endpoint. ``mode``: "modern" (stateless only), "legacy"
    (handshake only) or "dual". ``legacy_probe``: how a legacy server answers
    the modern server/discover probe ("http400" plain body, "rpc32601" JSON-RPC
    method-not-found, "rpc32000" not-initialized)."""

    def __init__(self, mode="modern", supported=(MODERN,), tools=None, ttl_ms=None,
                 legacy_probe="http400"):
        self.mode, self.supported, self.ttl_ms, self.legacy_probe = mode, list(supported), ttl_ms, legacy_probe
        self.tools = tools if tools is not None else [json.loads(json.dumps(t)) for t in DEFAULT_TOOLS]
        self.requests: list[tuple[str, dict, dict]] = []  # (method, headers, body)
        self.sessions: set[str] = set()
        self.list_calls = 0
        self.handlers: dict[str, object] = {}  # tool name -> fn(args, body) -> result dict
        # Extra modern methods (tasks/*): method -> fn(params, headers) -> result dict,
        # or an httpx.Response to send as is.
        self.methods: dict[str, object] = {}

    # ── helpers ──
    @staticmethod
    def _rpc(id_, result=None, error=None, status=200, headers=None):
        payload = {"jsonrpc": "2.0", "id": id_}
        if error is not None:
            payload["error"] = error
        else:
            payload["result"] = result
        return httpx.Response(status, json=payload, headers=headers or {})

    def _header_params(self, tool_name):
        for t in self.tools:
            if t["name"] == tool_name:
                out = []
                for prop, sub in (t.get("inputSchema", {}).get("properties") or {}).items():
                    if "x-mcp-header" in sub:
                        out.append((sub["x-mcp-header"], prop))
                return out
        return []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        method = body.get("method", "")
        headers = {k.lower(): v for k, v in request.headers.items()}
        self.requests.append((method, headers, body))
        meta = (body.get("params") or {}).get("_meta") or {}
        modern_req = "io.modelcontextprotocol/protocolVersion" in meta
        rid = body.get("id")

        if modern_req and self.mode in ("modern", "dual"):
            return self._modern(method, headers, body, meta, rid)
        if self.mode == "modern":
            # Modern-only server, legacy-shaped request: header validation fails.
            return self._rpc(rid, error={"code": -32020, "message": "missing MCP request headers"}, status=400)
        return self._legacy(method, headers, body, rid)

    def _modern(self, method, headers, body, meta, rid):
        version = meta["io.modelcontextprotocol/protocolVersion"]
        if headers.get("mcp-protocol-version") != version:
            return self._rpc(rid, error={"code": -32020, "message": "MCP-Protocol-Version header mismatch"}, status=400)
        if version not in self.supported:
            return self._rpc(rid, status=400, error={
                "code": -32022, "message": "Unsupported protocol version",
                "data": {"supported": self.supported, "requested": version}})
        if headers.get("mcp-method") != method:
            return self._rpc(rid, error={"code": -32020, "message": "Mcp-Method header missing or wrong"}, status=400)
        if "mcp-session-id" in headers:
            pass  # ignored by modern servers
        srv_meta = {"io.modelcontextprotocol/serverInfo": {"name": "fake-modern", "version": "9"}}
        if method == "server/discover":
            return self._rpc(rid, {"resultType": "complete", "supportedVersions": self.supported,
                                   "capabilities": {"tools": {}}, "instructions": "be nice", "_meta": srv_meta,
                                   "ttlMs": 60000, "cacheScope": "public"})
        if method == "tools/list":
            self.list_calls += 1
            res = {"resultType": "complete", "tools": self.tools, "cacheScope": "private", "_meta": srv_meta}
            if self.ttl_ms is not None:
                res["ttlMs"] = self.ttl_ms
            return self._rpc(rid, res)
        if method == "tools/call":
            params = body.get("params") or {}
            name = params.get("name")
            if _decode(headers.get("mcp-name", "")) != name:
                return self._rpc(rid, error={"code": -32020, "message": "Mcp-Name mismatch"}, status=400)
            args = params.get("arguments") or {}
            for hname, prop in self._header_params(name):
                if prop in args and args[prop] is not None:
                    want = args[prop]
                    got = headers.get(f"mcp-param-{hname}".lower())
                    if got is None:
                        return self._rpc(rid, error={"code": -32020, "message": f"missing Mcp-Param-{hname}"}, status=400)
                    got = _decode(got)
                    exp = ("true" if want else "false") if isinstance(want, bool) else str(want)
                    if got != exp:
                        return self._rpc(rid, error={"code": -32020, "message": f"Mcp-Param-{hname} mismatch"}, status=400)
                elif f"mcp-param-{hname}".lower() in headers:
                    return self._rpc(rid, error={"code": -32020, "message": f"unexpected Mcp-Param-{hname}"}, status=400)
            fn = self.handlers.get(name)
            if fn:
                return self._rpc(rid, fn(args, body))
            return self._rpc(rid, {"resultType": "complete",
                                   "content": [{"type": "text", "text": f"ok:{json.dumps(args, sort_keys=True)}"}],
                                   "structuredContent": {"echo": args}, "_meta": srv_meta})
        if method in self.methods:
            out = self.methods[method](body.get("params") or {}, headers)
            return out if isinstance(out, httpx.Response) else self._rpc(rid, out)
        return self._rpc(rid, error={"code": -32601, "message": f"Method not found: {method}"}, status=404)

    def _legacy(self, method, headers, body, rid):
        if rid is None:  # notification
            return httpx.Response(202)
        if method == "initialize":
            sid = f"sess-{len(self.sessions) + 1}"
            self.sessions.add(sid)
            return self._rpc(rid, {"protocolVersion": LEGACY, "capabilities": {"tools": {}},
                                   "serverInfo": {"name": "fake-legacy", "version": "1"}},
                             headers={"mcp-session-id": sid})
        sid = headers.get("mcp-session-id")
        if not sid or sid not in self.sessions:
            if self.legacy_probe == "rpc32601":
                return self._rpc(rid, error={"code": -32601, "message": "Method not found"})
            if self.legacy_probe == "rpc32000":
                return self._rpc(rid, error={"code": -32000, "message": "Bad Request: Server not initialized"}, status=400)
            return httpx.Response(400, text="Bad Request: Server not initialized")
        if headers.get("mcp-protocol-version") != LEGACY:
            return httpx.Response(400, text="Bad Request: unsupported MCP-Protocol-Version")
        if method == "tools/list":
            self.list_calls += 1
            return self._rpc(rid, {"tools": self.tools})
        if method == "tools/call":
            args = (body.get("params") or {}).get("arguments") or {}
            return self._rpc(rid, {"content": [{"type": "text", "text": f"legacy:{json.dumps(args, sort_keys=True)}"}]})
        return self._rpc(rid, error={"code": -32601, "message": "Method not found"})


@contextmanager
def serve(server: FakeServer):
    mock = httpx.AsyncClient(transport=httpx.MockTransport(server.handler))
    with patch("utils.http.shared_client", return_value=mock):
        yield mock


def make_client(name="t"):
    from mcp_client.client import MCPClient
    return MCPClient(name, "http://mcp.test/mcp", request_interval_ms=0)


# ── Era detection ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_modern_server_no_handshake_and_mirrored_headers():
    srv = FakeServer("modern")
    c = make_client()
    with serve(srv):
        await c.initialize()
        tools = await c.discover_tools()
        res = await c.call_tool("echo", {"text": "hi"})
    assert c.era == "modern" and c.protocol_version == MODERN and c.session_id is None
    assert c.server_info["name"] == "fake-modern" and c.supported_versions == [MODERN]
    assert [t["name"] for t in tools] == ["echo", "execute_sql"]
    assert res["text"] == 'ok:{"text": "hi"}' and res["structured"] == {"echo": {"text": "hi"}} and not res["is_error"]
    methods = [m for m, _h, _b in srv.requests]
    assert methods == ["server/discover", "tools/list", "tools/call"] and "initialize" not in methods
    for method, h, b in srv.requests:
        meta = b["params"]["_meta"]
        assert meta["io.modelcontextprotocol/protocolVersion"] == MODERN
        assert meta["io.modelcontextprotocol/clientInfo"]["name"] == "pantheon"
        # The tasks extension is always declared; elicitation where a person
        # could answer (discover, list) and on a tools/call only when the
        # caller passed an elicitor.
        caps = meta["io.modelcontextprotocol/clientCapabilities"]
        tasks = {"extensions": {"io.modelcontextprotocol/tasks": {}}}
        assert caps == (tasks if method == "tools/call" else {**tasks, "elicitation": {"form": {}, "url": {}}})
        assert h["mcp-protocol-version"] == MODERN and h["mcp-method"] == method
        assert "mcp-session-id" not in h
    assert srv.requests[-1][1]["mcp-name"] == "echo"


@pytest.mark.asyncio
@pytest.mark.parametrize("probe_reply", ["http400", "rpc32601", "rpc32000"])
async def test_legacy_server_falls_back_to_initialize(probe_reply):
    srv = FakeServer("legacy", legacy_probe=probe_reply)
    c = make_client()
    with serve(srv):
        await c.initialize()
        await c.discover_tools()
        res = await c.call_tool("echo", {"text": "yo"})
    assert c.era == "legacy" and c.protocol_version == LEGACY and c.session_id == "sess-1"
    assert c.server_info["name"] == "fake-legacy"
    assert res["text"] == 'legacy:{"text": "yo"}'
    methods = [m for m, _h, _b in srv.requests]
    assert methods == ["server/discover", "initialize", "notifications/initialized", "tools/list", "tools/call"]
    # Legacy requests carry no modern _meta and do carry the session.
    _m, h, b = srv.requests[-1]
    assert "_meta" not in b["params"] and h["mcp-session-id"] == "sess-1" and h["mcp-protocol-version"] == LEGACY
    assert "mcp-method" not in h


@pytest.mark.asyncio
async def test_dual_era_server_is_used_as_modern():
    srv = FakeServer("dual")
    c = make_client()
    with serve(srv):
        await c.initialize()
        await c.discover_tools()
    assert c.era == "modern" and "initialize" not in [m for m, _h, _b in srv.requests]


@pytest.mark.asyncio
async def test_unsupported_version_retries_with_a_listed_one(monkeypatch):
    from mcp_client import client as client_mod
    monkeypatch.setattr(client_mod, "MODERN_PROTOCOL_VERSIONS", ("2099-01-01", MODERN))
    srv = FakeServer("modern", supported=[MODERN])
    c = make_client()
    with serve(srv):
        await c.initialize()
        await c.discover_tools()
    assert c.era == "modern" and c.protocol_version == MODERN
    versions = [b["params"]["_meta"]["io.modelcontextprotocol/protocolVersion"] for _m, _h, b in srv.requests]
    assert versions == ["2099-01-01", MODERN, MODERN]


@pytest.mark.asyncio
async def test_modern_server_that_only_lists_legacy_versions_gets_the_handshake():
    srv = FakeServer("dual", supported=[LEGACY])
    c = make_client()
    with serve(srv):
        await c.initialize()
    assert c.era == "legacy" and c.session_id == "sess-1"


@pytest.mark.asyncio
async def test_no_common_version_is_an_error():
    from mcp_client.client import MCPError
    srv = FakeServer("modern", supported=["2030-01-01"])
    c = make_client()
    with serve(srv):
        with pytest.raises(MCPError) as ei:
            await c.initialize()
    assert ei.value.code == -32022 and "2030-01-01" in str(ei.value)


@pytest.mark.asyncio
async def test_modern_method_not_found_is_an_error_not_a_reinit():
    from mcp_client.client import MCPError
    srv = FakeServer("modern")
    c = make_client()
    with serve(srv):
        await c.initialize()
        with pytest.raises(MCPError) as ei:
            await c._send_jsonrpc("prompts/list")
    assert ei.value.code == -32601
    assert [m for m, _h, _b in srv.requests] == ["server/discover", "prompts/list"]


# ── x-mcp-header ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_param_headers_mirrored_encoded_and_omitted():
    srv = FakeServer("modern")
    c = make_client()
    with serve(srv):
        await c.discover_tools()
        await c.call_tool("execute_sql", {"region": "us-west1", "limit": 42, "dry": False, "query": "SELECT 1"})
        await c.call_tool("execute_sql", {"region": "Hello, 世界", "query": "x"})
        await c.call_tool("execute_sql", {"region": " padded ", "limit": None, "query": "x"})
    calls = [h for m, h, _b in srv.requests if m == "tools/call"]
    assert calls[0]["mcp-param-region"] == "us-west1" and calls[0]["mcp-param-limit"] == "42"
    assert calls[0]["mcp-param-dry"] == "false"
    assert calls[1]["mcp-param-region"] == "=?base64?SGVsbG8sIOS4lueVjA==?="
    assert "mcp-param-limit" not in calls[1] and "mcp-param-dry" not in calls[1]
    assert calls[2]["mcp-param-region"] == "=?base64?IHBhZGRlZCA=?=" and "mcp-param-limit" not in calls[2]
    # The annotation never reaches the model's schema.
    schemas = c.get_openai_tool_schemas()
    sql = next(s for s in schemas if s["function"]["name"] == "mcp_t_execute_sql")
    assert "x-mcp-header" not in json.dumps(sql)


@pytest.mark.asyncio
async def test_invalid_header_annotations_drop_only_that_tool(caplog):
    bad_number = _tool("bad_number", {"n": {"type": "number", "x-mcp-header": "N"}})
    bad_dupe = _tool("bad_dupe", {"a": {"type": "string", "x-mcp-header": "Key"},
                                   "b": {"type": "string", "x-mcp-header": "key"}})
    bad_nested = {"name": "bad_nested", "inputSchema": {"type": "object", "properties": {
        "rows": {"type": "array", "items": {"type": "object", "properties": {
            "id": {"type": "string", "x-mcp-header": "Id"}}}}}}}
    bad_token = _tool("bad_token", {"a": {"type": "string", "x-mcp-header": "Bad Name"}})
    ok_nested = {"name": "ok_nested", "inputSchema": {"type": "object", "properties": {
        "ctx": {"type": "object", "properties": {"tenant": {"type": "string", "x-mcp-header": "Tenant"}}}}}}
    srv = FakeServer("modern", tools=[_tool("echo"), bad_number, bad_dupe, bad_nested, bad_token, ok_nested])
    c = make_client()
    with serve(srv), caplog.at_level("WARNING"):
        tools = await c.discover_tools()
        await c.call_tool("ok_nested", {"ctx": {"tenant": "acme"}})
    assert [t["name"] for t in tools] == ["echo", "ok_nested"]
    assert sum("dropped tool" in r.message for r in caplog.records) == 4
    assert srv.requests[-1][1]["mcp-param-tenant"] == "acme"


@pytest.mark.asyncio
async def test_header_mismatch_relists_tools_and_retries():
    srv = FakeServer("modern", tools=[_tool("execute_sql", {"region": {"type": "string"}, "query": {"type": "string"}})])
    c = make_client()
    with serve(srv):
        await c.discover_tools()
        # The server now wants `region` mirrored; our cached definition does not know.
        srv.tools[0]["inputSchema"]["properties"]["region"]["x-mcp-header"] = "Region"
        res = await c.call_tool("execute_sql", {"region": "eu", "query": "q"})
    assert not res["is_error"] and res["text"].startswith("ok:")
    assert srv.list_calls == 2
    methods = [m for m, _h, _b in srv.requests]
    assert methods == ["server/discover", "tools/list", "tools/call", "tools/list", "tools/call"]
    assert srv.requests[-1][1]["mcp-param-region"] == "eu"


# ── ttlMs, resultType, name encoding ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_tools_list_ttl_triggers_refresh_before_a_call(monkeypatch):
    import mcp_client.client as client_mod
    srv = FakeServer("modern", ttl_ms=5000)
    c = make_client()
    now = [1000.0]
    monkeypatch.setattr(client_mod.time, "monotonic", lambda: now[0])
    with serve(srv):
        await c.discover_tools()
        assert c.tools_ttl_ms == 5000 and not c.tools_stale
        await c.call_tool("echo", {})
        assert srv.list_calls == 1
        now[0] += 6
        assert c.tools_stale
        await c.call_tool("echo", {})
        assert srv.list_calls == 2 and not c.tools_stale
    # Legacy servers have no ttlMs; the list is never considered stale.
    c2 = make_client()
    with serve(FakeServer("legacy")):
        await c2.discover_tools()
    assert c2.tools_ttl_ms == 0 and not c2.tools_stale


@pytest.mark.asyncio
async def test_input_required_state_only_is_retried_once_then_reported():
    srv = FakeServer("modern")
    seen = []

    def needs_state(args, body):
        seen.append(body["params"].get("requestState"))
        if body["params"].get("requestState") == "blob":
            return {"resultType": "complete", "content": [{"type": "text", "text": "done"}]}
        return {"resultType": "input_required", "requestState": "blob"}

    srv.handlers["echo"] = needs_state
    c = make_client()
    with serve(srv):
        res = await c.call_tool("echo", {"text": "x"})
    assert res["text"] == "done" and seen == [None, "blob"]


@pytest.mark.asyncio
async def test_input_required_elicitation_is_reported_not_answered():
    srv = FakeServer("modern")
    calls = []

    def elicit(args, body):
        calls.append(1)
        return {"resultType": "input_required", "requestState": "s1", "inputRequests": {
            "login": {"method": "elicitation/create", "params": {
                "mode": "form", "message": "Please provide your GitHub username",
                "requestedSchema": {"type": "object", "properties": {"name": {"type": "string"}}}}}}}

    srv.handlers["echo"] = elicit
    c = make_client()
    with serve(srv):
        res = await c.call_tool("echo", {})
    assert res["is_error"] and len(calls) == 1
    assert "needs user input" in res["text"] and "GitHub username" in res["text"] and "name" in res["text"]


@pytest.mark.asyncio
async def test_task_result_and_unknown_result_type():
    from mcp_client.client import MCPError
    srv = FakeServer("modern")
    srv.handlers["echo"] = lambda a, b: {"resultType": "task", "taskId": "t-1", "status": "working",
                                         "createdAt": "2026-10-23T00:00:00Z", "lastUpdatedAt": "2026-10-23T00:00:00Z",
                                         "ttlMs": 60000, "pollIntervalMs": 1000}
    c = make_client()
    with serve(srv):
        # A task whose ttl has long passed (tasks extension, test_mcp_tasks.py
        # covers the live paths): the client gives up and tries to cancel it.
        res = await c.call_tool("echo", {})
        assert res["is_error"] and "expire" in res["text"]
        srv.handlers["echo"] = lambda a, b: {"resultType": "banana"}
        with pytest.raises(MCPError):
            await c.call_tool("echo", {})


@pytest.mark.asyncio
async def test_tool_name_needing_encoding_in_mcp_name():
    srv = FakeServer("modern", tools=[_tool("Suche Städte")])
    c = make_client()
    with serve(srv):
        await c.discover_tools()
        res = await c.call_tool("Suche Städte", {})
    assert not res["is_error"]
    assert srv.requests[-1][1]["mcp-name"].startswith("=?base64?")


@pytest.mark.asyncio
async def test_connection_listing_reports_the_era():
    from mcp_client.manager import MCPManager
    mgr = MCPManager()
    mgr._configs = [{"name": "t", "url": "http://mcp.test/mcp", "request_interval_ms": 0},
                    {"name": "old", "url": "http://old.test/mcp", "request_interval_ms": 0}]
    with patch.object(MCPManager, "_save_configs"):
        with serve(FakeServer("modern")):
            r = await mgr.reconnect("t")
        with serve(FakeServer("legacy")):
            await mgr.reconnect("old")
    assert r["status"] == "connected"
    with patch("mcp_client.oauth.load_tokens", return_value=None):
        listing = {e["name"]: e for e in mgr.list_connections()}
    assert listing["t"]["protocol_era"] == "modern" and listing["t"]["protocol_version"] == MODERN
    assert listing["t"]["server"]["name"] == "fake-modern"
    assert listing["old"]["protocol_era"] == "legacy" and listing["old"]["protocol_version"] == LEGACY


# ── LLM schema simplifier ────────────────────────────────────────────────────

def test_simplify_for_llm_inlines_local_refs_and_drops_bookkeeping():
    from mcp_client.schema import simplify_for_llm
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://example.com/s.json",
        "type": "object",
        "properties": {
            "addr": {"$ref": "#/$defs/Address", "description": "where"},
            "legacy": {"$ref": "#/definitions/Old"},
            "remote": {"$ref": "https://evil.example/schema.json"},
            "loop": {"$ref": "#/$defs/Node"},
            "region": {"type": "string", "x-mcp-header": "Region"},
        },
        "$defs": {
            "Address": {"type": "object", "properties": {"city": {"type": "string"}}},
            "Node": {"type": "object", "properties": {"next": {"$ref": "#/$defs/Node"}}},
        },
        "definitions": {"Old": {"type": "integer"}},
    }
    out = simplify_for_llm(schema)
    assert "$schema" not in out and "$id" not in out and "$defs" not in out and "definitions" not in out
    assert out["properties"]["addr"] == {"type": "object", "properties": {"city": {"type": "string"}}, "description": "where"}
    assert out["properties"]["legacy"] == {"type": "integer"}
    assert out["properties"]["remote"] == {}
    assert out["properties"]["loop"]["properties"]["next"] == {}
    assert out["properties"]["region"] == {"type": "string"}
    assert simplify_for_llm(None) == {"type": "object", "properties": {}}
    assert simplify_for_llm({"$ref": "#/nowhere"}) == {"type": "object", "properties": {}}


def test_encode_header_value_rules():
    from mcp_client.schema import encode_header_value as enc
    assert enc("us-west1") == "us-west1" and enc("a b") == "a b"
    assert enc(42) == "42" and enc(True) == "true" and enc(False) == "false" and enc(7.0) == "7"
    assert enc("Hello, 世界") == "=?base64?SGVsbG8sIOS4lueVjA==?="
    assert enc(" padded ") == "=?base64?IHBhZGRlZCA=?="
    assert enc("line1\nline2") == "=?base64?bGluZTEKbGluZTI=?="
    assert enc("=?base64?literal?=") == "=?base64?PT9iYXNlNjQ/bGl0ZXJhbD89?="
    assert enc("") == "=?base64??="


# ── OAuth additions ──────────────────────────────────────────────────────────

def test_application_type_and_issuer_checks():
    from mcp_client import oauth
    assert oauth.application_type_for("http://localhost:8000/api/mcp/oauth/callback") == "native"
    assert oauth.application_type_for("http://127.0.0.1:3000/cb") == "native"
    assert oauth.application_type_for("https://homely.tail752d22.ts.net:18790/api/mcp/oauth/callback") == "web"
    pend = oauth.PendingAuth(name="n", issuer="https://as.example", token_endpoint="t", client_id="c",
                             redirect_uri="r", code_verifier="v", scopes=[], resource="", created_at=0.0)
    oauth.verify_issuer(pend, None)
    oauth.verify_issuer(pend, "https://as.example/")
    with pytest.raises(RuntimeError, match="mix-up"):
        oauth.verify_issuer(pend, "https://other.example")
    pend.iss_required = True
    with pytest.raises(RuntimeError, match="missing"):
        oauth.verify_issuer(pend, None)
    oauth.verify_issuer(pend, "https://as.example")


@pytest.mark.asyncio
async def test_dcr_retries_with_the_other_application_type():
    from mcp_client import oauth
    bodies = []

    def h(request: httpx.Request):
        body = json.loads(request.content)
        bodies.append(body["application_type"])
        if body["application_type"] == "web":
            return httpx.Response(400, json={"error": "invalid_redirect_uri"})
        return httpx.Response(201, json={"client_id": "cid", "token_endpoint_auth_method": "none"})

    meta = oauth.AuthServerMetadata(issuer="https://as.example", authorization_endpoint="a", token_endpoint="t",
                                    registration_endpoint="https://as.example/register")
    real_client = httpx.AsyncClient  # oauth.httpx is this same module; keep the real class
    with patch.object(oauth.httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(h))):
        reg = await oauth.register_client(as_meta=meta, redirect_uri="https://pantheon.example/cb")
    assert reg.client_id == "cid" and bodies == ["web", "native"]


@pytest.mark.asyncio
async def test_start_oauth_reuses_client_for_same_issuer_and_reregisters_on_change():
    from mcp_client import oauth
    from mcp_client.manager import MCPManager
    mgr = MCPManager()
    mgr._configs = [{"name": "n", "url": "https://mcp.example/mcp"}]
    prm = oauth.ResourceMetadata(resource="https://mcp.example/mcp", authorization_servers=["https://as1"],
                                 scopes_supported=["read"])
    as1 = oauth.AuthServerMetadata(issuer="https://as1", authorization_endpoint="https://as1/auth",
                                   token_endpoint="https://as1/token", registration_endpoint="https://as1/reg")
    as2 = oauth.AuthServerMetadata(issuer="https://as2", authorization_endpoint="https://as2/auth",
                                   token_endpoint="https://as2/token", registration_endpoint="https://as2/reg")
    register = AsyncMock(side_effect=[
        oauth.RegisteredClient(client_id="c1", client_secret=None, token_endpoint_auth_method="none"),
        oauth.RegisteredClient(client_id="c2", client_secret=None, token_endpoint_auth_method="none"),
    ])
    with patch.object(MCPManager, "_save_configs"), \
         patch.object(oauth, "probe_challenge", AsyncMock(return_value=oauth.BearerChallenge(resource_metadata="https://mcp.example/.well-known/prm"))), \
         patch.object(oauth, "fetch_resource_metadata", AsyncMock(return_value=prm)), \
         patch.object(oauth, "fetch_auth_server_metadata", AsyncMock(side_effect=[as1, as1, as2])), \
         patch.object(oauth, "register_client", register), \
         patch.object(oauth, "load_client_secret", return_value=None), \
         patch.object(oauth, "delete_tokens") as delete_tokens:
        r1 = await mgr.start_oauth("n", redirect_uri="https://p.example/cb")
        r2 = await mgr.start_oauth("n", redirect_uri="https://p.example/cb")
        r3 = await mgr.start_oauth("n", redirect_uri="https://p.example/cb")
    assert register.await_count == 2
    assert "client_id=c1" in r1["authorize_url"] and "client_id=c1" in r2["authorize_url"]
    assert "client_id=c2" in r3["authorize_url"] and "https://as2/auth" in r3["authorize_url"]
    assert mgr._configs[0]["oauth"]["issuer"] == "https://as2"
    delete_tokens.assert_called_once_with("n")


# ── Local port scanner ───────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("asyncio.open_connection", new_callable=AsyncMock)
@patch("httpx.AsyncClient.post")
async def test_scan_port_recognises_a_modern_server(mock_post, mock_open_connection):
    from api.mcp import _scan_port
    mock_writer = AsyncMock()
    mock_writer.close = MagicMock()
    mock_open_connection.return_value = (AsyncMock(), mock_writer)
    resp = MagicMock()
    resp.status_code = 200
    resp.headers = {"content-type": "application/json"}
    resp.json.return_value = {"jsonrpc": "2.0", "id": 1, "result": {
        "resultType": "complete", "supportedVersions": [MODERN], "capabilities": {},
        "_meta": {"io.modelcontextprotocol/serverInfo": {"name": "Local Modern"}}}}
    mock_post.return_value = resp
    res = await _scan_port(8130)
    assert res["name"] == "Local Modern" and res["protocol_era"] == "modern" and res["protocol_versions"] == [MODERN]
    sent = mock_post.call_args.kwargs
    assert sent["json"]["method"] == "server/discover" and sent["headers"]["Mcp-Method"] == "server/discover"
