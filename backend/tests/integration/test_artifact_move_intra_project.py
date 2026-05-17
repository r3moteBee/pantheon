"""Verify store.move within the same project — path-only update.

Run: pytest backend/tests/integration/test_artifact_move_intra_project.py -v
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


def test_move_intra_project_changes_path_keeps_id(store):
    a = store.create(project_id="p1", path="p1/old/foo.md", content="hi",
                     content_type="text/markdown")
    res = store.move(a["id"], dest_project_id="p1", dest_folder="p1/new")
    assert res["id"] == a["id"]
    assert res["project_id"] == "p1"
    assert res["path"] == "p1/new/foo.md"


def test_move_intra_project_collision_suffixes(store):
    a = store.create(project_id="p1", path="p1/a/foo.md", content="first",
                     content_type="text/markdown")
    b = store.create(project_id="p1", path="p1/b/foo.md", content="second",
                     content_type="text/markdown")
    res = store.move(b["id"], dest_project_id="p1", dest_folder="p1/a")
    assert res["path"] == "p1/a/foo-1.md"


def test_move_missing_source_raises(store):
    with pytest.raises(ValueError):
        store.move("nonexistent-id", dest_project_id="p1", dest_folder="p1")
