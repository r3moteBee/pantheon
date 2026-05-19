# Pantheon Backend

FastAPI service for the Pantheon agent harness — multi-tier memory, source-adapter ingestion, autonomous jobs, and a knowledge-graph substrate.

For working conventions, gotchas, deploy workflow, and the "things explicitly not done yet" list, see [`/CLAUDE.md`](../CLAUDE.md). This README covers backend layout only.

## Architecture (three layers)

1. **Memory** — `memory/` orchestrates SQLite (episodic, graph, file_index), ChromaDB (semantic), and JSON (projects metadata). `MemoryManager.recall(query, tiers=[...])` queries across them.
2. **Source adapters** — `sources/` turns a URL or video_id into a typed-topics-frontmatter markdown artifact + graph nodes/edges. 28 adapters across 9 mechanisms (youtube, blog, pdf, web, forum, podcast, github, cfr, malegis). Self-register at import. See [`sources/SOURCE_ADAPTERS.md`](sources/SOURCE_ADAPTERS.md).
3. **Jobs** — `jobs/` is the unified async job system. Job types: `autonomous_task`, `scheduled_job`, `coding_task`, `extraction`, `file_indexing`, `image_extraction`, `iteration_loop`. APScheduler fires → `JobStore` persists → `JobWorker` polls and dispatches to handlers.

## Directory layout

```
backend/
├── main.py             FastAPI app entry; reads version from frontend/package.json
├── config.py           Pydantic v2 Settings; settings.db_dir is canonical
├── agent/              AgentCore + tool dispatch + system prompts
├── api/                FastAPI routers (one per resource, see below)
├── sources/            Source-adapter plugin registry + adapters/
├── memory/             5-tier memory implementations + extraction
├── artifacts/          Artifact store (SQLite + blob storage)
├── jobs/               Unified async job system + handlers/
├── skills/             Skill registry + resolver + editor
├── tasks/              APScheduler integration
├── mcp_client/         MCP server connection pool
├── llm_config/         Named endpoints + role mapping (chat/prefill/vision/embed/rerank)
├── models/             ModelProvider + per-role getters
├── secrets/            Fernet-encrypted vault
├── secret_storage/     Storage backend for the vault
├── plugins/            Plugin loader (sources + tools)
├── integrations/       External-service integrations
├── telegram_bot/       Telegram bot (still in place; messaging gateway is planned)
├── sandbox/            Sandboxed execution helpers
├── utils/              Shared helpers
├── data/               BUNDLED defaults (personas, personality) — tracked in git
├── tests/integration/  Pytest integration tests
└── requirements.txt
```

Runtime data (databases, projects, user skills, Chroma collections) lives under `~/pantheon/data/`, **not** in `backend/data/`.

## API surface

19 routers mounted under `/api` from `main.py`:

`auth` · `chat` · `files` · `memory` · `personality` · `projects` · `settings` · `mcp` · `skills` · `tasks` · `personas` · `system` · `sources` · `connections` · `artifacts` · `conversations` · `jobs` · `llm_endpoints` · `project_export` / `project_import`

Notable endpoints:
- `GET /api/health` — version string (drives deploy verification)
- `GET /api/artifacts/feed` — agent-shaped cursor-paged feed; see [`docs/api/artifacts-feed.md`](../docs/api/artifacts-feed.md)
- `GET /api/artifacts` — UI-shaped artifacts list
- `GET /api/llm/endpoints`, `GET /api/llm/roles`, `POST /api/llm/probe` — named endpoints + role mapping
- `POST /api/chat/attach` — uploads to ArtifactStore + enqueues `image_extraction` for images

## LLM configuration

Named endpoints + role mapping (NOT flat per-role triplets). Stored in the vault under `llm_endpoint_key__<name>` and `llm_role_mapping`. Legacy flat-config keys are auto-migrated on first read. See the "LLM endpoints + role mapping" section of `/CLAUDE.md`.

To add a role: update `llm_config.models.ROLES` and the matching getter in `models/provider.py`, plus the frontend `RoleMapping.jsx`.

## Memory tiers

| Tier      | Backend             | Purpose                                       |
| --------- | ------------------- | --------------------------------------------- |
| Working   | in-process          | Per-conversation scratch (not persisted)      |
| Episodic  | SQLite              | Chat history + task logs                      |
| Semantic  | ChromaDB            | Embedded chunks + topic-label embeddings      |
| Graph     | SQLite              | Typed nodes + edges, idempotent inserts       |
| Archival  | SQLite (mostly unused) | Reserved for whole-document storage        |

## Install + run

Use the repo-root lifecycle scripts; don't invoke `uvicorn` directly.

```bash
# From repo root
./start.sh                  # boots backend + frontend
./stop.sh                   # graceful stop
curl -s localhost:8000/api/health   # verify version
```

Backend deps install into the project venv (`~/pantheon/.venv`):

```bash
~/pantheon/.venv/bin/pip install -r backend/requirements.txt
```

Full rebuild-after-change workflow is documented in `/CLAUDE.md` (Deploy / build / test workflow).

## Tests

```bash
cd ~/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ -v
```

Coverage is sparse — expand as you go. The autonomous-skill-resolution test (`tests/integration/test_autonomous_skill_resolution.py`) is the canary for the autonomous job path.

## Versioning

Single source of truth: `frontend/package.json` `"version"` field, format `YYYY.MM.DD.HXX`. Backend reads it at startup via `_resolve_app_version()` in `main.py` and surfaces it at `/api/health`. Bump on every push.
