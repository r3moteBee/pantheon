# `GET /api/artifacts/feed`

Agent-facing forward-walk over a project's artifacts. Designed for two patterns:

- **Change-detection polling** — "what changed since I last checked?" Steady-state empty responses are cheap (single indexed range scan, zero rows).
- **Bulk enumeration** — walk every artifact in a project for ingest / RAG indexing.

Both patterns use the same primitives — a `(updated_since, after_id)` cursor — so an agent that does change-detection today can switch to bulk without changing its request shape.

The list-shaped `GET /api/artifacts` is **not** an agent endpoint. It is UI-shaped (pinned-first, recency-DESC, silently capped at 200) and is not safe for either pattern.

---

## Authentication

Same as every other `/api/...` route: bearer token in the `Authorization` header.

```
Authorization: Bearer <token>
```

---

## Query parameters

| Param | Type | Default | Description |
|---|---|---|---|
| `project_id` | string | `"default"` | Scope. Use `"all"` (or `""`) for cross-project. |
| `updated_since` | ISO-8601 | none | Exclusive lower bound on `updated_at`. Doubles as the cursor on subsequent pages. |
| `after_id` | UUID | none | Tiebreaker — required when paginating within rows that share the same `updated_at`. Must be paired with `updated_since`; passing `after_id` alone returns `400`. |
| `limit` | int | `500` | Page size. `1 ≤ limit ≤ 5000`; out-of-range returns `422`. |
| `include_deleted` | bool | `false` | If true, soft-deleted rows appear as tombstones (with `deleted_at` populated). |
| `tag` | string | none | Filter to artifacts carrying this tag. |
| `content_type` | string | none | Filter by MIME (e.g. `text/markdown`). |
| `path_prefix` | string | none | Filter by path prefix. |
| `fields` | csv | full row minus `content`/`blob_path` | Column projection. Unknown columns return `400`. `id` and `updated_at` are always present so the cursor can advance. |

---

## Response envelope

```json
{
  "artifacts": [
    {
      "id": "abc-def-…",
      "project_id": "default",
      "path": "default-project/youtube-transcripts/…/transcript.md",
      "title": "…",
      "content_type": "text/markdown",
      "size_bytes": 12345,
      "sha256": "…",
      "tags": ["…"],
      "source": {…},
      "pinned": false,
      "current_version_id": "…",
      "created_at": "2026-05-10T…",
      "updated_at": "2026-05-17T20:14:32.123Z",
      "deleted_at": null
    }
  ],
  "next_cursor": {
    "updated_since": "2026-05-17T20:14:32.123Z",
    "after_id": "abc-def-…"
  },
  "has_more": true,
  "count": 1
}
```

- `next_cursor` is `null` when the page returned fewer rows than `limit` (no more rows).
- `has_more` is the convenience boolean. Equivalent to `next_cursor is not None`.
- `count` is the size of THIS page. There is no total count by design (would require an extra `COUNT(*)` query and isn't worth the cost for either polling or bulk use).
- `content` and `blob_path` are always stripped. Fetch a single artifact via `GET /api/artifacts/{id}` if you need the body.

---

## Sort order

Locked to `updated_at ASC, id ASC`. Forward chronological walk is the only safe order under concurrent writes — a row inserted mid-walk always appears at the tail of the forward walk, so a polling agent cannot miss it.

Sort is **not** configurable. If you want recency-DESC for human display, use `GET /api/artifacts`.

---

## Errors

| Status | When |
|---|---|
| `400` | Invalid ISO-8601 in `updated_since`; `after_id` passed without `updated_since`; unknown column in `fields`. |
| `404` | Unknown `project_id` (when not `"all"`/`""`). |
| `422` | FastAPI validation (e.g. `limit` out of range). |

---

## Patterns

### Change-detection polling

Persist `last_updated_at` between polls. On each poll, ask for everything since.

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/artifacts/feed?\
project_id=default&\
updated_since=2026-05-17T20:00:00.000Z&\
fields=id,sha256,deleted_at,updated_at"
```

Empty-result steady state:

```json
{"artifacts": [], "next_cursor": null, "has_more": false, "count": 0}
```

When rows come back, process them and store the **last row's** `updated_at` as the next poll's `updated_since`.

### Bulk enumeration

Start with no cursor, follow `next_cursor` until `has_more: false`:

```python
import requests

params = {"project_id": "default", "limit": 500}
seen = []
while True:
    r = requests.get("http://localhost:8000/api/artifacts/feed",
                     params=params,
                     headers={"Authorization": f"Bearer {TOKEN}"})
    r.raise_for_status()
    data = r.json()
    seen.extend(data["artifacts"])
    if not data["has_more"]:
        break
    params = {
        "project_id": "default",
        "limit": 500,
        **data["next_cursor"],
    }
```

### Tombstone-aware change tracking

A RAG agent that maintains its own index must know when to evict:

```bash
curl -H "Authorization: Bearer $TOKEN" \
  "http://localhost:8000/api/artifacts/feed?\
project_id=default&\
updated_since=<last>&\
include_deleted=true&\
fields=id,sha256,deleted_at,updated_at"
```

For each returned row: if `deleted_at` is non-null, drop it from your index; otherwise re-embed if `sha256` changed.

---

## Performance notes

- Filtered queries (`project_id` + `updated_since`) are served by the partial index `idx_artifacts_project_updated ON artifacts(project_id, updated_at DESC) WHERE deleted_at IS NULL`. Empty polls are a single indexed range scan over zero rows.
- Cross-project (`project_id=all`) queries fall back to a partial table scan over the `updated_at` axis. Fine for current data volumes; if cross-project polling becomes hot, that's the index to add next.
- `tag` filtering is served by `tags LIKE '%"<tag>"%'` on the JSON column. Cheap up to a few thousand artifacts per project. At 5K+, consider migrating tags to a join table (see the v2 notes in the design spec).
- Per-row payload after projection-default is ~750 bytes. 500 rows ≈ 380 KB on the wire. With explicit projection (`fields=id,sha256`) a row drops to ~150 bytes.

---

## What's NOT supported (deferred — see spec for rationale)

- `total_count` / progress estimates
- Server-Sent Events / WebSocket streaming
- `ETag` / `If-Modified-Since` headers (use `updated_since` instead)
- Per-agent API keys / scoped auth
- MCP tool wrapping
- Sort options beyond the locked forward walk

Design spec: [`docs/superpowers/specs/2026-05-17-agent-list-api-design.md`](../superpowers/specs/2026-05-17-agent-list-api-design.md)
