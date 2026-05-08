# CLAUDE.md — Pantheon agent harness

This file gives Claude Code the working context for the Pantheon codebase. Read it before making changes.

## What Pantheon is

A single-user agent harness with persistent multi-tier memory, a source-adapter ingestion pipeline, autonomous scheduled tasks, and a knowledge-graph substrate for research workflows. Originally a sister/predecessor of `tuatha` (multi-tenant), Pantheon stays single-user and easy-to-install.

The user (Brent) runs Pantheon locally at `~/pantheon` against a small set of MCP connectors (YouTube transcript MCP, GitHub MCP, etc.) and uses it for thematic / vendor research over time — schedule a daily ingest, let it accumulate, analyze the graph at end of week.

## Architecture in three layers

1. **Memory.** SQLite for episodic / graph / file index (one DB each under `data/db/`). ChromaDB for semantic memory at `data/chroma/`. JSON for project metadata at `data/db/projects.json`. The `MemoryManager` orchestrates all four tiers; agents call `mgr.recall(query, tiers=[...])` to query across them.

2. **Source adapters.** The ingestion pipeline that turns "a URL or video_id" into a typed-topics-frontmatter markdown artifact + graph nodes/edges. Adapters live in `backend/sources/adapters/` and self-register at import time. Currently 21 adapters across 8 mechanisms (`youtube`, `blog`, `pdf`, `web`, `forum`, `podcast`, `github`, `cfr`). Each adapter declares its `source_type`, `bucket_aliases`, `extractor_strategy`, `auto_extract`, and `auto_link_similarity`. See `backend/sources/SOURCE_ADAPTERS.md` for the full design.

3. **Jobs.** Unified async job system in `backend/jobs/`. Job types: `autonomous_task`, `scheduled_job`, `coding_task`, `extraction`, `file_indexing`. APScheduler fires schedules → `_enqueue_autonomous_job` creates a job row → `JobWorker` (asyncio task in the FastAPI process) polls and dispatches to the registered handler. Stall watchdog kills jobs idle for 5 min; total timeout configurable per-job.

## Directory layout

