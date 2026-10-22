"""The Memory browser's list endpoints carry per-entry metadata.

- /api/memory/semantic items get a normalized kind / created_at / source
  path+title next to the raw metadata (round-2 UI review N4).
- /api/memory/episodic/messages carry the session title (stored title, else
  the first user message, like the conversations list) (N11).

Run: pytest backend/tests/integration/test_memory_browser_metadata.py -v
"""
from __future__ import annotations

import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)

import pytest

from api.memory import semantic_entry_summary, session_title


# ── semantic_entry_summary ───────────────────────────────────────────────

def test_summary_artifact_chunk():
    s = semantic_entry_summary({
        "source": "artifact", "artifact_id": "a1", "path": "proj/notes/plan.md",
        "title": "Plan", "chunk_index": "0", "created_at": "2026-10-01T00:00:00+00:00",
    })
    assert s["kind"] == "artifact"
    assert s["source_path"] == "proj/notes/plan.md"
    assert s["source_title"] == "Plan"
    assert s["created_at"] == "2026-10-01T00:00:00+00:00"


def test_summary_index_artifact_chunk_is_artifact():
    # MemoryManager.index_artifact writes frontmatter (fm_*) fields and an
    # artifact:// source path; that is an artifact chunk, not a workspace file.
    s = semantic_entry_summary({
        "fm_artifact_id": "a1", "fm_artifact_path": "proj/Models/Card.swift", "fm_title": "Card",
        "source_path": "artifact://a1/proj/Models/Card.swift", "source_file": "Card.swift",
        "indexed_at": "2026-10-02T00:00:00+00:00",
    })
    assert s["kind"] == "artifact"
    assert s["source_path"] == "proj/Models/Card.swift"
    assert s["source_title"] == "Card"


def test_summary_workspace_file_chunk():
    s = semantic_entry_summary({
        "source_file": "Card.swift", "source_path": "proj/Models/Card.swift",
        "fm_title": "Card", "heading": "", "indexed_at": "2026-10-02T00:00:00+00:00",
    })
    assert s["kind"] == "workspace"
    assert s["source_path"] == "proj/Models/Card.swift"
    assert s["source_title"] == "Card"
    # No created_at: falls back to indexed_at.
    assert s["created_at"] == "2026-10-02T00:00:00+00:00"


def test_summary_extracted_fact_and_preference():
    fact = semantic_entry_summary({
        "source": "auto_extraction", "type": "extracted_fact",
        "session_id": "artifact:abc", "extracted_at": "2026-10-03T00:00:00+00:00",
    })
    assert fact["kind"] == "fact"
    assert fact["session_id"] == "artifact:abc"
    assert fact["created_at"] == "2026-10-03T00:00:00+00:00"
    pref = semantic_entry_summary({"source": "auto_extraction", "type": "user_preference"})
    assert pref["kind"] == "preference"


def test_summary_remembered_and_other_sources():
    # The remember tool / store API attach nothing beyond the defaults.
    assert semantic_entry_summary({"project_id": "default", "embedding_model": "m"})["kind"] == "remembered"
    assert semantic_entry_summary({"source": "telegram_note"})["kind"] == "telegram_note"
    assert semantic_entry_summary({"kind": "topic_node"})["kind"] == "topic"
    assert semantic_entry_summary({"type": "image_description", "source_path": "img/a.png"})["kind"] == "image"
    assert semantic_entry_summary({})["created_at"] is None


# ── session_title ────────────────────────────────────────────────────────

def test_session_title_prefers_stored_title():
    assert session_title("s1", {"title": "My chat", "first_user_message": "hello"}) == "My chat"
    # "New Chat" is the placeholder, not a title.
    assert session_title("s1", {"title": "New Chat", "first_user_message": "hello there"}) == "hello there"


def test_session_title_uses_first_user_message_first_line():
    first = "Compare ZFS and Btrfs\nfor a home NAS"
    assert session_title("s1", {"first_user_message": first}) == "Compare ZFS and Btrfs"
    assert session_title("abcdef12-rest", {}) == "Chat abcdef12"


# ── endpoints ────────────────────────────────────────────────────────────

@pytest.fixture
def client_with_session():
    from fastapi.testclient import TestClient
    from main import app
    from memory.episodic import EpisodicMemory
    import asyncio

    project_id = "test-proj-memory-browser"
    session_id = "sess-test-memory-browser"
    ep = EpisodicMemory()
    asyncio.run(ep.save_message(project_id=project_id, session_id=session_id,
                                role="user", content="What is a VLAN?\nSecond line"))
    asyncio.run(ep.save_message(project_id=project_id, session_id=session_id,
                                role="assistant", content="A VLAN is ..."))
    client = TestClient(app)
    try:
        yield client, project_id, session_id
    finally:
        import sqlite3
        with sqlite3.connect(ep.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
            conn.commit()


def test_messages_endpoint_carries_session_title(client_with_session):
    client, project_id, session_id = client_with_session
    r = client.get(f"/api/memory/episodic/messages?project_id={project_id}")
    assert r.status_code == 200, r.text
    msgs = [m for m in r.json()["messages"] if m["session_id"] == session_id]
    assert len(msgs) == 2
    for m in msgs:
        assert m["session_title"] == "What is a VLAN?"
        assert m["session_first_user_message"].startswith("What is a VLAN?")


def test_semantic_endpoint_shape_on_empty_project():
    from fastapi.testclient import TestClient
    from main import app
    client = TestClient(app)
    r = client.get("/api/memory/semantic?project_id=test-proj-memory-browser-empty")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == [] and body["total"] == 0
