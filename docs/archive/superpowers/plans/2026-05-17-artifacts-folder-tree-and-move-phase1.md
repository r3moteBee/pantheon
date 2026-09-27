# Artifacts: Folder Tree + Move (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat folder rail with a collapsible, project-aware tree; add move (intra- and cross-project) + duplicate operations via drag-and-drop and a Move/Duplicate modal.

**Architecture:** Backend gains `_unique_path` for conflict resolution, `graph.strip_artifact` + `semantic.strip_artifact` primitives, and `store.move` / `store.duplicate` methods that wire those together. Two new endpoints — single-id `POST /api/artifacts/{id}/move` and bulk `POST /api/artifacts/bulk/move` — accept a `mode` parameter that dispatches to move or duplicate. Frontend gets a reusable `FolderTree` component (single + multi-project rendering, persisted collapse, optional DnD targets), a `MoveModal` (selection + destination + new-folder input), and an inline cross-project confirmation in `ArtifactsPage`. Re-extraction of memory after a cross-project move or duplicate runs inline via `MemoryManager.index_artifact` (existing API).

**Tech Stack:** Python 3.12 + FastAPI + SQLite (chromadb for semantic) on the backend; React + Vite + Tailwind + Zustand on the frontend. Tests are pytest at `backend/tests/integration/`; no frontend test harness — manual smoke after build.

**Reference spec:** `docs/superpowers/specs/2026-05-17-artifacts-folder-tree-and-move-design.md`

**Pre-flight:**
- Branch: create `feat/artifacts-folder-tree-and-move` off `main` for this work. The current branch `feat/sec-plugin` has unrelated SEC-plugin commits that shouldn't be mixed in.
- Tests run from repo root: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/<file> -v`
- After frontend changes, **do not run the rebuild yourself** — the project convention is that Brent runs deploy commands. After the final task, output the rebuild command for him.

---

## File Structure

**Backend files touched:**
- `backend/artifacts/store.py` — new methods: `_unique_path`, `move`, `duplicate`; modify `rename`.
- `backend/memory/graph.py` — new async method: `strip_artifact(artifact_id)`.
- `backend/memory/semantic.py` — new async method: `strip_artifact(artifact_id)`.
- `backend/api/artifacts.py` — new endpoints: `POST /artifacts/{id}/move`, `POST /artifacts/bulk/move`; minor edit to `rename` response.
- `backend/tests/integration/test_artifact_rename_conflict.py` — new.
- `backend/tests/integration/test_artifact_move_intra_project.py` — new.
- `backend/tests/integration/test_artifact_move_cross_project.py` — new.
- `backend/tests/integration/test_artifact_duplicate.py` — new.
- `backend/tests/integration/test_artifact_bulk_move.py` — new.
- `backend/tests/integration/test_memory_strip_artifact.py` — new.

**Frontend files touched:**
- `frontend/src/components/FolderTree.jsx` — new (~150 lines).
- `frontend/src/components/MoveModal.jsx` — new (~120 lines).
- `frontend/src/pages/ArtifactsPage.jsx` — modified: replace flat folder rail render; add DnD handlers; add cross-project confirmation modal state + render; add Move/Duplicate buttons to detail + bulk toolbars.
- `frontend/src/api/client.js` — add `move`, `moveBulk`.
- `frontend/package.json` — version bump.

---

## Task 0: Create feature branch

**Files:** none (git operation)

- [ ] **Step 1: Create and switch to the feature branch from main**

```bash
cd /home/pan/pantheon
git checkout main
git pull
git checkout -b feat/artifacts-folder-tree-and-move
```

Expected: `Switched to a new branch 'feat/artifacts-folder-tree-and-move'`.

---

## Task 1: `_unique_path` helper

**Files:**
- Modify: `backend/artifacts/store.py` (add method to `ArtifactStore` class, around line 330 before `rename`)
- Test: `backend/tests/integration/test_artifact_rename_conflict.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_artifact_rename_conflict.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_rename_conflict.py -v
```

Expected: FAIL with `AttributeError: 'ArtifactStore' object has no attribute '_unique_path'`.

- [ ] **Step 3: Implement `_unique_path`**

In `backend/artifacts/store.py`, add this method to `ArtifactStore` just before the existing `rename` method (line ~330):

```python
def _unique_path(self, project_id: str, desired_path: str) -> str:
    """Return desired_path if free; otherwise suffix the basename: foo.md → foo-1.md."""
    with self._connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM artifacts WHERE project_id = ? AND path = ? AND deleted_at IS NULL",
            (project_id, desired_path),
        ).fetchone()
        if not row:
            return desired_path
        # Split into stem + extension. "foo.md" -> ("foo", ".md"); "README" -> ("README", "").
        last_slash = desired_path.rfind("/")
        dir_part = desired_path[: last_slash + 1] if last_slash >= 0 else ""
        base = desired_path[last_slash + 1 :]
        dot = base.rfind(".")
        if dot > 0:
            stem, ext = base[:dot], base[dot:]
        else:
            stem, ext = base, ""
        for n in range(1, 1001):
            candidate = f"{dir_part}{stem}-{n}{ext}"
            row = conn.execute(
                "SELECT 1 FROM artifacts WHERE project_id = ? AND path = ? AND deleted_at IS NULL",
                (project_id, candidate),
            ).fetchone()
            if not row:
                return candidate
        raise RuntimeError(f"_unique_path: exhausted suffixes for {desired_path}")
```

- [ ] **Step 4: Run test to verify it passes**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_rename_conflict.py -v
```

Expected: 5 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/artifacts/store.py backend/tests/integration/test_artifact_rename_conflict.py
git commit -m "backend/artifacts: add _unique_path helper for conflict-free paths"
```

---

## Task 2: Update `rename` to use `_unique_path` and return the final path

**Files:**
- Modify: `backend/artifacts/store.py:332-336` (`rename` method)
- Modify: `backend/tests/integration/test_artifact_rename_conflict.py` (add a rename test)

- [ ] **Step 1: Add a failing test for rename auto-suffix**

Append to `backend/tests/integration/test_artifact_rename_conflict.py`:

```python
def test_rename_auto_suffixes_on_collision(store):
    a = store.create(project_id="p1", path="p1/foo.md", content="first", content_type="text/markdown")
    b = store.create(project_id="p1", path="p1/bar.md", content="second", content_type="text/markdown")
    result = store.rename(b["id"], "p1/foo.md")
    assert result["path"] == "p1/foo-1.md"


def test_rename_no_collision_returns_requested(store):
    a = store.create(project_id="p1", path="p1/foo.md", content="x", content_type="text/markdown")
    result = store.rename(a["id"], "p1/bar.md")
    assert result["path"] == "p1/bar.md"
```

- [ ] **Step 2: Run to verify failures**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_rename_conflict.py::test_rename_auto_suffixes_on_collision -v
```

