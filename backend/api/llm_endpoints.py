"""LLM endpoints + role mapping API.

Routes (mounted under /api/llm by main.py):
  GET    /endpoints              list saved endpoints
  POST   /endpoints              create or update
  DELETE /endpoints/{name}       delete (also unbinds any roles using it)
  GET    /roles                  read role mapping
  PUT    /roles                  replace role mapping (full set in body)
  POST   /probe                  probe models for an endpoint
                                 (either by saved name, or ad-hoc tuple)
"""
from __future__ import annotations
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from llm_config import probe as _probe
from llm_config.models import EndpointWithKey, ROLES, RoleMappingPayload
from llm_config.store import (
    delete_endpoint, get_endpoint_api_key, get_role_mapping,
    list_endpoints, save_endpoint, set_role_mapping,
)
from models.provider import reset_provider

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/llm/endpoints")
async def get_endpoints() -> dict[str, Any]:
    eps = list_endpoints()
    return {"endpoints": [e.model_dump() for e in eps]}


@router.post("/llm/endpoints")
async def create_or_update_endpoint(payload: EndpointWithKey) -> dict[str, Any]:
    try:
        ep = save_endpoint(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    reset_provider()
    return ep.model_dump()


@router.delete("/llm/endpoints/{name}")
async def remove_endpoint(name: str) -> dict[str, str]:
    delete_endpoint(name)
    reset_provider()
    return {"status": "deleted", "name": name}


@router.get("/llm/roles")
async def get_roles() -> dict[str, Any]:
    rm = get_role_mapping()
    # Always return one entry per role so the UI can render the full table.
    full = {}
    for role in ROLES:
        full[role] = rm.get(role) or {"endpoint": "", "model": ""}
    return {"roles": full}


@router.put("/llm/roles")
async def update_roles(payload: RoleMappingPayload) -> dict[str, Any]:
    try:
        set_role_mapping(payload.roles)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    reset_provider()
    return await get_roles()


class ProbeRequest(BaseModel):
    """One of:
      - endpoint_name: probe a saved endpoint (uses its stored key)
      - {base_url, api_type, api_key}: ad-hoc probe (no save needed)
    """
    endpoint_name: str | None = None
    base_url: str | None = None
    api_type: str | None = None
    api_key: str | None = None


@router.post("/llm/probe")
async def probe_endpoint(req: ProbeRequest) -> dict[str, Any]:
    if req.endpoint_name:
        eps = {e.name: e for e in list_endpoints()}
        ep = eps.get(req.endpoint_name)
        if ep is None:
            raise HTTPException(status_code=404, detail=f"unknown endpoint {req.endpoint_name!r}")
        api_key = get_endpoint_api_key(req.endpoint_name) or ""
        result = await _probe.probe_models(
            base_url=ep.base_url, api_type=ep.api_type, api_key=api_key,
        )
    else:
        if not (req.base_url and req.api_type):
            raise HTTPException(status_code=400, detail="base_url and api_type required for ad-hoc probe")
        result = await _probe.probe_models(
            base_url=req.base_url, api_type=req.api_type, api_key=req.api_key or "",
        )
    return {
        "ok": result.ok, "models": result.models, "error": result.error,
        "base_url": result.base_url, "api_type": result.api_type,
    }
