# Phase 1 — Artifacts: project-aware folder tree, move, and duplicate-copy

**Date:** 2026-05-17
**Status:** approved
**Phase:** 1 of 2 (Phase 2: publisher/subscriber model — see `2026-05-17-publisher-subscriber-phase2-notes.md`)

## Problem

The Artifacts page renders the folder list in the left rail as a flat, sorted array of folder paths with `paddingLeft` indentation, and offers no way to relocate or duplicate an artifact between folders or projects.

Three gaps in the UI:

1. **No per-folder collapse.** With ~200 artifacts spread across many nested folders, the rail becomes a long flat list. The existing `foldersCollapsed` toggle only hides the entire rail, not individual subtrees.
2. **No move operation.** There is no UI affordance to relocate an artifact between folders. The backend `rename` endpoint exists but is unused.
3. **No project boundary in the UI.** Projects are memory boundaries (graph nodes, embeddings, episodic refs are project-scoped), but the folder rail treats project slugs as just another path segment. In the "All projects" view there is no visual signal that crossing a project boundary is a different operation than navigating folders.

## Goals

- Render the folder rail as a true tree with per-folder collapse, persisted across reloads.
- Make project boundaries visually and taxonomically distinct from folder boundaries.
- Let users move a single artifact via drag-and-drop onto a folder (within the same project).
- Let users move artifacts across project boundaries via an explicit, confirmed operation that relocates the artifact's memory along with it.
- Let users duplicate an artifact (intra- or cross-project) — independent copy that can diverge.
- Let users bulk-move or bulk-duplicate selected artifacts.
- Allow creating new folders during a move (no separate "create folder" step needed).
- Auto-resolve filename conflicts at the destination (suffix `foo.md` → `foo-1.md`).

## Non-goals

- **Publisher/subscriber sharing** (one canonical artifact visible read-only in multiple projects). Deferred to Phase 2 — see the linked notes doc for the full design space + open decisions.
- Renaming folders (recursive rename across all contained artifacts). Out of scope.
- A standalone "create folder" UI. Folders exist iff an artifact lives under that path.
- Backend changes to the `folder_tree` endpoint shape — the current flat sorted-path response is fine; the tree is constructed on the frontend.
- Moving / duplicating workspace files (non-artifact scratch on disk).

## Conceptual taxonomy

Two distinct boundary types in the UI, treated differently:

- **Project boundary.** Top-level container. Memory-scoped. Crossing it is an explicit, confirmed operation. Visually distinct (project icon + project color + project name as a header row in the tree).
- **Folder boundary.** A path component within a project. Crossing it is cheap (DnD, no confirmation). Visually plain (folder icon, nested under its project).

Three artifact operations, all project-aware:

| Operation | Semantics |
| --- | --- |
| **Rename** (intra-project, intra-folder) | Change the basename only. Path-only update. |
| **Move** | Relocate the artifact + its project-scoped memory to a different folder and/or project. Source project loses the artifact and its memory; destination project gains them. Cross-project moves require confirmation. |
| **Duplicate** | Create an independent copy of the artifact in the same or different project. New id, new artifact row, new blob row (content copied). Memory is **re-extracted** in the destination project. The two artifacts diverge from creation. |

## Backend design

### Store helper: `_unique_path`

Add to `backend/artifacts/store.py`:

```python
def _unique_path(self, project_id: str, desired_path: str) -> str:
    """If desired_path exists for project, suffix basename: foo.md → foo-1.md → foo-2.md."""
```

Implementation: split off extension; query `SELECT 1 FROM artifacts WHERE project_id=? AND path=?` in a loop incrementing the suffix; return the first free path. Caps at 1000 iterations.

### Modify `rename`

`store.rename(artifact_id, new_path)` calls `_unique_path` before the UPDATE so DnD/single-move auto-resolves conflicts. Returns the final path. Intra-project only — `rename` does NOT change `project_id`.

### Store method: `move`

```python
def move(self, artifact_id: str, dest_project_id: str, dest_folder: str) -> dict[str, Any]:
    """
    Move artifact to dest_project + dest_folder. Preserves basename.
    Reassigns project-scoped memory rows so the artifact's memory follows it.
    Returns the updated artifact dict including final path.
    """
```

Steps:

