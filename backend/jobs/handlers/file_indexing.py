"""file_indexing handler — index a workspace path into semantic + graph memory.

Payload shape:
    {"path": str, "force": bool}
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from jobs.context import JobContext, pinger_for
from jobs.handlers import register

logger = logging.getLogger(__name__)


@register("file_indexing", default_timeout_seconds=300,
          description="Chunk and embed workspace files; extract entities to graph.")
async def handle_file_indexing(ctx: JobContext) -> dict[str, Any]:
    pl = ctx.payload
    path = pl.get("path") or ""
    force = bool(pl.get("force", False))
    if not path:
        return {"status": "skipped", "reason": "no path"}

    target = Path(path)
    if not target.exists():
        return {"status": "skipped", "reason": f"path not found: {path}"}

    from memory.manager import create_memory_manager
    mgr = create_memory_manager(project_id=ctx.project_id)

    await ctx.heartbeat(progress=f"Indexing {target.name}…")
    async with pinger_for(ctx, interval=30.0):
        if target.is_file():
            result = await mgr.index_workspace_file(str(target), force=force)
        else:
            result = await mgr.index_workspace_directory(str(target), force=force)
    return {"path": str(target), "result": result}