Expected: FAIL — rename currently writes the path as-given without conflict check.

- [ ] **Step 3: Modify `rename` in `backend/artifacts/store.py`**

Replace the existing `rename` method (around line 332):

```python
def rename(self, artifact_id: str, new_path: str) -> dict[str, Any]:
    cur = self.get(artifact_id)
    if not cur:
        raise ValueError(f"artifact not found: {artifact_id}")
    final_path = self._unique_path(cur["project_id"], new_path)
    with self._connect() as conn:
        conn.execute("UPDATE artifacts SET path = ?, updated_at = ? WHERE id = ?",
                     (final_path, _now(), artifact_id))
    return self.get(artifact_id)
```

- [ ] **Step 4: Run all tests in the file**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_rename_conflict.py -v
```

Expected: 7 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/artifacts/store.py backend/tests/integration/test_artifact_rename_conflict.py
git commit -m "backend/artifacts: rename auto-suffixes on path conflict"
```

---

## Task 3: `graph.strip_artifact` helper

**Files:**
- Modify: `backend/memory/graph.py` (add async method after `delete_node`, around line 477)
- Test: `backend/tests/integration/test_memory_strip_artifact.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_memory_strip_artifact.py`:

```python
"""Verify graph.strip_artifact removes the artifact's 1:1 nodes but
preserves shared topic nodes that other artifacts reference.

Run: pytest backend/tests/integration/test_memory_strip_artifact.py -v
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from memory.graph import GraphMemory  # noqa: E402


@pytest.fixture
def graph(tmp_path):
    return GraphMemory(project_id="p1", db_path=str(tmp_path / "graph.db"))


def test_strip_artifact_removes_owned_nodes(graph):
    async def run():
        a_node = await graph.add_node(
            "source", "doc-A", metadata={"artifact_id": "art-1"}
        )
        topic = await graph.add_node("concept", "shared-topic", metadata={})
        await graph.add_edge_by_label("doc-A", "shared-topic", "DISCUSSES")

        await graph.strip_artifact("art-1")

        # The artifact's own node is gone; the shared topic stays.
        assert await graph.get_node(a_node) is None
        assert await graph.get_node(topic) is not None
    asyncio.run(run())


def test_strip_artifact_leaves_other_artifacts_alone(graph):
    async def run():
        await graph.add_node("source", "doc-A", metadata={"artifact_id": "art-1"})
        b_node = await graph.add_node("source", "doc-B", metadata={"artifact_id": "art-2"})
        await graph.strip_artifact("art-1")
        assert await graph.get_node(b_node) is not None
    asyncio.run(run())


def test_strip_artifact_is_idempotent(graph):
    async def run():
        await graph.add_node("source", "doc-A", metadata={"artifact_id": "art-1"})
        await graph.strip_artifact("art-1")
        await graph.strip_artifact("art-1")  # second call must not raise
    asyncio.run(run())
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_memory_strip_artifact.py -v
```

Expected: FAIL with `AttributeError: 'GraphMemory' object has no attribute 'strip_artifact'`.

- [ ] **Step 3: Implement `strip_artifact` in `backend/memory/graph.py`**

Add after the existing `delete_node` method (around line 500):

```python
async def strip_artifact(self, artifact_id: str) -> int:
    """Delete graph nodes whose metadata.artifact_id matches.

    Used to clean up an artifact's 1:1 contributions (source + content nodes)
    when the artifact moves to another project or is duplicated/removed.
    Shared topic/concept nodes referenced by other artifacts are preserved.

    Returns the number of nodes deleted.
    """
    deleted = 0
    with self._connect() as conn:
        rows = conn.execute(
            "SELECT id, metadata FROM graph_nodes WHERE project_id = ?",
            (self.project_id,),
        ).fetchall()
        target_ids: list[str] = []
        for row in rows:
            try:
                meta = json.loads(row["metadata"] or "{}")
            except json.JSONDecodeError:
                continue
            if meta.get("artifact_id") == artifact_id:
                target_ids.append(row["id"])
        for node_id in target_ids:
            # FK ON DELETE CASCADE removes incident edges.
            conn.execute("DELETE FROM graph_nodes WHERE id = ?", (node_id,))
            deleted += 1
        conn.commit()
    return deleted
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_memory_strip_artifact.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/memory/graph.py backend/tests/integration/test_memory_strip_artifact.py
git commit -m "backend/memory: add graph.strip_artifact for project-scoped cleanup"
```

---

## Task 4: `semantic.strip_artifact` helper

**Files:**
- Modify: `backend/memory/semantic.py` (add method after existing `delete`, around line 217)
- Modify: `backend/tests/integration/test_memory_strip_artifact.py` (add semantic test)

- [ ] **Step 1: Add a failing test for semantic strip**

Append to `backend/tests/integration/test_memory_strip_artifact.py`:

```python
from memory.semantic import SemanticMemory  # noqa: E402


@pytest.fixture
def semantic(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    # SemanticMemory uses ChromaDB under DATA_DIR/chroma; using tmp_path
    # gives each test an isolated collection.
    return SemanticMemory(project_id="p1")


def test_strip_artifact_deletes_matching_chunks(semantic):
    async def run():
        await semantic.add(
            content="chunk-1 from artifact A",
            metadata={"artifact_id": "art-1", "kind": "artifact_chunk"},
        )
        await semantic.add(
            content="chunk-2 from artifact A",
            metadata={"artifact_id": "art-1", "kind": "artifact_chunk"},
        )
        await semantic.add(
            content="chunk from artifact B",
            metadata={"artifact_id": "art-2", "kind": "artifact_chunk"},
        )
        n = await semantic.strip_artifact("art-1")
        assert n == 2
        # Only the surviving artifact's chunks remain.
        remaining = await semantic.list_memories(limit=10)
        assert len(remaining) == 1
        assert remaining[0]["metadata"]["artifact_id"] == "art-2"
    asyncio.run(run())
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_memory_strip_artifact.py::test_strip_artifact_deletes_matching_chunks -v
```

Expected: FAIL with `AttributeError: 'SemanticMemory' object has no attribute 'strip_artifact'`.

- [ ] **Step 3: Implement `strip_artifact` in `backend/memory/semantic.py`**

Add after the existing `delete` method:

```python
async def strip_artifact(self, artifact_id: str) -> int:
    """Delete all semantic chunks whose metadata.artifact_id matches.

    Returns the number of chunks deleted.
    """
    try:
        collection = await self._get_collection_async()
        # Chroma's where filter supports metadata equality.
        existing = await _asyncio.to_thread(
            collection.get,
            where={"artifact_id": artifact_id},
            include=[],
        )
        ids = existing.get("ids") if existing else []
        if not ids:
            return 0
        await _asyncio.to_thread(collection.delete, ids=ids)
        return len(ids)
    except Exception as e:
        logger.error(f"strip_artifact({artifact_id}) failed: {e}")
        return 0
```

