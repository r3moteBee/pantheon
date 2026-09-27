"""Prompt and tool budget: result cap, the one tool-error flag, web_fetch,
and no stale tool names in the prompt."""
from __future__ import annotations

import json
import os
import re
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from agent import tool_results  # noqa: E402


# ── Result cap ───────────────────────────────────────────────────────────────

def test_cap_keeps_short_results_and_trims_long_ones(monkeypatch):
    assert tool_results.cap("short") == "short"
    big = "H" * 30_000 + "T" * 30_000
    out = tool_results.cap(big, 10_000)
    assert out.startswith("H" * 7_500) and out.endswith("T" * 2_500)
    assert "50,000 of 60,000 characters omitted" in out
    monkeypatch.setenv("TOOL_RESULT_MAX_CHARS", "2000")
    assert len(tool_results.cap(big)) < 2400
    monkeypatch.setenv("TOOL_RESULT_MAX_CHARS", "junk")
    assert tool_results.max_chars() == tool_results.DEFAULT_MAX_CHARS


def test_is_error():
    for bad in ("Error: web_fetch got HTTP 404", "MCP tool error: timeout", "Search failed: dns",
                "Tool 'run_command' is disabled in this context",
                "some text\n\n[server reported isError=true on this tool result]"):
        assert tool_results.is_error(bad), bad
    for ok in ("Found 3 notes on error handling", "Saved artifact demo/x.md", "", None):
        assert not tool_results.is_error(ok), ok


class _Prov:
    model = "m"

    def __init__(self):
        self.rounds = [[{"type": "tool_call", "name": "recall", "args": {}, "id": "c1"}],
                       [{"type": "text_delta", "content": "ok"}]]
        self.seen = []

    async def chat(self, messages, tools=None, stream=True):
        self.seen.append(json.loads(json.dumps(messages)))
        for ev in self.rounds.pop(0):
            yield ev
        yield {"type": "done"}


@pytest.mark.asyncio
async def test_agent_caps_what_the_model_sees_but_not_the_ui(monkeypatch):
    from agent.core import AgentCore
    monkeypatch.setenv("TOOL_RESULT_MAX_CHARS", "5000")
    huge = "Error: " + "x" * 50_000
    prov = _Prov()
    agent = AgentCore(provider=prov, memory_manager=None, project_id="p", session_id="s")
    with patch("agent.core.execute_tool", AsyncMock(return_value=huge)), \
         patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.get_all_tool_schemas", return_value=[
             {"type": "function", "function": {"name": "recall", "parameters": {}}}]):
        events = [ev async for ev in agent.chat("go", stream=True)]
    res = [e for e in events if e["type"] == "tool_result"][0]
    assert res["result"] == huge and res["is_error"] is True
    tool_msg = prov.seen[1][-1]
    assert tool_msg["role"] == "tool" and len(tool_msg["content"]) < 5400
    assert "characters omitted" in tool_msg["content"]


@pytest.mark.asyncio
async def test_chat_counts_tool_errors_from_the_flag(monkeypatch, tmp_path):
    from secrets import vault as _v
    monkeypatch.setattr(_v, "_vault_instance", _v.SecretsVault(db_path=str(tmp_path / "vault.db"), master_key="k"))
    monkeypatch.setattr(_v, "_cache", {})
    from llm_config import usage
    monkeypatch.setattr(usage, "get_settings", lambda: SimpleNamespace(db_dir=tmp_path))
    from api.chat import _stream_turn

    class Agent:
        provider = None

        def _get_working_messages(self):
            return []

        async def chat(self, message, stream=True):
            yield {"type": "tool_call", "name": "x", "args": {}}
            yield {"type": "tool_result", "name": "x", "result": "Looks fine", "is_error": True}
            yield {"type": "tool_call", "name": "y", "args": {}}
            yield {"type": "tool_result", "name": "y", "result": "Error: legacy event"}
            yield {"type": "done", "full_response": "ok"}

    async def send(ev):
        pass
    await _stream_turn(Agent(), "hi", "sess-flag", send)
    row = usage.decision_rows(1)[-1]
    assert (row["tool_calls"], row["tool_errors"]) == (2, 2)


# ── web_fetch ────────────────────────────────────────────────────────────────

def _resp(status=200, ctype="text/html", text=""):
    return SimpleNamespace(status_code=status, headers={"content-type": ctype}, text=text)


@pytest.mark.asyncio
async def test_web_fetch_returns_markdown_and_truncates():
    from agent.tools import execute_tool
    html = ("<html><head><title>Launch  Notes</title></head><body><article><h1>Big news</h1>"
            + "<p>" + "Vendor ships a thing. " * 400 + "</p><ul><li>one</li><li>two</li></ul>"
            + "</article></body></html>")
    with patch("utils.net.safe_http_get", AsyncMock(return_value=_resp(text=html))) as g:
        out = await execute_tool("web_fetch", {"url": "https://example.com/a", "max_chars": 1000}, None)
    g.assert_awaited_once()
    assert out.startswith("# Launch Notes\nSource: https://example.com/a")
    assert "Vendor ships a thing" in out and "truncated" in out


@pytest.mark.asyncio
async def test_web_fetch_errors():
    from agent.tools import execute_tool
    assert tool_results.is_error(await execute_tool("web_fetch", {"url": "file:///etc/passwd"}, None))
    with patch("utils.net.safe_http_get", AsyncMock(return_value=_resp(404))):
        assert tool_results.is_error(await execute_tool("web_fetch", {"url": "https://e.com/x"}, None))
    with patch("utils.net.safe_http_get", AsyncMock(side_effect=ValueError("blocked private address"))):
        out = await execute_tool("web_fetch", {"url": "http://10.0.0.1/"}, None)
        assert tool_results.is_error(out) and "blocked" in out
    with patch("utils.net.safe_http_get", AsyncMock(return_value=_resp(ctype="application/pdf"))):
        assert "ingest_source" in await execute_tool("web_fetch", {"url": "https://e.com/a.pdf"}, None)


# ── No stale names ───────────────────────────────────────────────────────────

STALE = ("SubDownload", "data/workspace", "save_chat_as_artifact", "list_skills")


def test_prompt_and_tool_text_have_no_stale_names():
    from agent.prompts import build_system_prompt
    from agent.tools import TOOL_SCHEMAS
    text = build_system_prompt(project_id="default", project_name="Default") + json.dumps(TOOL_SCHEMAS)
    for name in STALE:
        assert name not in text, name


def test_backticked_tool_names_in_agent_guide_exist():
    from pathlib import Path
    from agent.tools import TOOL_SCHEMAS
    names = {t["function"]["name"] for t in TOOL_SCHEMAS}
    guide = (Path(__file__).resolve().parents[2] / "data" / "personality" / "agent.md").read_text()
    for ref in re.findall(r"`([a-z_]+)(?:\(|`)", guide):
        assert ref in names, ref
