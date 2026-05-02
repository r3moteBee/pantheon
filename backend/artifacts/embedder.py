"""Auto-embed artifact content into semantic memory.

Debounced 30s per artifact_id so rapid edits during coding tasks don't
thrash the embedder. Background tasks live in module state.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from artifacts.store import get_store, is_text_type

logger = logging.getLogger(__name__)

DEBOUNCE_SECONDS = 30
_pending: dict[str, asyncio.Task] = {}


def schedule_embed(artifact_id: str, project_id: str = "default", *, immediate: bool = False) -> None:
    """Schedule an embed pass for an artifact.

    The first call with a given artifact_id wins for the next 30 seconds —
    subsequent updates within that window cancel and reschedule. immediate=True
    skips the debounce.
    """
    existing = _pending.get(artifact_id)
    if existing and not existing.done():
        existing.cancel()
    delay = 0 if immediate else DEBOUNCE_SECONDS
    _pending[artifact_id] = asyncio.ensure_future(_run(artifact_id, project_id, delay))


async def _run(artifact_id: str, project_id: str, delay: int) -> None:
    try:
        if delay:
            await asyncio.sleep(delay)
        store = get_store()
        artifact = store.get(artifact_id)
        if not artifact:
            return
        if not is_text_type(artifact["content_type"]):
            return  # binary types skipped in v1
        content = artifact.get("content") or ""
        if not content.strip():
            return
        await _embed(artifact, project_id)
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.exception("artifact embed failed for %s: %s", artifact_id, e)
    finally:
        _pending.pop(artifact_id, None)


async def _embed(artifact: dict[str, Any], project_id: str) -> None:
    """Chunk + embed artifact content into semantic memory.

    Replaces any prior chunks for this artifact id by deleting vectors
    whose metadata.artifact_id matches before re-storing.
    """
    from memory.manager import create_memory_manager
    mgr = create_memory_manager(project_id=project_id)
    semantic = mgr.semantic

    # Drop prior vectors for this artifact (by walking and matching metadata)
    try:
        all_for_id = await semantic.list_by_model(embedding_model=None)
        stale_ids = [
            it["id"] for it in all_for_id
            if (it.get("metadata") or {}).get("artifact_id") == artifact["id"]
        ]
        for sid in stale_ids:
            await semantic.delete(sid)
    except Exception:
        logger.debug("could not drop prior artifact vectors", exc_info=True)

    # Chunk the new content
    chunks = _chunk(artifact.get("content") or "")
    for i, chunk in enumerate(chunks):
        await semantic.store(
            content=chunk,
            metadata={
                "source": "artifact",
                "artifact_id": artifact["id"],
                "version_id": artifact["current_version_id"],
                "path": artifact["path"],
                "title": artifact.get("title") or "",
                "content_type": artifact["content_type"],
                "chunk_index": i,
            },
        )
    logger.info("embedded artifact %s: %d chunks", artifact["id"], len(chunks))

    # Graph + entity extraction. Run the same MemoryExtractor used after
    # conversations so entities land in the project's graph and facts in
    # semantic memory. Treat the artifact content as a single "user"
    # message — the extraction prompt's persona-exclusion clause keeps
    # any embedded role-play out of the graph.
    try:
        from memory.extraction import run_extraction
        await run_extraction(
            messages=[
                {"role": "system", "content": f"Artifact: {artifact.get('path', '')}"},
                {"role": "user", "content": artifact.get("content") or ""},
            ],
            memory_manager=mgr,
            project_id=project_id,
            session_id=f"artifact:{artifact['id']}",
            min_messages=1,
        )
    except Exception:
        logger.exception("graph extraction on artifact %s failed", artifact.get("id"))


def _chunk(text: str, chunk_chars: int = 2000, overlap: int = 200) -> list[str]:
    if len(text) <= chunk_chars:
        return [text]
    chunks: list[str] = []
    i = 0
    while i < len(text):
        end = min(i + chunk_chars, len(text))
        chunks.append(text[i:end])
        if end == len(text):
            break
        i = end - overlap
    return chunks


async def drop_for_artifact(artifact_id: str, project_id: str = "default") -> int:
    """Delete every semantic vector tied to an artifact (used on soft-delete)."""
    from memory.manager import create_memory_manager
    mgr = create_memory_manager(project_id=project_id)
    semantic = mgr.semantic
    items = await semantic.list_by_model(embedding_model=None)
    n = 0
    for it in items:
        if (it.get("metadata") or {}).get("artifact_id") == artifact_id:
            if await semantic.delete(it["id"]):
                n += 1
    return n
