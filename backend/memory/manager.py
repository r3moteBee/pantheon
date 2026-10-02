"""Memory manager — orchestrates the memory tiers with active curation.

Enhanced with:
- Graph-augmented retrieval (semantic results enriched with graph context)
- Context budget management (token-aware recall limits)
- Automatic post-conversation extraction pipeline
- Episodic semantic search support
- Context-focus-aware recency boosting
"""
from __future__ import annotations
import asyncio
import logging
import math
from datetime import datetime, timezone
from typing import Any

from memory.episodic import EpisodicMemory
from memory.semantic import SemanticMemory
from memory.graph import GraphMemory
from memory.archival import ArchivalMemory

logger = logging.getLogger(__name__)

# ── Token estimation ─────────────────────────────────────────────────────────

CHARS_PER_TOKEN = 4
SESSION_FALLBACK_MAX = 2

# Seconds pre-recall needs besides reranking (semantic/episodic/graph search,
# graph augmentation, budgeting). The rerank budget is capped so it always
# leaves this much of settings.pre_recall_timeout_seconds.
RERANK_RECALL_HEADROOM_S = 1.5


def rerank_timeout() -> float:
    """Seconds ``_rerank`` may take before keeping the original order.

    agent.core cancels the whole recall at pre_recall_timeout_seconds, so a
    reranker slower than that (e.g. still loading, 15-20 s cold) used to cost
    the turn every recalled memory. Capping rerank below that budget makes a
    slow reranker degrade to "unranked" instead.
    """
    from config import get_settings
    s = get_settings()
    cap = max(0.5, s.pre_recall_timeout_seconds - RERANK_RECALL_HEADROOM_S)
    return max(0.1, min(s.rerank_timeout_seconds, cap))


def _as_probabilities(scores: list[float]) -> list[float]:
    """Rerank scores on a 0-1 scale. Cohere/Jina/TEI return probabilities;
    llama.cpp (and TEI with raw_scores) return cross-encoder logits, which are
    unbounded and can't be thresholded or blended with recency as they are."""
    if all(0.0 <= s <= 1.0 for s in scores):
        return scores
    return [1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, s)))) for s in scores]


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


# ── Context focus recency boost ─────────────────────────────────────────────

# Maps context_focus setting → (half-life in hours, recency weight).
# "focused" = 1-hour half-life with 50% weight → aggressively favours last few turns
# "balanced" = 12-hour half-life with 20% weight → mild recency boost
# "broad" = no recency modification at all
_CONTEXT_FOCUS_PARAMS: dict[str, tuple[float, float]] = {
    "focused":  (1.0,  0.50),
    "balanced": (12.0, 0.20),
    "broad":    (0.0,  0.0),
}


def _apply_context_focus(
    results: list[dict[str, Any]],
    context_focus: str,
) -> list[dict[str, Any]]:
    """Re-weight and re-sort recall results based on context focus setting.

    For 'focused' mode, results with recent timestamps get a strong boost,
    pushing stale conversation fragments down. For 'broad', results are
    returned as-is (pure relevance ordering).
    """
    focus = context_focus.lower().strip() if context_focus else "balanced"
    half_life_hours, recency_weight = _CONTEXT_FOCUS_PARAMS.get(
        focus, _CONTEXT_FOCUS_PARAMS["balanced"]
    )

    if recency_weight <= 0 or half_life_hours <= 0:
        return results  # "broad" — no modification

    now = datetime.now(timezone.utc)
    relevance_weight = 1.0 - recency_weight

    for r in results:
        timestamp = (r.get("metadata") or {}).get("timestamp") or ""
        if not timestamp:
            # No timestamp — treat as moderately old (keep original score mostly)
            recency_score = 0.3
        else:
            try:
                ts = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age_hours = max(0, (now - ts).total_seconds() / 3600)
                recency_score = math.exp(-0.693 * age_hours / half_life_hours)
            except (ValueError, TypeError):
                recency_score = 0.3

        original_score = r.get("score", 0.5)
        r["score"] = round(original_score * relevance_weight + recency_score * recency_weight, 4)

    results.sort(key=lambda x: x.get("score", 0), reverse=True)
    return results


# ── Context budget defaults ──────────────────────────────────────────────────

