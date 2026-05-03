"""Cross-artifact topic similarity pipeline.

Runs after MemoryManager.index_artifact() when an adapter declares
auto_link_similarity=True (or when a skill calls link_topic_similarity
explicitly for backfill). For each topic in the just-indexed
artifact:

  1. Look up similar topic-node embeddings via topic_embeddings.find_similar_topics
  2. For matches with score ≥ link_threshold (default 0.86):
        add SEMANTICALLY_SIMILAR_TO edge in both directions (so
        graph traversal is direction-agnostic).
  3. For matches with score ≥ merge_threshold (default 0.92):
        queue a merge proposal in the merge_proposals table — does
        NOT auto-merge. Operator/agent reviews and approves.

Defaults are conservative; thresholds and behavior are caller-
override-able.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_LINK_THRESHOLD = 0.86
DEFAULT_MERGE_THRESHOLD = 0.92


@dataclass
class SimilarityRunResult:
    topics_processed: int = 0
    edges_added: int = 0
    proposals_queued: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


async def link_artifact_topics(
    artifact_id: str,
    *,
    project_id: str,
    memory_manager: Any,
    link_threshold: float = DEFAULT_LINK_THRESHOLD,
    merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
    propose_merges: bool = True,
) -> SimilarityRunResult:
    """Run the similarity pipeline for the topics in one artifact."""
    from artifacts.store import get_store, is_text_type
    from memory.topic_embeddings import (
        upsert_topic_embedding, find_similar_topics,
    )
    from memory import merge_proposals

    result = SimilarityRunResult()
    store = get_store()
    a = store.get(artifact_id)
    if not a or a.get("deleted_at") or not is_text_type(a.get("content_type") or ""):
        result.skipped += 1
        return result

    # Pull topics out of frontmatter. This is cheaper than re-parsing
    # the markdown — the file_indexer already extracted them.
    import re as _re
    text = a.get("content") or ""
    m = _re.match(r"^---\n(.*?)\n---", text, _re.DOTALL)
    if not m:
        return result  # no frontmatter, no topics
    try:
        import yaml as _yaml
        fm = _yaml.safe_load(m.group(1)) or {}
    except Exception as e:
        result.errors.append(f"yaml parse: {e}")
        return result

    topics = fm.get("topics") or []
    if not isinstance(topics, list):
        return result

    semantic = memory_manager.semantic
    graph = memory_manager.graph
    when = a.get("updated_at") or a.get("created_at")

    # 1. Upsert each topic's embedding (idempotent).
    for t in topics:
        if not isinstance(t, dict):
            continue
        label = (t.get("label") or "").strip()
        if not label:
            continue
        topic_type = (t.get("type") or "concept").strip().lower()
        try:
            await upsert_topic_embedding(
                semantic,
                label=label, topic_type=topic_type,
                project_id=project_id,
                artifact_id=artifact_id,
                confidence=t.get("confidence"),
                when=when,
            )
        except Exception as e:
            result.errors.append(f"upsert {label}: {e}")

    # 2. For each topic, find type-compatible neighbors and add
    #    SEMANTICALLY_SIMILAR_TO edges / queue merge proposals.
    seen_edges: set[tuple[str, str]] = set()
    for t in topics:
        if not isinstance(t, dict):
            continue
        label = (t.get("label") or "").strip()
        topic_type = (t.get("type") or "concept").strip().lower()
        if not label:
            continue
        try:
            neighbors = await find_similar_topics(
                semantic,
                label=label, topic_type=topic_type,
                project_id=project_id, limit=20,
            )
        except Exception as e:
            result.errors.append(f"search {label}: {e}")
            continue

        result.topics_processed += 1
        for n in neighbors:
            other_label = (n.get("label") or "").strip()
            if not other_label or other_label.lower() == label.lower():
                continue
            score = float(n.get("score") or 0.0)
            if score < link_threshold:
                continue

            # Add SEMANTICALLY_SIMILAR_TO edge (both directions).
            pair_key = tuple(sorted([label.lower(), other_label.lower()]))
            if pair_key in seen_edges:
                continue
            seen_edges.add(pair_key)
            try:
                await graph.add_edge_by_label(
                    label, other_label, "SEMANTICALLY_SIMILAR_TO",
                )
                await graph.add_edge_by_label(
                    other_label, label, "SEMANTICALLY_SIMILAR_TO",
                )
                result.edges_added += 2
            except Exception as e:
                result.errors.append(f"edge {label}->{other_label}: {e}")

            # Queue a merge proposal if the score is high enough that
            # linking-with-edge starts feeling redundant.
            if propose_merges and score >= merge_threshold:
                try:
                    _, created = merge_proposals.propose(
                        project_id=project_id,
                        label_a=label, type_a=topic_type,
                        label_b=other_label,
                        type_b=n.get("topic_type") or topic_type,
                        similarity_score=score,
                        reason="auto_high_similarity",
                        proposal_metadata={
                            "via_artifact_id": artifact_id,
                            "via_artifact_path": a.get("path") or "",
                        },
                    )
                    if created:
                        result.proposals_queued += 1
                except Exception as e:
                    result.errors.append(f"propose {label}/{other_label}: {e}")

    return result


# ── Merge execution (only after explicit approval) ────────────────

async def execute_merge(
    proposal_id: str,
    *,
    canonical_label: str,
    project_id: str,
    memory_manager: Any,
    approved_by: str | None = None,
) -> dict[str, Any]:
    """Execute an approved merge: rewrite all edges that touch the
    deprecated node to point at the canonical node, then delete the
    deprecated node. Idempotent — running twice is a no-op once
    status is 'merged'.
    """
    from memory import merge_proposals

    proposal = merge_proposals.get_proposal(proposal_id)
    if not proposal:
        return {"ok": False, "reason": "proposal_not_found"}
    if proposal["status"] == "merged":
        return {"ok": True, "reason": "already_merged",
                "canonical_label": proposal.get("canonical_label")}
    if proposal["status"] not in {"approved", "pending"}:
        return {"ok": False,
                "reason": f"proposal_status={proposal['status']}"}
    if proposal["project_id"] != project_id:
        return {"ok": False, "reason": "project_mismatch"}

    a_label, b_label = proposal["node_a_label"], proposal["node_b_label"]
    if canonical_label not in {a_label, b_label}:
        return {"ok": False,
                "reason": f"canonical {canonical_label!r} must be one of {a_label!r}/{b_label!r}"}

    deprecated_label = b_label if canonical_label == a_label else a_label
    graph = memory_manager.graph

    canonical_node = await graph.get_node_by_label(canonical_label)
    deprecated_node = await graph.get_node_by_label(deprecated_label)
    if not canonical_node or not deprecated_node:
        return {"ok": False, "reason": "node_lookup_failed",
                "canonical_found": bool(canonical_node),
                "deprecated_found": bool(deprecated_node)}
    if canonical_node["id"] == deprecated_node["id"]:
        # Already merged at the graph level somehow.
        merge_proposals.set_status(
            proposal_id, "merged",
            canonical_label=canonical_label, approved_by=approved_by,
        )
        return {"ok": True, "reason": "nodes_already_identical"}

    # Rewrite edges. graph.add_edge_by_label / list_edges work by
    # label; for surgical rewrite we need direct access. Pull all
    # edges touching the deprecated node and re-create against the
    # canonical id, then delete the deprecated node (which cascades
    # the original edges).
    edges_rewritten = 0
    try:
        all_edges = await graph.list_edges(limit=10_000)
        for e in all_edges:
            a_id, b_id, rel = e["node_a_id"], e["node_b_id"], e["relationship"]
            touches = (a_id == deprecated_node["id"] or
                       b_id == deprecated_node["id"])
            if not touches:
                continue
            # Skip edges between the two merging nodes — they
            # disappear entirely (a node can't be similar to itself).
            if {a_id, b_id} == {canonical_node["id"], deprecated_node["id"]}:
                continue
            new_a = canonical_node["id"] if a_id == deprecated_node["id"] else a_id
            new_b = canonical_node["id"] if b_id == deprecated_node["id"] else b_id
            try:
                await graph.add_edge(new_a, new_b, rel)
                edges_rewritten += 1
            except Exception as ex:
                logger.debug("edge rewrite failed: %s", ex)
    except Exception as e:
        return {"ok": False, "reason": f"edge_rewrite_failed: {e}"}

    # Delete the deprecated node (cascades remaining edges).
    try:
        await graph.delete_node(deprecated_node["id"])
    except Exception as e:
        return {"ok": False, "reason": f"delete_failed: {e}"}

    merge_proposals.set_status(
        proposal_id, "merged",
        canonical_label=canonical_label, approved_by=approved_by,
    )
    logger.info("merge executed: %s absorbed into %s (%d edges rewritten)",
                deprecated_label, canonical_label, edges_rewritten)
    return {
        "ok": True,
        "canonical_label": canonical_label,
        "deprecated_label": deprecated_label,
        "edges_rewritten": edges_rewritten,
    }


# ── Backfill helper ──────────────────────────────────────────────

async def backfill(
    *,
    project_id: str,
    memory_manager: Any,
    path_prefix: str | None = None,
    link_threshold: float = DEFAULT_LINK_THRESHOLD,
    merge_threshold: float = DEFAULT_MERGE_THRESHOLD,
) -> dict[str, Any]:
    """Run link_artifact_topics() over every existing artifact in
    the project (or under a path prefix). Useful after enabling
    auto_link_similarity, or after creating the similarity tables
    on a project that already has indexed artifacts.
    """
    from artifacts.store import get_store, is_text_type
    store = get_store()
    items = store.list(
        project_id=project_id, path_prefix=path_prefix, limit=2000,
    )
    artifacts_processed = 0
    total_edges = 0
    total_proposals = 0
    total_topics = 0
    errors: list[str] = []
    for it in items:
        if not is_text_type(it.get("content_type") or ""):
            continue
        try:
            r = await link_artifact_topics(
                it["id"], project_id=project_id,
                memory_manager=memory_manager,
                link_threshold=link_threshold,
                merge_threshold=merge_threshold,
            )
            artifacts_processed += 1
            total_edges += r.edges_added
            total_proposals += r.proposals_queued
            total_topics += r.topics_processed
            errors.extend(r.errors[:5])
        except Exception as e:
            errors.append(f"{it.get('path')}: {e}")
    return {
        "artifacts_processed": artifacts_processed,
        "topics_processed": total_topics,
        "edges_added": total_edges,
        "proposals_queued": total_proposals,
        "errors": errors[:25],
    }
