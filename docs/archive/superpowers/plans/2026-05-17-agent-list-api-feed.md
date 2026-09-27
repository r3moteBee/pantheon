# Agent Feed API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GET /api/artifacts/feed`, a cursor-paginated, forward-walk endpoint optimized equally for change-detection polling and bulk enumeration by other agents.

**Architecture:** New `feed()` method on `ArtifactStore` (single chronological-forward query with cursor predicate) + new FastAPI route in `backend/api/artifacts.py` that wraps it with `Query()` validation, `fields=` projection, and the response envelope. Reuses the existing `idx_artifacts_project_updated` partial index (shipped in `2026.05.17.H5`). No schema changes.

**Tech Stack:** Python 3.12 / FastAPI / SQLite via stdlib `sqlite3`. Tests via `pytest` + `fastapi.testclient.TestClient`. Follow the pattern in `backend/tests/integration/test_artifact_bulk_move.py` for API tests and `test_artifact_move_intra_project.py` for store-only tests.

**Spec:** `docs/superpowers/specs/2026-05-17-agent-list-api-design.md`

---

## File Structure

- Modify: `backend/artifacts/store.py` — add `feed()` method on `ArtifactStore`
- Modify: `backend/api/artifacts.py` — add `GET /api/artifacts/feed` route
- Create: `backend/tests/integration/test_artifact_feed.py` — all tests for the new method + endpoint (one file, focused on this feature)
- Modify: `frontend/package.json` — version bump for the ship commit

The store method and the API route are small enough to live in their respective existing files without introducing new modules. Tests live in one new file so the whole feature's behavior is auditable in one place.

---

## Task 0: Create feature branch

**Files:** none

- [ ] **Step 1: Create branch off main**

```bash
cd /home/pan/pantheon
git checkout main
git pull --ff-only origin main
git checkout -b feat/artifacts-feed-api
```

Expected: clean checkout, no merge needed.

- [ ] **Step 2: Verify branch + clean tree**

```bash
git status
```

Expected: `On branch feat/artifacts-feed-api`, working tree clean (modulo the usual untracked `.pids`, `data/`, etc.).

---

## Task 1: `store.feed()` — basic forward walk

**Files:**
- Modify: `backend/artifacts/store.py`
- Create: `backend/tests/integration/test_artifact_feed.py`

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_artifact_feed.py`:

```python
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
```

- [ ] **Step 2: Run the test to confirm it fails**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py -v
```

Expected: FAIL with `AttributeError: 'ArtifactStore' object has no attribute 'feed'`.

- [ ] **Step 3: Add the minimal `feed()` method**

Open `backend/artifacts/store.py`. Find the `tag_counts_all` method (around line 590). Add the following AFTER `tag_counts_all`:

```python
    def feed(
        self,
        *,
        project_id: str | None,
        updated_since: str | None = None,
        after_id: str | None = None,
        include_deleted: bool = False,
        tag: str | None = None,
        content_type: str | None = None,
        path_prefix: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Chronological forward-walk over artifacts. See spec
        docs/superpowers/specs/2026-05-17-agent-list-api-design.md.
        """
        clauses: list[str] = []
        args: list[Any] = []
        if project_id not in (None, "all", ""):
            clauses.append("project_id = ?")
            args.append(project_id)
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM artifacts{where} "
                f"ORDER BY updated_at ASC, id ASC LIMIT ?",
                (*args, limit),
            ).fetchall()
        results = [self._hydrate_artifact(r) for r in rows]
        if tag:
            results = [r for r in results if tag in (r.get("tags") or [])]
        return results
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py -v
```

Expected: PASS — 1 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/artifacts/store.py backend/tests/integration/test_artifact_feed.py
git commit -m "backend/artifacts: add store.feed() forward-walk skeleton"
```

---

## Task 2: Cursor predicate + tiebreaker

**Files:**
- Modify: `backend/artifacts/store.py`
- Modify: `backend/tests/integration/test_artifact_feed.py`

- [ ] **Step 1: Write the failing test for cursor pagination + tiebreak**

Append to `backend/tests/integration/test_artifact_feed.py`:

```python
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
```

- [ ] **Step 2: Run the new test and confirm it fails**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py::test_feed_paginates_with_cursor_tiebreak -v
```

Expected: FAIL — the current `feed()` ignores `updated_since` and `after_id`, so the loop will repeat the same first 2 rows forever (or hit a row-count mismatch).

- [ ] **Step 3: Add cursor predicate to `feed()`**

