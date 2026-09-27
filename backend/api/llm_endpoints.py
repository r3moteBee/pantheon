"""LLM endpoints + role mapping API.

Routes (mounted under /api/llm by main.py):
  GET    /endpoints              list saved endpoints
  POST   /endpoints              create or update
  DELETE /endpoints/{name}       delete (also unbinds any roles using it)
  GET    /roles                  read role mapping
  PUT    /roles                  replace role mapping (full set in body)
  POST   /probe                  probe models for an endpoint
                                 (either by saved name, or ad-hoc tuple)
  GET    /task-classes           task-class metadata (labels, requirements)
  GET    /routes                 routing table + profiles + warnings
  PUT    /routes                 replace routing table
  GET    /profile                capability profile for one endpoint/model
  PUT    /profiles               save user-edited capability profiles
  DELETE /profiles               revert one profile to the built-in guess
  GET    /usage?hours=24         per-class/model call stats
"""
from __future__ import annotations
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from llm_config import probe as _probe
from llm_config.models import (
    EndpointWithKey, ProfilesPayload, ROLES, RoleMappingPayload, RoutesPayload,
    TASK_CLASSES,
)
from llm_config.store import (
    delete_endpoint, delete_profile, get_endpoint_api_key, get_profile,
    get_role_mapping, get_routes, list_endpoints, route_warnings,
    save_endpoint, set_profiles, set_role_mapping, set_routes,
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


# ── Task-class routing ────────────────────────────────────────────

@router.get("/llm/task-classes")
async def get_task_classes() -> dict[str, Any]:
    return {"task_classes": [{"id": k, **v} for k, v in TASK_CLASSES.items()]}


def _routes_view() -> dict[str, Any]:
    routes = get_routes()
    profiles: dict[str, Any] = {}
    for entries in routes.values():
        for e in entries:
            key = f"{e['endpoint']}/{e['model']}"
            if key not in profiles:
                profiles[key] = get_profile(e["endpoint"], e["model"]).model_dump()
    return {"routes": routes, "profiles": profiles, "warnings": route_warnings(routes)}


@router.get("/llm/routes")
async def read_routes() -> dict[str, Any]:
    return _routes_view()


@router.put("/llm/routes")
async def update_routes(payload: RoutesPayload) -> dict[str, Any]:
    try:
        set_routes(payload.routes)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    reset_provider()
    return _routes_view()


@router.get("/llm/profile")
async def read_profile(endpoint: str, model: str) -> dict[str, Any]:
    return get_profile(endpoint, model).model_dump()


@router.put("/llm/profiles")
async def update_profiles(payload: ProfilesPayload) -> dict[str, Any]:
    try:
        set_profiles(payload.profiles)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    reset_provider()
    return _routes_view()


@router.delete("/llm/profiles")
async def revert_profile(endpoint: str, model: str) -> dict[str, Any]:
    delete_profile(endpoint, model)
    return _routes_view()


@router.get("/llm/usage")
async def read_usage(hours: float = 24.0) -> dict[str, Any]:
    from llm_config.usage import summary
    return summary(max(0.1, min(hours, 24 * 30)))
