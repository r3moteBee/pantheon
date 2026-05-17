"""Verify store.duplicate creates an independent copy with a fresh id.

Run: pytest backend/tests/integration/test_artifact_duplicate.py -v
"""
from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from artifacts.store import ArtifactStore  # noqa: E402


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "artifacts.db"
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    return ArtifactStore(db_path=str(db), blobs_dir=blobs)


def test_duplicate_intra_project(store):
    a = store.create(project_id="p1", path="p1/foo.md", content="hello",
                     content_type="text/markdown", tags=["x"])
    b = store.duplicate(a["id"], dest_project_id="p1", dest_folder="p1/archive")
    assert b["id"] != a["id"]
    assert b["project_id"] == "p1"
    assert b["path"] == "p1/archive/foo.md"
    assert b["content"] == "hello"
    # Tags carry over.
    assert "x" in (b.get("tags") or [])


def test_duplicate_cross_project(store):
    a = store.create(project_id="p1", path="p1/foo.md", content="hello",
                     content_type="text/markdown")
    b = store.duplicate(a["id"], dest_project_id="p2", dest_folder="p2/notes")
    assert b["project_id"] == "p2"
    assert b["path"] == "p2/notes/foo.md"
    assert b["content"] == "hello"


def test_duplicate_collision_suffixes(store):
    a = store.create(project_id="p1", path="p1/foo.md", content="hello",
                     content_type="text/markdown")
    b = store.duplicate(a["id"], dest_project_id="p1", dest_folder="p1")
    # Same folder collision: original was p1/foo.md → duplicate is p1/foo-1.md.
    assert b["path"] == "p1/foo-1.md"


def test_duplicate_missing_source_raises(store):
    with pytest.raises(ValueError):
        store.duplicate("nonexistent-id", dest_project_id="p1", dest_folder="p1")
