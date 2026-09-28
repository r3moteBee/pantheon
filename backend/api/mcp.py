"""MCP Connections API — manage external MCP server integrations."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from mcp_client.manager import get_mcp_manager

logger = logging.getLogger(__name__)
router = APIRouter()


class AddConnectionRequest(BaseModel):
    name: str
    url: str
    api_key: str = ""
    headers: dict[str, str] = {}
    enabled: bool = True
    request_interval_ms: int = 1000  # Throttle between requests (ms). Use ~3000 for dev-tier API keys.
    auth_type: str = "api_key"  # "api_key" or "oauth2"


class UpdateConnectionRequest(BaseModel):
    url: str | None = None
    api_key: str | None = None
    headers: dict[str, str] | None = None
    enabled: bool | None = None
    request_interval_ms: int | None = None


class ToolToggleRequest(BaseModel):
    tool_name: str
    excluded: bool


class BudgetRequest(BaseModel):
    daily: int | None = None      # 0 = unlimited
    monthly: int | None = None
    costs: dict[str, Any] | None = None   # per-tool cost rules, see mcp_client.budget


class BudgetResetRequest(BaseModel):
    period: str  # "daily" | "monthly"


# ── List connections ─────────────────────────────────────────────────────────

@router.get("/mcp/connections")
async def list_connections() -> dict[str, Any]:
    """List all configured MCP connections (no secrets)."""
    mgr = get_mcp_manager()
    connections = mgr.list_connections()
    return {"connections": connections, "count": len(connections)}


# ── Add a connection ─────────────────────────────────────────────────────────

@router.post("/mcp/connections")
async def add_connection(req: AddConnectionRequest) -> dict[str, Any]:
    """Add a new MCP server connection."""
    mgr = get_mcp_manager()
    try:
        result = await mgr.add_connection(
            name=req.name,
            url=req.url,
            api_key=req.api_key,
            headers=req.headers,
            enabled=req.enabled,
            request_interval_ms=req.request_interval_ms,
            auth_type=req.auth_type,
        )
        return result
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Update a connection ─────────────────────────────────────────────────────

@router.put("/mcp/connections/{name}")
async def update_connection(name: str, req: UpdateConnectionRequest) -> dict[str, Any]:
    """Update an existing MCP connection."""
    mgr = get_mcp_manager()
    try:
        return await mgr.update_connection(
            name=name,
            url=req.url,
            api_key=req.api_key,
            headers=req.headers,
            enabled=req.enabled,
            request_interval_ms=req.request_interval_ms,
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Remove a connection ─────────────────────────────────────────────────────

@router.delete("/mcp/connections/{name}")
async def remove_connection(name: str) -> dict[str, str]:
    """Remove an MCP connection."""
    mgr = get_mcp_manager()
    return await mgr.remove_connection(name)


# ── Test a connection ────────────────────────────────────────────────────────

@router.post("/mcp/connections/{name}/test")
async def test_connection(name: str) -> dict[str, Any]:
    """Test an MCP connection (initialize + discover tools)."""
    mgr = get_mcp_manager()
    try:
        return await mgr.test_connection(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Reconnect ───────────────────────────────────────────────────────────────

@router.post("/mcp/connections/{name}/reconnect")
async def reconnect(name: str) -> dict[str, Any]:
    """Force reconnect to an MCP server."""
    mgr = get_mcp_manager()
    try:
        return await mgr.reconnect(name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


# ── Toggle a tool on/off ────────────────────────────────────────────────

@router.put("/mcp/connections/{name}/tools")
async def toggle_tool(name: str, req: ToolToggleRequest) -> dict[str, Any]:
    """Enable or disable a specific tool on a connection.

    Disabled tools are excluded from the agent's tool list but remain
    discoverable (shown as excluded in the tools list).
    """
    mgr = get_mcp_manager()
    cfg = None
    for c in mgr._configs:
        if c["name"] == name:
            cfg = c
            break
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Connection '{name}' not found")

    excluded: list[str] = cfg.get("excluded_tools", [])

    if req.excluded and req.tool_name not in excluded:
        excluded.append(req.tool_name)
    elif not req.excluded and req.tool_name in excluded:
        excluded.remove(req.tool_name)

    cfg["excluded_tools"] = excluded
    mgr._save_configs()

    logger.info("MCP '%s' tool '%s' %s", name, req.tool_name, "excluded" if req.excluded else "enabled")
    return {
        "name": name,
        "tool": req.tool_name,
        "excluded": req.excluded,
        "excluded_tools": excluded,
    }


# ── List discovered tools ───────────────────────────────────────────────────

@router.get("/mcp/tools")
async def list_mcp_tools() -> dict[str, Any]:
    """List all tools discovered from connected MCP servers."""
    mgr = get_mcp_manager()
    tools = mgr.get_discovered_tools()
    return {"tools": tools, "count": len(tools)}


# ── Per-connection budgets ──────────────────────────────────────────────────

async def _remote_usage(cfg: dict[str, Any]) -> dict[str, Any]:
    """What the service itself reports, for presets that expose it
    (Tavily's /usage: key and account totals). Best effort."""
    from mcp_client import budget
    if budget.preset_for(cfg) != "tavily" or not cfg.get("api_key"):
        return {}
    try:
        from utils.http import pooled_client
        async with pooled_client(timeout=10.0) as client:
            resp = await client.get(
                "https://api.tavily.com/usage",
                headers={"Authorization": f"Bearer {cfg['api_key']}"},
            )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.debug("Could not fetch Tavily remote usage: %s", e)
        return {}


def _cfg_or_404(name: str) -> dict[str, Any]:
    cfg = get_mcp_manager()._get_cfg(name)
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Connection '{name}' not found")
    return cfg


@router.get("/mcp/connections/{name}/budget")
async def get_budget(name: str) -> dict[str, Any]:
    """Usage this day/month against the connection's limits (+ the
    service's own numbers where a preset knows how to fetch them)."""
    from mcp_client import budget
    cfg = _cfg_or_404(name)
    return {**budget.status(cfg), "remote": await _remote_usage(cfg)}


@router.put("/mcp/connections/{name}/budget")
async def set_budget(name: str, req: BudgetRequest) -> dict[str, Any]:
    """Set daily/monthly limits (0 = unlimited) and optional per-tool costs."""
    _cfg_or_404(name)
    status = get_mcp_manager().set_budget(name, daily=req.daily, monthly=req.monthly, costs=req.costs)
    logger.info("MCP budget for %s: daily=%s monthly=%s", name, status["daily"]["limit"], status["monthly"]["limit"])
    return status


@router.post("/mcp/connections/{name}/budget/reset")
async def reset_budget(name: str, req: BudgetResetRequest) -> dict[str, Any]:
    """Zero this day's or month's usage counter for the connection."""
    from mcp_client import budget
    if req.period not in ("daily", "monthly"):
        raise HTTPException(status_code=400, detail="period must be daily or monthly")
    cfg = _cfg_or_404(name)
    budget.reset(name, req.period)
    return budget.status(cfg)


# ── Direct Tavily API test (bypasses MCP entirely) ────────────────────────────

@router.post("/mcp/tavily/test-direct")
async def test_tavily_direct() -> dict[str, Any]:
    """Test the Tavily API key directly against Tavily's REST API.

    Bypasses the MCP server completely to isolate whether the issue
    is the API key or the MCP transport layer.
    """
    import httpx

    mgr = get_mcp_manager()
    api_key = ""
    for cfg in mgr._configs:
        if "tavily" in cfg.get("name", "").lower() or "tavily" in cfg.get("url", "").lower():
            api_key = cfg.get("api_key", "")
            break

    if not api_key:
        return {"status": "error", "message": "No Tavily connection found with an API key"}

    results: dict[str, Any] = {
        "api_key_suffix": "…" + api_key[-4:] if len(api_key) > 12 else "set",
    }

    # Test 1: Usage endpoint (lightweight, should always work)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                "https://api.tavily.com/usage",
                headers={"Authorization": f"Bearer {api_key}"},
            )
            results["usage_test"] = {
                "status": resp.status_code,
                "body": resp.json() if resp.status_code == 200 else resp.text[:300],
            }
    except Exception as e:
        results["usage_test"] = {"status": "error", "message": str(e)}

    # Test 2: Minimal search (costs 1 credit)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.tavily.com/search",
                json={"query": "test", "max_results": 1},
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            results["search_test"] = {
                "status": resp.status_code,
                "body_preview": resp.text[:500],
            }
    except Exception as e:
        results["search_test"] = {"status": "error", "message": str(e)}

    return results


