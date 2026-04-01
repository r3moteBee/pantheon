"""Personality API — CRUD for soul.md and agent.md files."""
from __future__ import annotations
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from agent.personality import (
    load_soul,
    load_agent_config,
    save_soul,
    save_agent_config,
    load_project_personality,
    _TEMPLATE_DIR,
)
from config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter()


class PersonalityUpdate(BaseModel):
    content: str


@router.get("/personality/soul")
async def get_soul(
    project_id: str | None = Query(default=None),
) -> dict[str, str]:
    """Get soul.md content (global or project-specific)."""
    if project_id:
        overrides = load_project_personality(project_id)
        content = overrides.get("soul.md") or load_soul()
        return {"content": content, "project_id": project_id, "is_override": "soul.md" in overrides}
    return {"content": load_soul(), "is_override": False}


@router.put("/personality/soul")
async def update_soul(
    req: PersonalityUpdate,
    project_id: str | None = Query(default=None),
) -> dict[str, str]:
    """Update soul.md content."""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")
    save_soul(req.content, project_id=project_id)
    return {"status": "updated", "scope": project_id or "global"}


@router.get("/personality/agent")
async def get_agent_config(
    project_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Get agent.md content."""
    if project_id:
        overrides = load_project_personality(project_id)
        content = overrides.get("agent.md") or load_agent_config()
        return {"content": content, "project_id": project_id, "is_override": "agent.md" in overrides}
    return {"content": load_agent_config(), "is_override": False}


@router.put("/personality/agent")
async def update_agent_config(
    req: PersonalityUpdate,
    project_id: str | None = Query(default=None),
) -> dict[str, str]:
    """Update agent.md content."""
    if not req.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")
    save_agent_config(req.content, project_id=project_id)
    return {"status": "updated", "scope": project_id or "global"}


@router.get("/personality")
async def get_full_personality(
    project_id: str | None = Query(default=None),
) -> dict[str, Any]:
    """Get both soul.md and agent.md in one request."""
    from agent.personality import get_full_personality as _get_full
    personality = _get_full(project_id)
    return {
        "soul": personality["soul"],
        "agent": personality["agent"],
        "project_id": project_id,
    }


@router.get("/personality/status")
async def personality_status() -> dict[str, Any]:
    """Diagnostic endpoint — show personality file paths, existence, and sizes."""
    settings = get_settings()

    def _file_info(path: Path) -> dict[str, Any]:
        exists = path.exists()
        size = path.stat().st_size if exists else 0
        preview = ""
        if exists and size > 0:
            preview = path.read_text(encoding="utf-8")[:120].replace("\n", " ")
        return {
            "path": str(path),
            "exists": exists,
            "size_bytes": size,
            "non_empty": size > 0,
            "preview": preview,
        }

    return {
        "data_dir": str(settings.data_dir),
        "personality_dir": str(settings.personality_dir),
        "global": {
            "soul.md": _file_info(settings.personality_dir / "soul.md"),
            "agent.md": _file_info(settings.personality_dir / "agent.md"),
        },
        "template_dir": str(_TEMPLATE_DIR),
        "templates": {
            "soul.md": _file_info(_TEMPLATE_DIR / "soul.md"),
            "agent.md": _file_info(_TEMPLATE_DIR / "agent.md"),
        },
    }