1. Fetch artifact; compute `new_path = dest_folder + '/' + basename(current.path)`.
2. `_unique_path(dest_project_id, new_path)`.
3. If `dest_project_id == source.project_id` (intra-project move): single `UPDATE artifacts SET path=?, updated_at=? WHERE id=?`. **No memory change needed** — graph + semantic rows reference the artifact by `id`, not by path, and the project_id is unchanged.
4. If `dest_project_id != source.project_id` (cross-project move): conceptually this is "duplicate into dest + strip from source." Concrete steps in a single transaction (artifact rows) plus async cleanup:
   - `UPDATE artifacts SET project_id=?, path=?, updated_at=? WHERE id=?`.
   - **Strip source-side memory:**
     - Graph: delete nodes where `metadata.artifact_id == :id` (these are the source/content concept nodes that are 1:1 with the artifact). Shared topic/concept nodes that are also referenced by other artifacts in the source project stay put. Delete edges incident to the deleted nodes.
     - Semantic: delete chroma chunks where `metadata.artifact_id == :id` (regardless of project — these always belong to this artifact).
   - **Enqueue re-extraction in dest:** add a `file_indexing` job for the new artifact id in `dest_project_id`. The existing job worker handles it asynchronously.
   - Episodic: **not moved**. Episodic logs are session-scoped, not artifact-scoped — they record "in session X, this artifact was referenced." That history stays with the originating session.

Cross-project move briefly leaves the artifact with no memory in either project (a few seconds, until re-extraction completes). That's acceptable; recall just won't surface this artifact during that window.

Memory cleanup is best-effort: if graph/semantic deletion fails, log a warning but don't abort. Orphaned memory rows from this artifact's old project_id are a recoverable degradation (re-index will fix); a half-moved artifact is not.

### Store method: `duplicate`

```python
def duplicate(self, artifact_id: str, dest_project_id: str, dest_folder: str) -> dict[str, Any]:
    """Create an independent copy in dest_project / dest_folder. Returns new artifact row."""
```

Steps:

1. Fetch source artifact + content blob.
2. Compute `new_path = dest_folder + '/' + basename(source.path)`; `_unique_path(dest_project_id, new_path)`.
3. Insert new artifact row with fresh `id`, `project_id=dest_project_id`, `path=new_path`, `content` or `blob_path` copied.
4. **Memory is not copied.** Schedule re-extraction in the destination project by enqueuing a `file_indexing` job for the new artifact (existing job type, no new infra).

### Endpoint: `POST /api/artifacts/{id}/move`

Body:
```json
{
  "dest_project_id": "ae991c51",
  "dest_folder": "research/q2-2026",
  "mode": "move"
}
```

- `dest_project_id` optional; defaults to current. Omitting it = intra-project move.
- `mode` optional; defaults to `"move"`. Set to `"duplicate"` to invoke the duplicate path.
- `dest_folder` required; project slug is prepended server-side from `dest_project_id` (the client never passes the slug — it passes the in-project relative folder, and the server stitches them).

Response: the updated/new artifact dict including `old_path`, `new_path`, `new_project_id`, `mode`.

### Endpoint: `POST /api/artifacts/bulk/move`

Body:
```json
{
  "ids": ["...", "..."],
  "dest_project_id": "ae991c51",
  "dest_folder": "research/q2-2026",
  "mode": "move"
}
```

Server logic: per-row in a single transaction; per-row try/except. Response:

```json
{
  "results": [
    {"id": "...", "mode": "move", "old_path": "...", "new_path": "...", "new_project_id": "..."},
    {"id": "...", "error": "artifact not found"}
  ]
}
```

### Keep `rename` for backwards compat

The existing `POST /api/artifacts/{id}/rename` endpoint stays. It now uses `_unique_path` for conflict resolution. Same scope: change path within the same project only. The new `move` endpoint is the superset.

## Frontend design

### `FolderTree` component (new file: `frontend/src/components/FolderTree.jsx`)

Reusable component used in both the rail and the Move modal. Knows about project boundaries.

