"""Shared vision utilities — image description via vision-capable LLMs.

Used by both the chat attachment handler and the file indexer to generate
text descriptions of images for semantic indexing.
"""
from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Image extensions recognised throughout the system
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}

# Max image size to send to vision model (10 MB)
MAX_IMAGE_BYTES = 10 * 1024 * 1024


def _mime_for(ext: str) -> str:
    ext = ext.lower().lstrip(".")
    if ext == "jpg":
        return "image/jpeg"
    if ext == "svg":
        return "image/svg+xml"
    return f"image/{ext}"


async def describe_image(
    file_path: Path,
    content: bytes | None = None,
    *,
    detail_prompt: str | None = None,
) -> str | None:
    """Generate a text description of an image using a vision-capable model.

    Tries dedicated vision provider → primary → prefill (whichever succeeds
    first).  Returns a basic metadata string when all providers fail.

    Parameters
    ----------
    file_path : Path
        Path to the image file (used for extension / name).
    content : bytes | None
        Raw image bytes.  If *None*, the file is read from disk.
    detail_prompt : str | None
        Optional custom prompt override for the vision model.
    """
    from models.provider import get_vision_provider, get_provider, get_prefill_provider

    if content is None:
        if not file_path.exists():
            logger.warning("Image file not found: %s", file_path)
            return None
        content = file_path.read_bytes()

    if len(content) > MAX_IMAGE_BYTES:
        logger.info("Image too large for vision (%d MB): %s", len(content) // (1024 * 1024), file_path.name)
        size_kb = len(content) / 1024
        return f"Image file ({file_path.suffix.upper()}, {size_kb:.0f}KB) — too large for vision analysis"

    b64 = base64.b64encode(content).decode("utf-8")
    mime = _mime_for(file_path.suffix)

    system_prompt = (
        "You are a visual analysis assistant. Describe this image concisely "
        "in 1-3 sentences. Focus on the key content, any text visible, "
        "diagrams, charts, or notable elements. Be factual and specific."
    )

    user_text = detail_prompt or f"Describe this image ({file_path.name}):"

    vision_messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            {"type": "text", "text": user_text},
        ]},
    ]

    # Build provider fallback chain: vision (dedicated) → primary → prefill
    providers: list[tuple[str, Any]] = []
    vision_prov = get_vision_provider()
    if vision_prov:
        providers.append(("vision", lambda: vision_prov))
    providers.append(("primary", get_provider))
    providers.append(("prefill", get_prefill_provider))

    for label, get_prov in providers:
        try:
            provider = get_prov()
            result = await provider.chat_complete(vision_messages)
            desc = (result.get("content") or "").strip()
            if desc and len(desc) > 10:
                logger.info("Vision description for %s via %s: %s", file_path.name, label, desc[:100])
                return desc
        except Exception as e:
            logger.debug("Vision description via %s failed: %s", label, e)
            continue

    # Fallback: basic metadata description
    size_kb = len(content) / 1024
    return f"Image file ({file_path.suffix.upper()}, {size_kb:.0f}KB)"
