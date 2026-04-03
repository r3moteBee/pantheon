"""Memory manager — orchestrates all 5 memory tiers with active curation.

Enhanced with:
- Graph-augmented retrieval (semantic results enriched with graph context)
- Context budget management (token-aware recall limits)
- Automatic post-conversation extraction pipeline
- Episodic semantic search support
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any

from memory.working import WorkingMemory
from memory.episodic import EpisodicMemory
from memory.semantic import SemanticMemory
from memory.graph import GraphMemory
from memory.archival import ArchivalMemory

logger = logging.getLogger(__name__)

# ── Token estimation ─────────────────────────────────────────────────────────

CHARS_PER_TOKEN = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // CHARS_PER_TOKEN)


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
        max_working_tokens: int = 8000,
        context_budget: ContextBudget | None = None,
    ):
        self.project_id = project_id
        self.session_id = session_id
        self.embedding_fn = embedding_fn
        self.context_budget = context_budget or ContextBudget()

        # Initialize all tiers
        self.working = WorkingMemory(max_tokens=max_working_tokens)
        self.episodic = EpisodicMemory(
            project_id=project_id,
            embedding_fn=embedding_fn,
        )
        self.semantic = SemanticMemory(project_id=project_id, embedding_fn=embedding_fn)
        self.graph = GraphMemory(project_id=project_id)
        self.archival = ArchivalMemory(project_id=project_id)

    def set_active_project(self, project_id: str) -> None:
        """Switch all memory tiers to a different project namespace."""
        self.project_id = project_id
        self.episodic = EpisodicMemory(
            project_id=project_id,
            embedding_fn=self.embedding_fn,
        )
        self.semantic = SemanticMemory(project_id=project_id, embedding_fn=self.embedding_fn)
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

        if tier == "working":
            self.working.add_message("system", content)
            return "stored:working"

        elif tier == "episodic":
            note_id = await self.episodic.add_note(
                content=content,
                project_id=self.project_id,
                session_id=sid,
                tags=meta.get("tags", []),
            )
            return f"stored:episodic:{note_id}"

        elif tier == "semantic":
            doc_id = await self.semantic.store(content=content, metadata=meta)
            return f"stored:semantic:{doc_id}"

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
    ) -> list[dict[str, Any]]:
        """Search across memory tiers with graph augmentation and budget management.

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
                all_edges = await self.graph.list_edges(limit=500)
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
                from models.provider import get_reranker_provider
                reranker = get_reranker_provider()
                if reranker is not None:
                    all_results = await self._rerank(query, all_results, reranker)
            except Exception as e:
                logger.warning("Reranking failed, using original order: %s", e)

        # Graph-augmented enrichment: expand entities found in top results
        all_results = await self._graph_augment(all_results)

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

        # Collect entity labels from graph
        try:
            all_nodes = await self.graph.list_nodes(limit=500)
        except Exception:
            return results

        if not all_nodes:
            return results

        node_labels = {n["label"].lower(): n for n in all_nodes}
        augmented_entities: set[str] = set()
        augmented_items: list[dict[str, Any]] = []

        for result in results[:8]:  # Only check top results for entity mentions
            content = result.get("content", "").lower()
            for label_lower, node in node_labels.items():
                if (
                    label_lower in content
                    and label_lower not in augmented_entities
                    and len(augmented_entities) < max_augmentations
                    and result.get("tier") != "graph"  # Don't re-augment graph results
                ):
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

        logger.info("Reranking %d results with model %s", len(documents), reranker.model)
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()

            ranked = data.get("results", [])
            if ranked:
                reranked = []
                for item in sorted(ranked, key=lambda x: x.get("relevance_score", 0), reverse=True):
                    idx = item.get("index", 0)
                    if idx < len(results):
                        entry = results[idx].copy()
                        entry["score"] = round(item.get("relevance_score", 0), 4)
                        entry["reranked"] = True
                        reranked.append(entry)
                logger.info("Reranking complete — top score: %.4f", reranked[0]["score"] if reranked else 0)
                return reranked
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

        if tier == "working":
            return {
                "tier": "working",
                "items": [
                    {"role": m.role, "content": m.content, "timestamp": m.timestamp}
                    for m in self.working.get_messages(as_dicts=False)
                ],
                "token_count": self.working.get_token_count(),
            }

        elif tier == "episodic":
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

    async def consolidate_session(self) -> str:
        """Consolidate current session: summarize + extract structured knowledge.

        Enhanced to run the extraction pipeline in addition to the
        original summarization flow.
        """
        messages = self.working.get_messages(as_dicts=True)
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
            from models.provider import get_prefill_provider
            prefill = get_prefill_provider()
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

        # Clear working memory
        self.working.clear()

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
    try:
        from models.provider import get_embedding_provider
        emb_provider = get_embedding_provider()
        embedding_fn = emb_provider.embed
    except Exception:
        if provider:
            embedding_fn = provider.embed
    return MemoryManager(
        project_id=project_id,
        session_id=session_id,
        embedding_fn=embedding_fn,
    )
