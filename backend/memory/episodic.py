"""Tier 2: Episodic memory — SQLite-backed conversation history with semantic search.

Enhanced with optional embedding-based search so episodic recall is no longer
limited to LIKE substring matching.  When an embedding function is provided,
messages are embedded at write time and stored in a parallel ChromaDB collection
for vector search.  Falls back to LIKE search when embeddings are unavailable.
"""
from __future__ import annotations
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import asyncio as _asyncio

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Recency decay: messages from today score 1.0, 30+ days ago score ~0.5
_RECENCY_HALFLIFE_DAYS = 30.0


def _recency_score(timestamp_iso: str) -> float:
    """Compute a 0-1 recency multiplier from an ISO timestamp."""
    try:
        ts = datetime.fromisoformat(timestamp_iso.replace("Z", "+00:00"))
        age_days = (datetime.now(timezone.utc) - ts).total_seconds() / 86400.0
        import math
        return math.exp(-0.693 * age_days / _RECENCY_HALFLIFE_DAYS)
    except Exception:
        return 0.5


def episodic_collection_name(project_id: str) -> str:
    """Chroma collection for a project's message vectors (3-63 chars, alnum + '-')."""
    import re
    safe_name = re.sub(r'[^a-zA-Z0-9-]', '-', f"episodic-{project_id}")
    safe_name = re.sub(r'-+', '-', safe_name).strip('-')[:63]
    if len(safe_name) < 3:
        safe_name = f"ep-{safe_name}"
    return safe_name