class ContextBudget:
    """Token budget allocation for context assembly.

    Prevents recalled memories from overflowing the LLM context window.
    """

    def __init__(
        self,
        total_budget: int = 16000,
        personality_budget: int = 2000,
        recall_budget: int = 4000,
        working_budget: int = 8000,
        response_reserve: int = 2000,
    ):
        self.total_budget = total_budget
        self.personality_budget = personality_budget
        self.recall_budget = recall_budget
        self.working_budget = working_budget
        self.response_reserve = response_reserve

    def available_for_recall(self) -> int:
        """Max tokens available for recalled memories."""
        return self.recall_budget


class MemoryManager:
    """Central interface for all memory operations across all 5 tiers.

    Enhanced with graph-augmented retrieval, context budgeting, and
    automatic post-conversation extraction.

    Usage:
        manager = MemoryManager(project_id="my-project")
        await manager.remember("Alice is our main client", tier="semantic")
        results = await manager.recall("who is our client?")
    """

    def __init__(
        self,
        project_id: str = "default",
        session_id: str | None = None,
        embedding_fn: Any = None,
        context_budget: ContextBudget | None = None,
        embedding_model: str | None = None,
        embedding_batch_fn: Any = None,
    ):
        self.project_id = project_id
        self.session_id = session_id
        self.embedding_fn = embedding_fn
        self.embedding_batch_fn = embedding_batch_fn
        self.embedding_model = embedding_model
        self.context_budget = context_budget or ContextBudget()

        # Initialize all tiers. (Per-conversation working memory lives in
        # AgentCore.working_memory; consolidation reads episodic history.)
        self.episodic = EpisodicMemory(
            project_id=project_id,
            embedding_fn=embedding_fn,
        )
        self.semantic = SemanticMemory(
            project_id=project_id,
            embedding_fn=embedding_fn,
            embedding_model=embedding_model,
            embedding_batch_fn=embedding_batch_fn,
        )
        self.graph = GraphMemory(project_id=project_id)
        self.archival = ArchivalMemory(project_id=project_id)

    def set_active_project(self, project_id: str) -> None:
        """Switch all memory tiers to a different project namespace."""
        self.project_id = project_id
        self.episodic = EpisodicMemory(
            project_id=project_id,
            embedding_fn=self.embedding_fn,
        )
        self.semantic = SemanticMemory(
            project_id=project_id,
            embedding_fn=self.embedding_fn,
            embedding_model=self.embedding_model,
            embedding_batch_fn=self.embedding_batch_fn,
        )
        self.graph = GraphMemory(project_id=project_id)
        self.archival = ArchivalMemory(project_id=project_id)
        logger.info(f"Memory manager switched to project: {project_id}")

    async def remember(
        self,
        content: str,
        tier: str = "semantic",
        metadata: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> str:
        """Store content in the specified memory tier."""
        sid = session_id or self.session_id or "default"
        meta = metadata or {}

        if tier in ("episodic", "working"):
            # "working" is accepted for old callers: a session-scoped note.
            note_id = await self.episodic.add_note(
                content=content,
                project_id=self.project_id,
                session_id=sid,
                tags=meta.get("tags", []) + (["working"] if tier == "working" else []),
            )
            return f"stored:episodic:{note_id}"

        elif tier == "semantic":
            doc_id = await self.semantic.store(content=content, metadata=meta)
            return f"stored:semantic:{doc_id}"

        elif tier == "graph":
            # Extract entities/relationships from the text into the graph
            # (same pipeline as conversation extraction).
            from memory.extraction import run_extraction
            stats = await run_extraction(
                messages=[{"role": "user", "content": content}],
                memory_manager=self, project_id=self.project_id, session_id=sid,
                min_messages=1,
            )
            return (f"stored:graph:{stats.get('entities', 0)} entities, "
                    f"{stats.get('relationships', 0)} relationships")

        elif tier == "archival":
            filename = await self.archival.append_note(content)
            return f"stored:archival:{filename}"

        else:
            logger.warning(f"Unknown memory tier: {tier}, defaulting to semantic")
            doc_id = await self.semantic.store(content=content, metadata=meta)
            return f"stored:semantic:{doc_id}"

    async def recall(
        self,
        query: str,
        tiers: list[str] | None = None,
        project_id: str | None = None,
        limit_per_tier: int = 3,
        context_focus: str | None = None,
        in_context: set[str] | None = None,
        min_relevance: float = 0.0,
        session_fallback: str | None = None,
        session_min_similarity: float = 0.0,
        session_exclude: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Search across memory tiers with graph augmentation and budget management.

        ``in_context``: message texts the caller already sends the model (the
        conversation so far and the new message). Episodic hits with the same
        text are dropped BEFORE the per-tier cut, so they neither repeat what
        the model can already see nor push older sessions' messages out.

        ``min_relevance``: when the results were reranked, drop those the
        reranker scores below it (0-1). Pre-recall passes
        settings.recall_min_relevance so unrelated memories are not injected;
        a failed or timed-out rerank drops nothing.

        ``session_fallback``: the current conversation's id. Up to
        SESSION_FALLBACK_MAX of its messages not in ``session_exclude``
        (default: ``in_context``) are added when their embedding similarity
        is >= ``session_min_similarity``, whatever the reranker thought:
        cross-encoders score questions ABOUT the conversation ("did I mention
        a speech?") near zero even when the right turn is ranked first.
        AgentCore excludes only the newest messages, so a relevant turn deep
        in a long prompt is ALSO repeated next to the question — small models
        miss facts in the middle of a long history.

        Returns list of dicts with keys: content, source, score, metadata, tier
        """
        active_project = project_id or self.project_id
        if tiers is None:
            tiers = ["semantic", "episodic", "graph"]

        all_results: list[dict[str, Any]] = []

        if "semantic" in tiers:
            try:
                sem_results = await self.semantic.search(query, n=limit_per_tier)
                for r in sem_results:
                    r["source"] = "semantic"
                    r["tier"] = "semantic"
                all_results.extend(sem_results)
            except Exception as e:
                logger.error(f"Semantic recall error: {e}")

        if "episodic" in tiers:
            try:
                ep_results = await self.episodic.search_messages(
                    query=query,
                    project_id=active_project,
                    limit=limit_per_tier * 2,
                )
                if in_context:
                    ep_results = [r for r in ep_results
                                  if (r.get("content") or "").strip() not in in_context]
                for r in ep_results[:limit_per_tier]:
                    all_results.append({
                        "id": r.get("id", ""),
                        "content": f"[{r.get('role', 'unknown')}] {r['content']}",
                        "source": "episodic",
                        "tier": "episodic",
                        "score": r.get("score", 0.5),
                        "metadata": {
                            "session_id": r.get("session_id"),
                            "timestamp": r.get("timestamp"),
                        },
                    })
            except Exception as e:
                logger.error(f"Episodic recall error: {e}")

        if "graph" in tiers:
            try:
                graph_results = await self.graph.search_nodes(query, limit=limit_per_tier * 2)
                # Only the matched nodes' edges (was: newest 500 edges of
                # the whole project, joined + sorted on every turn).
                all_edges = await self.graph.edges_for_nodes(
                    [r["id"] for r in graph_results[:limit_per_tier]], per_node=10,
                )
                edge_index: dict[str, list[str]] = {}
                for e in all_edges:
                    a, b, rel = e["node_a_label"], e["node_b_label"], e["relationship"]
                    edge_index.setdefault(a, []).append(f"  → {rel}: {b}")
                    edge_index.setdefault(b, []).append(f"  ← {rel}: {a}")

                for r in graph_results[:limit_per_tier]:
                    label = r["label"]
                    rels = edge_index.get(label, [])
                    rel_text = ("\n" + "\n".join(rels)) if rels else ""
                    all_results.append({
                        "id": r["id"],
                        "content": f"[graph:{r['node_type']}] {label}{rel_text}",
                        "source": "graph",
                        "tier": "graph",
                        "score": 0.65,
                        "metadata": r.get("metadata", {}),
                    })
            except Exception as e:
                logger.error(f"Graph recall error: {e}")

        # Sort by score descending
        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)

        # Rerank if available
        if all_results:
            try:
                from models.provider import get_provider_for
                reranker = get_provider_for("rerank")
                if reranker is not None:
                    all_results = await self._rerank(query, all_results, reranker)
            except Exception as e:
                logger.warning("Reranking failed, using original order: %s", e)
            if min_relevance > 0 and all_results and all_results[0].get("reranked"):
                kept = [r for r in all_results if r.get("score", 0) >= min_relevance]
                if len(kept) < len(all_results):
                    logger.info("Recall: dropped %d of %d results below relevance %.2f",
                                len(all_results) - len(kept), len(all_results), min_relevance)
                all_results = kept

        if session_fallback and session_min_similarity > 0:
            all_results = await self._add_session_fallback(
                query, active_project, session_fallback,
                in_context or set() if session_exclude is None else session_exclude,
                session_min_similarity, all_results,
            )

        # Graph-augmented enrichment: expand entities found in top results
        all_results = await self._graph_augment(all_results)

        # Apply context-focus recency re-weighting
        focus = context_focus or "balanced"
        all_results = _apply_context_focus(all_results, focus)

        # Apply context budget: trim results to fit recall token budget
        all_results = self._apply_budget(all_results)

        return all_results

    async def _graph_augment(
        self,
        results: list[dict[str, Any]],
        max_augmentations: int = 3,
    ) -> list[dict[str, Any]]:
        """Enrich top results with graph relationships.

        For entities mentioned in semantic/episodic results, fetch their
        graph neighbors and append structured context.
        """
        if not results:
            return results

        augmented_entities: set[str] = set()
        augmented_items: list[dict[str, Any]] = []

        for result in results[:8]:  # Only check top results for entity mentions
            if len(augmented_entities) >= max_augmentations:
                break
            if result.get("tier") == "graph":  # Don't re-augment graph results
                continue
            try:
                mentioned = await self.graph.nodes_mentioned_in(
                    result.get("content", ""), limit=max_augmentations,
                )
            except Exception:
                return results
            for node in mentioned:
                label_lower = node["label"].lower()
                if label_lower in augmented_entities or len(augmented_entities) >= max_augmentations:
                    continue
                augmented_entities.add(label_lower)
                # Fetch 1-hop neighbors
                try:
                    neighbors = await self.graph.find_related(node["id"], depth=1, max_nodes=10)
                    if neighbors:
                        rel_lines = [
                            f"  {node['label']} → {n['relationship']}: {n['label']}"
                            for n in neighbors
                        ]
                        augmented_items.append({
                            "id": f"graph-aug-{node['id']}",
                            "content": f"[graph context for '{node['label']}']\n" + "\n".join(rel_lines),
                            "source": "graph_augmentation",
                            "tier": "graph",
                            "score": result.get("score", 0.5) * 0.8,  # Slightly lower than parent
                            "metadata": {"augmented_from": node["label"]},
                        })
                except Exception as e:
                    logger.debug("Graph augmentation failed for %s: %s", node["label"], e)

        if augmented_items:
            logger.info("Graph augmentation added %d context blocks", len(augmented_items))
            results.extend(augmented_items)
            results.sort(key=lambda x: x.get("score", 0), reverse=True)

        return results

    async def _add_session_fallback(self, query, project_id, session_id, in_context, min_sim, results):
        try:
            hits = await self.episodic.search_messages(query=query, project_id=project_id,
                                                       limit=10, session_id=session_id)
        except Exception as e:
            logger.warning("Session fallback search failed: %s", e)
            return results
        have = {r.get("content") for r in results}
        added = []
        for r in hits:
            if len(added) >= SESSION_FALLBACK_MAX:
                break
            text = (r.get("content") or "").strip()
            content = f"[{r.get('role', 'unknown')}] {r.get('content', '')}"
            if r.get("similarity", 0) < min_sim or text in in_context or content in have:
                continue
            added.append({
                "id": r.get("id", ""), "content": content, "source": "episodic", "tier": "episodic",
                "score": round(r["similarity"], 4),
                "metadata": {"session_id": session_id, "timestamp": r.get("timestamp"), "earlier_in_session": True},
            })
        if added:
            logger.info("Recall: %d earlier turn(s) of this conversation added by similarity", len(added))
        return results + added

    def _apply_budget(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Trim recalled results to fit within the recall token budget.

        Also deduplicates near-identical content.
        """
        budget = self.context_budget.available_for_recall()
        used = 0
        budgeted: list[dict[str, Any]] = []
        seen_content: set[str] = set()

        for r in results:
            content = r.get("content", "")
            # Simple dedup: skip if first 100 chars match something already included
            content_key = content[:100].lower().strip()
            if content_key in seen_content:
                continue
            seen_content.add(content_key)

            tokens = _estimate_tokens(content)
            if used + tokens > budget:
                # Try to fit a truncated version
                remaining = budget - used
                if remaining > 50:
                    truncated = content[:remaining * CHARS_PER_TOKEN]
                    r = {**r, "content": truncated + "...", "truncated": True}
                    budgeted.append(r)
                break
            budgeted.append(r)
            used += tokens

        if len(budgeted) < len(results):
            logger.debug(
                "Context budget applied: %d/%d results included (%d tokens used of %d)",
                len(budgeted), len(results), used, budget,
            )

        return budgeted

    async def _rerank(
        self,
        query: str,
        results: list[dict[str, Any]],
        reranker,
    ) -> list[dict[str, Any]]:
        """Rerank results using the reranker provider's /v1/rerank endpoint."""
        import httpx

        documents = [r.get("content", "")[:500] for r in results]
        url = f"{reranker.base_url}/rerank"
        payload = {
            "model": reranker.model,
            "query": query,
            "documents": documents,
            "top_n": len(documents),
        }
        headers = {"Content-Type": "application/json"}
        if reranker.api_key and reranker.api_key.lower() not in ("", "none", "ollama"):
            headers["Authorization"] = f"Bearer {reranker.api_key}"

        timeout = rerank_timeout()
        logger.info("Reranking %d results with model %s", len(documents), reranker.model)
        try:
            from utils.http import pooled_client
            async with pooled_client(timeout=timeout) as client:
                # Hard total cap: httpx timeouts are per phase, and a model that
                # is still loading holds the connection open without sending.
                resp = await asyncio.wait_for(
                    client.post(url, headers=headers, json=payload), timeout=timeout,
                )
                resp.raise_for_status()
                data = resp.json()

            ranked = data.get("results", [])
            if ranked:
                ranked = sorted(ranked, key=lambda x: x.get("relevance_score", 0), reverse=True)
                probs = _as_probabilities([item.get("relevance_score", 0) for item in ranked])
                reranked = []
                for item, p in zip(ranked, probs):
                    idx = item.get("index", 0)
                    if idx < len(results):
                        entry = results[idx].copy()
                        entry["score"] = round(p, 4)
                        entry["rerank_raw"] = item.get("relevance_score", 0)
                        entry["reranked"] = True
                        reranked.append(entry)
                logger.info("Reranking complete — top score: %.4f", reranked[0]["score"] if reranked else 0)
                return reranked
        except (asyncio.TimeoutError, httpx.TimeoutException):
            logger.warning(
                "Rerank timed out after %.1fs (is %s still loading?) — keeping the original order",
                timeout, reranker.model,
            )
        except httpx.HTTPStatusError as e:
            logger.warning("Rerank endpoint returned %s, skipping rerank", e.response.status_code)
        except Exception as e:
            logger.warning("Rerank request failed: %s", e)

        return results

    async def audit_memory(
        self,
        tier: str,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Return all memories for a tier (for inspection/editing in the UI)."""
        active_project = project_id or self.project_id

        if tier == "episodic":
            messages = await self.episodic.get_all_messages(project_id=active_project, limit=200)
            notes = await self.episodic.get_notes(project_id=active_project, limit=50)
            return {
                "tier": "episodic",
                "messages": messages,
                "notes": notes,
                "total_messages": len(messages),
            }

        elif tier == "semantic":
            items = await self.semantic.list_memories(limit=100)
            count = await self.semantic.count()
            return {
                "tier": "semantic",
                "items": items,
                "total": count,
            }

        elif tier == "graph":
            nodes = await self.graph.list_nodes(limit=200)
            edges = await self.graph.list_edges(limit=500)
            return {
                "tier": "graph",
                "nodes": nodes,
                "edges": edges,
            }

        elif tier == "archival":
            files = await self.archival.list_files()
            notes = await self.archival.list_notes()
            summary = await self.archival.get_project_summary()
            return {
                "tier": "archival",
                "files": files,
                "notes": notes,
                "project_summary": summary,
            }

        return {"tier": tier, "error": "Unknown tier"}

    async def consolidate_session(self, message_count: int = 40) -> str:
        """Consolidate this session: summarize + extract structured knowledge.

        Reads the session's recent messages from episodic memory (the
        persisted chat history), so it works from chat, the API and jobs.
        """
        if not self.session_id or self.session_id == "current":
            return "No session to consolidate (pass the chat's session id)."
        recent = await self.episodic.get_recent_messages(
            project_id=self.project_id, session_id=self.session_id, limit=message_count,
        )
        messages = [
            {"role": m.get("role"), "content": m.get("content") or ""}
            for m in recent                    # already oldest-first
        ]
        if not messages:
            return "No messages to consolidate."

        # Build raw transcript from recent messages
        summary_lines = []
        for msg in messages[-20:]:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")[:300]
            if role in ("user", "assistant"):
                summary_lines.append(f"{role}: {content}")

        if not summary_lines:
            return "No user/assistant messages to consolidate."

        raw_transcript = "\n".join(summary_lines)

        # 1. Summarize with prefill model (existing behavior)
        session_summary = None
        try:
            from models.provider import get_provider_for
            prefill = get_provider_for("summarize")
            logger.info("Using prefill model (%s) for session consolidation", prefill.model)
            result = await prefill.chat_complete([
                {"role": "system", "content": (
                    "You are a memory consolidation assistant. Summarise the following "
                    "conversation into a concise paragraph highlighting the key facts, "
                    "decisions, and action items. Omit filler and pleasantries. "
                    "Write in third person past tense."
                )},
                {"role": "user", "content": raw_transcript},
            ])
            summary = (result.get("content") or "").strip()
            if summary:
                session_summary = f"Session summary (session_id={self.session_id}):\n{summary}"
                logger.info("Prefill model generated %d-char consolidation summary", len(summary))
        except Exception as e:
            logger.warning("Prefill consolidation failed, falling back to raw transcript: %s", e)

        if not session_summary:
            session_summary = f"Session summary (session_id={self.session_id}):\n{raw_transcript}"

        # Store summary to semantic memory
        doc_id = await self.semantic.store(
            content=session_summary,
            metadata={
                "type": "session_summary",
                "session_id": self.session_id or "unknown",
                "project_id": self.project_id,
            },
        )

        # 2. Run extraction pipeline (new behavior)
        extraction_stats = {"entities": 0, "relationships": 0, "facts": 0, "user_preferences": 0}
        try:
            from memory.extraction import run_extraction
            extraction_stats = await run_extraction(
                messages=messages,
                memory_manager=self,
                project_id=self.project_id,
                session_id=self.session_id,
            )
            logger.info("Extraction during consolidation: %s", extraction_stats)
        except Exception as e:
            logger.warning("Extraction during consolidation failed: %s", e)

        total_extracted = sum(extraction_stats.values())
        logger.info(f"Session consolidated: summary={doc_id}, extracted={total_extracted} items")
        return (
            f"Session consolidated. Summary stored as {doc_id}. "
            f"Extracted {extraction_stats['entities']} entities, "
            f"{extraction_stats['relationships']} relationships, "
            f"{extraction_stats['facts']} facts, "
            f"{extraction_stats['user_preferences']} preferences."
        )

    async def run_extraction_on_recent(
        self,
        message_count: int = 20,
    ) -> dict[str, int]:
        """Run the extraction pipeline on recent episodic messages.

        Can be called periodically or after a configurable number of messages.
        """
        try:
            messages = await self.episodic.get_recent_messages(
                project_id=self.project_id,
                session_id=self.session_id,
                limit=message_count,
            )
            if not messages:
                return {"entities": 0, "relationships": 0, "facts": 0, "user_preferences": 0}

            from memory.extraction import run_extraction
            return await run_extraction(
                messages=messages,
                memory_manager=self,
                project_id=self.project_id,
                session_id=self.session_id,
            )
        except Exception as e:
            logger.error("Extraction on recent messages failed: %s", e)
            return {"entities": 0, "relationships": 0, "facts": 0, "user_preferences": 0}

    async def index_workspace_file(self, file_path: str, force: bool = False) -> dict:
        """Index a single workspace file into semantic memory and graph.

        Convenience method that wraps FileIndexer for single-file use.
        """
        from pathlib import Path
        from memory.file_indexer import FileIndexer
        indexer = FileIndexer(memory_manager=self, project_id=self.project_id)
        return await indexer.index_file(Path(file_path), force=force)

    async def index_workspace_directory(self, directory: str, force: bool = False) -> dict:
        """Index all supported files in a workspace directory."""
        from pathlib import Path
        from memory.file_indexer import FileIndexer
        indexer = FileIndexer(memory_manager=self, project_id=self.project_id)
        return await indexer.index_directory(Path(directory), force=force)

    async def index_artifact(self, artifact_id: str, force: bool = False) -> dict:
        """Index a text artifact into semantic + graph memory.

        Reads the artifact from the artifact store, then runs the
        same chunk/embed/graph pipeline used for workspace files
        (FileIndexer.index_text). The artifact's tags and id are
        forwarded as frontmatter extras so chunks are filterable
        by artifact and tags later.
        """
        from artifacts.store import get_store, is_text_type
        from memory.file_indexer import FileIndexer

        store = get_store()
        a = store.get(artifact_id)
        if not a or a.get("deleted_at"):
            return {"skipped": True, "reason": "artifact not found"}
        if not is_text_type(a["content_type"]):
            return {"skipped": True, "reason": f"non-text content_type {a['content_type']}"}

        text = a.get("content") or ""
        path = a["path"]
        is_md = (a["content_type"] or "").startswith("text/markdown") or path.endswith((".md", ".markdown"))
        extras = {
            "artifact_id": artifact_id,
            "artifact_path": path,
            "tags": a.get("tags") or [],
        }
        if a.get("title"):
            extras["title"] = a["title"]

        indexer = FileIndexer(memory_manager=self, project_id=self.project_id)
        result = await indexer.index_text(
            text,
            virtual_path=f"artifact://{artifact_id}/{path}",
            is_markdown=is_md,
            frontmatter_extras=extras,
            force=force,
            source_label=path,
        )
        # Cross-artifact similarity: always upsert topic embeddings here so
        # they exist for later backfill or manual link runs. The link
        # pipeline itself (SEMANTICALLY_SIMILAR_TO edges + merge proposals)
        # runs elsewhere — after ingest when the originating source adapter
        # sets auto_link_similarity, or via the link_topic_similarity tool.
        try:
            from memory.topic_embeddings import upsert_topic_embedding
            # Walk the frontmatter topics ourselves rather than re-
            # parsing — index_text already parsed it but didn't
            # surface the topic list.
            import re as _re, yaml as _yaml
            mfm = _re.match(r"^---\n(.*?)\n---", text, _re.DOTALL)
            if mfm:
                fm = _yaml.safe_load(mfm.group(1)) or {}
                for t in (fm.get("topics") or []):
                    if not isinstance(t, dict):
                        continue
                    label = (t.get("label") or "").strip()
                    if not label:
                        continue
                    await upsert_topic_embedding(
                        self.semantic,
                        label=label,
                        topic_type=(t.get("type") or "concept"),
                        project_id=self.project_id,
                        artifact_id=artifact_id,
                        confidence=t.get("confidence"),
                    )
        except Exception as _e:
            logger.debug("topic embedding upsert failed for %s: %s", artifact_id, _e)
        return result


def create_memory_manager(
    project_id: str = "default",
    session_id: str | None = None,
    provider: Any = None,
) -> MemoryManager:
    """Factory function to create a MemoryManager with optional embedding support.

    Uses the dedicated embedding provider when available so embeddings can be
    routed to a different endpoint/model than the primary chat LLM.
    """
    embedding_fn = None
    embedding_batch_fn = None
    embedding_model = None
    try:
        from models.provider import get_provider_for
        emb_provider = get_provider_for("embed")
        embedding_fn = emb_provider.embed
        embedding_batch_fn = getattr(emb_provider, "embed_many", None)
        embedding_model = getattr(emb_provider, "embedding_model", None)
    except Exception:
        if provider:
            embedding_fn = provider.embed
            embedding_batch_fn = getattr(provider, "embed_many", None)
            embedding_model = getattr(provider, "embedding_model", None)
    return MemoryManager(
        project_id=project_id,
        session_id=session_id,
        embedding_fn=embedding_fn,
        embedding_model=embedding_model,
        embedding_batch_fn=embedding_batch_fn,
    )
