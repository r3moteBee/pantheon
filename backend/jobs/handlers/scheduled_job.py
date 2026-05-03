"""scheduled_job handler — runs a configured prompt and routes output through a sink.

Payload shape:
    {
      "schedule_id": str,                # optional — back-link to schedules
      "prompt": str,                     # what to run
      "tools": list[str],                # optional restriction (future use)
      "output_sink": {
        "kind": "artifact" | "telegram" | "webhook" | "email" | "sms",
        ...sink-specific opts
      },
      "interval_seconds": int            # used for the misfire-coalesce check
    }

Idempotency: if another scheduled_job for the same schedule_id completed
within the last interval_seconds window, we skip with status='coalesced'.
Catches APScheduler misfires when the host wakes from sleep.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from jobs.context import JobContext, pinger_for
from jobs.handlers import register
from jobs.sinks import get_sink

logger = logging.getLogger(__name__)


@register("scheduled_job", default_timeout_seconds=600,
          description="Run a prompt on a schedule and ship the output through a sink.")
async def handle_scheduled_job(ctx: JobContext) -> dict[str, Any]:
    pl = ctx.payload or {}
    prompt = pl.get("prompt") or ctx.description or ""
    if not prompt:
        return {"status": "skipped", "reason": "no prompt"}

    schedule_id = pl.get("schedule_id")
    interval = int(pl.get("interval_seconds") or 0)
    sink_spec = pl.get("output_sink") or {"kind": "artifact"}
    sink_kind = sink_spec.get("kind", "artifact")

    # Coalesce duplicates that fire within the same interval window
    if schedule_id and interval > 0:
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=interval - 5)
        recent = ctx.store.list(
            project_id=ctx.project_id, job_type="scheduled_job",
            statuses=["completed"], started_within=timedelta(seconds=max(interval * 2, 60)),
            limit=20,
        )
        for r in recent:
            if (r.get("payload") or {}).get("schedule_id") != schedule_id:
                continue
            if r["id"] == ctx.job_id:
                continue
            try:
                completed_at = datetime.fromisoformat(r["completed_at"])
                if completed_at >= cutoff:
                    logger.info(
                        "scheduled_job %s coalescing — recent completion %s within window",
                        ctx.job_id[:8], r["id"][:8],
                    )
                    return {"status": "coalesced", "skipped_for": r["id"]}
            except Exception:
                pass

    # Build agent + memory
    import uuid
    session_id = f"sched-{ctx.job_id[:8]}-{uuid.uuid4().hex[:6]}"
    ctx.update_result({"session_id": session_id})
    await ctx.heartbeat(progress="Loading memory + agent…")

    from agent.core import AgentCore
    from memory.manager import create_memory_manager
    from models.provider import get_provider

    provider = get_provider()
    memory = create_memory_manager(
        project_id=ctx.project_id, session_id=session_id, provider=provider,
    )
    agent = AgentCore(
        provider=provider, memory_manager=memory,
        project_id=ctx.project_id, session_id=session_id,
    )

    await ctx.heartbeat(progress="Running prompt through agent…")
    async with pinger_for(ctx, interval=30.0):
        result_text = await agent.run_autonomous(prompt) or ""

    if ctx.cancel_requested():
        return {"status": "cancelled", "session_id": session_id}

    # Route to sink
    sink = get_sink(sink_kind)
    if not sink:
        return {
            "status": "failed", "session_id": session_id,
            "error": f"Unknown sink kind: {sink_kind}",
            "content": result_text[:500],
        }
    await ctx.heartbeat(progress=f"Routing output → {sink_kind}…")
    sink_opts = {**sink_spec, "schedule_id": schedule_id, "title": ctx.title}
    sink_result = await sink.fn(ctx, result_text, sink_opts)

    return {
        "status": "ok",
        "session_id": session_id,
        "sink": sink_kind,
        "sink_result": sink_result,
        "content_chars": len(result_text),
        # Surface artifact_id at top so the worker auto-writes it onto the job row
        "artifact_id": (sink_result or {}).get("artifact_id"),
    }
