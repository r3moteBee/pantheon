"""APScheduler setup and job management for autonomous tasks."""
from __future__ import annotations
import asyncio
import logging
import uuid
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.executors.asyncio import AsyncIOExecutor

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_scheduler: AsyncIOScheduler | None = None


def get_scheduler() -> AsyncIOScheduler:
    """Get or create the global APScheduler instance."""
    global _scheduler
    if _scheduler is None:
        jobstores = {
            "default": SQLAlchemyJobStore(
                url=f"sqlite:///{settings.scheduler_db_path}"
            )
        }
        executors = {
            "default": AsyncIOExecutor()
        }
        _scheduler = AsyncIOScheduler(
            jobstores=jobstores,
            executors=executors,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 300,
            },
        )
    return _scheduler


def list_jobs() -> list[dict[str, Any]]:
    """List all scheduled jobs."""
    scheduler = get_scheduler()
    jobs = []
    for job in scheduler.get_jobs():
        next_run = job.next_run_time.isoformat() if job.next_run_time else None
        kwargs = job.kwargs or {}
        jobs.append({
            "id": job.id,
            "name": job.name,
            "next_run": next_run,
            "trigger": str(job.trigger),
            "schedule": kwargs.get("schedule", str(job.trigger)),
            "description": kwargs.get("description", ""),
            "project_id": kwargs.get("project_id", "default"),
            "status": "scheduled" if next_run else "completed",
        })
    return jobs


def cancel_job(job_id: str) -> bool:
    """Cancel a job by ID. Returns True if the job existed."""
    scheduler = get_scheduler()
    job = scheduler.get_job(job_id)
    if job:
        job.remove()
        return True
    return False


async def schedule_agent_task(
    name: str,
    description: str,
    schedule: str,
    project_id: str = "default",
) -> str:
    """Schedule an autonomous agent task.

    schedule can be:
      - "now" — run immediately once
      - "interval:N" — run every N minutes
      - cron expression like "0 9 * * *" — daily at 9am
    """
    from tasks.autonomous import run_autonomous_task

    task_id = str(uuid.uuid4())[:8]
    scheduler = get_scheduler()

    if schedule == "now":
        scheduler.add_job(
            run_autonomous_task,
            trigger="date",
            id=task_id,
            name=name,
            kwargs={
                "task_id": task_id,
                "task_name": name,
                "description": description,
                "project_id": project_id,
                "schedule": schedule,
            },
            replace_existing=True,
        )
    elif schedule.startswith("interval:"):
        minutes = int(schedule.split(":")[1])
        scheduler.add_job(
            run_autonomous_task,
            trigger="interval",
            minutes=minutes,
            id=task_id,
            name=name,
            kwargs={
                "task_id": task_id,
                "task_name": name,
                "description": description,
                "project_id": project_id,
                "schedule": schedule,
            },
            replace_existing=True,
        )
    else:
        # Treat as cron expression
        from apscheduler.triggers.cron import CronTrigger
        parts = schedule.split()
        if len(parts) == 5:
            minute, hour, day, month, day_of_week = parts
            trigger = CronTrigger(
                minute=minute,
                hour=hour,
                day=day,
                month=month,
                day_of_week=day_of_week,
            )
        else:
            raise ValueError(f"Invalid schedule format: {schedule}")
        scheduler.add_job(
            run_autonomous_task,
            trigger=trigger,
            id=task_id,
            name=name,
            kwargs={
                "task_id": task_id,
                "task_name": name,
                "description": description,
                "project_id": project_id,
                "schedule": schedule,
            },
            replace_existing=True,
        )

    logger.info(f"Task scheduled: {name} (id={task_id}, schedule={schedule})")
    return task_id



# ── Phase H integration: schedule fires enqueue jobs instead of running directly ──

async def _enqueue_autonomous_job(
    task_id: str, task_name: str, description: str,
    project_id: str = "default", schedule: str = "now", **kwargs,
):
    """APScheduler trigger handler. Creates a queued autonomous_task job
    and lets the jobs worker run it. Heartbeats, timeouts, stall detection,
    and UI surfacing all happen via the unified jobs system.
    """
    from jobs.store import get_store
    get_store().create(
        job_type="autonomous_task",
        project_id=project_id,
        title=task_name,
        description=description,
        payload={"task_id": task_id, "task_name": task_name,
                 "description": description, "schedule": schedule},
        schedule_id=task_id,
    )


async def schedule_scheduled_job(
    name: str,
    prompt: str,
    schedule: str,
    *,
    project_id: str = "default",
    output_sink: dict | None = None,
    interval_seconds: int | None = None,
) -> str:
    """Register a scheduled_job in APScheduler. Each fire enqueues a
    'scheduled_job' jobs row with the configured prompt + output_sink.
    """
    schedule_id = str(uuid.uuid4())[:8]
    scheduler = get_scheduler()

    payload = {
        "schedule_id": schedule_id,
        "prompt": prompt,
        "output_sink": output_sink or {"kind": "artifact"},
        "interval_seconds": interval_seconds or 0,
    }

    async def _enqueue(**_kw):
        from jobs.store import get_store
        get_store().create(
            job_type="scheduled_job", project_id=project_id,
            title=name, description=prompt[:200],
            payload=payload, schedule_id=schedule_id,
        )

    if schedule == "now":
        scheduler.add_job(_enqueue, trigger="date", id=schedule_id, name=name,
                          replace_existing=True)
    elif schedule.startswith("interval:"):
        minutes = int(schedule.split(":")[1])
        if not interval_seconds:
            payload["interval_seconds"] = minutes * 60
        scheduler.add_job(_enqueue, trigger="interval", minutes=minutes,
                          id=schedule_id, name=name, replace_existing=True)
    else:
        from apscheduler.triggers.cron import CronTrigger
        parts = schedule.split()
        if len(parts) != 5:
            raise ValueError(f"Invalid schedule format: {schedule}")
        m, h, d, mo, dow = parts
        scheduler.add_job(_enqueue, trigger=CronTrigger(minute=m, hour=h, day=d,
                                                       month=mo, day_of_week=dow),
                          id=schedule_id, name=name, replace_existing=True)

    logger.info("scheduled_job registered: %s (id=%s, schedule=%s, sink=%s)",
                name, schedule_id, schedule, (output_sink or {}).get("kind", "artifact"))
    return schedule_id
