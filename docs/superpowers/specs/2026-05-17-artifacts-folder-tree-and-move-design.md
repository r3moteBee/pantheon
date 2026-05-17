# Artifacts: collapsible folder tree + move-between-folders

**Date:** 2026-05-17
**Status:** approved

## Problem

The Artifacts page renders the folder list in the left rail as a flat, sorted array of folder paths with `paddingLeft` indentation, and offers no way to relocate an artifact between folders.

Two gaps in the UI:

1. **No per-folder collapse.** With ~200 artifacts spread across many nested folders, the rail becomes a long flat list. The existing `foldersCollapsed` toggle only hides the entire rail, not individual subtrees.
2. **No move operation.** There is no agent or UI affordance to relocate an artifact; the backend `rename` endpoint (`POST /api/artifacts/{id}/rename` taking `new_path`) exists but is unused.

## Goals

- Render the folder rail as a true tree with per-folder collapse, persisted across reloads.
- Let users move a single artifact via drag-and-drop onto a folder.
- Let users bulk-move selected artifacts via a "Move…" button that opens a folder picker.
- Allow creating new folders during a move (no separate "create folder" step needed).
- Auto-resolve filename conflicts at the destination (suffix `foo.md` → `foo-1.md`).

## Non-goals

- Moving artifacts between projects. Move is scoped to within a single project.
- Renaming folders (recursive folder rename across all contained artifacts). Out of scope for this change.
- A standalone "create folder" UI. Folders exist iff an artifact lives under that path.
- Backend changes to the `folder_tree` endpoint shape — the current flat sorted-path response is fine; the tree is constructed on the frontend.

## Backend design

### Store helper: `_unique_path`

Add to `backend/artifacts/store.py`:

```python
def _unique_path(self, project_id: str, desired_path: str) -> str:
    """If desired_path exists for project, suffix basename: foo.md → foo-1.md → foo-2.md."""
```

Implementation: split off extension; query `SELECT 1 FROM artifacts WHERE project_id=? AND path=?` in a loop incrementing the suffix; return the first free path. Caps at 1000 iterations to avoid infinite loops on pathological inputs.

### Modify `rename`

`store.rename(artifact_id, new_path)` calls `_unique_path` before the UPDATE so DnD/single-move auto-resolves conflicts. Returns the actual final path (which the API hands back to the caller so the UI can show the conflict-renamed name in its toast).

### New endpoint: bulk move

`POST /api/artifacts/bulk/move` (matches existing convention: `/bulk/tags`, `/bulk/delete`, `/bulk/export`)

Request:
```json
{ "ids": ["...", "..."], "dest_folder": "default-project/research" }
```

Response:
```json
{
  "moved": [{"id": "...", "old_path": "...", "new_path": "..."}],
  "failed": [{"id": "...", "error": "..."}]
}
```

Server logic: for each id, fetch current path → take basename → join with `dest_folder` → `_unique_path` → UPDATE. Single transaction; per-row try/except so one bad row (e.g. id from a different project) doesn't blow up the batch.

`dest_folder` is treated as relative to the project; the project slug prefix is preserved automatically because the existing rows already include it and we only swap the directory portion. If `dest_folder` is empty string, move to the project root (i.e. just `<project_slug>/<basename>`).

## Frontend design

### `FolderTree` component (new file: `frontend/src/components/FolderTree.jsx`)

Reusable component used in both the rail and the Move modal.

**Props:**
- `folders: string[]` — flat list of folder paths from `/api/artifacts/folders`.
- `selected: string` — currently filtered folder (rail) or selected destination (modal).
- `onSelect: (path: string) => void` — click handler on folder name.
- `onDrop?: (path: string, event: DragEvent) => void` — optional, enables drop targets (rail only).
- `collapsedKey: string` — localStorage key for persisted collapsed set. Different keys for rail vs modal so collapse state doesn't leak between contexts.

**Behavior:**
- Internally builds a nested tree from the flat path list.
- Each folder row: chevron (▶ when collapsed, ▼ when expanded) if it has children, folder icon, name (last segment only).
- Click chevron → toggle that path in the collapsed set (saved to localStorage as JSON array).
- Click name → `onSelect(fullPath)`.
- **Default collapsed:** on first paint, if there is no localStorage entry for `collapsedKey`, initialize the collapsed set to every non-leaf path. After that the user's manual choices are honored.
- When `onDrop` is provided: each row gets `onDragOver={e => { e.preventDefault(); setHover(path) }}`, `onDragLeave`, `onDrop={e => { onDrop(path, e); setHover(null) }}`. Hovered row shows `bg-brand-600/20`.

### Drag-and-drop wiring in `ArtifactsPage.jsx`

- Each artifact row in the middle list gets `draggable={true}` and:
  ```js
  onDragStart={(e) => {
    e.dataTransfer.setData('application/pantheon-artifact', JSON.stringify({ id: a.id, path: a.path }))
    e.dataTransfer.effectAllowed = 'move'
  }}
  ```
