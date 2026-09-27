# Agent-friendly artifact list API — design

**Date:** 2026-05-17
**Status:** Approved (verbal, in conversation)
**Author:** Brent (with Claude)

## Purpose

Give other agents — peer Pantheon instances, MCP clients, internal jobs — a stable, cheap way to enumerate or change-detect artifacts in a project. The existing `/api/artifacts` endpoint is UI-shaped (pinned-first, recency-DESC, no cursor, silently capped at 200) and is not safe for either bulk walks or change-detection polling.

This spec defines a dedicated `/api/artifacts/feed` endpoint optimized equally for:

- **Change-detection polling** — agents poll periodically asking "what changed since I last checked?" Steady state is mostly empty responses.
- **Bulk enumeration** — agents (or new RAG pipelines) walk the whole project once to ingest everything.

Both modes share the same primitives (cursor + filters), so the marginal cost of supporting both vs one is small.

## Non-goals

- Replacing `/api/artifacts` (UI keeps using it; its semantics are unchanged).
- Push delivery (SSE, WebSocket). Pull-based polling is what consumers want today.
- Per-agent API keys, scoped auth, or rate limiting (uses existing bearer-token middleware).
- MCP tool wrapping. Once the feed is live and stable, deciding whether to expose it as an MCP tool is a separate conversation.
- `total_count` / exact progress estimates. Skipping the extra `COUNT(*)` keeps polls cheap; agents that need progress UI can track it locally.
- The `artifact_tags` join table. Only revisit if tag-filter perf actually hurts at 5K+ artifacts/project.

## Architecture

Single new FastAPI route under `backend/api/artifacts.py`:

```
GET /api/artifacts/feed
```

Backed by a new method on `ArtifactStore`:

```python
def feed(
    self,
    *,
    project_id: str | None,        # None / "all" / ""  → cross-project
    updated_since: str | None,
    after_id: str | None,
    include_deleted: bool = False,
    tag: str | None = None,
    content_type: str | None = None,
    path_prefix: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    ...
```

The feed method always sorts `updated_at ASC, id ASC`. The cursor predicate depends on which params are set:

| `updated_since` | `after_id` | Added WHERE clause |
|---|---|---|
| None | None | (none — full forward walk from beginning) |
| set | None | `updated_at > :updated_since` |
| set | set | `(updated_at > :updated_since OR (updated_at = :updated_since AND id > :after_id))` |
| None | set | **`400`** — `after_id` without `updated_since` is meaningless (no anchor) |

The set+set case is the standard tiebreaker form: same `updated_at` rows are ordered by `id`, and the cursor advances within that group without dupes or gaps. Concurrent writes during a walk do not cause missed rows — new items always appear at the tail of the forward walk.

Rows are hydrated via the existing `_hydrate_artifact` (JSON-decodes `tags` and `source`, casts `pinned` to bool), then `content` and `blob_path` are stripped before serialization. `fields` projection runs last.

The new index shipped in `2026.05.17.H5` (`idx_artifacts_project_updated ON artifacts(project_id, updated_at DESC) WHERE deleted_at IS NULL`) covers the range scan. `EXPLAIN QUERY PLAN` confirms direct index search (no temp B-tree sort) for the per-project case.

For `project_id` in `(None, "all", "")`, the same predicate runs without a `project_id` filter — table scan over the `updated_at` axis. No new index needed; the partial-index covers single-project queries and the cross-project walk is rare enough that a partial scan is acceptable.

## Query parameters

| Param | Type | Default | Purpose |
|---|---|---|---|
| `project_id` | string | required (or `"all"` / `""` for cross-project) | scope |
| `updated_since` | ISO-8601 | none | exclusive lower bound on `updated_at`. Also serves as the cursor on subsequent pages. |
| `after_id` | UUID | none | tiebreak when paginating within the same `updated_at`. |
| `limit` | int | 500 | page size; `1 <= limit <= 5000`, enforced via FastAPI `Query(ge=1, le=5000)` — out-of-range yields `422`. |
| `include_deleted` | bool | `false` | surface soft-deleted rows as tombstones. |
| `tag` | string | none | filter to artifacts with this tag. |
| `content_type` | string | none | filter by mime. |
| `path_prefix` | string | none | filter by path (prefix match). |
| `fields` | csv of column names | full row minus `content` + `blob_path` | column projection. |

### Cursor semantics

Agents page by storing the last row's `(updated_at, id)` and passing them as `updated_since` + `after_id` on the next call. The same params work for first-time change-detection polling — the cursor IS the polling timestamp. No mode flag, no separate cursor encoding. This is the "transparent cursor" pattern decided in brainstorming.

First call (full walk from beginning):

```
GET /api/artifacts/feed?project_id=default
```

First call (change-detection — only items newer than last poll):

```
GET /api/artifacts/feed?project_id=default&updated_since=2026-05-17T20:00:00.000Z
```

Subsequent pages:

```
GET /api/artifacts/feed?project_id=default
    &updated_since=2026-05-17T20:14:32.123Z
    &after_id=abc-def-...
```

### `include_deleted` semantics

When `false` (default), the SQL clause is `AND deleted_at IS NULL`. When `true`, the clause is dropped and tombstones appear in the result with `deleted_at` populated. The store already nulls `content` / `blob_path` on soft delete, so tombstones are cheap to ship.

### `fields` projection

Comma-separated list of column names. Unknown fields → `400`. Always-present fields (forced into the response regardless of `fields`): `id`, `updated_at`. This guarantees agents can always advance the cursor even if they only requested `id` + `sha256`.

Steady-state change-detection poll for an agent that only tracks file identity:

```
GET /api/artifacts/feed?project_id=X
    &updated_since=<last>
    &fields=id,sha256,deleted_at
→ {"artifacts": [], "next_cursor": null, "has_more": false, "count": 0}
```

One indexed range scan over zero rows, zero JSON parse work.

## Response envelope

```json
{
  "artifacts": [
    {
      "id": "abc-def-...",
      "project_id": "default",
      "path": "youtube-transcripts/.../transcript.md",
      "title": "...",
      "content_type": "text/markdown",
      "size_bytes": 12345,
      "sha256": "...",
      "tags": ["..."],
      "source": {...},
      "pinned": false,
      "current_version_id": "...",
      "created_at": "2026-05-10T...",
      "updated_at": "2026-05-17T20:14:32.123Z",
      "deleted_at": null
    }
  ],
  "next_cursor": {
    "updated_since": "2026-05-17T20:14:32.123Z",
    "after_id": "abc-def-..."
  },
  "has_more": true,
  "count": 1
}
```

- `next_cursor` is `null` when the page returned fewer rows than `limit` (i.e. there is no next page).
- `has_more` is the convenience boolean for loop control. Equivalent to `next_cursor is not None`.
- `count` is the row count of THIS page, not a total.
- `content` and `blob_path` are always stripped from responses (same as `/api/artifacts` after `2026.05.17.H4`).

No `total_count`. Agents that need progress UI can compute it locally as they walk. If a future use case genuinely needs server-side total, add `?include_count=true` so the cheap polling path stays cheap.

## Auth

Same bearer-token middleware as every other API route. No per-agent keys in v1.

## Errors

Standard FastAPI `HTTPException`:

- `400` — invalid ISO-8601 in `updated_since`; malformed `fields` (unknown column); `after_id` without `updated_since`.
- `404` — unknown `project_id` (when not `"all"`/`""`).
- `422` — FastAPI validation: `limit` out of range, malformed bool in `include_deleted`, etc.
- `429` — reserved for future rate-limit; not implemented in v1.

No new error envelope.

## Testing

### Unit (store layer)

- `feed_paginates_with_cursor_tiebreak` — insert N rows with deliberately-colliding `updated_at` values, walk in pages of 2, assert every row appears exactly once.
- `feed_excludes_tombstones_by_default` — soft-delete a row, assert it is not in the default response.
- `feed_include_deleted_surfaces_tombstones` — same setup, with `include_deleted=true`, assert tombstone is present with `deleted_at` populated.
- `feed_fields_projection_drops_columns` — request `fields=id,sha256`, assert response rows contain only `id`, `sha256`, plus the always-forced `updated_at`.
- `feed_fields_projection_unknown_column_400` — request unknown column, assert API returns `400`.
- `feed_after_id_without_updated_since_400` — assert the meaningless-anchor case errors.
- `feed_respects_filters` — `tag`, `content_type`, `path_prefix` each narrow the result correctly (incl. cross-project where applicable).
- `feed_cross_project` — `project_id="all"` returns rows from multiple projects, ordered globally by `updated_at`.

### Integration (API layer)

- `empty_poll_is_indexed` — instrument the SQL query, assert the plan uses `idx_artifacts_project_updated` (or the cross-project equivalent), no full scan. Run via `EXPLAIN QUERY PLAN` and assert.
- `concurrent_write_during_walk` — start a paginated walk, insert a new artifact between page 1 and page 2, assert it appears on a subsequent page (forward-walk safety).
- `cursor_round_trip` — first response's `next_cursor` echoed back as next request's params yields the next page with no duplicates / gaps.

## What's NOT in v1 (explicitly deferred)

- `total_count` / progress estimates.
- Server-Sent Events or WebSocket streaming.
- ETag / `If-Modified-Since` headers (agents do change-detection via `updated_since` already).
- Per-agent API keys / scoped auth.
- MCP tool wrapping.
- The `artifact_tags` join table.
- Sort options beyond the locked `updated_at ASC, id ASC`. (UI keeps the existing `/api/artifacts` for that.)

## Open questions (none blocking)

None at design close. The cursor scheme, sort order, tombstone policy, projection shape, and total-count decision were all resolved during brainstorming.