- [ ] **Step 4: Run test to verify pass**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_memory_strip_artifact.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/memory/semantic.py backend/tests/integration/test_memory_strip_artifact.py
git commit -m "backend/memory: add semantic.strip_artifact via chroma where-filter"
```

---

## Task 5: `store.duplicate` method

**Files:**
- Modify: `backend/artifacts/store.py` (add `duplicate` method after `rename`)
- Test: `backend/tests/integration/test_artifact_duplicate.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_artifact_duplicate.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_duplicate.py -v
```

Expected: FAIL with `AttributeError: 'ArtifactStore' object has no attribute 'duplicate'`.

- [ ] **Step 3: Implement `duplicate` in `backend/artifacts/store.py`**

Add after the existing `rename` method:

```python
def duplicate(
    self,
    artifact_id: str,
    dest_project_id: str,
    dest_folder: str,
) -> dict[str, Any]:
    """Create an independent copy of the artifact in dest_project / dest_folder.

    The new artifact has a fresh id and is a deep-copy of the source's
    content + metadata + tags. Memory (graph + semantic) is NOT copied;
    callers should index_artifact on the returned row in the dest project.
    """
    src = self.get(artifact_id)
    if not src:
        raise ValueError(f"artifact not found: {artifact_id}")
    basename = src["path"].rsplit("/", 1)[-1]
    folder = dest_folder.rstrip("/")
    desired = f"{folder}/{basename}" if folder else basename
    final_path = self._unique_path(dest_project_id, desired)

    # Materialize content: text artifacts store text inline; binary uses blob_path.
    content: str | bytes
    if src.get("content") is not None:
        content = src["content"]
    elif src.get("blob_path"):
        content = self._load_blob(src["blob_path"])
    else:
        content = b""

    new_row = self.create(
        project_id=dest_project_id,
        path=final_path,
        content=content,
        content_type=src.get("content_type") or "text/plain",
        title=src.get("title"),
        tags=list(src.get("tags") or []),
        source=src.get("source"),
    )
    return new_row
```

> **Why `create` and not some other entry-point:** `ArtifactStore.create` is the canonical insert method (see `backend/artifacts/store.py:102`). It accepts `content: str | bytes` — string for text types, bytes for binary; the method internally routes to blob storage when the content type is non-textual.

- [ ] **Step 4: Run tests to verify pass**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_duplicate.py -v
```

Expected: 4 tests PASS. If `save()` rejects `blob_bytes`, inspect its signature and adapt the implementation; the test surface stays the same.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/artifacts/store.py backend/tests/integration/test_artifact_duplicate.py
git commit -m "backend/artifacts: add store.duplicate with collision-safe paths"
```

---

## Task 6: `store.move` (intra-project)

**Files:**
- Modify: `backend/artifacts/store.py` (add `move` method)
- Test: `backend/tests/integration/test_artifact_move_intra_project.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_artifact_move_intra_project.py`:

```python
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
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_move_intra_project.py -v
```

Expected: FAIL with `AttributeError: 'ArtifactStore' object has no attribute 'move'`.

- [ ] **Step 3: Implement `move` in `backend/artifacts/store.py`**

Add after `duplicate`:

```python
def move(
    self,
    artifact_id: str,
    dest_project_id: str,
    dest_folder: str,
) -> dict[str, Any]:
    """Move an artifact to dest_project / dest_folder.

    Intra-project: changes `path` only. The artifact id is stable.
    Cross-project: updates `project_id` + `path`. Memory cleanup
    (graph + semantic) in source project must be performed by the
    CALLER via graph.strip_artifact + semantic.strip_artifact;
    re-extraction in dest must be triggered via
    MemoryManager.index_artifact(id) — those primitives live above
    the store layer.

    Returns the updated artifact dict.
    """
    src = self.get(artifact_id)
    if not src:
        raise ValueError(f"artifact not found: {artifact_id}")
    basename = src["path"].rsplit("/", 1)[-1]
    folder = dest_folder.rstrip("/")
    desired = f"{folder}/{basename}" if folder else basename
    final_path = self._unique_path(dest_project_id, desired)

    with self._connect() as conn:
        conn.execute(
            "UPDATE artifacts SET project_id = ?, path = ?, updated_at = ? WHERE id = ?",
            (dest_project_id, final_path, _now(), artifact_id),
        )
        conn.commit()
    return self.get(artifact_id)
```

- [ ] **Step 4: Run tests**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_move_intra_project.py -v
```

Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/artifacts/store.py backend/tests/integration/test_artifact_move_intra_project.py
git commit -m "backend/artifacts: add store.move with auto-suffix conflict handling"
```

---

## Task 7: API endpoint — `POST /api/artifacts/{id}/move`

**Files:**
- Modify: `backend/api/artifacts.py` (add endpoint after `rename_artifact`, around line 226)
- Test: `backend/tests/integration/test_artifact_move_cross_project.py` (new — covers both intra + cross-project + duplicate via the endpoint)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_artifact_move_cross_project.py`:

```python
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
        await g.add_node("source", "doc-foo", metadata={"artifact_id": aid})
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
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_move_cross_project.py -v
```

Expected: FAIL — the endpoint doesn't exist yet (404 from FastAPI on POST).

- [ ] **Step 3: Add the endpoint to `backend/api/artifacts.py`**

First add the request schema near the top of the file (after the existing `RenameRequest` class around line 40):

```python
class MoveRequest(BaseModel):
    dest_project_id: str | None = None  # None ⇒ current project
    dest_folder: str = ""
    mode: str = "move"  # "move" | "duplicate"
```

Then add the endpoint just after `rename_artifact` (around line 226):

```python
@router.post("/artifacts/{artifact_id}/move")
async def move_artifact(artifact_id: str, req: MoveRequest) -> dict[str, Any]:
    store = get_store()
    src = store.get(artifact_id)
    if not src:
        raise HTTPException(status_code=404, detail="artifact not found")
    if req.mode not in ("move", "duplicate"):
        raise HTTPException(status_code=400, detail=f"invalid mode: {req.mode}")
    dest_project = req.dest_project_id or src["project_id"]

    if req.mode == "duplicate":
        new_row = store.duplicate(artifact_id, dest_project, req.dest_folder)
        # Re-extract in dest (best-effort).
        try:
            from memory.manager import create_memory_manager
            mgr = create_memory_manager(project_id=dest_project)
            await mgr.index_artifact(new_row["id"], force=True)
        except Exception as e:
            logger.warning("index_artifact failed for duplicate %s: %s", new_row["id"], e)
        return new_row

    # mode == "move"
    cross_project = dest_project != src["project_id"]
    updated = store.move(artifact_id, dest_project, req.dest_folder)
    if cross_project:
        # Strip source project's memory for this artifact.
        try:
            from memory.graph import GraphMemory
            from memory.semantic import SemanticMemory
            src_graph = GraphMemory(project_id=src["project_id"])
            src_sem = SemanticMemory(project_id=src["project_id"])
            await src_graph.strip_artifact(artifact_id)
            await src_sem.strip_artifact(artifact_id)
        except Exception as e:
            logger.warning("memory strip failed on move %s: %s", artifact_id, e)
        # Re-extract in dest project.
        try:
            from memory.manager import create_memory_manager
            mgr = create_memory_manager(project_id=dest_project)
            await mgr.index_artifact(artifact_id, force=True)
        except Exception as e:
            logger.warning("index_artifact failed for moved %s: %s", artifact_id, e)
    return updated
```

