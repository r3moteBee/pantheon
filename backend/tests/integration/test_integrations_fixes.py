"""Batch-5 regressions: MCP OAuth/session, financial growth, conversions,
project import scoping, skill-import naming."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))


# ── OAuth token getter ───────────────────────────────────────────────────────

class _TokenStore:
    def __init__(self, tokens):
        self.tokens = dict(tokens)
        self.refresh_calls = 0
        self.flagged = None

    def load(self, name):
        return dict(self.tokens) if self.tokens else None

    def save(self, name, raw):
        self.tokens = {"access_token": raw["access_token"], "refresh_token": "r2", "expires_at": 9e12}
        return dict(self.tokens)


def _patch_oauth(ts: _TokenStore, refresh_impl):
    from mcp_client import oauth as oauth_mod
    return patch.multiple(
        oauth_mod,
        load_tokens=ts.load,
        save_tokens=ts.save,
        is_token_fresh=lambda t: t.get("expires_at", 0) > 1e12,
        refresh_tokens=refresh_impl,
        load_client_secret=lambda n: None,
        mark_refresh_failed=lambda n, e: setattr(ts, "flagged", e),
    )


@pytest.mark.asyncio
async def test_concurrent_forced_refresh_refreshes_once():
    from mcp_client.manager import _make_oauth_token_getter
    ts = _TokenStore({"access_token": "t1", "refresh_token": "r1", "expires_at": 9e12})

    async def refresh(**kw):
        ts.refresh_calls += 1
        await asyncio.sleep(0.05)
        return {"access_token": f"t{ts.refresh_calls + 1}"}

    getter = _make_oauth_token_getter(f"conn-{os.urandom(3).hex()}", {"token_endpoint": "x", "client_id": "c"})
    with _patch_oauth(ts, refresh):
        results = await asyncio.gather(*[
            getter(force_refresh=True, failed_token="t1") for _ in range(5)
        ])
    assert ts.refresh_calls == 1
    assert set(results) == {"t2"}


@pytest.mark.asyncio
async def test_rejected_refresh_marks_needs_auth_but_network_error_does_not():
    from mcp_client.manager import _make_oauth_token_getter
    cfg = {"token_endpoint": "x", "client_id": "c"}

    ts = _TokenStore({"access_token": "t1", "refresh_token": "r1", "expires_at": 0})

    async def rejected(**kw):
        raise RuntimeError('Token refresh failed: HTTP 400 — {"error":"invalid_grant"}')
    with _patch_oauth(ts, rejected):
        await _make_oauth_token_getter("a", cfg)()
    assert ts.flagged and "invalid_grant" in ts.flagged

    ts2 = _TokenStore({"access_token": "t1", "refresh_token": "r1", "expires_at": 0})

    async def offline(**kw):
        raise httpx.ConnectError("unreachable")
    with _patch_oauth(ts2, offline):
        await _make_oauth_token_getter("b", cfg)()
    assert ts2.flagged is None


# ── MCP session expiry ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_stale_session_404_reinitializes_and_retries():
    from mcp_client.client import MCPClient
    calls = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        method = body.get("method")
        sid = request.headers.get("mcp-session-id")
        calls.append((method, sid))
        if method == "initialize":
            return httpx.Response(200, headers={"mcp-session-id": "new"},
                                  json={"jsonrpc": "2.0", "id": body["id"],
                                        "result": {"protocolVersion": "2025-11-25"}})
        if "id" not in body:  # notification
            return httpx.Response(202)
        if sid == "old":
            return httpx.Response(404, text="session not found")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {"tools": []}})

    mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    c = MCPClient("t", "http://mcp.test/mcp", request_interval_ms=0)
    c.session_id = "old"
    c._initialized = True
    with patch("utils.http.shared_client", return_value=mock_client):
        result = await c._send_jsonrpc("tools/list")
    await mock_client.aclose()
    assert result == {"tools": []}
    assert c.session_id == "new"
    assert ("initialize", None) in calls


# ── Financial growth ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("httpx.AsyncClient.get")
async def test_growth_analysis_computes_yoy(mock_get):
    from agent.tools import execute_tool
    tickers = MagicMock(status_code=200)
    tickers.json.return_value = {"0": {"cik_str": 1, "ticker": "ACME", "title": "Acme"}}

    def entry(end, val, filed, start=None):
        e = {"form": "10-K", "fp": "FY", "fy": 2024, "end": end, "val": val, "filed": filed}
        if start:
            e["start"] = start
        return e

    facts = MagicMock(status_code=200)
    facts.json.return_value = {"entityName": "Acme", "facts": {"us-gaap": {
        # One 10-K (fy=2024) that also carries the prior-year comparative —
        # the old code picked whichever came first for both years.
        "Revenues": {"units": {"USD": [
            entry("2023-12-31", 100.0, "2025-02-01", "2023-01-01"),
            entry("2024-12-31", 150.0, "2025-02-01", "2024-01-01"),
        ]}},
    }}}
    mock_get.side_effect = [tickers, facts]
    res = await execute_tool("analyze_company_financials",
                             {"ticker": "ACME", "analysis_type": "growth", "years": 2}, MagicMock())
    assert "+50.00%" in res, res


# ── Conversions ──────────────────────────────────────────────────────────────

def test_save_converted_artifact_prefixes_and_upserts(tmp_path):
    from artifacts.conversions import save_converted_artifact
    from artifacts.store import ArtifactStore, project_slug

    store = ArtifactStore(db_path=str(tmp_path / "a.db"))
    base = tmp_path / "ws"
    (base / "docs").mkdir(parents=True)
    out = base / "docs" / "r.md"
    out.write_text("v1")
    with patch("artifacts.store.get_store", return_value=store), \
         patch("artifacts.embedder.schedule_embed"):
        a1 = save_converted_artifact(project_id="p1", target_path=out, base=base,
                                     source_rel="docs/r.docx", fmt="md")
        out.write_text("v2")
        a2 = save_converted_artifact(project_id="p1", target_path=out, base=base,
                                     source_rel="docs/r.docx", fmt="md")
    assert a1["path"] == f"{project_slug('p1')}/docs/r.md"
    assert a1["id"] == a2["id"]
    assert store.get(a2["id"])["content"] == "v2"


# ── Project import scoping ───────────────────────────────────────────────────

def test_import_upsert_cannot_overwrite_other_project_rows():
    import re
    src = open(os.path.join(os.path.dirname(__file__), "..", "..", "api", "project_import.py")).read()
    assert "INSERT OR REPLACE" not in src
    sql = re.search(r'(INSERT INTO messages\s*\(.*?WHERE messages\.project_id = excluded\.project_id)', src, re.S).group(1)
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE messages (id TEXT PRIMARY KEY, conversation_id TEXT, project_id TEXT, "
                 "session_id TEXT, role TEXT, content TEXT, timestamp TEXT, metadata TEXT)")
    conn.execute("INSERT INTO messages VALUES ('m1', NULL, 'victim', 's', 'user', 'secret', 't', '{}')")
    row = dict(id="m1", conversation_id=None, project_id="attacker", session_id="s",
               role="user", content="overwritten", timestamp="t", metadata="{}")
    conn.execute(sql, row)
    assert conn.execute("SELECT project_id, content FROM messages").fetchone() == ("victim", "secret")
    row["project_id"] = "victim"; row["content"] = "reimport"
    conn.execute(sql, row)
    assert conn.execute("SELECT content FROM messages").fetchone()[0] == "reimport"


# ── remember ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_remember_uses_agent_memory_manager():
    from agent.tools import execute_tool
    mgr = MagicMock()
    mgr.remember = AsyncMock(return_value="ref-1")
    res = await execute_tool("remember", {"content": "x", "tier": "working"}, mgr,
                             project_id="p1", session_id="s1")
    mgr.remember.assert_awaited_once()
    assert "ref-1" in res