- Rail's `FolderTree` gets `onDrop={(dest, e) => { const { id, path } = JSON.parse(e.dataTransfer.getData('application/pantheon-artifact')); handleMove(id, dest, path) }}`.
- `handleMove(id, destFolder, oldPath)` calls `artifactsApi.rename(id, destFolder + '/' + basename(oldPath))`, refreshes list/folders, shows toast `"Moved foo.md → research/"`. If the response path's basename differs from the requested basename, toast reads `"Moved foo.md → research/foo-1.md (renamed to avoid conflict)"`.

### "Move…" button + `MoveModal` (new file: `frontend/src/components/MoveModal.jsx`)

**Two surfaces show the button:**
1. Detail toolbar (when an artifact is open): button next to existing pin/delete.
2. Bulk-select toolbar (visible when `selected.size > 0`): alongside existing Delete/Export.

**MoveModal props:**
- `ids: string[]` — one or many.
- `folders: string[]` — current folder list.
- `projectSlug: string` — used as prefix when user types a new folder.
- `onClose: () => void`
- `onMoved: (result) => void`

**Modal layout:**
- Title: `"Move 1 artifact"` or `"Move N artifacts"`.
- `FolderTree` (selectable mode, no DnD), full height.
- Below tree: text input `+ New folder…` that lets the user type a path. While they're typing, the typed path is the selected destination (overrides tree click).
- Cancel / Move buttons. Move is disabled until a destination is chosen.
- On confirm:
  - `ids.length === 1` → `artifactsApi.rename(ids[0], dest + '/' + basename(currentPath))`
  - else → `artifactsApi.moveBulk(ids, dest)`
- On success: call `onMoved` with the response, parent refreshes list + closes modal + shows toast.

### `artifactsApi` additions (`frontend/src/api/client.js`)

- `rename(id, new_path)` — already exists if there's a callsite; if not, add it.
- `moveBulk(ids, dest_folder)` — new, posts to `/api/artifacts/bulk/move`.

## Conflict and edge-case behavior

| Case | Behavior |
| --- | --- |
| Destination filename exists | Server auto-suffixes (`foo.md` → `foo-1.md`). Toast notes the rename. |
| Move to same folder | No-op server-side; toast suppressed (UI checks `old_path === new_path`). |
| Empty `ids` array on bulk-move | Server returns `{moved: [], failed: []}`. |
| Id belongs to a different project | Per-row failure with error `"artifact not in current project"`. Other rows still move. |
| Drag artifact onto its own current folder | UI prevents drop (rail row compares `dest === current_folder` in `onDragOver` and refuses the drop visually). |
| Drag onto "All" pseudo-folder | Treated as the project root: `dest = projectSlug`. |
| Active project filter is "All projects" (`project_id=all`) | Move is **disabled** in this view (DnD inert, Move… button hidden). Moving across projects is a non-goal; cross-project DnD would silently violate it. |

## Testing

Add to `backend/tests/integration/`:

- `test_artifact_rename_conflict_suffix.py` — create `foo.md`, rename a second artifact to the same path, assert it becomes `foo-1.md`; rename a third, assert `foo-2.md`.
- `test_artifact_move_bulk.py` — create 5 artifacts across two folders, bulk-move 3 into a new folder, assert all three landed there with original basenames preserved; assert response includes `old_path`/`new_path` for each.
- `test_artifact_move_bulk_partial_failure.py` — include one id from a different project in the request, assert that row appears in `failed` and the rest succeed.

Frontend: no automated tests (project convention — there's no frontend test harness yet). Manual smoke after build:
1. Collapse a folder; reload; confirm it stays collapsed.
2. Drag an artifact onto a folder; confirm it moves.
3. Bulk-select 2 artifacts; click Move; pick a new folder via the `+ New folder…` input; confirm both move.
4. Move two artifacts with the same basename into the same folder; confirm the second one gets `-1` suffix and the toast says so.

## Files touched

- `backend/artifacts/store.py` — add `_unique_path`, modify `rename`.
- `backend/api/artifacts.py` — add `move-bulk` endpoint.
- `backend/tests/integration/test_artifact_rename_conflict_suffix.py` — new.
- `backend/tests/integration/test_artifact_move_bulk.py` — new.
- `backend/tests/integration/test_artifact_move_bulk_partial_failure.py` — new.
- `frontend/src/components/FolderTree.jsx` — new.
- `frontend/src/components/MoveModal.jsx` — new.
- `frontend/src/pages/ArtifactsPage.jsx` — replace flat folder render with `FolderTree`, wire DnD, add Move buttons + modal.
- `frontend/src/api/client.js` — add `moveBulk`; confirm `rename` exists.
- `frontend/package.json` — bump version per project convention.
