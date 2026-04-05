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


class UpdateConnectionRequest(BaseModel):
    url: str | None = None
    api_key: str | None = None
    headers: dict[str, str] | None = None
    enabled: bool | None = None


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
