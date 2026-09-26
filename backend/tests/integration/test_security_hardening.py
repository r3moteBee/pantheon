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
    return SimpleNamespace(
        auth_password=password, secret_key="s",
        cors_origins_list=["http://localhost:5173"],
    )


def test_ws_rejects_missing_token_when_auth_enabled():
    with patch("api.auth.get_settings", return_value=_settings("pw")):
        client = TestClient(_ws_app())
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/chat") as ws:
                ws.receive_text()


def test_ws_accepts_valid_token():
    from api.auth import compute_token
    token = compute_token("pw", "s")
    with patch("api.auth.get_settings", return_value=_settings("pw")):
        client = TestClient(_ws_app())
        with client.websocket_connect(f"/ws/chat?token={token}") as ws:
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
    with patch("agent.core.execute_tool") as mock_exec:
        async for ev in agent.chat("hi", stream=False):
            events.append(ev)
        mock_exec.assert_not_called()
    assert not (set(provider.tools_seen or []) & HOST_EXEC_TOOLS)
    results = [e for e in events if e.get("type") == "tool_result"]
    assert results and "disabled in this context" in results[0]["result"]


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
         patch("agent.tools.httpx.AsyncClient", _mock_client_factory(headers)):
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
         patch("agent.tools.httpx.AsyncClient", _mock_client_factory({})):
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
