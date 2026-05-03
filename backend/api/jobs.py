"""Jobs API — list / get / enqueue / cancel / retry / delete."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from jobs.store import get_store, JobNotFound, JobStatus
from jobs.handlers import known_types

logger = logging.getLogger(__name__)
router = APIRouter()


class CreateJobRequest(BaseModel):
    job_type: str
    project_id: str = "default"
    title: str | None = None
    description: str | None = None
    payload: dict[str, Any] | None = None
    timeout_seconds: int | None = None
    max_attempts: int = 1
    schedule_id: str | None = None


@router.get("/jobs")
async def list_jobs(
    project_id: str | None = Query(default=None),
    job_type: str | None = Query(default=None),
    status: str | None = Query(default=None),
    include_system: bool = Query(default=True),
    started_within_hours: int | None = Query(default=None, ge=1, le=720),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    statuses = [status] if status else None
    started_within = timedelta(hours=started_within_hours) if started_within_hours else None
    items = get_store().list(
        project_id=project_id, job_type=job_type, statuses=statuses,
        started_within=started_within, include_system=include_system,
        limit=limit, offset=offset,
    )
    return {"jobs": items, "count": len(items), "known_types": known_types()}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    j = get_store().get_or_none(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="job not found")
    return j


@router.post("/jobs")
async def create_job(req: CreateJobRequest) -> dict[str, Any]:
    return get_store().create(
        job_type=req.job_type, project_id=req.project_id,
        title=req.title, description=req.description,
        payload=req.payload, timeout_seconds=req.timeout_seconds,
        max_attempts=req.max_attempts, schedule_id=req.schedule_id,
    )


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str) -> dict[str, str]:
    if not get_store().cancel(job_id):
        raise HTTPException(status_code=400, detail="job not cancellable")
    return {"status": "cancel_requested", "id": job_id}


@router.post("/jobs/{job_id}/retry")
async def retry_job(job_id: str) -> dict[str, Any]:
    try:
        return get_store().retry(job_id)
    except JobNotFound:
        raise HTTPException(status_code=404, detail="job not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/jobs/{job_id}")
async def delete_job(job_id: str) -> dict[str, str]:
    if not get_store().delete(job_id):
        raise HTTPException(status_code=404, detail="job not found")
    return {"status": "deleted", "id": job_id}
