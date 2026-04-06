"""MCP Connections API — manage external MCP server integrations."""
from __future__ import annotations

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


class UpdateConnectionRequest(BaseModel):
    url: str | None = None
    api_key: str | None = None
    headers: dict[str, str] | None = None
    enabled: bool | None = None
    request_interval_ms: int | None = None


class ToolToggleRequest(BaseModel):
    tool_name: str
    excluded: bool


class TavilyThresholdRequest(BaseModel):
    daily_limit: int | None = None
    monthly_limit: int | None = None


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


# ── Tavily credit management ────────────────────────────────────────────────

@router.get("/mcp/tavily/usage")
async def get_tavily_usage() -> dict[str, Any]:
    """Get Tavily API credit usage — combines real Tavily API data with local thresholds.

    Queries https://api.tavily.com/usage for actual account/key usage,
    then merges in local threshold settings for the fallback system.
    """
    import httpx
    from mcp_client.tavily_credits import get_tavily_tracker

    tracker = get_tavily_tracker()
    local = tracker.get_usage()
    thresholds = tracker.get_thresholds()

    # Try to fetch real usage from Tavily's API
    remote: dict[str, Any] = {}
    try:
        mgr = get_mcp_manager()
        # Find the Tavily connection's API key
        api_key = ""
        for cfg in mgr._configs:
            if "tavily" in cfg.get("name", "").lower() or "tavily" in cfg.get("url", "").lower():
                api_key = cfg.get("api_key", "")
                break

        if api_key:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.tavily.com/usage",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                resp.raise_for_status()
                remote = resp.json()
    except Exception as e:
        logger.debug("Could not fetch Tavily remote usage: %s", e)

    return {
        "local": local,
        "thresholds": thresholds,
        "remote": remote,
    }


@router.put("/mcp/tavily/thresholds")
async def set_tavily_thresholds(req: TavilyThresholdRequest) -> dict[str, Any]:
    """Set Tavily daily and/or monthly credit thresholds. Set to 0 for unlimited."""
    from mcp_client.tavily_credits import get_tavily_tracker
    tracker = get_tavily_tracker()
    result = tracker.set_thresholds(
        daily_limit=req.daily_limit,
        monthly_limit=req.monthly_limit,
    )
    logger.info("Tavily thresholds updated: %s", result)
    return {"status": "updated", **result}


@router.post("/mcp/tavily/reset-daily")
async def reset_tavily_daily() -> dict[str, str]:
    """Reset today's Tavily credit usage counter."""
    from mcp_client.tavily_credits import get_tavily_tracker
    tracker = get_tavily_tracker()
    tracker.reset_daily()
    return {"status": "daily_usage_reset"}


@router.post("/mcp/tavily/reset-monthly")
async def reset_tavily_monthly() -> dict[str, str]:
    """Reset this month's Tavily credit usage counter."""
    from mcp_client.tavily_credits import get_tavily_tracker
    tracker = get_tavily_tracker()
    tracker.reset_monthly()
    return {"status": "monthly_usage_reset"}


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
        "api_key_prefix": api_key[:8] + "...",
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
    mask = api_key[:6] + "***" + api_key[-4:] if len(api_key) > 10 else "***"
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
        "api_key_prefix": api_key[:8] + "..." if api_key else "(none)",
        "session_id": active_client.session_id if active_client else None,
        "is_initialized": active_client._initialized if active_client else False,
        "tools_count": len(active_client.tools) if active_client else 0,
        "url_has_trailing_slash": cfg["url"].endswith("/"),
        "url_contains_apikey_param": "tavilyApiKey" in cfg["url"],
    }
