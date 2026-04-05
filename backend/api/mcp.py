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
