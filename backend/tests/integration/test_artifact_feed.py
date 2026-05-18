"""Verify ArtifactStore.feed() — forward-walk listing for agent consumers.

Run: pytest backend/tests/integration/test_artifact_feed.py -v
"""
from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))
os.environ.setdefault("AUTH_PASSWORD", "")

from artifacts.store import ArtifactStore  # noqa: E402


@pytest.fixture
def store(tmp_path):
    db = tmp_path / "artifacts.db"
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    return ArtifactStore(db_path=str(db), blobs_dir=blobs)


def test_feed_returns_rows_ordered_by_updated_at_asc(store):
    a = store.create(project_id="p1", path="p1/a.md", content="x",
                     content_type="text/markdown")
    b = store.create(project_id="p1", path="p1/b.md", content="x",
                     content_type="text/markdown")
    c = store.create(project_id="p1", path="p1/c.md", content="x",
                     content_type="text/markdown")
    rows = store.feed(project_id="p1", limit=10)
    assert [r["id"] for r in rows] == [a["id"], b["id"], c["id"]]
