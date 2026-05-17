"""Verify _unique_path suffix-on-conflict + rename auto-resolution.

Run: pytest backend/tests/integration/test_artifact_rename_conflict.py -v
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from artifacts.store import ArtifactStore  # noqa: E402


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "artifacts.db"
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    return ArtifactStore(db_path=str(db), blobs_dir=blobs)


def test_unique_path_returns_input_when_free(store):
    assert store._unique_path("p1", "p1/foo.md") == "p1/foo.md"


def test_unique_path_suffixes_on_collision(store):
    store.create(project_id="p1", path="p1/foo.md", content="a", content_type="text/markdown")
    assert store._unique_path("p1", "p1/foo.md") == "p1/foo-1.md"


def test_unique_path_increments_suffix(store):
    store.create(project_id="p1", path="p1/foo.md", content="a", content_type="text/markdown")
    store.create(project_id="p1", path="p1/foo-1.md", content="b", content_type="text/markdown")
    assert store._unique_path("p1", "p1/foo.md") == "p1/foo-2.md"


def test_unique_path_handles_no_extension(store):
    store.create(project_id="p1", path="p1/README", content="a", content_type="text/plain")
    assert store._unique_path("p1", "p1/README") == "p1/README-1"


def test_unique_path_scoped_per_project(store):
    store.create(project_id="p1", path="p1/foo.md", content="a", content_type="text/markdown")
    assert store._unique_path("p2", "p2/foo.md") == "p2/foo.md"


def test_rename_auto_suffixes_on_collision(store):
    a = store.create(project_id="p1", path="p1/foo.md", content="first", content_type="text/markdown")
    b = store.create(project_id="p1", path="p1/bar.md", content="second", content_type="text/markdown")
    result = store.rename(b["id"], "p1/foo.md")
    assert result["path"] == "p1/foo-1.md"


def test_rename_no_collision_returns_requested(store):
    a = store.create(project_id="p1", path="p1/foo.md", content="x", content_type="text/markdown")
    result = store.rename(a["id"], "p1/bar.md")
    assert result["path"] == "p1/bar.md"
