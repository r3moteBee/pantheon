"""webhook sink — POST job output to a user-supplied URL."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from jobs.sinks import register

logger = logging.getLogger(__name__)


@register("webhook", description="POST run output as JSON to a user-supplied URL.")
async def to_webhook(ctx, content: str, opts: dict) -> dict[str, Any]:
    url = opts.get("webhook_url")
    if not url:
        return {"sink": "webhook", "status": "unsupported",
                "reason": "no webhook_url configured"}

    headers = {"Content-Type": "application/json"}
    token_key = opts.get("webhook_token_key")
    if token_key:
        try:
            from secrets.vault import SecretsVault
            tok = SecretsVault().get_secret(token_key)
            if tok:
                headers["Authorization"] = f"Bearer {tok}"
        except Exception:
            logger.debug("webhook bearer-token lookup failed", exc_info=True)

    body = {
        "job_id": ctx.job_id,
        "schedule_id": opts.get("schedule_id"),
        "project_id": ctx.project_id,
        "title": ctx.title,
        "content": content,
        "format": opts.get("format", "markdown"),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, json=body, headers=headers)
        return {
            "sink": "webhook", "status": "ok" if r.is_success else "failed",
            "url": url, "status_code": r.status_code,
            "response": (r.text or "")[:500],
        }
    except Exception as e:
        return {"sink": "webhook", "status": "failed",
                "url": url, "error": str(e)}
