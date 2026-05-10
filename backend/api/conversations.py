"""Conversations API — list, view, resume, delete, save-as-artifact."""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from memory.episodic import EpisodicMemory

logger = logging.getLogger(__name__)
router = APIRouter()


def _lookup_session_project_id(ep: EpisodicMemory, session_id: str) -> str | None:
    """Return the owning project_id for a session, or None if the
    session row is missing. Read from the conversations table; messages
    also carry project_id but the conversation row is the canonical
    home."""
    with sqlite3.connect(ep.db_path) as conn:
        row = conn.execute(
            "SELECT project_id FROM conversations WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return row[0] if row else None


class SaveAsArtifactRequest(BaseModel):
    path: str | None = None
    title: str | None = None
    tags: list[str] | None = None


@router.get("/conversations")
async def list_conversations(
    project_id: str = Query("default"),
    limit: int = Query(50, ge=1, le=200),
) -> dict[str, Any]:
    ep = EpisodicMemory()
    sessions = await ep.get_sessions(project_id=project_id, limit=limit)
    return {"conversations": sessions, "count": len(sessions)}


@router.get("/conversations/{session_id}")
async def get_conversation(
    session_id: str,
    project_id: str = Query("default"),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    ep = EpisodicMemory()
    messages = await ep.get_history(session_id=session_id, limit=limit)
    session_project_id = _lookup_session_project_id(ep, session_id)
    if not messages and session_project_id is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {
        "session_id": session_id,
        "messages": messages,
        "count": len(messages),
        "project_id": session_project_id,
    }


@router.post("/conversations/{session_id}/resume")
async def resume_conversation(
    session_id: str,
    project_id: str = Query("default"),
) -> dict[str, Any]:
    """Returns the rehydrated conversation context.

    The frontend uses this to load the message history into the chat
    pane and continue the same session. AgentCore.from_session() is
    used by the WebSocket handler when subsequent messages arrive
    with the same session_id. Also returns the session's owning
    project_id so the frontend can sync the active-project pill when
    a user resumes a session from a different project than the one
    currently active.
    """
    ep = EpisodicMemory()
    messages = await ep.get_history(session_id=session_id, limit=500)
    if not messages:
        raise HTTPException(status_code=404, detail="no messages for session")
    session_project_id = _lookup_session_project_id(ep, session_id)
    return {
        "session_id": session_id,
        "messages": messages,
        "message_count": len(messages),
        "project_id": session_project_id,
    }


@router.delete("/conversations/{session_id}")
async def delete_conversation(session_id: str) -> dict[str, str]:
    ep = EpisodicMemory()
    with sqlite3.connect(ep.db_path) as conn:
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
        conn.commit()
    return {"status": "deleted", "session_id": session_id}


@router.post("/conversations/{session_id}/save-as-artifact")
async def save_chat_as_artifact(
    session_id: str,
    req: SaveAsArtifactRequest,
    project_id: str = Query("default"),
) -> dict[str, Any]:
    """Render the entire conversation to markdown, save as a chat-export
    artifact. Auto-embeds via the standard pipeline so future recall
    can find it."""
    ep = EpisodicMemory()
    messages = await ep.get_history(session_id=session_id, limit=2000)
    if not messages:
        raise HTTPException(status_code=404, detail="no messages for session")

    md = _render_chat_markdown(session_id, messages)
    title = req.title or _auto_title_from_messages(messages, session_id)
    tags = req.tags or ["chat-export"]

    from artifacts.store import get_store
    from artifacts import embedder
    store = get_store()

    # If we already saved this session, update the same artifact (creates v2,
    # v3, ...). Otherwise create a new one.
    existing = _find_chat_export(store, project_id, session_id)
    if existing and not req.path:
        a = store.update(
            existing["id"],
            content=md,
            title=title,
            tags=tags,
            edit_summary=f"Re-saved chat ({len(messages)} messages)",
            edited_by=session_id,
        )
    else:
        path = req.path or _unique_chat_path(store, project_id, session_id, title)
        try:
            a = store.create(
                project_id=project_id,
                path=path,
                content=md,
                content_type="chat-export",
                title=title,
                tags=tags,
                source={"kind": "chat-export", "session_id": session_id, "message_count": len(messages)},
                edited_by=session_id,
            )
        except sqlite3.IntegrityError:
            # Path collision (e.g. caller passed an explicit path that's taken).
            # Disambiguate with a unique suffix and retry once.
            path = _unique_chat_path(store, project_id, session_id, title, force_unique=True)
            a = store.create(
                project_id=project_id,
                path=path,
                content=md,
                content_type="chat-export",
                title=title,
                tags=tags,
                source={"kind": "chat-export", "session_id": session_id, "message_count": len(messages)},
                edited_by=session_id,
            )
    embedder.schedule_embed(a["id"], project_id, immediate=True)
    return a


def _find_chat_export(store, project_id: str, session_id: str) -> dict[str, Any] | None:
    """Find an existing chat-export artifact for this session, if any."""
    items = store.list(
        project_id=project_id,
        content_type="chat-export",
        limit=10000,
    )
    for a in items:
        src = a.get("source") or {}
        if src.get("session_id") == session_id:
            return a
    return None


def _unique_chat_path(store, project_id: str, session_id: str, title: str,
                      *, force_unique: bool = False) -> str:
    from artifacts.store import project_slug
    proj = project_slug(project_id)
    base_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = _slug(title)
    candidate = f"{proj}/chats/{base_date}-{slug}.md"
    if not force_unique and not store.get_by_path(project_id, candidate):
        return candidate
    hm = datetime.now(timezone.utc).strftime("%H%M")
    candidate = f"{proj}/chats/{base_date}-{hm}-{slug}.md"
    if not store.get_by_path(project_id, candidate):
        return candidate
    for i in range(2, 50):
        c2 = f"{proj}/chats/{base_date}-{hm}-{slug}-{i}.md"
        if not store.get_by_path(project_id, c2):
            return c2
    return f"{proj}/chats/{base_date}-{hm}-{session_id[:8]}.md"


def _render_chat_markdown(session_id: str, messages: list[dict[str, Any]]) -> str:
    lines = [
        f"# Chat — session {session_id}",
        "",
        f"_{len(messages)} messages, exported {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_",
        "",
    ]
    for m in messages:
        role = (m.get("role") or "?").upper()
        ts = m.get("timestamp") or ""
        content = (m.get("content") or "").strip()
        lines.append(f"## {role}  ·  {ts}")
        lines.append("")
        lines.append(content)
        lines.append("")
    return "\n".join(lines)


def _auto_title_from_messages(messages: list[dict[str, Any]], session_id: str) -> str:
    for m in messages:
        if (m.get("role") == "user") and m.get("content"):
            first = m["content"].strip().splitlines()[0]
            return first[:80]
    return f"Chat {session_id[:8]}"


_SLUG_RE = __import__("re").compile(r"[^a-z0-9]+")

def _slug(s: str) -> str:
    s = s.lower()
    s = _SLUG_RE.sub("-", s).strip("-")
    return s[:60] or "chat"
