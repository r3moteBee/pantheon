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

    def __init__(self, project_id: str = "default", embedding_fn: Any = None):
        self.project_id = project_id
        self.collection_name = _sanitize_collection_name(f"proj-{project_id}")
        self._embedding_fn = embedding_fn
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
            return sorted(items, key=lambda x: x["score"], reverse=True)
        except Exception as e:
            logger.error(f"Semantic search failed: {e}")
            return []

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