**Props:**
- `nodes: Array<ProjectNode>` — pre-grouped tree: each node is `{project_id, project_name, folders: string[]}`. The component handles project-as-root-row + nested folders internally.
- `selected: {project_id?: string, folder?: string}` — current selection.
- `onSelect: ({project_id, folder}) => void`
- `onDrop?: ({project_id, folder}, event) => void` — optional. Component decides whether the drop crosses a project boundary and lets the parent handle confirmation.
- `collapsedKey: string` — localStorage key for persisted collapsed set. Persists both project-collapsed and folder-collapsed states (distinct path keys).
- `showProjects: 'always' | 'multi-only'` — when `'multi-only'` and only one project in the tree, hide the project header to reduce noise.

**Visual treatment:**
- Project rows: project icon + project name + small badge with artifact count. Distinct color per project (derive from a hash of project_id → a small palette).
- Folder rows: folder icon + name, nested under the project row, indented per depth.
- Chevrons on rows that have children (both project rows and folder rows can be collapsed).

**Defaults:**
- All projects collapsed on first paint when `nodes.length > 1`.
- Within a project: all folders collapsed.
- Subsequent loads honor persisted state.

### Rail behavior (single-project view)

When `projectId !== 'all'`:
- `FolderTree` receives one project node with `showProjects='multi-only'` → project header hidden, folders render as today (just with collapse).
- DnD targets are the folder rows. No cross-project moves possible from here.

### Rail behavior (all-projects view)

When `projectId === 'all'`:
- `FolderTree` receives one node per project. Project headers always shown.
- DnD targets include both project rows (drop = "move to project root") and folder rows.
- Dropping onto a different project's row/folder triggers the **cross-project confirmation modal** before calling the API.

### Cross-project confirmation modal

When a move (DnD or button) would change `project_id`, show:

> **Move "foo.md" from Default Project to Real Estate?**
>
> This will remove the artifact and its memory (graph + embeddings) from Default Project. Real Estate's recall will gain this artifact.
>
> [Cancel] [Move]

Same modal pops for bulk cross-project moves with summary count: "Move 5 artifacts from Default Project to Real Estate?".

Modal is dismissable; cancel is the default action (Escape key + outside click). The "Move" button is destructive-styled (red border or amber).

### Drag-and-drop wiring in `ArtifactsPage.jsx`

- Each artifact row gets `draggable={true}` with dataTransfer payload `{id, project_id, path}`.
- Rail's `FolderTree` receives `onDrop` that:
  1. Parses the payload.
  2. If `dest.project_id === payload.project_id` → call `artifactsApi.move(id, undefined, dest.folder)` directly (no confirmation).
  3. Else → open cross-project confirmation modal; on confirm, call `artifactsApi.move(id, dest.project_id, dest.folder, 'move')`.
- Hold **Alt** during drop to invoke `duplicate` instead of `move`. Subtle affordance — toast confirms `"Duplicated foo.md → research/"` to make the choice visible.

### Bulk toolbar + "Move…" / "Duplicate…" buttons

When `selected.size > 0`:
- Existing toolbar gains two buttons next to Delete/Export: **Move…** and **Duplicate…**.
- Both open `MoveModal` with the action pre-set.

### `MoveModal` (new file: `frontend/src/components/MoveModal.jsx`)

**Props:**
- `ids: string[]` — one or many.
- `mode: 'move' | 'duplicate'` — drives wording + API call.
- `projectsAndFolders: ProjectNode[]` — full tree for picker.
- `currentProjectId: string` — for confirmation logic.
- `onClose: () => void`
- `onComplete: (results) => void`

**Layout:**
- Title: `"Move 1 artifact"` / `"Move 3 artifacts"` / `"Duplicate 1 artifact"` etc.
- `FolderTree` (selectable, no DnD) showing all projects + folders, always with project headers (`showProjects='always'`).
- Below tree: text input `+ New folder…` — typed path becomes the destination, scoped to the currently-selected project.
- If selected destination crosses project boundary: inline warning banner above the action button explaining the memory implication.
- Cancel / Confirm buttons. Confirm is disabled until a project + folder destination is chosen.
- On confirm:
  - 1 id → `artifactsApi.move(id, dest_project, dest_folder, mode)`
  - many → `artifactsApi.moveBulk(ids, dest_project, dest_folder, mode)`
- Result toast summarizes per-row results, with explicit notes for auto-suffix renames.

### Detail toolbar

