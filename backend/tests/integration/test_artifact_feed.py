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


def test_feed_paginates_with_cursor_tiebreak(store):
    # Insert 5 rows then force two of them to share an updated_at to
    # exercise the (updated_at, id) tiebreak.
    rows_in = [
        store.create(project_id="p1", path=f"p1/f{i}.md", content="x",
                     content_type="text/markdown")
        for i in range(5)
    ]
    # Force the middle two rows to collide on updated_at.
    collision_ts = rows_in[2]["updated_at"]
    with store._connect() as conn:
        conn.execute(
            "UPDATE artifacts SET updated_at = ? WHERE id = ?",
            (collision_ts, rows_in[3]["id"]),
        )

    with store._connect() as conn:
        expected_order = [
            r["id"] for r in conn.execute(
                "SELECT id FROM artifacts WHERE project_id = ? "
                "ORDER BY updated_at ASC, id ASC",
                ("p1",),
            ).fetchall()
        ]

    seen: list[str] = []
    cursor_updated = None
    cursor_id = None
    while True:
        page = store.feed(
            project_id="p1",
            updated_since=cursor_updated,
            after_id=cursor_id,
            limit=2,
        )
        if not page:
            break
        seen.extend(r["id"] for r in page)
        last = page[-1]
        cursor_updated = last["updated_at"]
        cursor_id = last["id"]
        if len(page) < 2:
            break

    # Every row appears exactly once, in (updated_at, id) order.
    assert sorted(seen) == sorted(r["id"] for r in rows_in)
    assert len(seen) == len(set(seen))
    assert seen == expected_order