class EpisodicMemory:
    """Tier 2: Persistent conversation history, task logs, and memory notes.

    Backed by SQLite for simplicity, reliability, and zero dependencies.
    Optionally enhanced with ChromaDB vector index for semantic search.
    """

    def __init__(
        self,
        db_path: str | None = None,
        project_id: str = "default",
        embedding_fn: Any = None,
    ):
        if db_path is None:
            try:
                from config import get_settings
                db_path = get_settings().episodic_db_path
            except Exception:
                db_path = "data/episodic.db"
        self.db_path = db_path
        self.project_id = project_id
        self._embedding_fn = embedding_fn
        self._vector_collection = None
        self._vector_client = None
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialize database schema."""
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL DEFAULT 'default',
                    session_id TEXT NOT NULL,
                    title TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT,
                    project_id TEXT NOT NULL DEFAULT 'default',
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}'
                );

                CREATE TABLE IF NOT EXISTS task_logs (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL DEFAULT 'default',
                    task_id TEXT NOT NULL,
                    task_name TEXT,
                    event TEXT NOT NULL,
                    details TEXT,
                    timestamp TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_notes (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL DEFAULT 'default',
                    session_id TEXT,
                    content TEXT NOT NULL,
                    tags TEXT DEFAULT '[]',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
                CREATE INDEX IF NOT EXISTS idx_messages_session_ts ON messages(session_id, timestamp);
                CREATE INDEX IF NOT EXISTS idx_messages_project ON messages(project_id);
                CREATE INDEX IF NOT EXISTS idx_task_logs_task ON task_logs(task_id);
                CREATE INDEX IF NOT EXISTS idx_notes_project ON memory_notes(project_id);
            """)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        from db_utils import apply_sqlite_pragmas, ClosingConnection
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        apply_sqlite_pragmas(conn)
        return ClosingConnection(conn)  # type: ignore

    # ── Vector index (ChromaDB) for semantic episodic search ─────────────

    def _get_vector_collection(self):
        """Lazy-init a ChromaDB collection for episodic message embeddings."""
        if self._vector_collection is not None:
            return self._vector_collection
        try:
            import chromadb
            safe_name = episodic_collection_name(self.project_id)

            from config import get_settings
            cfg = get_settings()
            chroma_host = cfg.chroma_host.strip() if cfg.chroma_host else ""

            if chroma_host:
                try:
                    self._vector_client = chromadb.HttpClient(host=chroma_host, port=cfg.chroma_port)
                    self._vector_client.heartbeat()
                except Exception:
                    self._vector_client = None

            if self._vector_client is None:
                data_dir = cfg.data_dir or "data"
                chroma_path = f"{data_dir}/chroma/episodic-{self.project_id}"
                self._vector_client = chromadb.PersistentClient(path=chroma_path)

            self._vector_collection = self._vector_client.get_or_create_collection(
                name=safe_name,
                metadata={"hnsw:space": "cosine"},
            )
            return self._vector_collection
        except Exception as e:
            logger.warning("Episodic vector index unavailable: %s", e)
            return None

    async def _index_message_vector(self, msg_id: str, content: str, metadata: dict) -> None:
        """Embed and index a message in the vector store (fire-and-forget)."""
        if not self._embedding_fn:
            return
        try:
            collection = await _asyncio.to_thread(self._get_vector_collection)
            if collection is None:
                return
            embedding = await self._embedding_fn(content)
            # Flatten metadata for ChromaDB
            flat_meta = {k: str(v) for k, v in metadata.items()}
            await _asyncio.to_thread(
                collection.upsert,
                ids=[msg_id],
                documents=[content],
                embeddings=[embedding],
                metadatas=[flat_meta],
            )
        except Exception as e:
            logger.debug("Episodic vector index failed for %s: %s", msg_id, e)

    # ── Core message operations ──────────────────────────────────────────

    async def save_message(
        self,
        session_id: str,
        project_id: str,
        role: str,
        content: str,
        metadata: dict | None = None,
    ) -> str:
        """Save a conversation message to episodic memory.

        Also indexes the message in the vector store for semantic search.
        """
        msg_id = str(uuid.uuid4())
        now = _now_iso()
        with self._connect() as conn:
            # Upsert conversation record
            conn.execute("""
                INSERT INTO conversations (id, project_id, session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO NOTHING
            """, (session_id, project_id, session_id, now, now))

            conn.execute("""
                UPDATE conversations SET updated_at = ? WHERE id = ?
            """, (now, session_id))

            conn.execute("""
                INSERT INTO messages (id, project_id, session_id, role, content, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                msg_id, project_id, session_id, role, content, now,
                json.dumps(metadata or {})
            ))
            conn.commit()

        # Fire-and-forget vector indexing (non-blocking)
        if self._embedding_fn and role in ("user", "assistant"):
            from utils.background import spawn
            spawn(self._index_message_vector(
                msg_id, content,
                {"project_id": project_id, "session_id": session_id, "role": role, "timestamp": now},
            ))

        return msg_id

    async def get_history(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return the most recent ``limit`` messages of a session, in
        chronological order. ``offset`` skips that many of the newest
        messages (for paging backwards).

        Selecting newest-first matters: with ASC + LIMIT, a resumed chat
        longer than ``limit`` would rehydrate the agent with its *oldest*
        turns and none of the recent ones.
        """
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, role, content, timestamp, metadata
                FROM messages
                WHERE session_id = ?
                ORDER BY timestamp DESC, rowid DESC
                LIMIT ? OFFSET ?
            """, (session_id, limit, offset)).fetchall()
        rows = list(reversed(rows))
        return [
            {
                "id": r["id"],
                "role": r["role"],
                "content": r["content"],
                "timestamp": r["timestamp"],
                "metadata": json.loads(r["metadata"] or "{}"),
            }
            for r in rows
        ]

    async def count_messages(self, session_id: str) -> int:
        """Messages in a session (for absolute positions when only the newest
        N were loaded — see agent.history)."""
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM messages WHERE session_id = ?", (session_id,)).fetchone()
        return int(row[0]) if row else 0

    async def get_session_project_id(self, session_id: str) -> str | None:
        """Return the owning project_id for a session, or None when the
        conversation row is missing. Canonical lookup — messages also
        carry project_id but the conversation row is the source of truth."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT project_id FROM conversations WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return row["project_id"] if row else None

    async def get_conversation(self, session_id: str) -> dict[str, Any] | None:
        """Get a single conversation session by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT id, project_id, session_id, title, created_at, updated_at, metadata "
                "FROM conversations WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        if row:
            return {
                "id": row["id"],
                "project_id": row["project_id"],
                "session_id": row["session_id"],
                "title": row["title"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "metadata": row["metadata"],
            }
        return None

    async def get_sessions(
        self,
        project_id: str = "default",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """List recent conversation sessions for a project."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT c.id, c.session_id, c.title, c.created_at, c.updated_at, c.metadata,
                       COUNT(m.id) as message_count
                FROM conversations c
                LEFT JOIN messages m ON m.session_id = c.session_id
                WHERE c.project_id = ?
                GROUP BY c.id
                ORDER BY c.updated_at DESC
                LIMIT ?
            """, (project_id, limit)).fetchall()
        return [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "title": r["title"],
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "metadata": json.loads(r["metadata"] or "{}"),
                "message_count": r["message_count"],
            }
            for r in rows
        ]

    async def search_messages(
        self,
        query: str,
        project_id: str = "default",
        limit: int = 20,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Search messages — uses semantic search when available, falls back to LIKE.
        ``session_id`` restricts the semantic search to one conversation."""
        # Try semantic search first
        if self._embedding_fn:
            try:
                results = await self._semantic_search(query, project_id, limit, session_id)
                if results:
                    return results
            except Exception as e:
                logger.debug("Semantic episodic search failed, falling back to LIKE: %s", e)
        if session_id:
            return []   # callers of the session filter want similarity scores

        # Fallback: LIKE search
        return await self._like_search(query, project_id, limit)

    async def _semantic_search(
        self,
        query: str,
        project_id: str,
        limit: int,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Vector similarity search across episodic messages."""
        collection = await _asyncio.to_thread(self._get_vector_collection)
        if collection is None:
            return []

        count = await _asyncio.to_thread(collection.count)
        if count == 0:
            return []

        query_embedding = await self._embedding_fn(query)
        results = await _asyncio.to_thread(
            collection.query,
            query_embeddings=[query_embedding],
            n_results=min(limit, count),
            include=["documents", "metadatas", "distances"],
            where=({"$and": [{"project_id": project_id}, {"session_id": session_id}]}
                   if session_id else {"project_id": project_id}),
        )

        items = []
        if results and results.get("ids") and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i] if results.get("distances") else 1.0
                similarity = max(0.0, 1.0 - distance)
                meta = results["metadatas"][0][i] if results.get("metadatas") else {}
                timestamp = meta.get("timestamp", "")

                # Apply recency weighting
                recency = _recency_score(timestamp) if timestamp else 0.5
                final_score = similarity * 0.7 + recency * 0.3

                items.append({
                    "id": doc_id,
                    "session_id": meta.get("session_id", ""),
                    "role": meta.get("role", "unknown"),
                    "content": results["documents"][0][i],
                    "timestamp": timestamp,
                    "score": round(final_score, 4),
                    "similarity": round(similarity, 4),
                    "recency": round(recency, 4),
                })

        return sorted(items, key=lambda x: x.get("score", 0), reverse=True)

    async def _like_search(
        self,
        query: str,
        project_id: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Fallback LIKE-based text search."""
        pattern = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, session_id, role, content, timestamp
                FROM messages
                WHERE project_id = ? AND content LIKE ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (project_id, pattern, limit)).fetchall()
        results = []
        for r in rows:
            recency = _recency_score(r["timestamp"])
            results.append({
                **dict(r),
                "score": round(0.5 * 0.7 + recency * 0.3, 4),
            })
        return results

    async def search_by_date(
        self,
        project_id: str,
        start_date: str,
        end_date: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Search messages within a date range."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, session_id, role, content, timestamp, metadata
                FROM messages
                WHERE project_id = ? AND timestamp >= ? AND timestamp <= ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, (project_id, start_date, end_date, limit)).fetchall()
        return [
            {**dict(r), "metadata": json.loads(r["metadata"] or "{}")}
            for r in rows
        ]

    async def get_recent_messages(
        self,
        project_id: str = "default",
        session_id: str | None = None,
        limit: int = 40,
    ) -> list[dict[str, Any]]:
        """Get recent messages for extraction (most recent first)."""
        with self._connect() as conn:
            if session_id:
                rows = conn.execute("""
                    SELECT id, session_id, role, content, timestamp, metadata
                    FROM messages
                    WHERE project_id = ? AND session_id = ?
                    ORDER BY timestamp DESC LIMIT ?
                """, (project_id, session_id, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT id, session_id, role, content, timestamp, metadata
                    FROM messages
                    WHERE project_id = ?
                    ORDER BY timestamp DESC LIMIT ?
                """, (project_id, limit)).fetchall()
        results = [
            {**dict(r), "metadata": json.loads(r["metadata"] or "{}")}
            for r in rows
        ]
        results.reverse()  # Return in chronological order
        return results

    # ── Task logs ────────────────────────────────────────────────────────

    async def log_task_event(
        self,
        task_id: str,
        event: str,
        project_id: str = "default",
        task_name: str | None = None,
        details: str | None = None,
    ) -> str:
        """Log a task lifecycle event."""
        log_id = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO task_logs (id, project_id, task_id, task_name, event, details, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (log_id, project_id, task_id, task_name, event, details, _now_iso()))
            conn.commit()
        return log_id

    async def get_task_logs(
        self,
        task_id: str | None = None,
        project_id: str = "default",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Retrieve task logs."""
        with self._connect() as conn:
            if task_id:
                rows = conn.execute("""
                    SELECT * FROM task_logs
                    WHERE task_id = ? AND project_id = ?
                    ORDER BY timestamp DESC LIMIT ?
                """, (task_id, project_id, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM task_logs
                    WHERE project_id = ?
                    ORDER BY timestamp DESC LIMIT ?
                """, (project_id, limit)).fetchall()
        return [dict(r) for r in rows]

    # ── Memory notes ─────────────────────────────────────────────────────

    async def add_note(
        self,
        content: str,
        project_id: str = "default",
        session_id: str | None = None,
        tags: list[str] | None = None,
    ) -> str:
        """Add a memory note."""
        note_id = str(uuid.uuid4())
        now = _now_iso()
        with self._connect() as conn:
            conn.execute("""
                INSERT INTO memory_notes (id, project_id, session_id, content, tags, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (note_id, project_id, session_id, content, json.dumps(tags or []), now, now))
            conn.commit()
        return note_id

    async def get_notes(
        self,
        project_id: str = "default",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get memory notes for a project."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, content, tags, session_id, created_at, updated_at
                FROM memory_notes
                WHERE project_id = ?
                ORDER BY updated_at DESC LIMIT ?
            """, (project_id, limit)).fetchall()
        return [
            {**dict(r), "tags": json.loads(r["tags"] or "[]")}
            for r in rows
        ]

    async def update_note(self, note_id: str, content: str) -> bool:
        """Update a memory note."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE memory_notes SET content = ?, updated_at = ? WHERE id = ?",
                (content, _now_iso(), note_id)
            )
            conn.commit()
        return cursor.rowcount > 0

    async def delete_note(self, note_id: str) -> bool:
        """Delete a memory note."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM memory_notes WHERE id = ?", (note_id,))
            conn.commit()
        return cursor.rowcount > 0

    async def delete_message(self, message_id: str) -> bool:
        """Delete a specific message."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            conn.commit()
        return cursor.rowcount > 0

    async def set_conversation_title(
        self, session_id: str, title: str, *, only_if_empty: bool = True,
    ) -> bool:
        """Set a conversation's title. By default only fills a NULL/empty
        title so a user-chosen one is never overwritten. Returns True when
        a row changed."""
        sql = "UPDATE conversations SET title = ? WHERE session_id = ?"
        if only_if_empty:
            sql += " AND (title IS NULL OR title = '')"
        with self._connect() as conn:
            cursor = conn.execute(sql, (title, session_id))
            conn.commit()
        return cursor.rowcount > 0

    async def merge_conversation_metadata(
        self, session_id: str, metadata: dict[str, Any],
    ) -> None:
        """Merge ``metadata`` into a conversation's metadata JSON. Creates
        the conversation row (project 'default', title 'New Chat') when it
        doesn't exist yet."""
        now = _now_iso()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT metadata FROM conversations WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO conversations (id, project_id, session_id, title, "
                    "created_at, updated_at, metadata) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (session_id, "default", session_id, "New Chat", now, now,
                     json.dumps(metadata)),
                )
            else:
                existing = json.loads(row["metadata"] or "{}")
                existing.update(metadata)
                conn.execute(
                    "UPDATE conversations SET metadata = ?, updated_at = ? WHERE session_id = ?",
                    (json.dumps(existing), now, session_id),
                )
            conn.commit()

    async def delete_conversation(self, session_id: str) -> None:
        """Delete a conversation row and all its messages."""
        with self._connect() as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
            conn.commit()

    async def project_stats(self, project_id: str) -> dict[str, Any]:
        """Diagnostic counts for one project: messages, conversations, and
        (up to 20) distinct project_ids present in the messages table."""
        with self._connect() as conn:
            msg_count = conn.execute(
                "SELECT count(*) FROM messages WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
            conv_count = conn.execute(
                "SELECT count(*) FROM conversations WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
            pids = [r[0] for r in conn.execute(
                "SELECT DISTINCT project_id FROM messages LIMIT 20"
            ).fetchall()]
        return {"messages": msg_count, "conversations": conv_count,
                "project_ids_in_db": pids}

    async def get_all_messages(
        self,
        project_id: str = "default",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Get all messages for a project (for audit mode)."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT id, session_id, role, content, timestamp, metadata
                FROM messages
                WHERE project_id = ?
                ORDER BY timestamp DESC LIMIT ?
            """, (project_id, limit)).fetchall()
        return [
            {**dict(r), "metadata": json.loads(r["metadata"] or "{}")}
            for r in rows
        ]
