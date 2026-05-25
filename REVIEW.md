# Reviewer guide

A short orientation for someone looking at this codebase for the first time.
Skim this, then dive in.

## Start here

1. **[README.md](README.md)** — what Pantheon is, install, architecture overview.
2. **[CLAUDE.md](CLAUDE.md)** — working context: layered architecture (memory / sources / jobs), naming conventions, gotchas, and a "Design rationale" section explaining the deliberate calls that are easy to misread.
3. **[backend/README.md](backend/README.md)** — backend layout, the 19 API routers, memory tiers, where the LLM config + role mapping live.
4. **[backend/sources/SOURCE_ADAPTERS.md](backend/sources/SOURCE_ADAPTERS.md)** — adapter plugin protocol (read if reviewing ingestion).
5. **[docs/USAGE.md](docs/USAGE.md)** — agent-driving guide (read if reviewing UX, prompts, or agent tools).

## What kind of system this is

Pantheon is a **single-user, single-process** agent harness. Not multi-tenant.
Not horizontally scaled. APScheduler and the JobWorker run as asyncio tasks
inside the FastAPI process — by design.

If a reviewer reflexively suggests "split this into a queue worker," "use
request-scoped DB pools," or "add per-tenant isolation" — those patterns
belong to sister project `tuatha`, not here. The single-user constraint
is load-bearing.

## What shipped recently (for context on the diff)

Most recent work, oldest first:

| Date | Area | What |
|---|---|---|
| 2026-05-19 | Artifacts | Folder tree, drag-and-drop move, duplicate, optimized `/api/artifacts` payload 4.3MB → 145KB |
| 2026-05-19 | Artifact feed | New cursor-paginated `/api/artifacts/feed` for agent / RAG consumers (186 tests) |
| 2026-05-19 | Image extraction | Async vision + OCR + topic extraction for uploaded image artifacts |
| 2026-05-20 | Performance | WAL pragmas across 10 SQLite stores; batched re-embed; trimmed recent-jobs block 15→5 |
| 2026-05-20 | Artifact downloads | Fixed `Content-Disposition` so downloads keep their real filename + extension |
| 2026-05-21 | Artifact downloads | Fixed auth: `/raw` endpoint now accepts `?token=` so `<a href download>` works |
| 2026-05-21 | Mermaid + SVG | Export to SVG / PNG / PDF; fixed `htmlLabels=false` so exports are clean |
| 2026-05-24 | MCP | Bumped protocol to `2025-11-25`; added `MCP-Protocol-Version` header |
| 2026-05-24 | MCP | Full OAuth 2.1 — PRM (RFC 9728), DCR (RFC 7591), PKCE, OIDC discovery fallback, structured tool outputs |
| 2026-05-24 | MCP UI | Edit form on connection cards (URL, API key, enabled toggle) |
| 2026-05-24 | Docs | Full README rewrite; deleted 7 dead scaffolding artifacts |

The MCP OAuth work is the largest single addition — see the **"MCP — protocol version + OAuth + structured outputs"** section in `CLAUDE.md` for the full storage shape and refresh semantics.

## Things worth a careful look

| Area | Where | Why |
|---|---|---|
| MCP OAuth flow | `backend/mcp_client/oauth.py`, `backend/api/mcp_oauth.py`, `backend/mcp_client/manager.py:start_oauth/complete_oauth` | New, security-sensitive, well-isolated; the callback endpoint is in `_PUBLIC_PATHS` for a defensible reason (see CLAUDE.md) |
| OAuth token refresh | `backend/mcp_client/client.py:_send_jsonrpc` 401 path | Single-retry refresh on 401; loop's `max_attempts` is bumped by 1 when a `token_getter` is present |
| Structured tool outputs | `backend/mcp_client/client.py:call_tool` (returns `{text, structured, is_error}`) + `manager.py:_format_tool_result` | New return shape; all callers updated. LLM sees text + `<structured-output>` block when present |
| Memory tier orchestration | `backend/memory/manager.py:recall` | Unconditional cross-tier search — **not** pattern-gated, by design (see CLAUDE.md Design rationale) |
| Source adapters | `backend/sources/adapters/` (28 adapters, 9 mechanisms) | Self-register at import. The `ingest()` pipeline in `backend/sources/registry.py` is the load-bearing flow |
| Agent tool dispatch | `backend/agent/tools.py` (~3000 lines) | Single big file is intentional — `TOOL_SCHEMAS` dict + one dispatch block. Easier to grep than a tools/ directory of one-off classes |
| SQLite write performance | `backend/db_utils.py:apply_sqlite_pragmas` | Every long-lived store routes through this. WAL + `synchronous=NORMAL`. New stores must call it (gotcha called out in CLAUDE.md) |

