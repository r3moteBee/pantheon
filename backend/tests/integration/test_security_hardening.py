"""Regression tests for the batch-1 security fixes (WS auth, host-exec gating,
download/convert path traversal, skill-import names)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from utils.paths import check_project_id, is_within, safe_filename
from utils.document_converter import validate_format


# ── WebSocket auth ───────────────────────────────────────────────────────────

def _ws_app():
    from api.chat import websocket_chat
    app = FastAPI()
    app.websocket("/ws/chat")(websocket_chat)
    return app


def _settings(password=""):
    import tempfile
    from pathlib import Path
    return SimpleNamespace(
        auth_password=password, secret_key="s",
        cors_origins_list=["http://localhost:5173"],
        db_dir=Path(tempfile.mkdtemp()), auth_session_days=30,
    )


def test_ws_rejects_missing_token_when_auth_enabled():
    with patch("api.auth.get_settings", return_value=_settings("pw")):
        client = TestClient(_ws_app())
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/chat") as ws:
                ws.receive_text()


def test_ws_accepts_session_cookie_but_not_query_token():
    from api.auth import SESSION_COOKIE, create_session
    with patch("api.auth.get_settings", return_value=_settings("pw")):
        token = create_session()
        client = TestClient(_ws_app())
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(f"/ws/chat?token={token}") as ws:
                ws.receive_text()
        client.cookies.set(SESSION_COOKIE, token)
        with client.websocket_connect("/ws/chat") as ws:
            ws.close()


def test_ws_rejects_cross_site_origin_even_without_auth():
    with patch("api.auth.get_settings", return_value=_settings("")):
        client = TestClient(_ws_app())
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                "/ws/chat", headers={"origin": "https://evil.example"},
            ) as ws:
                ws.receive_text()


def test_ws_allows_same_host_and_listed_origins():
    from api.auth import origin_is_allowed
    with patch("api.auth.get_settings", return_value=_settings("")):
        assert origin_is_allowed(None, "localhost:8000")
        assert origin_is_allowed("http://localhost:8000", "localhost:8000")
        assert origin_is_allowed("http://localhost:5173", "localhost:8000")
        assert not origin_is_allowed("http://evil.example", "localhost:8000")


# ── Host-exec gating ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("mode,ctx,expected", [
    ("interactive", "interactive", True),
    ("interactive", "background", False),
    ("always", "background", True),
    ("never", "interactive", False),
])
def test_host_exec_allowed_modes(mode, ctx, expected):
    from agent.tools import host_exec_allowed
    with patch("config.get_settings", return_value=SimpleNamespace(agent_host_exec=mode)):
        assert host_exec_allowed(ctx) is expected


class _FakeProvider:
    """Captures the tool list and asks for run_command once."""
    def __init__(self):
        self.tools_seen = None
        self.calls = 0

    async def chat_complete(self, messages, tools=None, **kw):
        self.tools_seen = [t["function"]["name"] for t in (tools or [])]
        self.calls += 1
        if self.calls == 1:
            return {"content": "", "tool_calls": [
                {"id": "t1", "name": "run_command", "args": {"command": "id"}},
            ]}
        return {"content": "done", "tool_calls": []}


@pytest.mark.asyncio
async def test_background_agent_hides_and_blocks_host_exec_tools():
    from agent.core import AgentCore
    from agent.tools import HOST_EXEC_TOOLS
    provider = _FakeProvider()
    agent = AgentCore(provider=provider, host_exec=False)
    events = []
    # The real execute_tool refuses (the gate lives in the dispatcher).
    async for ev in agent.chat("hi", stream=False):
        events.append(ev)
    assert not (set(provider.tools_seen or []) & HOST_EXEC_TOOLS)
    results = [e for e in events if e.get("type") == "tool_result"]
    assert results and "disabled in this context" in results[0]["result"]


@pytest.mark.asyncio
async def test_execute_tool_refuses_host_exec_by_default():
    from agent.tools import execute_tool
    for name in ("run_command", "code_execute", "git_status"):
        res = await execute_tool(name, {"command": "id", "language": "python", "code": "1"}, None)
        assert "disabled in this context" in res, name


# ── Path helpers ─────────────────────────────────────────────────────────────

def test_is_within_rejects_sibling_prefix(tmp_path):
    base = tmp_path / "workspace"
    base.mkdir()
    assert is_within(base / "a.txt", base)
    assert not is_within(tmp_path / "workspace_old" / "a.txt", base)
    assert not is_within(base / ".." / "x", base)


@pytest.mark.parametrize("bad", ["", "..", ".", "a/b", "/etc", "a\\b"])
def test_check_project_id_rejects_traversal(bad):
    with pytest.raises(ValueError):
        check_project_id(bad)


def test_safe_filename_strips_directories():
    assert safe_filename("../../.ssh/authorized_keys") == "authorized_keys"
    assert safe_filename("..") == "download"
    assert safe_filename("") == "download"
    assert safe_filename("report.pdf") == "report.pdf"


@pytest.mark.parametrize("bad", ["x/../../etc", "md/..", "", "a b", "../soul.md"])
def test_validate_format_rejects_paths(bad):
    with pytest.raises(ValueError):
        validate_format(bad)


def test_validate_format_normalizes():
    assert validate_format(".DOCX") == "docx"


# ── download_file ────────────────────────────────────────────────────────────

def _mock_client_factory(headers):
    # safe_http_get builds its own client in utils.net; tests hit fake
    # hosts, so the DNS-based SSRF check is bypassed via _private_fetch_allowed.
    real = httpx.AsyncClient

    def handler(request):
        return httpx.Response(200, content=b"payload", headers=headers)

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)
    return factory


@pytest.mark.asyncio
async def test_download_file_content_disposition_traversal_contained(tmp_path):
    from agent.tools import execute_tool
    ws = tmp_path / "workspace"
    ws.mkdir()
    headers = {"content-disposition": 'attachment; filename="../../../evil.sh"'}
    with patch("agent.tools._get_workspace_base", return_value=ws.resolve()), \
         patch("utils.net.httpx.AsyncClient", _mock_client_factory(headers)), \
         patch("utils.net._private_fetch_allowed", return_value=True):
        res = await execute_tool(
            "download_file", {"url": "https://x.test/dl", "path": "docs"}, None,
        )
    assert "Downloaded" in res, res
    assert (ws / "docs" / "evil.sh").read_bytes() == b"payload"
    assert not (tmp_path / "evil.sh").exists()


@pytest.mark.asyncio
async def test_download_file_honors_filename_arg(tmp_path):
    from agent.tools import execute_tool
    ws = tmp_path / "workspace"
    ws.mkdir()
    with patch("agent.tools._get_workspace_base", return_value=ws.resolve()), \
         patch("utils.net.httpx.AsyncClient", _mock_client_factory({})), \
         patch("utils.net._private_fetch_allowed", return_value=True):
        await execute_tool(
            "download_file",
            {"url": "https://x.test/a", "path": "docs", "filename": "../r.pdf"}, None,
        )
    assert (ws / "docs" / "r.pdf").exists()


@pytest.mark.asyncio
async def test_download_file_rejects_non_http_scheme(tmp_path):
    from agent.tools import execute_tool
    with patch("agent.tools._get_workspace_base", return_value=tmp_path.resolve()):
        res = await execute_tool(
            "download_file", {"url": "file:///etc/passwd", "path": "x.txt"}, None,
        )
    assert "only http(s)" in res


# ── SSRF guard ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000/api/settings",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/",
    "http://[::1]/",
    "http://localhost/",
    "file:///etc/passwd",
])
async def test_check_public_url_blocks_internal(url):
    from utils.net import UnsafeURLError, check_public_url
    with patch("utils.net._private_fetch_allowed", return_value=False):
        with pytest.raises(UnsafeURLError):
            await check_public_url(url)


@pytest.mark.asyncio
async def test_safe_http_get_blocks_redirect_to_internal():
    from utils.net import UnsafeURLError, safe_http_get

    def handler(request):
        return httpx.Response(302, headers={"location": "http://127.0.0.1/secret"})

    checked = []

    async def fake_check(url):
        checked.append(url)
        if "127.0.0.1" in url:
            raise UnsafeURLError("private")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with patch("utils.net.check_public_url", fake_check):
            with pytest.raises(UnsafeURLError):
                await safe_http_get("https://public.test/start", client=client)
    assert checked == ["https://public.test/start", "http://127.0.0.1/secret"]


# ── DNS rebinding / CSRF ─────────────────────────────────────────────────────

@pytest.mark.parametrize("host,ok", [
    ("localhost:8000", True), ("127.0.0.1:8000", True), ("[::1]:8000", True),
    ("192.168.1.20:8000", True), ("pantheon.local", True), ("testserver", True),
    ("attacker.example.com", False), ("attacker.example.com:8000", False),
])
def test_host_is_allowed(host, ok):
    from api.auth import host_is_allowed
    with patch("api.auth.get_settings", return_value=SimpleNamespace(allowed_hosts="")):
        assert host_is_allowed(host) is ok


def test_host_is_allowed_honors_allowed_hosts():
    from api.auth import host_is_allowed
    with patch("api.auth.get_settings", return_value=SimpleNamespace(allowed_hosts="pantheon.example.com")):
        assert host_is_allowed("pantheon.example.com")


def test_cross_origin_post_blocked():
    from main import app
    client = TestClient(app)
    r = client.post("/api/auth/login", json={"password": "x"},
                    headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_login_rate_limited():
    import api.auth as auth
    from main import app
    auth._failures.clear()
    client = TestClient(app)
    with patch("api.auth.get_settings", return_value=_settings("right")):
        codes = [client.post("/api/auth/login", json={"password": "wrong"}).status_code
                 for _ in range(auth._MAX_FAILURES + 1)]
    auth._failures.clear()
    assert codes[:auth._MAX_FAILURES] == [401] * auth._MAX_FAILURES
    assert codes[-1] == 429


# ── skip_review ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_background_create_task_cannot_skip_review():
    from agent.tools import execute_tool
    from unittest.mock import AsyncMock
    with patch("tasks.scheduler.schedule_agent_task", new_callable=AsyncMock,
               return_value="abcd1234") as sched:
        res = await execute_tool("create_task", {
            "name": "x", "description": "d", "schedule": "now",
            "plan": "1. do it", "skip_review": True,
        }, None, interactive=False)
    assert sched.await_args.kwargs["plan_status"] == "proposed"
    assert "review skipped" not in res


# ── SSRF: DNS pinning ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_safe_http_get_connects_to_validated_ip_with_original_host():
    """The request goes to the IP check_public_url validated (no second DNS
    lookup that could be rebound), with the real hostname in Host."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from utils import net

    seen = {}

    class H(BaseHTTPRequestHandler):
        def do_GET(self):
            seen["host"] = self.headers.get("Host")
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        async def fake_check(url):
            return ["127.0.0.1"]
        async with httpx.AsyncClient(trust_env=False) as client:
            with patch("utils.net.check_public_url", fake_check), \
                 patch("utils.net._proxied", return_value=False):
                # "rebind.example" doesn't resolve at all — success proves
                # we connected to the pinned IP without a second lookup.
                r = await net.safe_http_get(f"http://rebind.example:{port}/x", client=client)
        assert r.status_code == 200
        assert seen["host"] == f"rebind.example:{port}"
    finally:
        srv.shutdown()


def test_pinned_args_https_sets_sni():
    from utils.net import _pinned_request_args
    url, headers, ext = _pinned_request_args("https://api.example.com/p?q=1", "93.184.216.34")
    assert url == "https://93.184.216.34/p?q=1"
    assert headers == {"Host": "api.example.com"}
    assert ext == {"sni_hostname": "api.example.com"}
    url6, _, _ = _pinned_request_args("http://h.example:8080/", "2606:2800::1")
    assert url6 == "http://[2606:2800::1]:8080/"