# ── Debug ──────────────────────────────────────────────────────────────────────

@router.get("/mcp/debug/{name}")
async def debug_connection(name: str) -> dict[str, Any]:
    """Debug a connection — shows what URL/headers are actually being sent.

    API keys are masked. Use the backend logs for full request/response tracing.
    """
    mgr = get_mcp_manager()
    cfg = None
    for c in mgr._configs:
        if c["name"] == name:
            cfg = c
            break
    if not cfg:
        raise HTTPException(status_code=404, detail=f"Connection '{name}' not found")

    from mcp_client.client import MCPClient
    client = MCPClient(
        name=cfg["name"],
        url=cfg["url"],
        api_key=cfg.get("api_key", ""),
        headers=cfg.get("headers", {}),
    )

    built_url = client._build_url()
    built_headers = client._build_headers()

    # Mask secrets
    api_key = cfg.get("api_key", "")
    mask = "***" + api_key[-4:] if len(api_key) > 12 else "***"
    safe_url = built_url.replace(api_key, mask) if api_key else built_url
    safe_headers = {
        k: (v.replace(api_key, mask) if api_key and api_key in v else v)
        for k, v in built_headers.items()
    }

    active_client = mgr._clients.get(name)

    return {
        "name": name,
        "stored_url": cfg["url"][:50] + "..." if len(cfg["url"]) > 50 else cfg["url"],
        "built_url": safe_url,
        "built_headers": safe_headers,
        "has_api_key": bool(api_key),
        "api_key_suffix": ("…" + api_key[-4:] if len(api_key) > 12 else "set") if api_key else "(none)",
        "session_id": active_client.session_id if active_client else None,
        "is_initialized": active_client._initialized if active_client else False,
        "tools_count": len(active_client.tools) if active_client else 0,
        "url_has_trailing_slash": cfg["url"].endswith("/"),
        "url_contains_apikey_param": "tavilyApiKey" in cfg["url"],
    }