## Don't-second-guess list

CLAUDE.md has a "Design rationale" section detailing seven decisions that reviewers commonly question. Read that before suggesting:

- "Cache memory recall behind a phrase trigger" — no
- "Sandbox skills with WASM" — they're markdown, not code
- "Add an in-app conversation summarizer" — Claude harness already does this
- "Switch the skill resolver to embeddings" — keyword scoring is deterministic + faster, deliberately
- "Break tools.py into one-class-per-file" — flat dispatch is the pattern
- "Componentize settings/" — already done; check the current tree
- "Add a queue worker process" — single-process is the design

## Known gaps (intentionally not done)

From CLAUDE.md "Things explicitly NOT done yet":

- No CI (tests run manually: `~/pantheon/.venv/bin/python -m pytest tests/integration/`)
- Image-only / scanned PDFs need OCR (not built)
- JS-rendered web pages fail trafilatura (need a Playwright fallback)
- Reddit OAuth (today's workaround: `extras['raw_payload']`)
- Per-project source-adapter scoping (currently global registry)
- UI panel for merge-proposal review (agent-tool only)

## Test posture

```bash
cd ~/pantheon/backend
~/pantheon/.venv/bin/python -m pytest tests/integration/ -v
```

Last run: **199 passed, 5 skipped** in ~6 s. Coverage is uneven — heavy on
adapters (MA Legislature is 142 tests alone), thin on agent/tools.py and the
job handlers. `test_autonomous_skill_resolution.py` is the canary for the
autonomous-job code path; if it goes red, that path regressed.

There is no frontend test suite.

## How the version string works

Single source of truth: `frontend/package.json` `"version"` field, format
`YYYY.MM.DD.HXX`. The backend reads it at startup via `_resolve_app_version()`
in `backend/main.py` and surfaces it at `GET /api/health`. Used to confirm a
deploy picked up new code.

Current: `2026.05.24.H4`.

## Repo layout, in one screen

```
~/pantheon/
├── README.md              Install + architecture
├── CLAUDE.md              Working context — read before hacking
├── REVIEW.md              This file
├── deploy.sh              One-command installer
├── start.sh / stop.sh     Local-mode lifecycle
├── Makefile               Docker-mode lifecycle
├── docker-compose.yml
├── Caddyfile              HTTPS reverse proxy (optional)
│
├── backend/
│   ├── main.py            FastAPI entry + auth middleware + router mounts
│   ├── config.py          Pydantic v2 Settings
│   ├── db_utils.py        Shared SQLite PRAGMA helper
│   ├── agent/             AgentCore + tool dispatch + prompts
│   ├── api/               19 routers (one per resource)
│   ├── sources/           Source-adapter plugin registry + adapters/
│   ├── memory/            Tier implementations (episodic/semantic/graph/working/archival)
│   ├── artifacts/         Versioned artifact store (SQLite + blob)
│   ├── jobs/              Async job system + handlers/
│   ├── skills/            Skill registry + resolver + editor
│   ├── tasks/             APScheduler integration
│   ├── mcp_client/        MCP client + OAuth + manager
│   ├── llm_config/        Named endpoints + role mapping
│   ├── secrets/           Fernet-encrypted vault
│   └── tests/integration/ pytest suite
│
├── frontend/
│   └── src/{pages,components,api,store,utils}
│
└── data/                  Runtime data — gitignored
```

## Things you shouldn't touch (or ask first)

- **`data/`** — runtime user data (memory, projects, vault). Never delete in
  review.
- **`backend/data/`** — bundled default personas + personality templates.
  Tracked in git; edit only if you have a reason.
- **The vault encryption scheme** in `backend/secrets/vault.py` —
  Fernet + PBKDF2; changing parameters invalidates everyone's stored secrets.

## Questions?

CLAUDE.md is the most useful document for understanding why things are the
way they are. Most reviewer questions ("why is this implemented this way?",
"shouldn't this be split?") have answers in the **Design rationale** section
there.