In `backend/artifacts/store.py`, replace the body of `feed()` (the method you added in Task 1) with the version below. The change is the cursor-clause block between the `include_deleted` check and the `where` assembly:

```python
    def feed(
        self,
        *,
        project_id: str | None,
        updated_since: str | None = None,
        after_id: str | None = None,
        include_deleted: bool = False,
        tag: str | None = None,
        content_type: str | None = None,
        path_prefix: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Chronological forward-walk over artifacts. See spec
        docs/superpowers/specs/2026-05-17-agent-list-api-design.md.
        """
        if after_id is not None and updated_since is None:
            raise ValueError("after_id requires updated_since")
        clauses: list[str] = []
        args: list[Any] = []
        if project_id not in (None, "all", ""):
            clauses.append("project_id = ?")
            args.append(project_id)
        if not include_deleted:
            clauses.append("deleted_at IS NULL")
        if updated_since is not None and after_id is not None:
            clauses.append(
                "(updated_at > ? OR (updated_at = ? AND id > ?))"
            )
            args.extend([updated_since, updated_since, after_id])
        elif updated_since is not None:
            clauses.append("updated_at > ?")
            args.append(updated_since)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM artifacts{where} "
                f"ORDER BY updated_at ASC, id ASC LIMIT ?",
                (*args, limit),
            ).fetchall()
        results = [self._hydrate_artifact(r) for r in rows]
        if tag:
            results = [r for r in results if tag in (r.get("tags") or [])]
        return results
```

- [ ] **Step 4: Run the cursor test plus the original test**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py -v
```

Expected: PASS — 2 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/artifacts/store.py backend/tests/integration/test_artifact_feed.py
git commit -m "backend/artifacts: feed() cursor predicate with (updated_at, id) tiebreak"
```

---

## Task 3: `include_deleted` tombstone handling

**Files:**
- Modify: `backend/tests/integration/test_artifact_feed.py`
- (No store change needed — Task 1 already added the `include_deleted` clause; this task validates it.)

- [ ] **Step 1: Write two tests — excludes by default, surfaces when asked**

Append to `backend/tests/integration/test_artifact_feed.py`:

```python
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
```

- [ ] **Step 2: Confirm both tests pass (no code change required)**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py -v
```

Expected: PASS — 4 passed.

(Method name verified during planning: `ArtifactStore.soft_delete(artifact_id)` at `backend/artifacts/store.py:450`.)

- [ ] **Step 3: Commit**

```bash
cd /home/pan/pantheon
git add backend/tests/integration/test_artifact_feed.py
git commit -m "backend/artifacts: cover feed() tombstone exclude/include semantics"
```

---

## Task 4: Filter parameters (tag / content_type / path_prefix)

**Files:**
- Modify: `backend/artifacts/store.py`
- Modify: `backend/tests/integration/test_artifact_feed.py`

- [ ] **Step 1: Write the failing filters test**

Append to `backend/tests/integration/test_artifact_feed.py`:

```python
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
```

- [ ] **Step 2: Run the test to confirm it fails on content_type + path_prefix**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py::test_feed_respects_filters -v
```

Expected: FAIL — `content_type` and `path_prefix` are not yet wired into SQL (the tag filter already works in Python from Task 1).

- [ ] **Step 3: Wire `content_type` and `path_prefix` into the SQL clause**

In `backend/artifacts/store.py`, in `feed()`, add the two new clauses BEFORE the `if updated_since` cursor block. Insert after the `include_deleted` clause:

```python
        if content_type is not None:
            clauses.append("content_type = ?")
            args.append(content_type)
        if path_prefix is not None:
            clauses.append("path LIKE ?")
            args.append(path_prefix + "%")
```

- [ ] **Step 4: Verify all 5 tests pass**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py -v
```

Expected: PASS — 5 passed.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/artifacts/store.py backend/tests/integration/test_artifact_feed.py
git commit -m "backend/artifacts: feed() supports content_type and path_prefix filters"
```

---

## Task 5: Cross-project mode