```
~/pantheon/
├── backend/
│   ├── main.py                  FastAPI app entry; reads version from frontend/package.json
│   ├── agent/                   AgentCore + tool dispatch + system prompts
│   │   ├── core.py              AgentCore class; runs the agent loop
│   │   ├── tools.py             ALL agent tools (schemas + dispatch). 1500+ lines.
│   │   ├── prompts.py           build_system_prompt(); appends recent-jobs + available-skills blocks
│   │   └── browser_tools.py     Playwright browser tools (optional)
│   ├── api/                     FastAPI routers
│   │   ├── chat.py              REST + websocket chat. Both run resolve_explicit + resolve_auto.
│   │   ├── tasks.py             Schedule CRUD; run-now; rerun_job
│   │   ├── jobs.py              Jobs CRUD
│   │   ├── artifacts.py         Artifact CRUD + bulk export
│   │   ├── projects.py          Project metadata; reads/writes data/db/projects.json
│   │   ├── project_export.py    Export project as zip (artifacts, episodic, graph, semantic)
│   │   ├── project_import.py    Import a project zip
│   │   ├── personas.py          Persona CRUD (apollo, athena, zeus, ...)
│   │   ├── connections.py       GitHub PAT connections
│   │   ├── llm_endpoints.py     /api/llm/{endpoints,roles,probe} — named endpoints + role mapping
│   │   ├── settings.py          Legacy flat-config CRUD; still in place for backward compat
│   │   └── skills.py            Skill registry CRUD + auto-discovery toggle + debug-match
│   ├── sources/                 Source-adapter plugin registry — see SOURCE_ADAPTERS.md
│   │   ├── base.py              SourceAdapter, IngestRequest, FetchedContent, AdapterResult
│   │   ├── registry.py          register_adapter, ingest, batch_ingest
│   │   ├── extraction.py        TopicExtractor + 6 built-in strategies
│   │   ├── similarity.py        link_artifact_topics + execute_merge + backfill
│   │   ├── util.py              slugify, parse_relative_date, html_to_markdown
│   │   └── adapters/
│   │       ├── youtube.py       3 adapters (interview, keynote, other)
│   │       ├── blog.py          4 adapters (announcement, influencer, technical, news)
│   │       ├── pdf.py           4 adapters (datasheet, whitepaper, research, marketing)
│   │       ├── web.py           3 adapters (product-page, service-page, changelog)
│   │       ├── forum.py         2 adapters (reddit, hackernews)
│   │       ├── podcast.py       1 adapter  (episode — trafilatura or extras['transcript'])
│   │       ├── github.py        2 adapters (release, changelog) — uses GH API + raw fetch
│   │       └── cfr.py           2 adapters (section, part) — eCFR Versioner API → markdown
│   ├── memory/                  Memory tiers — episodic, semantic, graph, file_index
│   │   ├── manager.py           MemoryManager — orchestrates all tiers
│   │   ├── episodic.py          EpisodicMemory — chat history + task logs
│   │   ├── semantic.py          SemanticMemory — ChromaDB wrapper
│   │   ├── graph.py             GraphMemory — SQLite nodes + edges. add_edge is idempotent.
│   │   ├── file_indexer.py      FileIndexer — chunk + embed + extract entities to graph.
│   │   │                        _index_typed_topics_to_graph handles the canonical frontmatter shape.
│   │   ├── topic_embeddings.py  Topic-label embeddings keyed by (project_id, topic_type, label)
│   │   ├── merge_proposals.py   SQLite store for reviewable graph node merges
│   │   ├── extraction.py        Conversation entity extractor (different from sources/extraction.py)
│   │   └── archival.py          Archival memory (mostly unused)
│   ├── artifacts/               Artifact store
│   │   └── store.py             SQLite + blob storage; project_slug() used everywhere
│   ├── jobs/                    Unified async job system
│   │   ├── store.py             JobStore — create / get / list / fail / rerun
│   │   ├── worker.py            JobWorker — asyncio polling loop in same process as FastAPI
│   │   ├── watchdog.py          Stall detector — kills jobs idle for 5 min
│   │   └── handlers/            One file per job_type
│   │       ├── autonomous_task.py    The big one — runs an agent loop with skill resolution
│   │       ├── scheduled_job.py      Lightweight scheduled prompts
│   │       ├── coding_task.py        Github sub-agent for PR-shaped coding work
│   │       ├── extraction.py         Memory extractor
│   │       └── file_indexing.py      Workspace file indexer
│   ├── skills/                  Skill system (callable recipes, distinct from scheduled tasks)
│   │   ├── registry.py          SkillRegistry — bundled skills + user skills
│   │   ├── resolver.py          resolve_explicit (/slug) + resolve_auto (keyword scoring)
│   │   ├── editor.py            create_blank_skill — used by the create_skill agent tool
│   │   └── models.py            SkillManifest (Pydantic), MemoryAccess (Enum), etc.
│   ├── tasks/scheduler.py       APScheduler integration; schedule_agent_task accepts skill_name
│   ├── mcp_client/manager.py    MCP server connection pool
│   ├── llm_config/              Named endpoints + role-mapping registry (replaces flat per-role config)
│   │   ├── models.py            Pydantic: SavedEndpoint, EndpointWithKey, EndpointPublic, RoleAssignment
│   │   ├── store.py             Vault-backed CRUD; resolve_role(role) → ResolvedRole
│   │   ├── migration.py         One-shot migrator from legacy llm_*/prefill_*/vision_*/embedding_*/reranker_* keys
│   │   └── probe.py             Generic /models discovery for openai / ollama / anthropic / custom
│   ├── models/provider.py       ModelProvider + 5 role getters that consult llm_config.store.resolve_role
│   ├── secrets/vault.py         Encrypted secret storage in data/db/vault.db
│   ├── config.py                Settings (Pydantic v2). settings.db_dir is canonical.
│   ├── data/                    BUNDLED defaults — personas/, personality/. Tracked in git.
│   ├── tests/integration/       Pytest integration tests. Sparse coverage; expand as you go.
│   └── requirements.txt         Backend deps
├── frontend/                    Vite + React. package.json's "version" drives backend version too.
│   ├── src/
│   │   ├── pages/ArtifactsPage.jsx
│   │   ├── components/Chat.jsx, ChatTabs.jsx, Layout.jsx
│   │   ├── components/chat-tabs/ProjectTasksPanel.jsx (Tasks panel — schedules + jobs)
│   │   ├── components/settings/    LLM endpoints + role mapping UI (EndpointCard, AddEndpointForm,
│   │   │                            EndpointList, RoleMapping, RoleMappingRow)
│   │   ├── api/client.js        Axios wrappers for backend endpoints (incl. llmApi.*)
│   │   └── store/index.js       Zustand store
│   ├── tailwind.config.js       Uses @tailwindcss/typography for prose styling
│   └── package.json
├── data/                        RUNTIME data — NOT in git
│   ├── db/                      All SQLite + projects.json + vault
│   ├── chroma/                  ChromaDB collections
│   ├── projects/                Per-project workspace files
│   ├── skills/                  User-installed skills (skill.json + instructions.md)
│   └── personality/             User-overridden soul.md / agent.md (defaults in backend/data/personality)
├── start.sh / stop.sh           Lifecycle scripts
└── deploy.sh                    Pull + rebuild on the local dev box
```

