"""System diagnostics — sandbox health, runtime info."""
from __future__ import annotations
import logging
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/system/sandbox")
async def sandbox_health() -> dict[str, Any]:
    """Return sandbox backend health for the Settings page."""
    from sandbox import get_sandbox
    sb = get_sandbox()
    info = await sb.health()
    info.setdefault("backend", sb.name)
    return info