When an artifact is open in the detail pane: add **Move…** and **Duplicate…** buttons to the existing pin/delete toolbar. Both open `MoveModal` with `ids=[currentId]` and `mode` set.

### `artifactsApi` additions (`frontend/src/api/client.js`)

- `move(id, dest_project_id, dest_folder, mode='move')` — posts to `/api/artifacts/{id}/move`.
- `moveBulk(ids, dest_project_id, dest_folder, mode='move')` — posts to `/api/artifacts/bulk/move`.
- `rename(id, new_path)` — confirm/add if missing.

## Conflict and edge-case behavior

| Case | Behavior |
| --- | --- |
| Destination filename exists | Server auto-suffixes (`foo.md` → `foo-1.md`). Toast notes the rename. |
| Move/duplicate to same project + folder | Move is no-op; duplicate produces `foo-1.md` (or next free suffix). |
| Empty `ids` array on bulk-move | Server returns `{results: []}`. |
| Id not found | Per-row failure with error `"artifact not found"`. Other rows still process. |
| Drag onto own current folder | UI prevents drop (compares dest vs source). |
| Drag onto a project row | Treated as that project's root: `dest_folder = ''` (in-project relative), server stitches to `<project_slug>`. |
| Cross-project move + memory reassignment fails | Artifact move commits; warning logged; UI toast: `"Moved, but memory reassignment had errors — re-index recommended"`. |
| Duplicate with very large blob | Server copies the blob row inline (same transaction). Acceptable — typical artifact blobs are <1 MB. |
| Duplicate triggers re-extraction | A `file_indexing` job is enqueued for the new artifact id in the destination project. Existing job-system semantics apply (visible in jobs list). |

## Testing

Add to `backend/tests/integration/`:

- `test_artifact_rename_conflict_suffix.py` — `foo.md` rename collisions become `foo-1.md`, `foo-2.md`.
- `test_artifact_move_intra_project.py` — single move within project; verify path + folder list updated.
- `test_artifact_move_cross_project.py` — cross-project move; verify `project_id` updated on artifact, graph nodes, and chroma chunks; verify source project no longer surfaces the artifact in `list_artifacts`.
- `test_artifact_duplicate.py` — duplicate intra and cross-project; verify new id, copied content, fresh memory state (re-extraction job enqueued).
- `test_artifact_bulk_move_partial_failure.py` — mix of valid + invalid ids; assert per-row results.

Frontend: no automated tests (project convention). Manual smoke after build:

1. Collapse a project; reload; confirm it stays collapsed.
2. Drag an artifact onto a folder in the same project; confirm immediate move (no confirmation).
3. In "All projects" view, drag an artifact onto a different project's folder; confirm the cross-project modal pops, and the move completes after confirming.
4. Alt+drag an artifact onto another folder; confirm a duplicate is created instead of moved.
5. Bulk-select 3 artifacts across two source folders; click Move…; select a destination in a different project; confirm + verify all three landed and source project no longer lists them.
6. Bulk-select 2 artifacts with the same basename; Duplicate… to the same folder; confirm one gets `-1` suffix.

## Files touched

**Backend:**
- `backend/artifacts/store.py` — add `_unique_path`, `move`, `duplicate`; modify `rename`.
- `backend/api/artifacts.py` — add `move` and `bulk/move` endpoints; update `rename` to return new path.
- `backend/memory/graph.py` — add `strip_artifact(artifact_id)` helper (deletes 1:1 source/content nodes + incident edges, leaves shared topic nodes intact).
- `backend/memory/semantic.py` — add `strip_artifact(artifact_id)` (chroma chunk delete by metadata).
- `backend/tests/integration/` — five new test files (listed above).

**Frontend:**
- `frontend/src/components/FolderTree.jsx` — new.
- `frontend/src/components/MoveModal.jsx` — new.
- `frontend/src/components/CrossProjectMoveConfirm.jsx` — new (small).
- `frontend/src/pages/ArtifactsPage.jsx` — replace flat folder render with `FolderTree`; wire DnD + Alt-modifier; add Move/Duplicate buttons in detail and bulk toolbars; integrate confirmation modal.
- `frontend/src/api/client.js` — add `move`, `moveBulk`; confirm `rename`.
- `frontend/package.json` — bump version per project convention.
