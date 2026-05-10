"""Integration tests for /conversations endpoints.

Verifies that /conversations/{session_id} and /resume return the
session's owning project_id so the frontend can sync the
active-project pill on session resume.

Run: pytest backend/tests/integration/test_conversations_endpoint.py -v
"""
from __future__ import annotations

import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client_with_session():
    """Spin up the FastAPI app, seed one conversation in a non-default project,
    and yield (client, project_id, session_id)."""
    from main import app
    from memory.episodic import EpisodicMemory
    import asyncio

    project_id = "test-proj-malegis"
    session_id = "sess-test-resume-sync"

    ep = EpisodicMemory()
    asyncio.run(
        ep.save_message(
            project_id=project_id,
            session_id=session_id,
            role="user",
            content="hello from a non-default project",
        )
    )
    client = TestClient(app)
    try:
        yield client, project_id, session_id
    finally:
        # Clean up the seeded session so reruns are deterministic.
        import sqlite3
        with sqlite3.connect(ep.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
            conn.commit()


def test_get_conversation_returns_project_id(client_with_session):
    client, project_id, session_id = client_with_session
    r = client.get(f"/api/conversations/{session_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == session_id
    assert body["project_id"] == project_id


def test_resume_returns_project_id(client_with_session):
    client, project_id, session_id = client_with_session
    r = client.post(f"/api/conversations/{session_id}/resume")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == session_id
    assert body["project_id"] == project_id


def test_get_conversation_unknown_session_404(client_with_session):
    client, _, _ = client_with_session
    r = client.get("/api/conversations/does-not-exist-zzz")
    assert r.status_code == 404
