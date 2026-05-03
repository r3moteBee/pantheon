"""telegram sink — relays output through the configured Telegram bot."""
from __future__ import annotations

import logging
from typing import Any

from jobs.sinks import register

logger = logging.getLogger(__name__)


@register("telegram", description="Relay output via the project's Telegram bot.")
async def to_telegram(ctx, content: str, opts: dict) -> dict[str, Any]:
    chat_id = opts.get("telegram_chat_id")
    try:
        from telegram_bot.bot import send_message_to_all, send_message_to
    except Exception as e:
        return {"sink": "telegram", "status": "unsupported",
                "reason": f"telegram_bot unavailable: {e}"}

    # Telegram messages have a 4096-char limit. Chunk safely.
    chunks = [content[i:i + 3500] for i in range(0, len(content), 3500)]
    sent = 0
    last_msg_id = None
    for chunk in chunks:
        try:
            if chat_id and "send_message_to" in dir():
                msg = await send_message_to(chat_id, chunk)
            else:
                msg = await send_message_to_all(chunk)
            last_msg_id = getattr(msg, "message_id", None) if msg else None
            sent += 1
        except Exception as e:
            return {"sink": "telegram", "status": "failed",
                    "sent": sent, "error": str(e)}
    return {"sink": "telegram", "status": "ok", "sent": sent,
            "chat_id": chat_id, "message_id": last_msg_id}