Make sure `logger` is imported at the top of `artifacts.py` (`import logging; logger = logging.getLogger(__name__)`); if not present, add it.

- [ ] **Step 4: Run tests**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_move_cross_project.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/api/artifacts.py backend/tests/integration/test_artifact_move_cross_project.py
git commit -m "backend/api: POST /artifacts/{id}/move — move + duplicate with memory cleanup"
```

---

## Task 8: API endpoint — `POST /api/artifacts/bulk/move`

**Files:**
- Modify: `backend/api/artifacts.py` (add bulk endpoint after the single-id move endpoint)
- Test: `backend/tests/integration/test_artifact_bulk_move.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_artifact_bulk_move.py`:

```python
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
    ok = [r for r in data["results"] if "new_path" in r]
    err = [r for r in data["results"] if "error" in r]
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
```

- [ ] **Step 2: Run to verify failure**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_bulk_move.py -v
```

Expected: FAIL — endpoint does not exist.

- [ ] **Step 3: Add the bulk endpoint to `backend/api/artifacts.py`**

First add a request schema (near other request models):

```python
class BulkMoveRequest(BaseModel):
    ids: list[str]
    dest_project_id: str | None = None
    dest_folder: str = ""
    mode: str = "move"
```

Then add the endpoint near the other bulk endpoints (around line 301):

```python
@router.post("/artifacts/bulk/move")
async def bulk_move(req: BulkMoveRequest) -> dict[str, Any]:
    store = get_store()
    if req.mode not in ("move", "duplicate"):
        raise HTTPException(status_code=400, detail=f"invalid mode: {req.mode}")
    results: list[dict[str, Any]] = []
    for aid in req.ids:
        try:
            src = store.get(aid)
            if not src:
                results.append({"id": aid, "error": "artifact not found"})
                continue
            dest_project = req.dest_project_id or src["project_id"]
            cross = dest_project != src["project_id"]
            if req.mode == "duplicate":
                new_row = store.duplicate(aid, dest_project, req.dest_folder)
                try:
                    from memory.manager import create_memory_manager
                    mgr = create_memory_manager(project_id=dest_project)
                    await mgr.index_artifact(new_row["id"], force=True)
                except Exception as e:
                    logger.warning("index_artifact failed for dup %s: %s", new_row["id"], e)
                results.append({
                    "id": new_row["id"],
                    "src_id": aid,
                    "old_path": src["path"],
                    "new_path": new_row["path"],
                    "new_project_id": new_row["project_id"],
                    "mode": "duplicate",
                })
            else:
                updated = store.move(aid, dest_project, req.dest_folder)
                if cross:
                    try:
                        from memory.graph import GraphMemory
                        from memory.semantic import SemanticMemory
                        await GraphMemory(project_id=src["project_id"]).strip_artifact(aid)
                        await SemanticMemory(project_id=src["project_id"]).strip_artifact(aid)
                        from memory.manager import create_memory_manager
                        mgr = create_memory_manager(project_id=dest_project)
                        await mgr.index_artifact(aid, force=True)
                    except Exception as e:
                        logger.warning("memory steps failed for move %s: %s", aid, e)
                results.append({
                    "id": aid,
                    "old_path": src["path"],
                    "new_path": updated["path"],
                    "new_project_id": updated["project_id"],
                    "mode": "move",
                })
        except Exception as e:
            logger.exception("bulk_move row failed")
            results.append({"id": aid, "error": str(e)})
    return {"results": results}
```

- [ ] **Step 4: Run tests**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_bulk_move.py -v
```

Expected: 4 tests PASS.

- [ ] **Step 5: Run all new backend tests together**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_artifact_rename_conflict.py tests/integration/test_memory_strip_artifact.py tests/integration/test_artifact_duplicate.py tests/integration/test_artifact_move_intra_project.py tests/integration/test_artifact_move_cross_project.py tests/integration/test_artifact_bulk_move.py -v
```

Expected: All tests PASS (24 total: 7 + 4 + 4 + 3 + 4 + 4 — adjust expectation if final counts differ).

- [ ] **Step 6: Commit**

```bash
cd /home/pan/pantheon
git add backend/api/artifacts.py backend/tests/integration/test_artifact_bulk_move.py
git commit -m "backend/api: POST /artifacts/bulk/move — batched move + duplicate"
```

---

## Task 9: API client additions (`frontend/src/api/client.js`)

**Files:**
- Modify: `frontend/src/api/client.js:191-230` (the `artifactsApi` block)

- [ ] **Step 1: Edit the API client**

In `frontend/src/api/client.js`, add the following to the `artifactsApi` object (after the existing `rename` line around line 213):

```javascript
  move: (id, dest_folder, { dest_project_id = null, mode = 'move' } = {}) =>
    api.post(`/api/artifacts/${id}/move`, { dest_folder, dest_project_id, mode }),
  moveBulk: (ids, dest_folder, { dest_project_id = null, mode = 'move' } = {}) =>
    api.post('/api/artifacts/bulk/move', { ids, dest_folder, dest_project_id, mode }),
```

- [ ] **Step 2: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/api/client.js
git commit -m "frontend/api: add move + moveBulk client methods"
```

---

## Task 10: `FolderTree` component

**Files:**
- Create: `frontend/src/components/FolderTree.jsx`

- [ ] **Step 1: Create the component**

Write `frontend/src/components/FolderTree.jsx`:

```jsx
import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, ChevronDown, Folder, FolderOpen, Box } from 'lucide-react'

/**
 * Project-aware folder tree.
 *
 * Props:
 *  - nodes: Array<{ project_id, project_name, folders: string[], artifact_count?: number }>
 *  - selected: { project_id?: string, folder?: string }
 *  - onSelect: ({ project_id, folder }) => void
 *  - onDrop?: ({ project_id, folder }, event) => void   // optional DnD target
 *  - collapsedKey: string                                // localStorage key
 *  - showProjects: 'always' | 'multi-only'              // hide project headers when only one
 */