## Deploy / build / test workflow

The user runs Pantheon locally on a Linux box at `~/pantheon`. The venv lives at `~/pantheon/.venv`. The user runs deploy commands themselves — do NOT ssh and run them yourself.

Standard rebuild after code changes:

```bash
cd ~/pantheon && git pull
~/pantheon/.venv/bin/pip install -r backend/requirements.txt   # if deps changed
cd frontend && VITE_API_URL="" npm run build && cd ..          # if frontend changed
./stop.sh && pkill -f "uvicorn main:app" 2>/dev/null
find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
./start.sh && sleep 3 && curl -s http://localhost:8000/api/health
```

The curl at the end shows the version string — confirm it changed before declaring a deploy successful.

If a frontend dep changes, also `cd frontend && npm install` before the build.

Run integration tests:

```bash
cd ~/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ -v
```

Currently 6 tests. Expand them when fixing regressions.

## Versioning convention

There's ONE source of truth: `frontend/package.json`'s `"version"` field. The backend reads it at startup via `_resolve_app_version()` in `main.py`. Bump it on every push:

- Format: `YYYY.MM.DD.HXX` (e.g. `2026.05.04.H1`)
- Increment the H suffix on each ship within a day
- The H counter wraps from H9 to Ha to Hz to H10 etc. — keep it short

The version surfaces at `GET /api/health` and in the FastAPI title. The user uses it to confirm the deploy actually picked up new code.

## Source adapters in 30 seconds

Pattern: `<mechanism>/<genre>` (e.g. `youtube/keynote`, `pdf/datasheet`, `blog/announcement`). Each adapter declares:

- `source_type` — canonical id, must be unique
- `display_name` — for UI
- `bucket_aliases` — heuristic shortcuts (`youtube`, `pdf`, `blog`, `web`)
- `requires_mcp` — MCP tools needed; validated when scheduled tasks fire
- `artifact_path_template` — path template for the saved artifact
- `extractor_strategy` — which `TopicExtractor` to run by default
- `auto_extract: bool` — whether to run extraction inline (default True)
- `auto_link_similarity: bool` — whether to run cross-artifact similarity post-save

`registry.ingest(IngestRequest)` runs the full pipeline: fetch → extract topics → save artifact (deduped on canonical path) → schedule embedding → run typed-topics graph extractor → optional similarity pipeline. `batch_ingest(reqs)` runs many with per-item failure isolation.

