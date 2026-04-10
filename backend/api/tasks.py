"""Tasks API — create, list, cancel, and view autonomous task logs."""
from __future__ import annotations
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class CreateTaskRequest(BaseModel):
    name: str
    description: str
    schedule: str = "now"
    project_id: str = "default"


@router.get("/tasks")
async def list_tasks(
    project_id: str = Query(default="default"),
) -> dict[str, Any]:
    """List scheduled tasks for a specific project."""
    from tasks.scheduler import list_jobs
    all_jobs = list_jobs()
    jobs = [j for j in all_jobs if j.get("project_id", "default") == project_id]
    return {"tasks": jobs, "count": len(jobs), "project_id": project_id}


@router.get("/tasks/all")
async def list_all_tasks() -> dict[str, Any]:
    """List all scheduled tasks across all projects (for global settings view)."""
    from tasks.scheduler import list_jobs
    jobs = list_jobs()
    return {"tasks": jobs, "count": len(jobs)}


@router.post("/tasks")
async def create_task(req: CreateTaskRequest) -> dict[str, Any]:
    """Create a new autonomous task."""
    from tasks.scheduler import schedule_agent_task
    try:
        task_id = await schedule_agent_task(
            name=req.name,
            description=req.description,
            schedule=req.schedule,
            project_id=req.project_id,
        )
        return {
            "status": "scheduled",
            "task_id": task_id,
            "name": req.name,
            "schedule": req.schedule,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/tasks/{task_id}")
async def cancel_task(task_id: str) -> dict[str, str]:
    """Cancel a scheduled task."""
    from tasks.scheduler import cancel_job
    success = cancel_job(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"status": "cancelled", "task_id": task_id}


@router.get("/tasks/{task_id}/logs")
async def get_task_logs(
    task_id: str,
    project_id: str = Query(default="default"),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Get logs for a specific task."""
    from memory.episodic import EpisodicMemory
    episodic = EpisodicMemory()
    logs = await episodic.get_task_logs(task_id=task_id, project_id=project_id, limit=limit)
    return {"task_id": task_id, "logs": logs, "count": len(logs)}


@router.get("/tasks/logs/all")
async def get_all_task_logs(
    project_id: str = Query(default="default"),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """Get all task logs for a project."""
    from memory.episodic import EpisodicMemory
    episodic = EpisodicMemory()
    logs = await episodic.get_task_logs(project_id=project_id, limit=limit)
    return {"logs": logs, "count": len(logs)}