export default function FolderTree({
  nodes,
  selected = {},
  onSelect,
  onDrop,
  collapsedKey,
  showProjects = 'multi-only',
}) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const raw = localStorage.getItem(collapsedKey)
      if (raw) return new Set(JSON.parse(raw))
    } catch {}
    // Default: everything collapsed.
    const init = new Set()
    for (const node of nodes) {
      init.add(`project:${node.project_id}`)
      for (const folder of node.folders) init.add(`folder:${node.project_id}:${folder}`)
    }
    return init
  })

  useEffect(() => {
    try { localStorage.setItem(collapsedKey, JSON.stringify(Array.from(collapsed))) } catch {}
  }, [collapsed, collapsedKey])

  const [dropHover, setDropHover] = useState(null)
  const hideProjectHeader = showProjects === 'multi-only' && nodes.length <= 1

  const toggle = (key) => {
    setCollapsed((s) => {
      const n = new Set(s)
      n.has(key) ? n.delete(key) : n.add(key)
      return n
    })
  }

  // Build per-project nested-tree structure for rendering.
  const trees = useMemo(() => nodes.map((node) => ({
    ...node,
    tree: buildTree(node.folders),
  })), [nodes])

  const handleDragOver = (target) => (e) => {
    if (!onDrop) return
    e.preventDefault()
    setDropHover(JSON.stringify(target))
  }
  const handleDragLeave = () => setDropHover(null)
  const handleDrop = (target) => (e) => {
    if (!onDrop) return
    e.preventDefault()
    setDropHover(null)
    onDrop(target, e)
  }

  const renderFolder = (project_id, folderPath, displayName, depth) => {
    const key = `folder:${project_id}:${folderPath}`
    const isSelected = selected.project_id === project_id && selected.folder === folderPath
    const hover = dropHover === JSON.stringify({ project_id, folder: folderPath })
    return (
      <button
        key={key}
        type="button"
        onClick={() => onSelect({ project_id, folder: folderPath })}
        onDragOver={handleDragOver({ project_id, folder: folderPath })}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop({ project_id, folder: folderPath })}
        className={`w-full text-left text-xs px-2 py-1 rounded flex items-center gap-1 ${
          isSelected ? 'bg-brand-600 text-white' : hover ? 'bg-brand-600/30 text-white' : 'hover:bg-gray-900 text-gray-400'
        }`}
        style={{ paddingLeft: 12 + depth * 12 }}
      >
        <Folder className="w-3 h-3" />
        <span>{displayName}</span>
      </button>
    )
  }

  return (
    <div className="space-y-0.5">
      {trees.map((node) => {
        const projKey = `project:${node.project_id}`
        const projCollapsed = collapsed.has(projKey)
        const projSelected = selected.project_id === node.project_id && !selected.folder
        const projHover = dropHover === JSON.stringify({ project_id: node.project_id, folder: '' })
        return (
          <div key={node.project_id}>
            {!hideProjectHeader && (
              <div className="flex items-center">
                <button
                  type="button"
                  onClick={() => toggle(projKey)}
                  className="p-0.5 text-gray-500 hover:text-gray-300"
                  aria-label={projCollapsed ? 'Expand project' : 'Collapse project'}
                >
                  {projCollapsed ? <ChevronRight className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                </button>
                <button
                  type="button"
                  onClick={() => onSelect({ project_id: node.project_id, folder: '' })}
                  onDragOver={handleDragOver({ project_id: node.project_id, folder: '' })}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop({ project_id: node.project_id, folder: '' })}
                  className={`flex-1 text-left text-xs px-1 py-1 rounded flex items-center gap-1 font-semibold ${
                    projSelected ? 'bg-brand-600 text-white' : projHover ? 'bg-brand-600/30 text-white' : 'hover:bg-gray-900 text-gray-200'
                  }`}
                >
                  <Box className="w-3 h-3" />
                  <span>{node.project_name}</span>
                  {typeof node.artifact_count === 'number' && (
                    <span className="ml-auto text-gray-500">{node.artifact_count}</span>
                  )}
                </button>
              </div>
            )}
            {!projCollapsed && (
              <div>
                {renderTreeRows(node.tree, node.project_id, collapsed, toggle, renderFolder, hideProjectHeader ? 0 : 1)}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// Turn a flat list of folder paths into a nested-row render order.
function buildTree(folders) {
  // folders are like ["p1", "p1/sub", "p1/sub/deep", "p1/other"]; produce
  // { name, path, depth, children: [...] } recursively.
  const root = { children: new Map(), path: '' }
  for (const path of folders) {
    const parts = path.split('/')
    let cursor = root
    let acc = ''
    for (const part of parts) {
      acc = acc ? `${acc}/${part}` : part
      if (!cursor.children.has(acc)) {
        cursor.children.set(acc, { name: part, path: acc, children: new Map() })
      }
      cursor = cursor.children.get(acc)
    }
  }
  return root
}

function renderTreeRows(treeNode, project_id, collapsed, toggle, renderFolder, baseDepth) {
  const rows = []
  const walk = (node, depth) => {
    for (const child of node.children.values()) {
      const key = `folder:${project_id}:${child.path}`
      const isCollapsed = collapsed.has(key)
      const hasChildren = child.children.size > 0
      rows.push(
        <div key={`row:${key}`} className="flex items-center">
          {hasChildren ? (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); toggle(key) }}
              className="p-0.5 text-gray-500 hover:text-gray-300"
              style={{ marginLeft: 4 + depth * 12 }}
              aria-label={isCollapsed ? 'Expand folder' : 'Collapse folder'}
            >
              {isCollapsed ? <ChevronRight className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            </button>
          ) : (
            <span style={{ marginLeft: 4 + depth * 12, width: 16 }} />
          )}
          <div className="flex-1 min-w-0">
            {renderFolder(project_id, child.path, child.name, 0)}
          </div>
        </div>
      )
      if (!isCollapsed) walk(child, depth + 1)
    }
  }
  walk(treeNode, baseDepth)
  return rows
}
```

> **Why both `tree` and `nodes`:** the `nodes` prop is the flat input the parent provides. `buildTree` reshapes it into the structure the renderer needs. Keeping both keeps the parent API simple (just pass folders as strings).

- [ ] **Step 2: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/components/FolderTree.jsx
git commit -m "frontend: add project-aware FolderTree with per-folder collapse + DnD"
```

---

## Task 11: `MoveModal` component

**Files:**
- Create: `frontend/src/components/MoveModal.jsx`

- [ ] **Step 1: Create the component**

Write `frontend/src/components/MoveModal.jsx`:

```jsx
import { useEffect, useMemo, useState } from 'react'
import { X, AlertTriangle } from 'lucide-react'
import FolderTree from './FolderTree'
import { artifactsApi } from '../api/client'

/**
 * Move / Duplicate modal.
 *
 * Props:
 *  - ids: string[]                              // 1 or many
 *  - mode: 'move' | 'duplicate'
 *  - projects: Array<{ id, name }>              // all projects in the system
 *  - foldersByProject: Record<string, string[]> // folder paths keyed by project_id
 *  - currentProjectId: string
 *  - onClose: () => void
 *  - onComplete: (response) => void
 */
export default function MoveModal({
  ids,
  mode,
  projects,
  foldersByProject,
  currentProjectId,
  onClose,
  onComplete,
}) {
  const [destProject, setDestProject] = useState(currentProjectId)
  const [destFolder, setDestFolder] = useState('')
  const [newFolder, setNewFolder] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const nodes = useMemo(() => projects.map((p) => ({
    project_id: p.id,
    project_name: p.name,
    folders: foldersByProject[p.id] || [],
  })), [projects, foldersByProject])

  const effectiveFolder = newFolder.trim() || destFolder
  const crossProject = destProject && destProject !== currentProjectId
  const canConfirm = ids.length > 0 && destProject && !busy

  const handleSelect = ({ project_id, folder }) => {
    setDestProject(project_id)
    setDestFolder(folder || '')
    setNewFolder('')
  }

  const confirm = async () => {
    if (!canConfirm) return
    setBusy(true)
    setError(null)
    try {
      let response
      if (ids.length === 1) {
        const res = await artifactsApi.move(ids[0], effectiveFolder, {
          dest_project_id: destProject,
          mode,
        })
        response = { results: [{ ...res.data }] }
      } else {
        const res = await artifactsApi.moveBulk(ids, effectiveFolder, {
          dest_project_id: destProject,
          mode,
        })
        response = res.data
      }
      onComplete(response)
      onClose()
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed')
      setBusy(false)
    }
  }

  const title = `${mode === 'move' ? 'Move' : 'Duplicate'} ${ids.length} artifact${ids.length === 1 ? '' : 's'}`

  return (
    <div className="fixed inset-0 z-40 bg-black/60 flex items-center justify-center" onClick={onClose}>
      <div
        className="bg-gray-950 border border-gray-800 rounded-lg w-[480px] max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-3 border-b border-gray-800">
          <div className="text-sm font-semibold text-gray-200">{title}</div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-auto p-3 space-y-3">
          <FolderTree
            nodes={nodes}
            selected={{ project_id: destProject, folder: destFolder }}
            onSelect={handleSelect}
            collapsedKey="pan_artifacts_move_modal_collapsed"
            showProjects="always"
          />
          <div>
            <label className="text-xs text-gray-500 mb-1 block">Or enter a new folder path (relative to project):</label>
            <input
              type="text"
              value={newFolder}
              onChange={(e) => setNewFolder(e.target.value)}
              placeholder="e.g. research/q2-2026"
              className="w-full bg-gray-900 border border-gray-800 text-gray-200 text-xs rounded px-2 py-1"
            />
          </div>
          {crossProject && (
            <div className="flex gap-2 text-xs bg-amber-900/30 border border-amber-700/50 rounded p-2 text-amber-200">
              <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <div>
                {mode === 'move'
                  ? 'This is a cross-project move. The artifact and its graph + semantic memory will be removed from the source project and re-extracted in the destination.'
                  : 'This is a cross-project duplicate. A new independent artifact will be created in the destination project with its own memory.'}
              </div>
            </div>
          )}
          {error && (
            <div className="text-xs bg-red-900/30 border border-red-700/50 rounded p-2 text-red-200">
              {error}
            </div>
          )}
        </div>
        <div className="p-3 border-t border-gray-800 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1 text-xs text-gray-400 hover:text-gray-200"
          >
            Cancel
          </button>
          <button
            onClick={confirm}
            disabled={!canConfirm}
            className={`px-3 py-1 text-xs rounded ${
              canConfirm
                ? (crossProject ? 'bg-amber-700 hover:bg-amber-600 text-white' : 'bg-brand-600 hover:bg-brand-500 text-white')
                : 'bg-gray-800 text-gray-600 cursor-not-allowed'
            }`}
          >
            {busy ? 'Working…' : (mode === 'move' ? 'Move' : 'Duplicate')}
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/components/MoveModal.jsx
git commit -m "frontend: add MoveModal with cross-project warning + new-folder input"
```

---

## Task 12: Wire `FolderTree` into `ArtifactsPage` rail (replace flat folder render)

**Files:**
- Modify: `frontend/src/pages/ArtifactsPage.jsx`

- [ ] **Step 1: Read the current rail render section**

Open `frontend/src/pages/ArtifactsPage.jsx` and find the block starting near line 180 — the "Folders" section that renders `folders.map((f) => ...)`. You'll replace lines 180-203 with `FolderTree` usage.

- [ ] **Step 2: Add the import**

Add to the imports at top of the file (around line 1-10, near other component imports):

```jsx
import FolderTree from '../components/FolderTree'
```

- [ ] **Step 3: Add a folders-per-project loader**

Find the existing `refresh` function (around line 77 of `ArtifactsPage.jsx`) that calls `artifactsApi.folders(projectId)`. Just below it, add state and an effect to populate folders for ALL projects so the FolderTree can render the multi-project case even when filtered to one project:

Locate the state declarations near the top of the component (around line 60). Add:

```jsx
const [allFoldersByProject, setAllFoldersByProject] = useState({})
const [allProjects, setAllProjects] = useState([])
```

Locate the `useEffect` block that fetches folders. Add a parallel effect that fetches all projects + their folders:

```jsx
useEffect(() => {
  let cancelled = false
  ;(async () => {
    try {
      const projRes = await projectsApi.list()
      // /api/projects returns a {id: {...}} dict; convert to array.
      const projects = Object.values(projRes.data || {})
      if (cancelled) return
      setAllProjects(projects)
      const folderEntries = await Promise.all(projects.map(async (p) => {
        const res = await artifactsApi.folders(p.id)
        return [p.id, res.data?.folders || []]
      }))
      if (cancelled) return
      setAllFoldersByProject(Object.fromEntries(folderEntries))
    } catch (e) {
      console.warn('failed to load projects/folders for tree', e)
    }
  })()
  return () => { cancelled = true }
}, [])
```

Also make sure `projectsApi` is imported alongside `artifactsApi` at the top of the file (existing imports likely include it; if not, add `import { artifactsApi, projectsApi } from '../api/client'`).

- [ ] **Step 4: Replace the flat folder render**

Find the block:

```jsx
{folders.map((f) => (
  <button ...>
    ...
  </button>
))}
```

Replace the whole `<div>` wrapping "Folders" + the buttons (around lines 180-203) with:

```jsx
<div>
  <div className="text-xs font-semibold text-gray-400 uppercase mb-2 flex items-center gap-1">
    <FolderOpen className="w-3 h-3" /> Folders
  </div>
  <button
    onClick={() => setFilterFolder('')}
    className={`w-full text-left text-xs px-2 py-1 rounded ${!filterFolder ? 'bg-brand-600 text-white' : 'hover:bg-gray-900 text-gray-400'}`}
  >
    All
  </button>
  <FolderTree
    nodes={
      projectId === 'all'
        ? allProjects.map((p) => ({
            project_id: p.id,
            project_name: p.name,
            folders: allFoldersByProject[p.id] || [],
          }))
        : [{
            project_id: projectId,
            project_name: (allProjects.find((p) => p.id === projectId) || {}).name || projectId,
            folders,
          }]
    }
    selected={{ project_id: projectId, folder: filterFolder }}
    onSelect={({ folder }) => setFilterFolder(folder || '')}
    onDrop={handleArtifactDrop /* added in next task */}
    collapsedKey="pan_artifacts_rail_collapsed"
    showProjects="multi-only"
  />
</div>
```

> Note: `handleArtifactDrop` doesn't exist yet — the next task adds it. The component still renders without a value (FolderTree only invokes it when a drop occurs). For now, you can omit the `onDrop` prop entirely and re-add it in the next task.

- [ ] **Step 5: Build & smoke**

Tell Brent:
> "Ready for a frontend rebuild and visual smoke. Please run:
> ```
> cd ~/pantheon/frontend && VITE_API_URL='' npm run build && cd ..
> ```
> Then hard-reload the Artifacts page. Expected: folders rail still works, but now with chevrons next to folders that have children, and folders default to collapsed."

- [ ] **Step 6: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/pages/ArtifactsPage.jsx
git commit -m "frontend/artifacts: replace flat folder rail with collapsible FolderTree"
```

---

## Task 13: Drag-and-drop wiring + intra-project move

**Files:**
- Modify: `frontend/src/pages/ArtifactsPage.jsx`

- [ ] **Step 1: Add drag attributes to the artifact list rows**

Find the artifact list rows in the middle pane (search for `selected.has(a.id)` near line 358). Add to each row's outer element (whichever `<button>` or `<div>` represents the row):

```jsx
draggable
onDragStart={(e) => {
  e.dataTransfer.setData('application/pantheon-artifact', JSON.stringify({
    id: a.id,
    project_id: a.project_id,
    path: a.path,
  }))
  e.dataTransfer.effectAllowed = 'copyMove'
}}
```

- [ ] **Step 2: Add the drop handler in `ArtifactsPage`**

Add inside the component, near the other handlers:

```jsx
const [crossProjectMove, setCrossProjectMove] = useState(null)
//   crossProjectMove shape: { id, source, dest: { project_id, folder }, mode: 'move'|'duplicate' }

const handleArtifactDrop = async ({ project_id, folder }, event) => {
  let payload
  try {
    payload = JSON.parse(event.dataTransfer.getData('application/pantheon-artifact'))
  } catch { return }
  const mode = event.altKey ? 'duplicate' : 'move'
  if (payload.project_id !== project_id) {
    // Cross-project — confirm first.
    setCrossProjectMove({
      id: payload.id,
      source: payload,
      dest: { project_id, folder: folder || '' },
      mode,
    })
    return
  }
  // Intra-project — execute directly.
  await performMove(payload.id, project_id, folder || '', mode, payload)
}

const performMove = async (id, dest_project_id, dest_folder, mode, payload) => {
  try {
    const res = await artifactsApi.move(id, dest_folder, { dest_project_id, mode })
    // Refresh list + folders.
    await refresh()
    const basename = (payload?.path || '').split('/').pop()
    const renamed = res.data.path && basename && !res.data.path.endsWith(basename)
    const verb = mode === 'duplicate' ? 'Duplicated' : 'Moved'
    setToast(`${verb} ${basename} → ${res.data.path}${renamed ? ' (renamed to avoid conflict)' : ''}`)
  } catch (e) {
    setToast(`${mode === 'duplicate' ? 'Duplicate' : 'Move'} failed: ${e?.response?.data?.detail || e.message}`)
  }
}
```

If a `setToast` helper does not exist in this file, add a minimal one near the state block:

```jsx
const [toast, setToast] = useState(null)
useEffect(() => {
  if (!toast) return
  const t = setTimeout(() => setToast(null), 3500)
  return () => clearTimeout(t)
}, [toast])
```

And render it once at the bottom of the page JSX:

```jsx
{toast && (
  <div className="fixed bottom-4 right-4 z-50 bg-gray-900 border border-gray-700 text-gray-200 text-xs rounded px-3 py-2 shadow-lg">
    {toast}
  </div>
)}
```

- [ ] **Step 3: Wire the handler into `FolderTree`**

In the `<FolderTree ...>` element added in the previous task, add the `onDrop={handleArtifactDrop}` prop (or restore it if you omitted it):

```jsx
<FolderTree
  ...
  onDrop={handleArtifactDrop}
  ...
/>
```

- [ ] **Step 4: Render cross-project confirmation inline**

Just before the closing `</div>` of the page root, add:

```jsx
{crossProjectMove && (
  <CrossProjectMoveConfirm
    info={crossProjectMove}
    projects={allProjects}
    onCancel={() => setCrossProjectMove(null)}
    onConfirm={async () => {
      await performMove(
        crossProjectMove.id,
        crossProjectMove.dest.project_id,
        crossProjectMove.dest.folder,
        crossProjectMove.mode,
        crossProjectMove.source
      )
      setCrossProjectMove(null)
    }}
  />
)}
```

- [ ] **Step 5: Add the inline `CrossProjectMoveConfirm` component**

At the bottom of `ArtifactsPage.jsx`, after the component's export, add:

```jsx
function CrossProjectMoveConfirm({ info, projects, onCancel, onConfirm }) {
  const sourceName = (projects.find((p) => p.id === info.source.project_id) || {}).name || info.source.project_id
  const destName = (projects.find((p) => p.id === info.dest.project_id) || {}).name || info.dest.project_id
  const basename = (info.source.path || '').split('/').pop()
  const verb = info.mode === 'duplicate' ? 'Duplicate' : 'Move'
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center" onClick={onCancel}>
      <div className="bg-gray-950 border border-gray-800 rounded-lg p-4 max-w-md" onClick={(e) => e.stopPropagation()}>
        <div className="text-sm font-semibold text-gray-100 mb-2">
          {verb} "{basename}" from {sourceName} to {destName}?
        </div>
        <div className="text-xs text-gray-400 mb-4">
          {info.mode === 'move'
            ? `This removes the artifact and its memory (graph + embeddings) from ${sourceName}. ${destName}'s recall will gain it.`
            : `This creates an independent copy in ${destName}. The original in ${sourceName} stays put.`}
        </div>
        <div className="flex justify-end gap-2">
          <button onClick={onCancel} className="px-3 py-1 text-xs text-gray-400 hover:text-gray-200">Cancel</button>
          <button onClick={onConfirm} className="px-3 py-1 text-xs rounded bg-amber-700 hover:bg-amber-600 text-white">{verb}</button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 6: Build & smoke**

Tell Brent:
> "Ready for another rebuild + smoke. After running the rebuild, drag an artifact from the list onto a folder in the rail. Expected:
> - Drop on a folder in the same project → moves directly with a toast confirming the new path.
> - Drop on a folder in a different project (only possible in 'All projects' view) → confirmation modal pops; clicking the destructive 'Move' button performs the move.
> - Hold Alt while dropping → 'Duplicate' action instead of 'Move'."

- [ ] **Step 7: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/pages/ArtifactsPage.jsx
git commit -m "frontend/artifacts: wire DnD move + Alt-duplicate + cross-project confirm"
```

---

## Task 14: Move… and Duplicate… buttons in detail + bulk toolbars

**Files:**
- Modify: `frontend/src/pages/ArtifactsPage.jsx`

- [ ] **Step 1: Add MoveModal state + import**

Add to imports at top of the file:

```jsx
import MoveModal from '../components/MoveModal'
import { Move, Copy } from 'lucide-react'
```

Add modal state near the other useState declarations:

```jsx
const [moveModal, setMoveModal] = useState(null)
// shape: { ids: string[], mode: 'move' | 'duplicate' }
```

- [ ] **Step 2: Add Move + Duplicate buttons to the bulk toolbar**

Find the existing bulk-select toolbar block (`{selected.size > 0 && (...)}` near line 302). Inside that block, alongside the existing Delete/Export buttons, add:

```jsx
<button
  onClick={() => setMoveModal({ ids: Array.from(selected), mode: 'move' })}
  className="text-xs px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-200 flex items-center gap-1"
>
  <Move className="w-3 h-3" /> Move
</button>
<button
  onClick={() => setMoveModal({ ids: Array.from(selected), mode: 'duplicate' })}
  className="text-xs px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-200 flex items-center gap-1"
>
  <Copy className="w-3 h-3" /> Duplicate
</button>
```

- [ ] **Step 3: Add Move + Duplicate buttons to the detail toolbar**

Find the detail-pane toolbar where `pin` / `delete` actions exist (search the file for `artifactsApi.pin` to find the relevant block). Add the same two buttons there but with the single id:

```jsx
<button
  onClick={() => setMoveModal({ ids: [activeId], mode: 'move' })}
  className="text-xs px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-200 flex items-center gap-1"
  title="Move to another folder / project"
>
  <Move className="w-3 h-3" /> Move…
</button>
<button
  onClick={() => setMoveModal({ ids: [activeId], mode: 'duplicate' })}
  className="text-xs px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-200 flex items-center gap-1"
  title="Make an independent copy in another folder / project"
>
  <Copy className="w-3 h-3" /> Duplicate…
</button>
```

- [ ] **Step 4: Render the MoveModal**

Just below the `CrossProjectMoveConfirm` render (added in the previous task), add:

```jsx
{moveModal && (
  <MoveModal
    ids={moveModal.ids}
    mode={moveModal.mode}
    projects={allProjects}
    foldersByProject={allFoldersByProject}
    currentProjectId={projectId === 'all' ? (allProjects[0]?.id || 'default') : projectId}
    onClose={() => setMoveModal(null)}
    onComplete={async (response) => {
      await refresh()
      const verb = moveModal.mode === 'duplicate' ? 'Duplicated' : 'Moved'
      const n = response.results?.length || 0
      setToast(`${verb} ${n} artifact${n === 1 ? '' : 's'}`)
      // Clear selection so the bulk toolbar deselects.
      setSelected(new Set())
    }}
  />
)}
```

- [ ] **Step 5: Build & smoke**

Tell Brent:
> "Ready for the final feature rebuild + smoke. Test plan:
> 1. Open an artifact → click Move… in the detail toolbar → pick a destination folder (try the New folder input) → confirm.
> 2. Open the bulk-select view (check 2-3 boxes) → click Duplicate → pick a cross-project destination → confirm the warning banner appears.
> 3. Toast appears with the action summary.
> 4. After action, the artifact list refreshes and selection clears."

- [ ] **Step 6: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/pages/ArtifactsPage.jsx
git commit -m "frontend/artifacts: add Move/Duplicate buttons to detail + bulk toolbars"
```

---

## Task 15: Version bump + final smoke

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Bump the version**

Open `frontend/package.json`. Find the `"version"` field and bump it per the project convention (`YYYY.MM.DD.HXX`). On 2026-05-17 with no prior shipped version today, use `2026.05.17.H1`. If others have shipped already today, use the next H suffix.

- [ ] **Step 2: Commit the bump**

```bash
cd /home/pan/pantheon
git add frontend/package.json
git commit -m "release: bump to 2026.05.17.H1 — artifacts folder tree + move"
```

- [ ] **Step 3: Run the full backend test suite once more**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ -v
```

Expected: all tests PASS, including pre-existing ones (the new code should not regress any). If anything fails, fix before declaring done.

- [ ] **Step 4: Hand the rebuild + smoke off to Brent**

Tell Brent:

> "Phase 1 is implementation-complete. Please rebuild + smoke:
>
> ```
> cd ~/pantheon && git pull
> cd frontend && VITE_API_URL='' npm run build && cd ..
> ./stop.sh && pkill -f 'uvicorn main:app' 2>/dev/null
> find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
> ./start.sh && sleep 3 && curl -s http://localhost:8000/api/health
> ```
>
> The health endpoint should now return version `2026.05.17.H1`.
>
> **Manual smoke checklist:**
> 1. Open the Artifacts page. Confirm the folder rail renders as a tree with chevrons, folders default-collapsed.
> 2. Collapse a folder; reload the page; confirm it stays collapsed.
> 3. Drag a single artifact onto a folder in the same project; confirm the move happens without a confirmation prompt and the toast names the new path.
> 4. Switch to 'All projects' view; drag an artifact onto a folder under a different project; confirm the cross-project modal appears; confirm the move; verify the artifact shows up in the new project.
> 5. Hold Alt and drag an artifact onto another folder; confirm a duplicate is created (the original stays put; the new one is in the destination).
> 6. Bulk-select 2 artifacts; click Move; choose a new destination via the `+ New folder…` input; confirm the bulk move.
> 7. Open an artifact; click Duplicate…; choose a cross-project destination; confirm the warning banner appears."

- [ ] **Step 5: (After Brent confirms) push the branch**

```bash
cd /home/pan/pantheon
git push -u origin feat/artifacts-folder-tree-and-move
```

Brent's branch workflow per memory: feature branch → push → fast-forward main → delete branch. Don't force-push, don't open a PR — that's Brent's call after confirming smoke.

---

## Done

All Phase 1 tasks complete. Phase 2 (publisher/subscriber model) waits on a dedicated brainstorm against `docs/superpowers/specs/2026-05-17-publisher-subscriber-phase2-notes.md`.
