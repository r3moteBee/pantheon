"""Topic-label embedding storage on top of SemanticMemory.

Each registered topic node (created from a typed-topics frontmatter
artifact) gets its label embedded once and upserted into the
semantic collection with metadata kind=topic_node. Re-saving the
same (project_id, label, topic_type) tuple updates instead of
duplicating thanks to a deterministic doc_id.

This is the substrate the cross-artifact similarity pipeline sits
on. Search filters by kind=topic_node and topic_type so we don't
compare a topic against transcript chunks.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


_DOC_ID_PREFIX = "topic"


def _slug_for_id(label: str) -> str:
    """Make a doc_id-safe slug. Lowercase, runs of non-alnum collapse
    to '-'. Length-capped so very long labels still produce sensible
    ids; uniqueness is preserved by including the project_id and
    topic_type in the prefix."""
    s = (label or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if not s:
        s = "unnamed"
    if len(s) > 80:
        s = s[:80].rstrip("-")
    return s


def topic_doc_id(project_id: str, topic_type: str, label: str) -> str:
    """Deterministic doc_id so re-saving the same topic upserts."""
    tt = (topic_type or "concept").strip().lower() or "concept"
    return f"{_DOC_ID_PREFIX}:{project_id}:{tt}:{_slug_for_id(label)}"


async def upsert_topic_embedding(
    semantic_memory: Any,
    *,
    label: str,
    topic_type: str,
    project_id: str,
    artifact_id: str | None = None,
    confidence: float | None = None,
    when: str | None = None,
) -> str:
    """Upsert one topic-label embedding into the semantic collection.

    The doc_id is deterministic, so calling this twice for the same
    (project_id, topic_type, label) is a no-op overwrite — safe to
    call from index_artifact on every save.

    Returns the doc_id.
    """
    if not label or not label.strip():
        return ""
    doc_id = topic_doc_id(project_id, topic_type, label)
    metadata = {
        "kind": "topic_node",
        "topic_label": label,
        "topic_type": (topic_type or "concept"),
        "project_id": project_id,
        "first_seen_artifact_id": artifact_id or "",
        "first_seen_at": when or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    if confidence is not None:
        metadata["confidence"] = float(confidence)
    try:
        await semantic_memory.store(content=label, metadata=metadata, doc_id=doc_id)
    except Exception as e:
        logger.warning("upsert_topic_embedding failed for %r/%r: %s",
                       topic_type, label, e)
        return ""
    return doc_id


async def find_similar_topics(
    semantic_memory: Any,
    *,
    label: str,
    topic_type: str,
    project_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Search for topic nodes similar to label within the same
    type-compatibility group. Returns raw search hits (with score)
    so callers can apply their own thresholding.

    Type-gating rules per the skill spec:
      concept ↔ concept
      technology ↔ technology
      vendor ↔ vendor
      market ↔ market_segment (mapped here as either side)

    Cross-group comparisons return empty.
    """
    tt = (topic_type or "concept").strip().lower()

    # Build the where filter for ChromaDB. We search within
    # kind=topic_node + project_id, and one of the type-compatible
    # topic_types.
    compatible_types: list[str] = [tt]
    if tt == "market":
        compatible_types.append("market_segment")
    elif tt == "market_segment":
        compatible_types.append("market")
    # framework is treated like technology in our adapter type map,
    # but they're stored under their own type so search both.
    if tt == "technology":
        compatible_types.append("framework")
    elif tt == "framework":
        compatible_types.append("technology")
    # vendor and organization are normalized to "organization" in
    # the file_indexer; include both for safety.
    if tt == "vendor":
        compatible_types.append("organization")
    elif tt == "organization":
        compatible_types.append("vendor")

    if len(compatible_types) == 1:
        type_filter: dict[str, Any] = {"topic_type": compatible_types[0]}
    else:
        type_filter = {"topic_type": {"$in": compatible_types}}

    where = {
        "$and": [
            {"kind": "topic_node"},
            {"project_id": project_id},
            type_filter,
        ]
    }
    try:
        results = await semantic_memory.search(query=label, n=limit, where=where)
    except Exception as e:
        logger.warning("find_similar_topics search failed for %r: %s", label, e)
        return []
    # Filter out self-matches (same canonical label)
    canonical_self = label.strip().lower()
    out = []
    for r in results or []:
        meta = r.get("metadata") or {}
        other_label = (meta.get("topic_label") or "").strip()
        if other_label.lower() == canonical_self:
            continue
        out.append({
            "label": other_label,
            "topic_type": meta.get("topic_type") or "",
            "score": float(r.get("score") or 0.0),  # ChromaDB returns 1-distance as score in our wrapper
            "raw_distance": r.get("distance"),
            "doc_id": r.get("id") or "",
            "first_seen_artifact_id": meta.get("first_seen_artifact_id") or "",
        })
    return out