**Files:**
- Modify: `backend/tests/integration/test_artifact_feed.py`
- (No store change — Task 1's `if project_id not in (None, "all", "")` already handles this; this task validates it.)

- [ ] **Step 1: Write the failing cross-project test**

Append to `backend/tests/integration/test_artifact_feed.py`:

```python
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
```

- [ ] **Step 2: Run both tests**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py -v
```

Expected: PASS — 7 passed (`test_feed_after_id_without_updated_since_raises` was wired into the store body in Task 2's Step 3; `test_feed_cross_project_returns_rows_from_all_projects` rides on Task 1's project_id branching).

- [ ] **Step 3: Commit**

```bash
cd /home/pan/pantheon
git add backend/tests/integration/test_artifact_feed.py
git commit -m "backend/artifacts: cover feed() cross-project + after_id-without-anchor"
```

---

## Task 6: API endpoint — `GET /api/artifacts/feed`

**Files:**
- Modify: `backend/api/artifacts.py`
- Modify: `backend/tests/integration/test_artifact_feed.py`

- [ ] **Step 1: Write the failing API smoke test**

Append to `backend/tests/integration/test_artifact_feed.py` (note: this adds a `TestClient` fixture; the existing tests do not need one):

```python
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
```

- [ ] **Step 2: Run to confirm 404**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py::test_feed_endpoint_returns_envelope_with_artifacts -v
```

Expected: FAIL — endpoint returns 404 (not registered yet).

- [ ] **Step 3: Add the endpoint**

Open `backend/api/artifacts.py`. Find the existing `@router.get("/artifacts/tags")` handler (around line 163). AFTER its closing, insert:

```python
@router.get("/artifacts/feed")
async def feed(
    project_id: str = Query("default"),
    updated_since: str | None = Query(None),
    after_id: str | None = Query(None),
    include_deleted: bool = Query(False),
    tag: str | None = Query(None),
    content_type: str | None = Query(None),
    path_prefix: str | None = Query(None),
    limit: int = Query(500, ge=1, le=5000),
) -> dict[str, Any]:
    try:
        rows = get_store().feed(
            project_id=project_id,
            updated_since=updated_since,
            after_id=after_id,
            include_deleted=include_deleted,
            tag=tag,
            content_type=content_type,
            path_prefix=path_prefix,
            limit=limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    # Strip heavy fields the feed never returns (spec).
    for row in rows:
        row.pop("content", None)
        row.pop("blob_path", None)
    if len(rows) >= limit:
        last = rows[-1]
        next_cursor = {
            "updated_since": last["updated_at"],
            "after_id": last["id"],
        }
        has_more = True
    else:
        next_cursor = None
        has_more = False
    return {
        "artifacts": rows,
        "next_cursor": next_cursor,
        "has_more": has_more,
        "count": len(rows),
    }
```

- [ ] **Step 4: Run the smoke test**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py::test_feed_endpoint_returns_envelope_with_artifacts -v
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/api/artifacts.py backend/tests/integration/test_artifact_feed.py
git commit -m "backend/api: GET /artifacts/feed with envelope + content stripping"
```

---

## Task 7: `fields=` projection at API layer

**Files:**
- Modify: `backend/api/artifacts.py`
- Modify: `backend/tests/integration/test_artifact_feed.py`

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/integration/test_artifact_feed.py`:

```python
ALLOWED_FIELDS = {
    "id", "project_id", "path", "title", "content_type", "size_bytes",
    "sha256", "tags", "source", "pinned", "current_version_id",
    "created_at", "updated_at", "deleted_at",
}


def test_feed_fields_projection_drops_columns(client):
    _seed("p_proj_1", "p_proj_1/a.md")
    r = client.get("/api/artifacts/feed", params={
        "project_id": "p_proj_1",
        "fields": "id,sha256",
        "limit": 10,
    })
    assert r.status_code == 200, r.text
    row = r.json()["artifacts"][0]
    # Requested fields + always-forced cursor fields.
    assert set(row.keys()) == {"id", "sha256", "updated_at"}


def test_feed_fields_projection_unknown_column_400(client):
    _seed("p_proj_2", "p_proj_2/a.md")
    r = client.get("/api/artifacts/feed", params={
        "project_id": "p_proj_2",
        "fields": "id,not_a_column",
    })
    assert r.status_code == 400, r.text


def test_feed_after_id_without_updated_since_400(client):
    r = client.get("/api/artifacts/feed", params={
        "project_id": "p_proj_3",
        "after_id": "deadbeef",
    })
    assert r.status_code == 400, r.text
```

- [ ] **Step 2: Run to confirm two of the three fail**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py::test_feed_fields_projection_drops_columns tests/integration/test_artifact_feed.py::test_feed_fields_projection_unknown_column_400 tests/integration/test_artifact_feed.py::test_feed_after_id_without_updated_since_400 -v
```

Expected:
- `test_feed_after_id_without_updated_since_400` PASS (already wired in Task 6's `except ValueError` block).
- `test_feed_fields_projection_drops_columns` FAIL (no projection yet).
- `test_feed_fields_projection_unknown_column_400` FAIL (no validation yet).

- [ ] **Step 3: Add `fields` projection to the endpoint**

In `backend/api/artifacts.py`, update the `feed` handler signature + body. Add `fields` to the signature:

```python
    fields: str | None = Query(None),
```

(Insert it right above `limit:` in the signature.)

Then, AFTER the `for row in rows: row.pop("content", None); row.pop("blob_path", None)` block and BEFORE the `if len(rows) >= limit` block, add:

```python
    if fields is not None:
        requested = {f.strip() for f in fields.split(",") if f.strip()}
        allowed = {
            "id", "project_id", "path", "title", "content_type", "size_bytes",
            "sha256", "tags", "source", "pinned", "current_version_id",
            "created_at", "updated_at", "deleted_at",
        }
        unknown = requested - allowed
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown fields: {sorted(unknown)}",
            )
        # `id` and `updated_at` are always present so agents can advance the cursor.
        keep = requested | {"id", "updated_at"}
        rows = [{k: v for k, v in row.items() if k in keep} for row in rows]
