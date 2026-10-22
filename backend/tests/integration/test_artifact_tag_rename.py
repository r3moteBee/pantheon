"""Tag rename / merge across a project (UI review #27).

Run: pytest backend/tests/integration/test_artifact_tag_rename.py -v
"""
from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from artifacts.store import ArtifactStore  # noqa: E402


@pytest.fixture
def store(tmp_path):
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    return ArtifactStore(db_path=str(tmp_path / "artifacts.db"), blobs_dir=blobs)


def _mk(store, project, path, tags):
    return store.create(project_id=project, path=path, content="x",
                        content_type="text/markdown", tags=tags)


def test_rename_tag_renames_in_project_only(store):
    a = _mk(store, "p1", "a.md", ["enhance-marble-maze", "keep"])
    b = _mk(store, "p2", "b.md", ["enhance-marble-maze"])
    assert store.rename_tag("p1", "enhance-marble-maze", "marble-maze") == 1
    assert store.get(a["id"])["tags"] == ["marble-maze", "keep"]
    assert store.get(b["id"])["tags"] == ["enhance-marble-maze"]
    assert store.tag_counts("p1") == {"marble-maze": 1, "keep": 1}


def test_rename_tag_merges_without_duplicates(store):
    a = _mk(store, "p1", "a.md", ["enhance-marblemaze-game", "enhance-marble-maze"])
    c = _mk(store, "p1", "c.md", ["enhance-marblemaze-game"])
    assert store.rename_tag("p1", "enhance-marblemaze-game", "enhance-marble-maze") == 2
    assert store.get(a["id"])["tags"] == ["enhance-marble-maze"]
    assert store.get(c["id"])["tags"] == ["enhance-marble-maze"]


def test_rename_tag_exact_match_and_no_version(store):
    a = _mk(store, "p1", "a.md", ["maze-extra"])
    assert store.rename_tag("p1", "maze", "labyrinth") == 0
    assert store.get(a["id"])["tags"] == ["maze-extra"]
    assert store.rename_tag("p1", "maze-extra", "maze-extra") == 0
    store.rename_tag("p1", "maze-extra", "labyrinth")
    assert len(store.list_versions(a["id"])) == 1
