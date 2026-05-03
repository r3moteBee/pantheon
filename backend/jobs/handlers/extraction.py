"""extraction handler — wraps memory/extraction.run_extraction.

Payload shape:
    {"session_id": str, "min_messages": int}
or:
    {"messages": [...], "min_messages": int}   # explicit transcript
"""
from __future__ import annotations

import logging
from typing import Any

from jobs.context import JobContext, pinger_for
from jobs.handlers import register

logger = logging.getLogger(__name__)


@register("extraction", default_timeout_seconds=120,
          description="Post-conversation entity / fact / relationship extraction.")
async def handle_extraction(ctx: JobContext) -> dict[str, Any]:
    pl = ctx.payload
    session_id = pl.get("session_id")
    messages = pl.get("messages")
    min_messages = int(pl.get("min_messages") or 4)

    if not messages and session_id:
        await ctx.heartbeat(progress=f"Loading messages for session {session_id[:8]}…")
        from memory.episodic import EpisodicMemory
        ep = EpisodicMemory()
        messages = await ep.get_history(session_id=session_id, limit=200)

    if not messages:
        return {"status": "skipped", "reason": "no messages"}

    from memory.manager import create_memory_manager
    from memory.extraction import run_extraction
    mgr = create_memory_manager(project_id=ctx.project_id, session_id=session_id)

    await ctx.heartbeat(progress=f"Extracting from {len(messages)} message(s)…")
    async with pinger_for(ctx, interval=30.0):
        stats = await run_extraction(
            messages=messages, memory_manager=mgr,
            project_id=ctx.project_id, session_id=session_id,
            min_messages=min_messages,
        )
    return {"stats": stats, "session_id": session_id, "messages_processed": len(messages)}