```

- [ ] **Step 4: Run all three tests**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py -v
```

Expected: PASS — all tests in the file (~12 tests).

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/api/artifacts.py backend/tests/integration/test_artifact_feed.py
git commit -m "backend/api: feed endpoint fields= projection with forced cursor fields"
```

---

## Task 8: Cursor round-trip + has_more correctness

**Files:**
- Modify: `backend/tests/integration/test_artifact_feed.py`

- [ ] **Step 1: Write the cursor round-trip test**

Append to `backend/tests/integration/test_artifact_feed.py`:

```python
def test_feed_cursor_round_trip_walks_all_pages(client):
    for i in range(7):
        _seed("p_cursor_1", f"p_cursor_1/f{i}.md")

    seen: list[str] = []
    params: dict = {"project_id": "p_cursor_1", "limit": 3}
    while True:
        r = client.get("/api/artifacts/feed", params=params)
        assert r.status_code == 200, r.text
        data = r.json()
        seen.extend(row["id"] for row in data["artifacts"])
        if not data["has_more"]:
            break
        params = {
            "project_id": "p_cursor_1",
            "limit": 3,
            "updated_since": data["next_cursor"]["updated_since"],
            "after_id": data["next_cursor"]["after_id"],
        }

    assert len(seen) == 7
    assert len(set(seen)) == 7  # no dupes


def test_feed_last_page_has_no_cursor(client):
    _seed("p_lastpage_1", "p_lastpage_1/only.md")
    r = client.get("/api/artifacts/feed", params={"project_id": "p_lastpage_1", "limit": 10})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["count"] == 1
    assert data["has_more"] is False
    assert data["next_cursor"] is None
```

- [ ] **Step 2: Run and verify they pass**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py -v
```

Expected: PASS — all tests still pass (the round-trip behaviour was already implemented in Task 6).

- [ ] **Step 3: Commit**

```bash
cd /home/pan/pantheon
git add backend/tests/integration/test_artifact_feed.py
git commit -m "backend/api: cover feed cursor round-trip + last-page envelope"
```

---

## Task 9: Confirm the index actually serves the empty-poll path

**Files:**
- Modify: `backend/tests/integration/test_artifact_feed.py`

- [ ] **Step 1: Write the EXPLAIN QUERY PLAN test**

Append to `backend/tests/integration/test_artifact_feed.py`:

```python
def test_feed_empty_poll_uses_index(store):
    # Seed a few rows so SQLite's planner has stats to work with.
    for i in range(20):
        store.create(project_id="p_idx_1", path=f"p_idx_1/f{i}.md",
                     content="x", content_type="text/markdown")
    with store._connect() as conn:
        plan = conn.execute(
            "EXPLAIN QUERY PLAN "
            "SELECT * FROM artifacts "
            "WHERE project_id = ? AND deleted_at IS NULL AND updated_at > ? "
            "ORDER BY updated_at ASC, id ASC LIMIT 500",
            ("p_idx_1", "2099-01-01T00:00:00Z"),
        ).fetchall()
    plan_text = " | ".join(row["detail"] for row in plan)
    assert "idx_artifacts_project_updated" in plan_text, plan_text
```

