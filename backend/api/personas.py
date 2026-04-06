"""Personas API — list, get, create, update, delete persona templates."""
from __future__ import annotations
import json
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()

# Bundled personas ship with the package (read-only)
_BUNDLED_DIR = Path(__file__).parent.parent / "data" / "personas"

def _user_dir() -> Path:
    """User-created personas directory."""
    d = settings.data_dir / "personas"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_bundled() -> list[dict[str, Any]]:
    """Load all bundled persona JSON files."""
    personas = []
    if _BUNDLED_DIR.is_dir():
        for f in sorted(_BUNDLED_DIR.glob("*.json")):
            try:
                p = json.loads(f.read_text(encoding="utf-8"))
                p["is_bundled"] = True
                personas.append(p)
            except Exception as e:
                logger.warning("Failed to load bundled persona %s: %s", f.name, e)
    return personas


def _load_user() -> list[dict[str, Any]]:
    """Load all user-created persona JSON files."""
    personas = []
    d = _user_dir()
    for f in sorted(d.glob("*.json")):
        try:
            p = json.loads(f.read_text(encoding="utf-8"))
            p["is_bundled"] = False
            personas.append(p)
        except Exception as e:
            logger.warning("Failed to load user persona %s: %s", f.name, e)
    return personas


def _find_persona(persona_id: str) -> tuple[dict[str, Any] | None, Path | None]:
    """Find a persona by ID across bundled and user dirs. Returns (data, filepath)."""
    # Check user dir first (allows overriding bundled)
    d = _user_dir()
    fp = d / f"{persona_id}.json"
    if fp.exists():
        try:
            return json.loads(fp.read_text(encoding="utf-8")), fp
        except Exception:
            pass
    # Check bundled
    fp = _BUNDLED_DIR / f"{persona_id}.json"
    if fp.exists():
        try:
            data = json.loads(fp.read_text(encoding="utf-8"))
            data["is_bundled"] = True
            return data, fp
        except Exception:
            pass
    return None, None


# ── Request / Response models ──────────────────────────────────────────────

class CreatePersonaRequest(BaseModel):
    name: str
    tagline: str = ""
    description: str = ""
    icon: str = "🎭"
    traits: list[str] = []
    best_for: str = ""
    soul: str = ""

class UpdatePersonaRequest(BaseModel):
    name: str | None = None
    tagline: str | None = None
    description: str | None = None
    icon: str | None = None
    traits: list[str] | None = None
    best_for: str | None = None
    soul: str | None = None


# ── Endpoints ──────────────────────────────────────────────────────────────

@router.get("/personas")
async def list_personas() -> dict[str, Any]:
    """List all personas (bundled + user-created)."""
    bundled = _load_bundled()
    user = _load_user()
    # De-duplicate: user personas override bundled ones with same ID
    seen = {p["id"] for p in user}
    combined = user + [p for p in bundled if p["id"] not in seen]
    return {"personas": combined, "count": len(combined)}


@router.get("/personas/{persona_id}")
async def get_persona(persona_id: str) -> dict[str, Any]:
    """Get a single persona by ID."""
    persona, _ = _find_persona(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")
    return persona


@router.post("/personas")
async def create_persona(req: CreatePersonaRequest) -> dict[str, Any]:
    """Create a new user persona."""
    persona_id = re.sub(r'[^a-zA-Z0-9-_]', '-', req.name.lower().strip())[:32]
    if not persona_id:
        persona_id = str(uuid.uuid4())[:8]

    # Check collision
    existing, _ = _find_persona(persona_id)
    if existing and not existing.get("is_bundled"):
        raise HTTPException(status_code=409, detail=f"Persona '{persona_id}' already exists")

    persona = {
        "id": persona_id,
        "name": req.name,
        "tagline": req.tagline,
        "description": req.description,
        "icon": req.icon,
        "traits": req.traits,
        "best_for": req.best_for,
        "is_default": False,
        "is_bundled": False,
        "soul": req.soul,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    fp = _user_dir() / f"{persona_id}.json"
    fp.write_text(json.dumps(persona, indent=2), encoding="utf-8")
    logger.info("Persona created: %s", persona_id)
    return persona


@router.put("/personas/{persona_id}")
async def update_persona(persona_id: str, req: UpdatePersonaRequest) -> dict[str, Any]:
    """Update a user-created persona."""
    persona, fp = _find_persona(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")
    if persona.get("is_bundled") and fp and _BUNDLED_DIR in fp.parents:
        raise HTTPException(status_code=403, detail="Cannot edit bundled personas. Clone it as a custom persona instead.")

    for field in ["name", "tagline", "description", "icon", "traits", "best_for", "soul"]:
        val = getattr(req, field)
        if val is not None:
            persona[field] = val
    persona["updated_at"] = datetime.now(timezone.utc).isoformat()

    fp = _user_dir() / f"{persona_id}.json"
    fp.write_text(json.dumps(persona, indent=2), encoding="utf-8")
    return persona


@router.delete("/personas/{persona_id}")
async def delete_persona(persona_id: str) -> dict[str, str]:
    """Delete a user-created persona."""
    persona, fp = _find_persona(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")
    if persona.get("is_bundled") and fp and _BUNDLED_DIR in fp.parents:
        raise HTTPException(status_code=403, detail="Cannot delete bundled personas")

    fp = _user_dir() / f"{persona_id}.json"
    if fp.exists():
        fp.unlink()
    logger.info("Persona deleted: %s", persona_id)
    return {"status": "deleted", "persona_id": persona_id}


@router.post("/personas/{persona_id}/apply/{project_id}")
async def apply_persona(persona_id: str, project_id: str) -> dict[str, str]:
    """Apply a persona's soul content to a project's personality override."""
    persona, _ = _find_persona(persona_id)
    if not persona:
        raise HTTPException(status_code=404, detail="Persona not found")

    soul_content = persona.get("soul", "")
    if not soul_content:
        raise HTTPException(status_code=400, detail="Persona has no soul content")

    # Write to project personality
    from agent.personality import save_soul
    save_soul(soul_content, project_id)

    # Also store the persona_id in the project metadata
    _update_project_persona(project_id, persona_id)

    logger.info("Applied persona '%s' to project '%s'", persona_id, project_id)
    return {"status": "applied", "persona_id": persona_id, "project_id": project_id}


def _update_project_persona(project_id: str, persona_id: str) -> None:
    """Store the persona_id in the project's metadata."""
    meta_file = settings.db_dir / "projects.json"
    if not meta_file.exists():
        return
    try:
        projects = json.loads(meta_file.read_text())
        if project_id in projects:
            projects[project_id]["persona_id"] = persona_id
            projects[project_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            meta_file.write_text(json.dumps(projects, indent=2))
    except Exception as e:
        logger.warning("Failed to update project persona metadata: %s", e)
