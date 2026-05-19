"""image_extraction handler — offline vision + OCR + topic extraction.

Reads the image bytes from ArtifactStore, runs a vision-capable LLM to
produce caption + OCR text + structured topics, then:
  1. Updates the image artifact's tags (topics + 'vision-extracted')
     and title (first 60 chars of caption).
  2. Creates a sibling text artifact at <path>.extraction.md with full
     frontmatter — flows through the standard typed-topics graph
     extractor via the embedder/file-index path.

Idempotent: if the sibling already exists with parent_sha256 matching
the current image, returns {status: skipped, reason: already extracted}.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from jobs.context import JobContext, pinger_for
from jobs.handlers import register

logger = logging.getLogger(__name__)

_VISION_MIME_ALLOWED = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"}

_VISION_SYSTEM_PROMPT = (
    "You are a visual analysis assistant. Given an image, produce a "
    "JSON object with three fields:\n"
    '  - "caption": one or two sentences describing the image\n'
    '  - "ocr_text": any visible text in the image, verbatim (empty string if none)\n'
    '  - "topics": an array of {label, type} where type is one of: '
    "concept, technology, vendor, organization, person, market_segment, framework\n"
    "Return ONLY the JSON object, no preamble. Topics: 3-7 items, lowercase labels."
)


async def _call_vision_extractor(image_bytes: bytes, mime: str) -> dict[str, Any]:
    """Run vision model and return parsed {caption, ocr_text, topics}.

    Tries providers in order: vision → primary → prefill. Raises on
    total failure so the handler can record an error.
    """
    from models.provider import get_vision_provider, get_provider, get_prefill_provider

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    messages = [
        {"role": "system", "content": _VISION_SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            {"type": "text", "text": "Extract caption, OCR text, and topics."},
        ]},
    ]

    providers: list[tuple[str, Any]] = []
    vp = get_vision_provider()
    if vp:
        providers.append(("vision", lambda: vp))
    providers.append(("primary", get_provider))
    providers.append(("prefill", get_prefill_provider))

    last_err: Exception | None = None
    for label, get_prov in providers:
        try:
            provider = get_prov()
            resp = await provider.chat_complete(messages)
            text = (resp.get("content") or "").strip()
            # Strip code fences if present (handle leading preamble text)
            if "```" in text:
                # Take content between first and last fence
                first = text.find("```")
                last = text.rfind("```")
                if last > first:
                    text = text[first + 3 : last]
                    if text.startswith("json"):
                        text = text[4:]
                    text = text.strip()
            data = json.loads(text)
            return {
                "caption": str(data.get("caption", "")).strip(),
                "ocr_text": str(data.get("ocr_text", "")).strip(),
                "topics": [t for t in (data.get("topics") or [])
                           if isinstance(t, dict) and t.get("label")],
            }
        except Exception as e:
            logger.debug("vision via %s failed: %s", label, e)
            last_err = e
            continue
    raise RuntimeError(f"all vision providers failed; last error: {last_err}")


def _build_extraction_markdown(*, image_path: str, parent_id: str,
                               parent_sha: str, vision: dict[str, Any]) -> str:
    """Render the .extraction.md sibling artifact body."""
    def _yaml_str(s: str) -> str:
        # Single-quote YAML string with internal quote escaping
        return "'" + str(s).replace("'", "''") + "'"
    topics_yaml = "\n".join(
        f"  - label: {_yaml_str(t['label'])}\n    type: {_yaml_str(t.get('type', 'concept'))}"
        for t in vision["topics"]
    )
    return (
        "---\n"
        f"parent_artifact_id: {parent_id}\n"
        f"parent_sha256: {parent_sha}\n"
        f"source_image: {_yaml_str(image_path)}\n"
        "extraction_kind: image_vision\n"
        "topics:\n"
        f"{topics_yaml}\n"
        "---\n\n"
        f"# {vision['caption'] or 'Image extraction'}\n\n"
        f"**Source image:** `{image_path}`\n\n"
        "## Caption\n\n"
        f"{vision['caption']}\n\n"
        "## OCR Text\n\n"
        f"{vision['ocr_text'] or '_(no text detected)_'}\n"
    )


@register("image_extraction", default_timeout_seconds=300,
          description="Vision + OCR + topic extraction for an image artifact.")
async def handle_image_extraction(ctx: JobContext) -> dict[str, Any]:
    pl = ctx.payload or {}
    artifact_id = pl.get("artifact_id")
    if not artifact_id:
        return {"status": "skipped", "reason": "missing artifact_id"}

    from artifacts.store import get_store as get_artifact_store
    store = get_artifact_store()
    artifact = store.get(artifact_id)
    if not artifact:
        return {"status": "skipped", "reason": "artifact not found", "artifact_id": artifact_id}

    artifact_ct = (artifact.get("content_type") or "").lower()
    if artifact_ct not in _VISION_MIME_ALLOWED:
        return {"status": "skipped", "reason": f"content_type {artifact_ct!r} not vision-compatible",
                "artifact_id": artifact_id}

    image_path: str = artifact["path"]
    parent_sha: str = artifact.get("sha256") or ""
    sibling_path = f"{image_path}.extraction.md"

    # Idempotency check
    existing = store.get_by_path(artifact["project_id"], sibling_path)
    if existing:
        existing_body = existing.get("content") or ""
        if f"parent_sha256: {parent_sha}" in existing_body:
            return {"status": "skipped", "reason": "already extracted",
                    "artifact_id": artifact_id,
                    "extraction_artifact_id": existing["id"]}

    # Load image bytes
    blob_path = artifact.get("blob_path")
    if not blob_path:
        return {"status": "failed", "error": "image artifact has no blob_path",
                "artifact_id": artifact_id}
    await ctx.heartbeat(progress="Loading image bytes…")
    image_bytes = store._load_blob(blob_path)

    # Vision call (long single-await — wrap in pinger so watchdog stays happy)
    await ctx.heartbeat(progress="Running vision extraction…")
    try:
        async with pinger_for(ctx, interval=20.0):
            vision = await _call_vision_extractor(
                image_bytes, artifact["content_type"]
            )
    except Exception as e:
        logger.warning("image_extraction failed for %s: %s", artifact_id, e)
        return {"status": "failed", "error": str(e)[:500],
                "artifact_id": artifact_id}

    # 1. Update image artifact: tags = old + topic labels + sentinel
    await ctx.heartbeat(progress="Updating image artifact metadata…")
    existing_tags = list(artifact.get("tags") or [])
    topic_tags = [t["label"] for t in vision["topics"]]
    new_tags = list(dict.fromkeys(existing_tags + topic_tags + ["vision-extracted"]))
    caption_title = (vision["caption"] or artifact["title"] or "").strip()
    if len(caption_title) > 80:
        caption_title = caption_title[:77] + "…"
    store.update(
        artifact_id,
        title=caption_title or artifact["title"],
        tags=new_tags,
        edit_summary="vision-extracted",
        edited_by="image_extraction",
    )

    # 2. Create or update sibling extraction artifact
    body = _build_extraction_markdown(
        image_path=image_path, parent_id=artifact_id,
        parent_sha=parent_sha, vision=vision,
    )
    sibling_tags = ["image-extraction", "chat-attachment"] + topic_tags
    if existing:
        # Sibling exists but parent_sha mismatched — replace content (versioned update)
        sibling = store.update(
            existing["id"],
            content=body,
            tags=sibling_tags,
            edit_summary="re-extracted (image content changed)",
            edited_by="image_extraction",
        )
    else:
        sibling = store.create(
            project_id=artifact["project_id"],
            path=sibling_path,
            content=body,
            content_type="text/markdown",
            title=f"Extraction: {image_path.rsplit('/', 1)[-1]}",
            tags=sibling_tags,
            source={"kind": "image_extraction", "parent_artifact_id": artifact_id},
            edited_by="image_extraction",
        )

    # 3. Optional: schedule semantic embed for the new sibling so RAG can find it
    try:
        from artifacts import embedder
        embedder.schedule_embed(sibling["id"], artifact["project_id"])
    except Exception:
        logger.debug("schedule_embed for sibling failed", exc_info=True)

    # 4. Notify parent session if this came from a chat upload
    parent_session_id = pl.get("parent_session_id")
    if parent_session_id:
        try:
            from memory.episodic import EpisodicMemory
            ep = EpisodicMemory()
            topics_str = ", ".join(topic_tags[:5]) or "(no topics)"
            msg = (
                f"📷 **Image analyzed:** {caption_title}\n\n"
                f"_Topics: {topics_str}_  "
                f"·  _Extraction artifact:_ `{sibling_path}`"
            )
            await ep.save_message(
                session_id=parent_session_id,
                project_id=artifact["project_id"],
                role="assistant", content=msg,
                metadata={
                    "kind": "image_extraction_completion_notice",
                    "image_artifact_id": artifact_id,
                    "extraction_artifact_id": sibling["id"],
                },
            )
        except Exception:
            logger.debug("parent-session notify failed", exc_info=True)

    return {
        "status": "completed",
        "artifact_id": artifact_id,
        "extraction_artifact_id": sibling["id"],
        "caption": vision["caption"],
        "topic_count": len(vision["topics"]),
    }