- [ ] **Step 2: Run the test**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py::test_feed_empty_poll_uses_index -v
```

Expected: PASS — the per-test `ArtifactStore` fixture runs the migration on init, which creates `idx_artifacts_project_updated` (added in `2026.05.17.H5`).

If the test fails with a plan mentioning `idx_artifacts_pinned` or `SCAN artifacts`, the migration may not have included the new index in the test environment. Confirm `backend/data/migrations/001_artifacts.sql` has the index DDL (committed in `e036a2e`); if missing, re-add it and rerun.

- [ ] **Step 3: Commit**

```bash
cd /home/pan/pantheon
git add backend/tests/integration/test_artifact_feed.py
git commit -m "backend/artifacts: lock in feed() empty-poll uses (project_id, updated_at) index"
```

---

## Task 10: Concurrent-write-during-walk safety

**Files:**
- Modify: `backend/tests/integration/test_artifact_feed.py`

- [ ] **Step 1: Write the concurrent-write test**

Append to `backend/tests/integration/test_artifact_feed.py`:

```python
def test_feed_concurrent_write_during_walk_is_picked_up(store):
    # Two rows; walk page 1, write a row, walk page 2 — the new row appears.
    a = store.create(project_id="p_conc_1", path="p_conc_1/a.md",
                     content="x", content_type="text/markdown")
    b = store.create(project_id="p_conc_1", path="p_conc_1/b.md",
                     content="x", content_type="text/markdown")
    page1 = store.feed(project_id="p_conc_1", limit=2)
    assert [r["id"] for r in page1] == [a["id"], b["id"]]

    # New write happens "during" the walk (between pages).
    c = store.create(project_id="p_conc_1", path="p_conc_1/c.md",
                     content="x", content_type="text/markdown")

    last = page1[-1]
    page2 = store.feed(
        project_id="p_conc_1",
        updated_since=last["updated_at"],
        after_id=last["id"],
        limit=2,
    )
    assert [r["id"] for r in page2] == [c["id"]]
```

- [ ] **Step 2: Run and verify**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py::test_feed_concurrent_write_during_walk_is_picked_up -v
```

Expected: PASS.

- [ ] **Step 3: Run the whole feed test file once more**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_feed.py -v
```

Expected: PASS — all tests (roughly 14 total).

- [ ] **Step 4: Run the full integration test suite to confirm no regression**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ -v
```

Expected: PASS — pre-existing 171 + the new feed tests (~14) all green; 5 live-network skipped as usual.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/tests/integration/test_artifact_feed.py
git commit -m "backend/artifacts: cover feed() forward-walk safety under concurrent writes"
```

---

## Task 11: Version bump + ship

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Bump the version**

Open `frontend/package.json`. Find the `"version"` field. On 2026-05-17 the most recent ship was `2026.05.17.H5`; the next H is whatever is current — if nothing else has shipped since `H5`, use `H6`. Otherwise increment from the current value.

- [ ] **Step 2: Commit the bump**

```bash
cd /home/pan/pantheon
git add frontend/package.json
git commit -m "release: bump frontend for /api/artifacts/feed endpoint"
```

- [ ] **Step 3: Hand off rebuild + smoke to Brent**

Tell Brent:

> "Feed endpoint is implementation-complete on branch `feat/artifacts-feed-api`. Please rebuild + smoke:
>
> ```
> cd ~/pantheon && git pull
> ./stop.sh; pkill -f 'uvicorn main:app' 2>/dev/null
> find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
> ./start.sh && sleep 4 && curl -s http://localhost:8000/api/health
> ```
>
> Confirm the health endpoint returns the new version. Then probe the new endpoint:
>
> ```
> . /tmp/.pan_token
> # Empty poll (steady state) — should be tiny + fast
> curl -s -H \"Authorization: Bearer $TOKEN\" \\
>   'http://localhost:8000/api/artifacts/feed?project_id=default&updated_since=2099-01-01T00:00:00Z' \\
>   | python3 -m json.tool
>
> # Walk first page
> curl -s -H \"Authorization: Bearer $TOKEN\" \\
>   'http://localhost:8000/api/artifacts/feed?project_id=default&limit=5&fields=id,sha256,path,updated_at' \\
>   | python3 -m json.tool
>
> # Cursor round-trip — paste next_cursor.updated_since + after_id into a second call
> ```"

- [ ] **Step 4: After Brent confirms, push the branch**

```bash
cd /home/pan/pantheon
git push -u origin feat/artifacts-feed-api
```

Brent's workflow: feature branch → push → fast-forward main → delete branch. He drives the final merge.

---

## Done

All Phase 1 tasks complete. The endpoint is live, tested at both store and API layers, indexed, and safe under concurrent writes. Future v2 items (total_count, MCP wrapping, artifact_tags join table, ETag/If-Modified-Since) wait on real demand.