async def _scan_port(port: int) -> dict[str, Any] | None:
    url = f"http://127.0.0.1:{port}"
    try:
        # Fast socket probe
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection("127.0.0.1", port),
            timeout=0.15
        )
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
    except Exception:
        return None

    # Probe via HTTP to extract metadata/SSE
    import httpx
    try:
        async with httpx.AsyncClient(timeout=0.4) as client:
            resp = await client.get(url)
            content_type = resp.headers.get("content-type", "")
            is_sse = "event-stream" in content_type.lower()
            name = f"Local MCP (Port {port})"
            try:
                data = resp.json()
                if isinstance(data, dict) and "name" in data:
                    name = data["name"]
            except Exception:
                pass
            return {
                "port": port,
                "url": url,
                "status": "open",
                "content_type": content_type,
                "is_sse": is_sse,
                "name": name,
            }
    except Exception:
        # Return base open port details if HTTP check fails
        return {
            "port": port,
            "url": url,
            "status": "open",
            "is_sse": False,
            "name": f"Local Connection (Port {port})",
        }


@router.post("/mcp/scan")
async def scan_local_mcp_ports() -> dict[str, Any]:
    """Scan local ports (8120-8145) to discover running HTTP/SSE MCP servers."""
    ports = range(8120, 8146)
    tasks = [_scan_port(p) for p in ports]
    results = await asyncio.gather(*tasks)
    discovered = [r for r in results if r is not None]
    return {"discovered": discovered, "count": len(discovered)}

