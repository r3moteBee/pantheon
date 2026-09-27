# Pantheon Backend

FastAPI service for the Pantheon agent harness — multi-tier memory, source-adapter ingestion, async jobs, and a knowledge-graph substrate.

For working conventions, gotchas, the deploy workflow and the "not done yet" list, see [`/CLAUDE.md`](../CLAUDE.md). This README covers backend layout only.

## Architecture (three layers)

1. **Memory** — `memory/` orchestrates SQLite (episodic, graph, file index), ChromaDB (semantic) and JSON (project metadata). `MemoryManager.recall(query, tiers=[...])` queries across them.
2. **Source adapters** — `sources/` turns a URL or ID into a typed-topics-frontmatter markdown artifact + graph nodes/edges. 29 adapters across 10 mechanisms (`youtube`, `blog`, `pdf`, `web`, `forum`, `podcast`, `github`, `cfr`, `malegis`, `sec`), self-registered at import. See [`sources/SOURCE_ADAPTERS.md`](sources/SOURCE_ADAPTERS.md).
3. **Jobs** — `jobs/` is the unified async job system. Job types: `autonomous_task`, `coding_task`, `image_extraction`, `iteration_loop` (`POST /api/jobs` accepts all but `image_extraction`). APScheduler fires → `JobStore` persists → `JobWorker` (asyncio task in the FastAPI process) dispatches to `jobs/handlers/`; a watchdog stalls idle jobs.

## Directory layout

```
backend/
├── main.py             FastAPI app entry, auth middleware; reads version from frontend/package.json
├── config.py           Pydantic v2 Settings; settings.db_dir is canonical
├── db_utils.py         apply_sqlite_pragmas() — WAL/NORMAL/foreign_keys for every store
├── security_log.py     Security audit log
├── agent/              AgentCore loop, tools.py (schemas + dispatch), prompts, browser tools, textual tool-call recovery
├── api/                FastAPI routers (see below)
├── artifacts/          Artifact store (SQLite + blobs), embedder, previews, conversions
├── integrations/       GitHub API client
├── jobs/               JobStore, JobWorker, watchdog, orphan recovery, handlers/
├── llm_config/         Named endpoints, task-class routes, model profiles, probe, chat router, usage log, tuning
├── mcp_client/         MCP client (protocol 2025-11-25), connection manager, OAuth 2.1
├── memory/             Episodic, semantic, graph, file index, archival, topic embeddings, merge proposals
├── messaging/          Bot gateway + adapters (Telegram, Slack, Discord, Matrix, Mattermost)
├── models/             ModelProvider / RoutedProvider; get_provider_for(<task class>)
├── sandbox/            Code-execution backends (subprocess default, Firecracker opt-in via PANTHEON_SANDBOX)
├── secrets/            Encrypted vault (PBKDF2-derived Fernet key)
├── skills/             Skill registry, resolver, editor, importer/exporter, scanner, versioning
├── sources/            Source-adapter registry, extractors, similarity + adapters/
├── tasks/              APScheduler integration
├── utils/              Shared helpers (safe_http_get, paths, progress, background.spawn, self-doc, …)
├── data/               BUNDLED defaults (personas, personality, migrations) — tracked in git
├── tests/integration/  Pytest integration tests
└── requirements.txt
```

Runtime data (databases, projects, user skills, Chroma collections) lives under `~/pantheon/data/`, **not** in `backend/data/`.

## API surface

19 routers mounted under `/api` in `main.py`:

`auth` · `chat` · `files` · `memory` · `personality` · `projects` · `settings` · `mcp` · `mcp_oauth` · `skills` · `tasks` · `personas` · `system` · `connections` · `artifacts` · `conversations` · `jobs` · `llm_endpoints` · `messaging`

`project_export` / `project_import` are helpers called from the `projects` router, not mounted separately.

Notable endpoints:
- `GET /api/health` — version string (drives deploy verification)
- `POST /api/auth/login` — sets the HttpOnly `pantheon_session` cookie; scripts may send `Authorization: Bearer <token>`. Query-string tokens are never accepted.
- `GET /api/artifacts/feed` — agent-shaped cursor-paged feed; see [`docs/api/artifacts-feed.md`](../docs/api/artifacts-feed.md)
- `/api/llm/endpoints`, `/api/llm/probe`, `/api/llm/task-classes`, `/api/llm/routes`, `/api/llm/profile(s)`, `/api/llm/usage` — LLM endpoints, routing and usage
- `/api/llm/router`, `/api/llm/router/{decisions,feedback,tuning,simulate,apply}` — per-turn chat router + tuning
- `POST /api/chat/attach` — uploads to ArtifactStore + enqueues `image_extraction` for images

WebSocket endpoints bypass the HTTP middleware and call `api.auth.authorize_websocket()` themselves.

## LLM configuration

Named endpoints (`{name, base_url, api_type, api_key}`, key in the vault as `llm_endpoint_key__<name>`) + task-class routes (vault `llm_routes`: ordered primary + fallbacks per class) + model profiles. Classes are defined in `llm_config/models.py` `TASK_CLASSES`: `agent`, `code`, `quick`, `long_context`, `extract`, `summarize`, `vision`, `image_gen`, `embed`, `rerank`. Call sites ask by class: `get_provider_for("extract")`. Legacy flat keys and the old role mapping are migrated once by `llm_config/migration.py`; `/api/llm/roles` still works as a compatibility shim.

To add a task class: add it to `TASK_CLASSES` + the `TaskClass` literal, handle any special semantics in `get_provider_for`, and call `get_provider_for("<class>")`. The UI renders classes from `/api/llm/task-classes`. Details: "LLM endpoints + task-class routing" in `/CLAUDE.md`.

## Memory tiers

| Tier      | Backend             | Purpose                                       |
| --------- | ------------------- | --------------------------------------------- |
| Working   | in-process          | `AgentCore.working_memory` (not persisted)    |
| Episodic  | SQLite              | Chat history + task logs                      |
| Semantic  | ChromaDB            | Embedded chunks + topic-label embeddings      |
| Graph     | SQLite              | Typed nodes + edges, idempotent inserts       |
| Archival  | Markdown files      | Notes + project summary under `<data_dir>/projects/<id>/notes` |

## Install + run

Use the repo-root lifecycle scripts:

```bash
# From repo root
./start.sh                  # boots the backend (serves frontend/dist)
./stop.sh                   # graceful stop
curl -s localhost:8000/api/health   # verify version
```

Backend deps install into the project venv (`~/pantheon/.venv`):

```bash
~/pantheon/.venv/bin/pip install -r backend/requirements.txt
```

For hot reload during development: `cd backend && ../.venv/bin/uvicorn main:app --reload --port 8000`. Full rebuild-after-change workflow is in `/CLAUDE.md` (Deploy / build / test workflow).

## Tests

```bash
cd ~/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ -v
```

`tests/integration/conftest.py` lowers the vault KDF iteration count for speed. `tests/integration/test_autonomous_skill_resolution.py` is the canary for the autonomous job path.

## Versioning

Single source of truth: `frontend/package.json` `"version"`, format `YYYY.MM.DD.HXX`. The backend reads it at startup via `_resolve_app_version()` in `main.py` and surfaces it at `/api/health`. Bump on every push, then run `npm install --package-lock-only` in `frontend/` and commit the lockfile.