To add a new adapter: drop a file under `backend/sources/adapters/`, subclass `SourceAdapter` or one of the `_*AdapterBase` classes, call `register_adapter(YourClass())` at module import time, and add the import to `backend/sources/adapters/__init__.py`.

Built-in extractors (in `backend/sources/extraction.py`):
- `llm_default` — generic prose
- `llm_announcement` — vendor / event announcements (who/what/when/dollars/partners)
- `llm_structured_specs` — datasheets / product pages (specs + pricing + features)
- `llm_research_paper` — academic papers (abstract + methodology + findings)
- `llm_changelog` — release notes
- `noop` — pass-through for sources with pre-baked topics

## Skills vs scheduled tasks

These are different and the agent must not confuse them:

- **Skill** — a reusable callable definition (slug + instructions.md). Invoked with `/skill-name` in chat, or via `skill_name` on `create_task` for scheduled runs. Created via `create_skill` agent tool.
- **Scheduled task** — a one-shot or recurring autonomous run. Has a schedule (`now`, `delay:N`, `interval:N`, cron) and optionally a `skill_name` binding. Created via `create_task` agent tool.

The autonomous_task handler resolves `payload.skill_name` (with underscore↔hyphen tolerance), validates `requires_mcp` against the live MCP manager, and passes the full skill_context + project_name + active_skill_name to AgentCore. If the skill's required MCP tools are offline, the handler fails fast with a clear error rather than running a doomed loop.

System prompt distinguishes these explicitly under the "Skills vs scheduled tasks" section in `agent/prompts.py`. Don't merge them.

## LLM endpoints + role mapping

Pantheon's LLM configuration is **named endpoints + role mapping**, not flat per-role triplets. Two concepts:

- **Saved endpoint** — `{name, base_url, api_type, api_key}` stored once. `api_type` is `openai` (covers OpenAI-compat), `anthropic`, `ollama`, or `custom`. The API key lives in the vault keyed by `llm_endpoint_key__<name>`.
- **Role mapping** — five roles (`chat`, `prefill`, `vision`, `embed`, `rerank`) each point at one saved endpoint + a model id. JSON-serialized in vault under `llm_role_mapping`.

The 5 role-getter functions in `models/provider.py` (`get_provider`, `get_prefill_provider`, `get_vision_provider`, `get_embedding_provider`, `get_reranker_provider`) all consult `llm_config.store.resolve_role(role)` and instantiate `ModelProvider` with the resolved tuple. There's a per-role cache (`_role_cache`) cleared by `reset_provider()` — every endpoint/role mutation in the API router calls it.

**Migration.** Legacy `llm_*`, `prefill_*`, `vision_*`, `embedding_*`, `reranker_*` vault keys are auto-migrated to the new shape on first read of `list_endpoints()` / `get_role_mapping()` / `resolve_role()`. Idempotent via the `llm_config_migrated_v1` flag. Heuristic: `:11434` → ollama, `anthropic.com` → anthropic, otherwise openai. The legacy keys are NOT deleted; the legacy `/api/settings` flat-config endpoint still works for backward compat.

**Frontend.** The Settings page has two panels: **Endpoints** (list of cards + add form, each with Probe + Delete) and **Role Mapping** (one row per role with cascading endpoint+model dropdowns + per-row Fetch button). Components under `frontend/src/components/settings/`. API client calls go through `llmApi.*` in `frontend/src/api/client.js`.

**Adding a new role.** Update both `llm_config.models.ROLES` (Python tuple) and `frontend/src/components/settings/RoleMapping.jsx` (`ROLES` array). Then add a getter in `models/provider.py` and any caller. Migration's `_ROLE_TO_LEGACY` only matters if the new role has legacy flat keys.

## Conventions and gotchas

**Storage layers.** Two distinct things, NOT interchangeable:
- **Artifacts** — durable, indexed, searchable. SQLite + blob. Tools: `save_to_artifact`, `read_artifact`, `list_artifacts`, `update_artifact`, `save_transcript_artifact`. Bare paths get auto-prefixed with the project slug.
- **Workspace files** — ephemeral scratch on disk. Tools: `read_file`, `write_file`, `list_workspace_files`. Don't use these for anything you want to keep.

