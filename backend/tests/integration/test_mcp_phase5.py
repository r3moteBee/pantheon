"""MCP client follow-ups after phases 1-4: paginated lists, scope selection
and step-up (403 insufficient_scope), well-known PRM fallback, and
elicitation on a legacy (2025-11-25) server's tools/call response stream."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from mcp_client import oauth  # noqa: E402
from mcp_client.client import MCPError  # noqa: E402
from tests.integration.test_mcp_dual_era import LEGACY, FakeServer, _tool, make_client, serve  # noqa: E402


# ── Pagination ───────────────────────────────────────────────────────────────

class PagedServer(FakeServer):
    """tools/list in pages of two, modern or legacy."""

    def __init__(self, mode="modern", n=5, **kw):
        super().__init__(mode, tools=[_tool(f"t{i}") for i in range(n)], **kw)
        self.cursors: list = []

    def _page(self, body):
        cursor = (body.get("params") or {}).get("cursor")
        self.cursors.append(cursor)
        start = int(cursor) if cursor else 0
        page = self.tools[start:start + 2]
        res = {"tools": page}
        if start + 2 < len(self.tools):
            res["nextCursor"] = str(start + 2)
        return res

    def _modern(self, method, headers, body, meta, rid):
        if method == "tools/list":
            self.list_calls += 1
            return self._rpc(rid, {"resultType": "complete", "ttlMs": 5000, **self._page(body)})
        return super()._modern(method, headers, body, meta, rid)

    def _legacy(self, method, headers, body, rid):
        if method == "tools/list" and headers.get("mcp-session-id") in self.sessions:
            return self._rpc(rid, self._page(body))
        return super()._legacy(method, headers, body, rid)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["modern", "legacy"])
async def test_tools_list_follows_next_cursor(mode):
    srv = PagedServer(mode)
    c = make_client()
    with serve(srv):
        tools = await c.discover_tools()
    assert [t["name"] for t in tools] == ["t0", "t1", "t2", "t3", "t4"]
    assert srv.cursors == [None, "2", "4"]
    if mode == "modern":
        assert c.tools_ttl_ms == 5000


@pytest.mark.asyncio
async def test_pagination_stops_on_a_repeating_cursor():
    srv = PagedServer("modern", n=4)
    srv._page = lambda body: {"tools": srv.tools[:2], "nextCursor": "loop"}
    c = make_client()
    with serve(srv):
        tools = await c.discover_tools()
    assert len(tools) == 4 and srv.list_calls == 2   # first page + one with the cursor, then it repeats


# ── Scopes ───────────────────────────────────────────────────────────────────

def test_bearer_challenge_parsing():
    ch = oauth.parse_bearer_challenge(
        'Bearer realm="x", resource_metadata="https://mcp.example/.well-known/oauth-protected-resource", scope="files:read files:write"')
    assert ch.resource_metadata == "https://mcp.example/.well-known/oauth-protected-resource"
    assert ch.scopes == ["files:read", "files:write"] and ch.error is None
    ch = oauth.parse_bearer_challenge('Bearer error="insufficient_scope", scope="files:write", error_description="Write needed"')
    assert ch.error == "insufficient_scope" and ch.scopes == ["files:write"] and ch.error_description == "Write needed"
    assert oauth.parse_bearer_challenge("Basic realm=x").scopes == []
    assert oauth.parse_bearer_challenge("").resource_metadata is None
    # Unquoted values and the legacy helper still work.
    assert oauth.parse_www_authenticate("Bearer resource_metadata=https://a.example/prm") == "https://a.example/prm"


def test_scope_selection_order_and_union():
    assert oauth.select_scopes(challenge=["a"], prm=["b"], as_meta=["c"]) == ["a"]
    assert oauth.select_scopes(challenge=[], prm=["b"], as_meta=["c"]) == ["b"]
    assert oauth.select_scopes(challenge=None, prm=None, as_meta=["c"]) == ["c"]
    assert oauth.select_scopes(challenge=None, prm=None, as_meta=None) == []
    assert oauth.select_scopes(challenge=["a"], prm=None, as_meta=None, required=["w", "a"]) == ["w", "a"]


def test_well_known_prm_urls():
    assert oauth.well_known_prm_urls("https://mcp.example/public/mcp") == [
        "https://mcp.example/.well-known/oauth-protected-resource/public/mcp",
        "https://mcp.example/.well-known/oauth-protected-resource"]
    assert oauth.well_known_prm_urls("https://mcp.example/") == ["https://mcp.example/.well-known/oauth-protected-resource"]


@pytest.mark.asyncio
async def test_start_oauth_uses_challenge_scopes_and_well_known_prm():
    from mcp_client.manager import MCPManager
    cfg = {"name": "n", "url": "https://mcp.example/public/mcp", "auth_type": "oauth2",
           "oauth": {"required_scopes": ["files:write"]}}
    mgr = MCPManager.__new__(MCPManager)
    mgr._configs, mgr._clients, mgr._save_configs = [cfg], {}, lambda: None
    prm = oauth.ResourceMetadata(resource="https://mcp.example/public/mcp", authorization_servers=["https://as.example"],
                                 scopes_supported=["files:read", "profile"])
    as_meta = oauth.AuthServerMetadata(issuer="https://as.example", authorization_endpoint="https://as.example/auth",
                                       token_endpoint="https://as.example/token", registration_endpoint="https://as.example/reg",
                                       scopes_supported=["everything"])
    fetched: list[str] = []

    async def fetch_prm(url):
        fetched.append(url)
        if url.endswith("/oauth-protected-resource/public/mcp"):
            raise RuntimeError("404")
        return prm
    dcr = AsyncMock(return_value=oauth.RegisteredClient(client_id="c", client_secret=None, token_endpoint_auth_method="none"))
    with patch.object(oauth, "probe_challenge", AsyncMock(return_value=oauth.BearerChallenge(scopes=["files:read"]))), \
         patch.object(oauth, "fetch_resource_metadata", fetch_prm), \
         patch.object(oauth, "fetch_auth_server_metadata", AsyncMock(return_value=as_meta)), \
         patch.object(oauth, "register_client", dcr), \
         patch("config.get_settings") as gs:
        gs.return_value.public_base_url = ""
        gs.return_value.mcp_cimd_enabled = True
        gs.return_value.mcp_client_id_metadata_url = ""
        r = await mgr.start_oauth("n", redirect_uri="https://p.example/cb")
    # No resource_metadata in the challenge: the path-qualified well-known URL, then the root one.
    assert fetched == ["https://mcp.example/.well-known/oauth-protected-resource/public/mcp",
                       "https://mcp.example/.well-known/oauth-protected-resource"]
    # Step-up scopes kept, challenge scopes authoritative over the PRM's.
    assert cfg["oauth"]["scopes"] == ["files:write", "files:read"] and "scope=files%3Awrite+files%3Aread" in r["authorize_url"]
    assert cfg["oauth"]["required_scopes"] == ["files:write"]


@pytest.mark.asyncio
async def test_403_insufficient_scope_flags_the_connection_for_step_up():
    from mcp_client.manager import MCPManager
    cfg = {"name": "n", "url": "http://mcp.test/mcp", "auth_type": "oauth2", "request_interval_ms": 0,
           "oauth": {"token_endpoint": "t", "client_id": "c", "scopes": ["files:read"]}}
    mgr = MCPManager.__new__(MCPManager)
    mgr._configs, mgr._clients, mgr._save_configs = [cfg], {}, lambda: None
    srv = FakeServer("modern")
    real_modern = srv._modern

    def forbid(method, headers, body, meta, rid):
        if method == "tools/call":
            return httpx.Response(403, headers={"WWW-Authenticate":
                                                'Bearer error="insufficient_scope", scope="files:write", error_description="Write needed"'})
        return real_modern(method, headers, body, meta, rid)
    srv._modern = forbid
    marked = []
    with patch.object(oauth, "load_tokens", return_value={"access_token": "tok", "expires_at": 0}), \
         patch.object(oauth, "mark_refresh_failed", lambda n, e: marked.append(e)), \
         serve(srv):
        client = mgr._create_client(cfg)
        with pytest.raises(MCPError, match="files:write"):
            await client.call_tool("echo", {})
    assert cfg["oauth"]["required_scopes"] == ["files:read", "files:write"]
    assert cfg["oauth"]["step_up"] == {"scopes": ["files:write"], "reason": "Write needed"}
    assert marked == ["insufficient_scope: files:write"]
    listed = mgr.list_connections()[0]["oauth"]
    assert listed["step_up"]["scopes"] == ["files:write"]


# ── Legacy elicitation on the response stream ────────────────────────────────

class _Stream(httpx.AsyncByteStream):
    """An SSE body that emits a server request, waits until the client has
    answered it (the server sets ``answered``), then emits the result."""

    def __init__(self, first: dict, answered: asyncio.Event, final: dict):
        self.first, self.answered, self.final = first, answered, final

    async def __aiter__(self):
        yield f"event: message\ndata: {json.dumps(self.first)}\n\n".encode()
        await asyncio.wait_for(self.answered.wait(), timeout=5)
        yield f"data: {json.dumps(self.final)}\n\n".encode()


class ElicitingLegacyServer(FakeServer):
    def __init__(self, request_params, **kw):
        super().__init__("legacy", **kw)
        self.request_params = request_params
        self.answered = asyncio.Event()
        self.answers: list[dict] = []

    def _legacy(self, method, headers, body, rid):
        if headers.get("mcp-session-id") in self.sessions:
            if method == "tools/call":
                srv_req = {"jsonrpc": "2.0", "id": "srv-1", "method": "elicitation/create", "params": self.request_params}
                final = {"jsonrpc": "2.0", "id": rid, "result": {"content": [{"type": "text", "text": "legacy done"}]}}
                return httpx.Response(200, stream=_Stream(srv_req, self.answered, final),
                                      headers={"content-type": "text/event-stream"})
            if not method and body.get("id") == "srv-1":   # the client's answer to our request
                self.answers.append(body)
                self.answered.set()
                return httpx.Response(202)
        return super()._legacy(method, headers, body, rid)


FORM = {"mode": "form", "message": "Your name?", "requestedSchema": {
    "type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}


@pytest.mark.asyncio
async def test_legacy_server_elicitation_is_answered_on_the_stream():
    srv = ElicitingLegacyServer(FORM)
    prompts = []

    async def elicit(prompt):
        prompts.append(prompt)
        return {"elicitation": {"action": "accept", "content": {"name": "Mona"}}}

    c = make_client()
    with serve(srv):
        await c.initialize()
        res = await c.call_tool("echo", {"text": "hi"}, elicit=elicit)
    # The handshake declared the capability, the request was shown, the
    # answer went back as a JSON-RPC response with the session id, and the
    # model learns what the user did - names only.
    init = [b for m, _h, b in srv.requests if m == "initialize"][0]
    assert init["params"]["capabilities"] == {"elicitation": {"form": {}, "url": {}}}
    assert len(prompts) == 1 and prompts[0].tool == "echo" and prompts[0].requests[0].message == "Your name?"
    assert srv.answers == [{"jsonrpc": "2.0", "id": "srv-1", "result": {"action": "accept", "content": {"name": "Mona"}}}]
    answer_headers = [h for m, h, b in srv.requests if b.get("id") == "srv-1" and "method" not in b][0]
    assert answer_headers.get("mcp-session-id") in srv.sessions and answer_headers.get("mcp-protocol-version") == LEGACY
    assert res["text"] == "[The user filled in the server's form (name).]\nlegacy done" and not res["is_error"]


@pytest.mark.asyncio
async def test_legacy_elicitation_without_elicitor_is_cancelled():
    srv = ElicitingLegacyServer(FORM)
    c = make_client()
    with serve(srv):
        res = await c.call_tool("echo", {})
    assert srv.answers[0]["result"] == {"action": "cancel"}
    assert "forms can't be shown here" in res["text"] and res["text"].endswith("legacy done")


@pytest.mark.asyncio
async def test_legacy_stream_answers_ping_and_refuses_unknown_requests():
    srv = ElicitingLegacyServer(FORM)
    pings = {"jsonrpc": "2.0", "id": "p1", "method": "ping"}
    unknown = {"jsonrpc": "2.0", "id": "r1", "method": "roots/list"}
    final = {"jsonrpc": "2.0", "id": None, "result": {"content": [{"type": "text", "text": "ok"}]}}

    class Multi(httpx.AsyncByteStream):
        def __init__(self, rid):
            self.rid = rid

        async def __aiter__(self):
            yield f"data: {json.dumps(pings)}\n\n".encode()
            yield f"data: {json.dumps(unknown)}\n\n".encode()
            yield f"data: {json.dumps({'jsonrpc': '2.0', 'method': 'notifications/progress', 'params': {}})}\n\n".encode()
            await asyncio.sleep(0.05)
            yield f"data: {json.dumps({**final, 'id': self.rid})}\n\n".encode()
    replies = []
    orig = srv._legacy

    def legacy(method, headers, body, rid):
        if headers.get("mcp-session-id") in srv.sessions and method == "tools/call":
            return httpx.Response(200, stream=Multi(rid), headers={"content-type": "text/event-stream"})
        if not method and body.get("id") in ("p1", "r1"):
            replies.append(body)
            return httpx.Response(202)
        return orig(method, headers, body, rid)
    srv._legacy = legacy
    c = make_client()
    with serve(srv):
        res = await c.call_tool("echo", {})
    assert res["text"] == "ok"
    assert replies[0] == {"jsonrpc": "2.0", "id": "p1", "result": {}}
    assert replies[1]["id"] == "r1" and replies[1]["error"]["code"] == -32601
