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


class UpdatePlanRequest(BaseModel):
    plan: str


@router.post("/tasks/{task_id}/approve")
async def approve_task(task_id: str) -> dict[str, Any]:
    """Approve a proposed schedule and resume its APScheduler trigger."""
    from tasks.scheduler import approve_schedule
    try:
        return approve_schedule(task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.patch("/tasks/{task_id}/plan")
async def update_task_plan(task_id: str, req: UpdatePlanRequest) -> dict[str, Any]:
    """Edit the plan markdown on a schedule (typically while proposed)."""
    from tasks.scheduler import update_schedule_plan
    try:
        return update_schedule_plan(task_id, req.plan)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/tasks/{task_id}/run-now")
async def run_task_now(task_id: str) -> dict[str, Any]:
    """Fire a scheduled task immediately.

    For recurring schedules, the schedule stays in place (just adds an
    ad-hoc run). For one-shot schedules ('now', 'delay:N'), the schedule
    is removed after firing — the run lives on as a job-history row.
    """
    from tasks.scheduler import run_job_now
    try:
        return await run_job_now(task_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


# ── Phase G: persistent task_runs dashboard ──────────────────────────────────

from fastapi import HTTPException

@router.get("/tasks/runs")
async def list_task_runs(
    project_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Cross-project task run history. project_id=None returns all."""
    from tasks.runs import list_runs
    runs = list_runs(project_id=project_id, status=status, limit=limit, offset=offset)
    return {"runs": runs, "count": len(runs)}


@router.get("/tasks/runs/{run_id}")
async def get_task_run(run_id: str) -> dict[str, Any]:
    from tasks.runs import get_run
    r = get_run(run_id)
    if not r:
        raise HTTPException(status_code=404, detail="run not found")
    return r


@router.delete("/tasks/runs/{run_id}")
async def delete_task_run(run_id: str) -> dict[str, str]:
    from tasks.runs import delete_run
    if not delete_run(run_id):
        raise HTTPException(status_code=404, detail="run not found")
    return {"status": "deleted", "id": run_id}


@router.post("/tasks/runs/{run_id}/cancel")
async def cancel_task_run(run_id: str) -> dict[str, str]:
    from tasks.runs import cancel_run
    if not cancel_run(run_id):
        raise HTTPException(status_code=404, detail="run not running")
    return {"status": "cancelled", "id": run_id}



@router.post("/jobs/{job_id}/rerun")
async def rerun_job_endpoint(job_id: str) -> dict[str, Any]:
    """Re-create a finished job (completed/failed/cancelled/stalled)
    with the same payload, title, schedule binding, and timeout.
    The new job is queued; the worker picks it up on next poll.
    Original job row is preserved as audit history."""
    from jobs.store import get_store
    store = get_store()
    try:
        new_job = store.rerun(job_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        # Treat JobNotFound (custom) and any other lookup failure as 404.
        raise HTTPException(status_code=404, detail=f"job {job_id} not found: {e}")
    return {
        "ok": True,
        "new_job_id": new_job["id"],
        "queued_at": new_job.get("created_at"),
        "from_job_id": job_id,
    }
