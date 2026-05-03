"""autonomous_task handler — wraps the existing tasks/autonomous flow.

Payload shape:
    {
      "task_name": str,
      "description": str,        # the prompt for the agent loop
      "schedule": "now" | cron,  # informational
    }

Existing tasks/scheduler.schedule_agent_task() and
tasks/autonomous.run_autonomous_task() get wrapped so heartbeats and
result/error reporting flow through the jobs row.
"""
from __future__ import annotations

import logging
from typing import Any

from jobs.context import JobContext, pinger_for
from jobs.handlers import register

logger = logging.getLogger(__name__)


@register("autonomous_task", default_timeout_seconds=600,
          description="Single-fire autonomous agent loop with a free-form prompt.")
async def handle_autonomous_task(ctx: JobContext) -> dict[str, Any]:
    pl = ctx.payload
    task_name = pl.get("task_name") or ctx.title or "Autonomous task"
    description = pl.get("description") or ctx.description or ""

    await ctx.heartbeat(progress="Spinning up agent…")
    ctx.update_result({"task_name": task_name})

    # Build the agent + memory the same way tasks/autonomous does, but
    # report progress + heartbeats along the way.
    import uuid
    session_id = f"autonomous-{ctx.job_id[:8]}-{uuid.uuid4().hex[:6]}"
    ctx.update_result({"session_id": session_id})
    await ctx.heartbeat(progress="Loading memory + agent…")

    from agent.core import AgentCore
    from memory.manager import create_memory_manager
    from memory.episodic import EpisodicMemory
    from models.provider import get_provider

    provider = get_provider()
    memory = create_memory_manager(
        project_id=ctx.project_id, session_id=session_id, provider=provider,
    )
    agent = AgentCore(
        provider=provider, memory_manager=memory,
        project_id=ctx.project_id, session_id=session_id,
    )

    episodic = EpisodicMemory()
    try:
        await episodic.log_task_event(
            task_id=ctx.job_id, event="started",
            project_id=ctx.project_id, task_name=task_name,
            details=f"Task description: {description}",
        )
    except Exception:
        logger.debug("episodic log_task_event failed", exc_info=True)

    # Run the agent loop. Wrap the whole call in pinger_for so the
    # watchdog stays happy even if the LLM round-trip blocks for minutes.
    full_prompt = (
        f"You are running an autonomous task: '{task_name}'\n"
        f"Job ID: {ctx.job_id}\n"
        f"Complete the task fully and save important results to memory.\n\n"
        f"Task:\n{description}"
    )

    await ctx.heartbeat(progress="Running agent loop…")
    async with pinger_for(ctx, interval=30.0):
        result_text = await agent.run_autonomous(full_prompt)

    if ctx.cancel_requested():
        return {"status": "cancelled", "session_id": session_id,
                "summary": (result_text or "")[:200]}

    try:
        await episodic.log_task_event(
            task_id=ctx.job_id, event="completed",
            project_id=ctx.project_id, task_name=task_name,
            details=f"Result: {(result_text or '')[:500]}",
        )
    except Exception:
        logger.debug("episodic log_task_event(completed) failed", exc_info=True)

    return {
        "session_id": session_id,
        "task_name": task_name,
        "summary": (result_text or "")[:1000],
    }
