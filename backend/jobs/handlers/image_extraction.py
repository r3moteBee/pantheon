"""image_extraction handler — offline vision + OCR + topic extraction
for image artifacts. Full implementation lands in Task 2; this skeleton
exists so /chat/attach can enqueue jobs without 'No handler registered'.
"""
from __future__ import annotations

import logging
from typing import Any

from jobs.context import JobContext
from jobs.handlers import register

logger = logging.getLogger(__name__)


@register("image_extraction", default_timeout_seconds=300,
          description="Vision + OCR + topic extraction for an image artifact.")
async def handle_image_extraction(ctx: JobContext) -> dict[str, Any]:
    artifact_id = (ctx.payload or {}).get("artifact_id")
    if not artifact_id:
        return {"status": "skipped", "reason": "missing artifact_id"}
    await ctx.heartbeat(progress="(skeleton — implemented in Task 2)")
    return {"status": "skipped", "reason": "handler skeleton", "artifact_id": artifact_id}
