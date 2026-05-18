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


def test_feed_excludes_tombstones_by_default(store):
    a = store.create(project_id="p1", path="p1/keep.md", content="x",
                     content_type="text/markdown")
    b = store.create(project_id="p1", path="p1/gone.md", content="x",
                     content_type="text/markdown")
    store.soft_delete(b["id"])
    rows = store.feed(project_id="p1", limit=10)
    ids = [r["id"] for r in rows]
    assert a["id"] in ids
    assert b["id"] not in ids


def test_feed_include_deleted_surfaces_tombstones(store):
    a = store.create(project_id="p1", path="p1/keep.md", content="x",
                     content_type="text/markdown")
    b = store.create(project_id="p1", path="p1/gone.md", content="x",
                     content_type="text/markdown")
    store.soft_delete(b["id"])
    rows = store.feed(project_id="p1", include_deleted=True, limit=10)
    by_id = {r["id"]: r for r in rows}
    assert a["id"] in by_id and by_id[a["id"]]["deleted_at"] is None
    assert b["id"] in by_id and by_id[b["id"]]["deleted_at"] is not None


def test_feed_respects_filters(store):
    store.create(project_id="p1", path="p1/notes/a.md", content="x",
                 content_type="text/markdown", tags=["alpha"])
    store.create(project_id="p1", path="p1/notes/b.md", content="x",
                 content_type="text/markdown", tags=["beta"])
    store.create(project_id="p1", path="p1/code/c.py", content="x",
                 content_type="text/x-python", tags=["alpha"])

    # tag filter
    rows = store.feed(project_id="p1", tag="alpha", limit=10)
    assert {r["path"] for r in rows} == {"p1/notes/a.md", "p1/code/c.py"}

    # content_type filter
    rows = store.feed(project_id="p1", content_type="text/x-python", limit=10)
    assert {r["path"] for r in rows} == {"p1/code/c.py"}

    # path_prefix filter
    rows = store.feed(project_id="p1", path_prefix="p1/notes/", limit=10)
    assert {r["path"] for r in rows} == {"p1/notes/a.md", "p1/notes/b.md"}


def test_feed_cross_project_returns_rows_from_all_projects(store):
    a = store.create(project_id="p1", path="p1/a.md", content="x",
                     content_type="text/markdown")
    b = store.create(project_id="p2", path="p2/b.md", content="x",
                     content_type="text/markdown")
    c = store.create(project_id="p3", path="p3/c.md", content="x",
                     content_type="text/markdown")
    rows = store.feed(project_id="all", limit=10)
    assert {r["id"] for r in rows} == {a["id"], b["id"], c["id"]}
    # Globally ordered ascending by updated_at.
    assert [r["id"] for r in rows] == [a["id"], b["id"], c["id"]]


def test_feed_after_id_without_updated_since_raises(store):
    with pytest.raises(ValueError):
        store.feed(project_id="p1", after_id="abc", limit=10)


from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
from artifacts.store import get_store  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


def _seed(project_id, path, content="x", content_type="text/markdown",
          tags=None):
    return get_store().create(
        project_id=project_id, path=path, content=content,
        content_type=content_type, tags=tags or [],
    )


def test_feed_endpoint_returns_envelope_with_artifacts(client):
    a = _seed("p_api_1", "p_api_1/a.md")
    b = _seed("p_api_1", "p_api_1/b.md")
    r = client.get("/api/artifacts/feed", params={"project_id": "p_api_1", "limit": 10})
    assert r.status_code == 200, r.text
    data = r.json()
    assert "artifacts" in data
    assert "next_cursor" in data
    assert "has_more" in data
    assert "count" in data
    ids = [row["id"] for row in data["artifacts"]]
    assert a["id"] in ids and b["id"] in ids
    # `content` is stripped from list responses (spec).
    assert all("content" not in row for row in data["artifacts"])
