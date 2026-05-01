"""Tier 3: Semantic memory — ChromaDB vector store for knowledge retrieval."""
from __future__ import annotations
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import asyncio as _asyncio
logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_collection_name(name: str) -> str:
    """ChromaDB collection names must be 3-63 chars, alphanumeric + hyphens."""
    import re
    sanitized = re.sub(r'[^a-zA-Z0-9-]', '-', name)
    sanitized = re.sub(r'-+', '-', sanitized).strip('-')
    if len(sanitized) < 3:
        sanitized = f"mem-{sanitized}"
    return sanitized[:63]


class SemanticMemory:
    """Tier 3: Semantic vector memory using ChromaDB.
    
    Stores embeddings of key insights, facts, and summaries from past sessions.
    Each project gets its own ChromaDB collection for namespace isolation.
    """

    def __init__(
        self,
        project_id: str = "default",
        embedding_fn: Any = None,
        embedding_model: str | None = None,
    ):
        self.project_id = project_id
        self.collection_name = _sanitize_collection_name(f"proj-{project_id}")
        self._embedding_fn = embedding_fn
        # Identifier for the model that produces vectors via _embedding_fn.
        # Tagged onto every stored vector so we can detect mismatches at
        # recall time and re-embed when the user changes embedding model.
        self._embedding_model = embedding_model or "default"
        # Track whether we've warned about a model mismatch this session,
        # so the warning fires once and not on every search.
        self._mismatch_warned = False
        self._client = None
        self._collection = None

        # Read connection config from settings
        from config import get_settings
        cfg = get_settings()
        self._chroma_host = cfg.chroma_host.strip() if cfg.chroma_host else ""
        self._chroma_port = cfg.chroma_port

    def _get_client(self):
        if self._client is None:
            import chromadb
            if self._chroma_host:
                try:
                    self._client = chromadb.HttpClient(
                        host=self._chroma_host,
                        port=self._chroma_port,
                    )
                    # Verify the connection actually works
                    self._client.heartbeat()
                    logger.debug(f"Connected to ChromaDB at {self._chroma_host}:{self._chroma_port}")
                except Exception as e:
                    logger.warning(f"ChromaDB HTTP connection failed: {e}. Falling back to local persistent client.")
                    self._client = None

            if self._client is None:
                from config import get_settings
                data_dir = get_settings().data_dir or "data"
                chroma_path = f"{data_dir}/chroma/{self.project_id}"
                self._client = chromadb.PersistentClient(path=chroma_path)
                logger.debug(f"Using local ChromaDB at {chroma_path}")
        return self._client

    def _get_collection(self):
        if self._collection is None:
            client = self._get_client()
            # Use cosine distance for semantic similarity
            self._collection = client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    async def _get_collection_async(self):
        """Thread-safe async wrapper for _get_collection."""
        return await _asyncio.to_thread(self._get_collection)

    async def store(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
        doc_id: str | None = None,
    ) -> str:
        """Store a piece of content with its embedding."""
        doc_id = doc_id or str(uuid.uuid4())
        meta = {
            "project_id": self.project_id,
            "created_at": _now_iso(),
            "embedding_model": self._embedding_model,
            "embedded_at": _now_iso(),
            **(metadata or {}),
        }
        # Flatten metadata values to strings (ChromaDB requirement)
        meta = {k: str(v) for k, v in meta.items()}

        try:
            collection = await self._get_collection_async()
            if self._embedding_fn:
                embedding = await self._embedding_fn(content)
                await _asyncio.to_thread(
                    collection.upsert,
                    ids=[doc_id],
                    documents=[content],
                    metadatas=[meta],
                    embeddings=[embedding],
                )
            else:
                # Let ChromaDB use its default embedding function
                await _asyncio.to_thread(
                    collection.upsert,
                    ids=[doc_id],
                    documents=[content],
                    metadatas=[meta],
                )
            logger.debug(f"Stored semantic memory: {doc_id}")
            return doc_id
        except Exception as e:
            logger.error(f"Failed to store semantic memory: {e}")
            raise

    async def search(
        self,
        query: str,
        n: int = 5,
        where: dict | None = None,
    ) -> list[dict[str, Any]]:
        """Search for semantically similar memories."""
        try:
            collection = await self._get_collection_async()
            count = await _asyncio.to_thread(collection.count)
            if count == 0:
                return []
            kwargs: dict[str, Any] = {
                "n_results": min(n, count),
                "include": ["documents", "metadatas", "distances"],
            }
            # Use custom embedding fn so search matches the model used at store time
            if self._embedding_fn:
                logger.debug("Embedding query with custom model for semantic search")
                query_embedding = await self._embedding_fn(query)
                kwargs["query_embeddings"] = [query_embedding]
            else:
                # Fall back to ChromaDB's default embedding
                kwargs["query_texts"] = [query]
            if where:
                kwargs["where"] = where

            results = await _asyncio.to_thread(collection.query, **kwargs)

            items = []
            if results and results.get("ids") and results["ids"][0]:
                for i, doc_id in enumerate(results["ids"][0]):
                    distance = results["distances"][0][i] if results.get("distances") else 1.0
                    # Convert cosine distance to similarity score (0-1)
                    similarity = max(0.0, 1.0 - distance)
                    items.append({
                        "id": doc_id,
                        "content": results["documents"][0][i],
                        "metadata": results["metadatas"][0][i] if results.get("metadatas") else {},
                        "score": round(similarity, 4),
                        "source": "semantic",
                    })
            self._maybe_warn_model_mismatch(items)
            return sorted(items, key=lambda x: x["score"], reverse=True)
        except Exception as e:
            logger.error(f"Semantic search failed: {e}")
            return []

    def _maybe_warn_model_mismatch(self, items: list[dict[str, Any]]) -> None:
        """Log once per session if any retrieved vector was embedded with a
        different model than the one currently configured. Mismatched vectors
        produce unreliable similarity scores; the user should run the
        /api/memory/reembed maintenance endpoint to fix them."""
        if self._mismatch_warned or self._embedding_model == "default":
            return
        for item in items:
            md = item.get("metadata") or {}
            stored_model = md.get("embedding_model")
            if stored_model and stored_model != self._embedding_model:
                logger.warning(
                    "Semantic recall returned vectors embedded with %r; "
                    "current model is %r. Similarity scores against "
                    "mismatched vectors are unreliable. POST /api/memory/reembed "
                    "to re-embed stale vectors.",
                    stored_model,
                    self._embedding_model,
                )
                self._mismatch_warned = True
                return

    async def delete(self, doc_id: str) -> bool:
        """Delete a memory by ID."""
        try:
            collection = await self._get_collection_async()
            await _asyncio.to_thread(collection.delete, ids=[doc_id])
            return True
        except Exception as e:
            logger.error(f"Failed to delete semantic memory {doc_id}: {e}")
            return False

    async def list_memories(
        self,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List all semantic memories for this project."""
        try:
            collection = await self._get_collection_async()
            total = await _asyncio.to_thread(collection.count)
            if total == 0:
                return []
            results = await _asyncio.to_thread(
                collection.get,
                include=["documents", "metadatas"],
                limit=limit,
                offset=offset,
            )
            items = []
            if results and results.get("ids"):
                for i, doc_id in enumerate(results["ids"]):
                    items.append({
                        "id": doc_id,
                        "content": results["documents"][i] if results.get("documents") else "",
                        "metadata": results["metadatas"][i] if results.get("metadatas") else {},
                        "source": "semantic",
                    })
            return items
        except Exception as e:
            logger.error(f"Failed to list semantic memories: {e}")
            return []

    async def count(self) -> int:
        """Return the number of stored memories."""
        try:
            collection = await self._get_collection_async()
            return await _asyncio.to_thread(collection.count)
        except Exception:
            return 0
    async def list_by_model(
        self,
        embedding_model: str | None = None,
        limit: int = 10000,
    ) -> list[dict[str, Any]]:
        """List stored memories filtered by embedding_model metadata.

        If embedding_model is None, returns all. Used by the re-embed
        maintenance endpoint to find stale vectors.
        """
        try:
            collection = await self._get_collection_async()
            kwargs: dict[str, Any] = {
                "include": ["documents", "metadatas"],
                "limit": limit,
            }
            if embedding_model is not None:
                kwargs["where"] = {"embedding_model": embedding_model}
            results = await _asyncio.to_thread(collection.get, **kwargs)
            items: list[dict[str, Any]] = []
            if results and results.get("ids"):
                for i, doc_id in enumerate(results["ids"]):
                    items.append({
                        "id": doc_id,
                        "content": results["documents"][i] if results.get("documents") else "",
                        "metadata": results["metadatas"][i] if results.get("metadatas") else {},
                    })
            return items
        except Exception as e:
            logger.error(f"Failed to list semantic memories by model: {e}")
            return []

    async def reembed_stale(self) -> dict[str, int]:
        """Re-embed every vector whose embedding_model metadata differs from
        the currently configured model. Returns counts of scanned/re-embedded.
        Requires self._embedding_fn to be configured.
        """
        if not self._embedding_fn:
            logger.warning("reembed_stale called without an embedding_fn; nothing to do")
            return {"scanned": 0, "reembedded": 0, "skipped_no_fn": 1}

        collection = await self._get_collection_async()
        results = await _asyncio.to_thread(
            collection.get,
            include=["documents", "metadatas"],
        )
        ids = results.get("ids") or []
        scanned = len(ids)
        reembedded = 0
        for i, doc_id in enumerate(ids):
            md = (results.get("metadatas") or [])[i] or {}
            stored_model = md.get("embedding_model")
            if stored_model == self._embedding_model:
                continue
            content = (results.get("documents") or [])[i] or ""
            if not content:
                continue
            try:
                new_emb = await self._embedding_fn(content)
                new_md = {**md, "embedding_model": self._embedding_model, "embedded_at": _now_iso()}
                # ChromaDB metadata values must be strings
                new_md = {k: str(v) for k, v in new_md.items()}
                await _asyncio.to_thread(
                    collection.upsert,
                    ids=[doc_id],
                    documents=[content],
                    metadatas=[new_md],
                    embeddings=[new_emb],
                )
                reembedded += 1
            except Exception as e:
                logger.error(f"Re-embed failed for {doc_id}: {e}")
        logger.info("reembed_stale: scanned=%d reembedded=%d", scanned, reembedded)
        return {"scanned": scanned, "reembedded": reembedded}