**MCP `save_*` tools are NOT artifact tools.** `mcp_SubDownload_save_to_library` writes to that MCP server's external storage, which Pantheon cannot see. Always use `save_to_artifact` (or `save_transcript_artifact` for video transcripts) for Pantheon persistence.

**save_to_artifact dedup.** Re-ingesting the same canonical path UPDATES the existing artifact (creating a new version in `artifact_versions`) rather than creating a duplicate. Pass `extras={"force_new": true}` when you legitimately want a separate artifact (e.g. new product version where the old datasheet should also be kept).

**Graph idempotency.** `graph.add_edge` is idempotent on `(project_id, node_a_id, node_b_id, relationship)`. Re-running the typed-topics extractor doesn't pile parallel edges. Same for `add_node` on `(project_id, label)`.

**HTML→markdown.** `trafilatura.extract(output_format="html")` for cleaned article HTML, then `markdownify` for HTML→markdown. trafilatura's own markdown converter flattens lists into inline prose; markdownify preserves them.

**PDF extraction.** Default is `pdfplumber` (handles tables / structured layouts). `pypdf` is the faster fallback for prose. Image-only / scanned PDFs need OCR which isn't built yet.

**Topic-extraction failures are visible.** Every artifact saved through `registry.ingest()` gets an `extraction_status` block in its frontmatter showing `{strategy, ok, error?, raw_excerpt?, topic_count}`. If topics is empty, look there to see why.

**Path normalization for artifacts.** `save_to_artifact`, `list_artifacts`, `read_artifact`, and `index_artifact` all share the same path normalization: bare folder names get the project slug prepended automatically. Pass `path_prefix='NBJ/'` not `'default-project/NBJ/'`.

**Skill name slugify tolerance.** Both `/content_ingest_graph` and `/content-ingest-graph` resolve to `content-ingest-graph`. Don't worry about which separator the user types.

**Date parsing.** YouTube's MCP returns relative strings like `"4 months ago"`. The YouTube adapter accepts either `extras["published"]` (relative) or `extras["published_at"]` (ISO). When orchestrating an ingest after `mcp_SubDownload_search_youtube`, ALWAYS forward each video's `published` string so paths get real dates instead of `unknown-date/`.

**Job task timeout.** Default is 1800s (30 min) for autonomous_task. Pass `timeout_seconds` on `create_task` for batch ingests that need longer.

**Job heartbeats.** The autonomous_task handler emits a heartbeat on every tool call with the current plan step matched. The stall watchdog kills jobs idle for 5 min — the per-step heartbeat keeps it happy.

**parent_session_id.** When `create_task` is called from a chat session, that session_id is captured as `parent_session_id` and threaded through to the autonomous handler. On completion, the handler posts a "Task completed" message into that originating session so the user sees the result where they asked for it.

**DB path canonical location.** ALL SQLite stores live under `settings.db_dir` which resolves to `data/db/`. Don't use relative `data/foo.db` paths — they'll resolve to CWD-relative which on the dev box ends up at `backend/data/`.

## Memory tier semantics

- **Episodic** — chat history + task logs. Searchable by content/timestamp. Persistent.
- **Semantic** — embedded chunks from indexed artifacts and workspace files. Topic-label embeddings stored here too with `metadata.kind=topic_node`.
- **Graph** — typed nodes + edges. Source / video / topic / person / concept node types. Edges: PRODUCES, DISCUSSES, FEATURES_SPEAKER, SEMANTICALLY_SIMILAR_TO.
- **Working** — in-process AgentCore working_memory; not persisted.
- **Archival** — mostly unused; reserved for whole-document storage.

`mgr.recall(query, tiers=[...])` searches across them and returns provenance-tagged hits: `[semantic/artifact] ... ↳ source: NBJ/... id=... tags=[...]` for artifact chunks, `[semantic/file:foo.md]` for workspace file chunks, `[episodic session=abc12345 ts=...]` for chat history, `[graph:concept] ...` for graph nodes.

