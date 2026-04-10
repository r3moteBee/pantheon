"""Projects API — create, list, switch, delete, export/import project namespaces."""
from __future__ import annotations
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()

_PROJECTS_META_FILE = None


def _meta_file() -> Path:
    path = settings.db_dir / "projects.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_projects() -> dict[str, Any]:
    meta = _meta_file()
    if meta.exists():
        try:
            return json.loads(meta.read_text())
        except Exception:
            pass
    # Initialize with default project
    default = {
        "default": {
            "id": "default",
            "name": "Default Project",
            "description": "The default project workspace",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
        }
    }
    meta.write_text(json.dumps(default, indent=2))
    return default


def _save_projects(projects: dict[str, Any]) -> None:
    _meta_file().write_text(json.dumps(projects, indent=2))


class CreateProjectRequest(BaseModel):
    name: str
    description: str = ""
    id: str | None = None


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    description: str | None = None


@router.get("/projects")
async def list_projects() -> dict[str, Any]:
    """List all projects."""
    projects = _load_projects()
    return {"projects": list(projects.values()), "count": len(projects)}


@router.post("/projects")
async def create_project(req: CreateProjectRequest) -> dict[str, Any]:
    """Create a new project."""
    projects = _load_projects()
    project_id = req.id or str(uuid.uuid4())[:8]

    # Sanitize ID
    import re
    project_id = re.sub(r'[^a-zA-Z0-9-_]', '-', project_id).lower()

    if project_id in projects:
        raise HTTPException(status_code=409, detail=f"Project '{project_id}' already exists")

    project = {
        "id": project_id,
        "name": req.name,
        "description": req.description,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "active": True,
    }
    projects[project_id] = project

    # Create project directories
    project_dir = settings.projects_dir / project_id
    for subdir in ["workspace", "personality", "notes"]:
        (project_dir / subdir).mkdir(parents=True, exist_ok=True)

    _save_projects(projects)
    logger.info(f"Project created: {project_id}")
    return project


@router.get("/projects/{project_id}")
async def get_project(project_id: str) -> dict[str, Any]:
    """Get project details."""
    projects = _load_projects()
    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found")
    project = projects[project_id]

    # Add memory stats
    try:
        from memory.semantic import SemanticMemory
        sem = SemanticMemory(project_id=project_id)
        project["semantic_memory_count"] = await sem.count()
    except Exception:
        project["semantic_memory_count"] = 0

    return project


@router.put("/projects/{project_id}")
async def update_project(project_id: str, req: UpdateProjectRequest) -> dict[str, Any]:
    """Update project metadata."""
    projects = _load_projects()
    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found")
    if req.name:
        projects[project_id]["name"] = req.name
    if req.description is not None:
        projects[project_id]["description"] = req.description
    projects[project_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
    _save_projects(projects)
    return projects[project_id]


@router.delete("/projects/{project_id}")
async def delete_project(project_id: str) -> dict[str, str]:
    """Delete a project and all its data."""
    if project_id == "default":
        raise HTTPException(status_code=400, detail="Cannot delete the default project")
    projects = _load_projects()
    if project_id not in projects:
        raise HTTPException(status_code=404, detail="Project not found")

    # Delete project files
    project_dir = settings.projects_dir / project_id
    if project_dir.exists():
        shutil.rmtree(project_dir)

    del projects[project_id]
    _save_projects(projects)
    logger.info(f"Project deleted: {project_id}")
    return {"status": "deleted", "project_id": project_id}


# ── Export / Import ──────────────────────────────────────────────────────────


class ExportRequest(BaseModel):
    components: list[str] | None = None  # None = all


@router.post("/projects/{project_id}/export")
async def export_project_endpoint(
    project_id: str,
    req: ExportRequest | None = None,
) -> Response:
    """Export a project as a .zip archive with selectable components.

    Components: "metadata", "memory", "files", "tasks". Omit for all.
    """
    from api.project_export import export_project

    components = req.components if req else None
    try:
        archive = export_project(project_id, components=components)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Project not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    filename = f"pantheon-{project_id}-export.zip"
    return Response(
        content=archive,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/projects/{project_id}/export/preview")
async def export_preview(
    project_id: str,
    req: ExportRequest | None = None,
) -> dict[str, Any]:
    """Preview what would be exported without creating the archive."""
    from api.project_export import (
        _collect_metadata,
        _collect_episodic,
        _collect_graph,
        _collect_semantic,
        _collect_tasks,
    )

    components = req.components if req else ["metadata", "memory", "files", "tasks"]
    meta = _collect_metadata(project_id)
    if not meta and project_id != "default":
        raise HTTPException(status_code=404, detail="Project not found")

    preview: dict[str, Any] = {"project_id": project_id, "components": {}}

    if "metadata" in components:
        preview["components"]["metadata"] = {"name": meta.get("name", project_id)}

    if "memory" in components:
        episodic = _collect_episodic(project_id)
        graph = _collect_graph(project_id)
        try:
            from memory.semantic import SemanticMemory
            sem = SemanticMemory(project_id=project_id)
            sem_count = sem._get_collection().count()
        except Exception:
            sem_count = 0

        preview["components"]["memory"] = {
            "conversations": len(episodic["conversations"]),
            "messages": len(episodic["messages"]),
            "task_logs": len(episodic["task_logs"]),
            "memory_notes": len(episodic["memory_notes"]),
            "graph_nodes": len(graph["nodes"]),
            "graph_edges": len(graph["edges"]),
            "semantic_memories": sem_count,
        }

    if "files" in components:
        project_dir = settings.projects_dir / project_id
        file_count = 0
        for subdir in ("workspace", "personality", "notes"):
            src = project_dir / subdir
            if src.is_dir():
                file_count += sum(1 for _ in src.rglob("*") if _.is_file())
        preview["components"]["files"] = {"count": file_count}

    if "tasks" in components:
        tasks = _collect_tasks(project_id)
        preview["components"]["tasks"] = {"count": len(tasks)}

    return preview


@router.post("/projects/import")
async def import_project_endpoint(
    file: UploadFile = File(...),
    target_id: str | None = Query(None, description="Override the project ID"),
    components: str | None = Query(None, description="Comma-separated components to import"),
    overwrite: bool = Query(False, description="Merge into existing project if it exists"),
) -> dict[str, Any]:
    """Import a project from a Pantheon export archive.

    Runs a 3-layer security scan before importing:
    - Layer 1: Archive structure validation
    - Layer 2: Content safety scan
    - Layer 3: Data integrity verification
    """
    from api.project_import import import_project

    archive_bytes = await file.read()

    comp_list = None
    if components:
        comp_list = [c.strip() for c in components.split(",")]

    result = import_project(
        archive_bytes,
        target_project_id=target_id,
        components=comp_list,
        overwrite=overwrite,
    )
    status_code = 200 if result.success else 422
    return result.model_dump()


@router.post("/projects/import/scan")
async def scan_import_archive(
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Run the security scanner on an archive without importing.

    Use this to preview scan results before committing to an import.
    """
    from api.project_import import scan_archive

    archive_bytes = await file.read()
    result = scan_archive(archive_bytes)
    return result.model_dump()
