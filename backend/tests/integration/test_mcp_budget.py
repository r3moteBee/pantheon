"""Per-connection MCP call budgets (the generalised Tavily credit tracker)."""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))


@pytest.fixture
def db(monkeypatch, tmp_path):
    import config
    real = config.get_settings()
    fake = real.model_copy(update={"data_dir": tmp_path / "data"})
    monkeypatch.setattr(config, "get_settings", lambda: fake)
    from mcp_client import budget
    budget._ready.clear()
    return fake


def test_costs_presets_and_overrides():
    from mcp_client import budget
    tav = {"name": "tavily", "url": "https://mcp.tavily.com/mcp/"}
    assert budget.preset_for(tav) == "tavily" and budget.unit(tav) == "credits"
    assert budget.cost(tav, "tavily-search", {"search_depth": "advanced"}) == 2
    assert budget.cost(tav, "tavily_search", {}) == 1
    assert budget.cost(tav, "tavily-crawl", {"extract_depth": "advanced"}) == 3
    assert budget.cost(tav, "something-new", {}) == 1
    plain = {"name": "yt", "url": "https://yt.example/mcp"}
    assert budget.preset_for(plain) is None and budget.unit(plain) == "calls"
    assert budget.cost(plain, "fetch_transcript") == 1
    custom = {**plain, "budget": {"costs": {"fetch_transcript": 5,
                                            "search": {"default": 1, "by_arg": {"mode": {"deep": 4}}}}}}
    assert budget.cost(custom, "fetch_transcript") == 5
    assert budget.cost(custom, "search", {"mode": "Deep"}) == 4


def test_limits_usage_warning_and_reset(db):
    from mcp_client import budget
    cfg = {"name": "yt", "url": "u", "budget": {"daily": 5, "monthly": 0}}
    assert budget.check(cfg) is None
    for _ in range(4):
        budget.record("yt", "fetch_transcript", 1)
    s = budget.status(cfg)
    assert s["daily"] == {"used": 4.0, "limit": 5, "remaining": 1.0}
    assert s["monthly"]["limit"] == 0 and s["monthly"]["remaining"] is None
    assert s["by_tool"] == [{"tool": "fetch_transcript", "used": 4.0, "calls": 4}]
    assert "4/5" in budget.warning(cfg)
    assert budget.check(cfg) is None                 # the 5th fits
    assert "Daily budget" in budget.check(cfg, 2)    # a 2-credit call doesn't
    budget.record("yt", "fetch_transcript", 1)
    assert budget.check(cfg) is not None
    budget.reset("yt", "daily")
    assert budget.status(cfg)["daily"]["used"] == 0
    budget.record("other", "x", 3)
    budget.forget("yt")
    assert budget.used("other")["daily"] == 3


class _Client:
    def __init__(self, name):
        self.name = name
        self.tools = [{"name": "tavily-search"}, {"name": "tavily-extract"}]
        self.call_tool = AsyncMock(return_value={"text": "results", "structured": None, "is_error": False})


def _manager(cfg):
    from mcp_client.manager import MCPManager
    m = MCPManager.__new__(MCPManager)
    client = _Client(cfg["name"])
    m._clients = {cfg["name"]: client}
    m._configs = [cfg]
    return m, client


@pytest.mark.asyncio
async def test_manager_meters_blocks_and_falls_back(db):
    from mcp_client import budget
    cfg = {"name": "tavily", "url": "https://mcp.tavily.com/mcp/", "budget": {"daily": 3}}
    m, client = _manager(cfg)
    out = await m.execute_tool("mcp_tavily_tavily-search", {"query": "gpus", "search_depth": "advanced"})
    assert out == "results"
    assert budget.status(cfg)["daily"]["used"] == 2
    # Next advanced search (2 credits) would exceed 3 → built-in web search.
    with patch("agent.tools.web._web_search", AsyncMock(return_value="ddg hits")):
        out = await m.execute_tool("mcp_tavily_tavily-search", {"query": "gpus", "search_depth": "advanced"})
    assert "Daily budget for 'tavily' reached" in out and "ddg hits" in out
    # Non-search tools are refused outright.
    out = await m.execute_tool("mcp_tavily_tavily-extract", {"urls": ["a"], "extract_depth": "advanced"})
    assert "blocked to stay within budget" in out
    assert client.call_tool.await_count == 1
    # Adapters get an exception, not prose.
    with pytest.raises(RuntimeError, match="budget"):
        await m.call_tool_raw("mcp_tavily_tavily-extract", {"extract_depth": "advanced"})


@pytest.mark.asyncio
async def test_disconnected_search_tool_falls_back(db):
    m, _ = _manager({"name": "yt", "url": "u"})
    with patch("agent.tools.web._web_search", AsyncMock(return_value="ddg hits")):
        out = await m.execute_tool("mcp_gone_web_search", {"query": "x"})
    assert "not connected" in out and "ddg hits" in out
    assert await m.execute_tool("mcp_gone_fetch", {}) == "Unknown MCP tool: mcp_gone_fetch"


def test_migration_from_tavily_tracker(db, monkeypatch):
    from mcp_client import budget
    from secrets import vault as _v
    v = _v.SecretsVault(db_path=str(db.data_dir / "vault.db"), master_key="k")
    monkeypatch.setattr(_v, "_vault_instance", v)
    monkeypatch.setattr(_v, "_cache", {})
    v.set_secret("tavily_daily_limit", "50")
    v.set_secret("tavily_monthly_limit", "1000")
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    db.db_dir.mkdir(parents=True, exist_ok=True)
    (db.db_dir / "tavily_usage.json").write_text(json.dumps({
        "daily": {now.strftime("%Y-%m-%d"): 7}, "monthly": {now.strftime("%Y-%m"): 120}}))
    configs = [{"name": "Tavily", "url": "https://mcp.tavily.com/mcp/"}, {"name": "yt", "url": "u"}]
    assert budget.migrate_tavily(configs) is True
    assert configs[0]["budget"] == {"daily": 50, "monthly": 1000} and "budget" not in configs[1]
    s = budget.status(configs[0])
    assert (s["daily"]["used"], s["monthly"]["used"]) == (7, 120)
    assert v.get_secret("tavily_daily_limit") is None
    assert (db.db_dir / "tavily_usage.json.migrated").exists()
    assert budget.migrate_tavily(configs) is False   # once
