"""Verify POST /api/artifacts/bulk/move handles batches with per-row results.

Run: pytest backend/tests/integration/test_artifact_bulk_move.py -v
"""
from __future__ import annotations

import os
import tempfile

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))
os.environ.setdefault("AUTH_PASSWORD", "")

from main import app  # noqa: E402
from artifacts.store import get_store  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


def _seed(project_id, path, content="x"):
    return get_store().create(project_id=project_id, path=path, content=content,
                              content_type="text/markdown")["id"]


def test_bulk_move_all_succeed(client):
    ids = [_seed("p1", f"p1/a/file-{i}.md") for i in range(3)]
    r = client.post("/api/artifacts/bulk/move", json={
        "ids": ids,
        "dest_folder": "p1/b",
        "mode": "move",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["results"]) == 3
    assert all("new_path" in row and row["new_path"].startswith("p1/b/") for row in data["results"])


def test_bulk_move_partial_failure(client):
    good = _seed("p1", "p1/x/foo.md")
    r = client.post("/api/artifacts/bulk/move", json={
        "ids": [good, "definitely-not-real"],
        "dest_folder": "p1/y",
        "mode": "move",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["results"]) == 2
    ok = [row for row in data["results"] if "new_path" in row]
    err = [row for row in data["results"] if "error" in row]
    assert len(ok) == 1 and len(err) == 1


def test_bulk_duplicate(client):
    ids = [_seed("p1", f"p1/src/file-{i}.md", content=f"c{i}") for i in range(2)]
    r = client.post("/api/artifacts/bulk/move", json={
        "ids": ids,
        "dest_project_id": "p2",
        "dest_folder": "p2/copies",
        "mode": "duplicate",
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data["results"]) == 2
    # Source artifacts still exist.
    for sid in ids:
        assert get_store().get(sid) is not None
    # New ids are different.
    new_ids = [row["id"] for row in data["results"] if "id" in row]
    assert all(nid not in ids for nid in new_ids)


def test_bulk_move_empty_ids(client):
    r = client.post("/api/artifacts/bulk/move", json={
        "ids": [],
        "dest_folder": "p1/x",
        "mode": "move",
    })
    assert r.status_code == 200
    assert r.json() == {"results": []}
