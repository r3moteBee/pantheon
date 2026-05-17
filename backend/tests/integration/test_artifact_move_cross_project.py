"""Verify the POST /api/artifacts/{id}/move endpoint handles intra-project,
cross-project, and duplicate modes, with memory cleanup on cross-project.

Run: pytest backend/tests/integration/test_artifact_move_cross_project.py -v
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))
# Disable auth for tests.
os.environ.setdefault("AUTH_PASSWORD", "")

from main import app  # noqa: E402
from artifacts.store import get_store  # noqa: E402
from memory.graph import GraphMemory  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


def _seed_artifact(project_id: str, path: str, content: str = "x") -> str:
    a = get_store().create(project_id=project_id, path=path, content=content,
                           content_type="text/markdown")
    return a["id"]


def test_move_intra_project_via_endpoint(client):
    aid = _seed_artifact("p1", "p1/foo.md")
    r = client.post(f"/api/artifacts/{aid}/move", json={
        "dest_folder": "p1/new",
        "mode": "move",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["path"] == "p1/new/foo.md"
    assert data["project_id"] == "p1"


def test_move_cross_project_via_endpoint(client):
    aid = _seed_artifact("p1", "p1/foo.md", content="content-A")
    # Seed a graph node tied to this artifact in p1.
    async def add_node():
        g = GraphMemory(project_id="p1")
        await g.add_node("concept", "doc-foo", metadata={"artifact_id": aid})
    asyncio.run(add_node())

    r = client.post(f"/api/artifacts/{aid}/move", json={
        "dest_project_id": "p2",
        "dest_folder": "p2/inbox",
        "mode": "move",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["project_id"] == "p2"
    assert data["path"] == "p2/inbox/foo.md"

    # Source project's graph node for this artifact should be gone.
    async def check_graph():
        g = GraphMemory(project_id="p1")
        node = await g.get_node_by_label("doc-foo")
        return node
    assert asyncio.run(check_graph()) is None


def test_duplicate_via_endpoint(client):
    aid = _seed_artifact("p1", "p1/foo.md", content="seed")
    r = client.post(f"/api/artifacts/{aid}/move", json={
        "dest_project_id": "p2",
        "dest_folder": "p2/shared",
        "mode": "duplicate",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    # New id; original still exists.
    assert data["id"] != aid
    assert get_store().get(aid) is not None
    assert data["project_id"] == "p2"
    assert data["path"] == "p2/shared/foo.md"


def test_move_unknown_artifact_returns_404(client):
    r = client.post("/api/artifacts/nope-id/move", json={
        "dest_folder": "p1/x",
        "mode": "move",
    })
    assert r.status_code == 404