## Cross-artifact similarity + merge proposals

When `auto_link_similarity=True` on an adapter, after `index_artifact` runs, the similarity pipeline:

1. Embeds each topic label into the semantic collection with `kind=topic_node` metadata
2. For each topic, finds type-compatible neighbors (concept↔concept, technology↔framework, vendor↔organization, market↔market_segment) above cosine 0.86
3. Adds `SEMANTICALLY_SIMILAR_TO` edges in both directions (graph traversal direction-agnostic)
4. For matches above 0.92, queues a merge proposal in `topic_merge_proposals` table

Merges are NEVER auto-applied. The user reviews via `list_merge_proposals` agent tool and explicitly approves with `approve_merge(proposal_id, canonical_label)`. The merge then rewrites every edge touching the deprecated node to point at the canonical, deletes the deprecated node, and marks the proposal as `merged`. Idempotent — re-approving returns "already merged".

## Things explicitly NOT done yet

- JS-rendered web pages fail trafilatura (need playwright fallback when you hit a real failure)
- Image-only / scanned PDFs need OCR
- No CI configured (tests run manually)
- Forum / podcast / github adapters need real-traffic shakedown (only smoke-tested at registry / parsing level so far)
- Reddit OAuth flow — the public `.json` endpoints get 403'd from non-residential IPs; today the workaround is `extras['raw_payload']` (paste the JSON from a logged-in browser). Phase C item: register a Reddit app, store client credentials in the vault, hit `oauth.reddit.com` with a bearer token
- Per-project source-adapter scoping deferred (currently global registry)
- UI panel for merge-proposal review (agent-tool only currently)

## Working style

- Brent prefers concise responses. Skip preambles.
- He runs deploy commands himself; output the command, don't try to ssh.
- After making code changes, give him the rebuild command — don't pretend the change is live.
- If something seems suspect (an empty job result, a UI showing stale state), grep the code before guessing — Pantheon has many small layers and surface-level reasoning often gets the wrong layer.
- When a fix is structural (e.g. wrong abstraction, missing field), say so even if it means more work — Brent would rather pay the architectural cost once than carry the debt.
- Don't pile new features on top of broken ones; fix the foundation first.

## Common dev tasks

**Add a new agent tool:**
1. Add schema entry to `TOOL_SCHEMAS` in `backend/agent/tools.py` (or insert before `create_skill` if it's a content-related tool)
2. Add dispatch branch in the giant `if/elif` block at the bottom of `tools.py`
3. Bump version in `frontend/package.json`
4. Restart backend

**Add a new source adapter:**
1. Create `backend/sources/adapters/<name>.py`
2. Subclass `SourceAdapter` — at minimum implement `fetch()` and set class attrs
3. Call `register_adapter(YourClass())` at module import
4. Import the module in `backend/sources/adapters/__init__.py`
5. Optionally add a specialized extractor in `backend/sources/extraction.py`

**Add a new extractor strategy:**
1. Subclass `LLMDefaultExtractor` (so you inherit the JSON-recovery + diagnostics logic)
2. Define your prompt and parse the response into `ExtractedFields(topics, speakers, claims, status, frontmatter_additions)`
3. Call `register_extractor(YourClass())`
4. Reference it from an adapter via `extractor_strategy = "your_name"`

**Re-ingest an artifact with new behavior:**
Just rerun `ingest_source` with the same identifier — H87 dedup auto-updates the existing artifact rather than creating a duplicate. Pass `force_new=True` if you want a separate artifact.

**Backfill similarity over existing artifacts:**
`link_topic_similarity(path_prefix="youtube-transcripts/")` — runs `link_artifact_topics` over each existing artifact under that prefix.

**Fix the integration tests:**
`backend/tests/integration/test_autonomous_skill_resolution.py` is the seed. Use it as the canary — if it goes red after a refactor, the autonomous task path probably regressed.
