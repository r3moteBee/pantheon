"""Messaging gateway API routes."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class MappingUpdate(BaseModel):
    project_id: str


class BulkMappingUpdate(BaseModel):
    mappings: list[dict[str, str]]


class DefaultProjectUpdate(BaseModel):
    project_id: str


# ------------------------------------------------------------------
# Adapter status & control
# ------------------------------------------------------------------


@router.get("/messaging/status")
async def messaging_status() -> dict[str, Any]:
    """Return the status of all registered messaging adapters."""
    from messaging.gateway import get_messaging_gateway

    gw = get_messaging_gateway()
    statuses = await gw.status()
    return {"adapters": [s.model_dump() for s in statuses]}


@router.post("/messaging/{adapter_name}/restart")
async def restart_adapter(adapter_name: str) -> dict[str, str]:
    """Restart a specific messaging adapter."""
    from messaging.gateway import get_messaging_gateway

    gw = get_messaging_gateway()
    result = await gw.restart_adapter(adapter_name)
    if result["status"] == "error":
        raise HTTPException(status_code=500, detail=result["message"])
    return result


# ------------------------------------------------------------------
# Channel discovery
# ------------------------------------------------------------------


@router.get("/messaging/channels")
async def list_channels() -> dict[str, Any]:
    """List all discovered channels across running adapters."""
    from messaging.gateway import get_messaging_gateway

    gw = get_messaging_gateway()
    channels = await gw.list_all_channels()
    return {"channels": [c.model_dump() for c in channels]}


# ------------------------------------------------------------------
# Channel → Project mappings
# ------------------------------------------------------------------


@router.get("/messaging/mappings")
async def get_mappings() -> dict[str, Any]:
    """Return all channel→project mappings."""
    from messaging.channel_store import get_channel_store

    store = get_channel_store()
    mappings = store.get_mappings()
    return {"mappings": [m.model_dump() for m in mappings]}


@router.put("/messaging/mappings")
async def bulk_update_mappings(req: BulkMappingUpdate) -> dict[str, Any]:
    """Bulk create/update channel→project mappings."""
    from messaging.channel_store import get_channel_store

    store = get_channel_store()
    store.bulk_update(req.mappings)
    return {"status": "updated", "count": len(req.mappings)}


@router.put("/messaging/mappings/{channel_id:path}")
async def set_mapping(channel_id: str, req: MappingUpdate) -> dict[str, str]:
    """Set or update a single channel→project mapping."""
    from messaging.channel_store import get_channel_store

    store = get_channel_store()
    store.set_mapping(channel_id, req.project_id)
    return {"status": "set", "channel_id": channel_id, "project_id": req.project_id}


@router.delete("/messaging/mappings/{channel_id:path}")
async def remove_mapping(channel_id: str) -> dict[str, str]:
    """Remove a channel→project mapping (channel falls back to default)."""
    from messaging.channel_store import get_channel_store

    store = get_channel_store()
    removed = store.remove_mapping(channel_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Mapping not found")
    return {"status": "removed", "channel_id": channel_id}


# ------------------------------------------------------------------
# Default project
# ------------------------------------------------------------------


@router.get("/messaging/default-project")
async def get_default_project() -> dict[str, str]:
    """Return the default project for unmapped channels."""
    from messaging.channel_store import get_channel_store

    store = get_channel_store()
    return {"project_id": store.get_default_project()}


@router.put("/messaging/default-project")
async def set_default_project(req: DefaultProjectUpdate) -> dict[str, str]:
    """Set the default project for unmapped channels."""
    from messaging.channel_store import get_channel_store

    store = get_channel_store()
    store.set_default_project(req.project_id)
    return {"status": "set", "project_id": req.project_id}
