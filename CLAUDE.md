# CLAUDE.md — Pantheon agent harness

This file gives Claude Code the working context for the Pantheon codebase. Read it before making changes.

## What Pantheon is

A single-user agent harness with persistent multi-tier memory, a source-adapter ingestion pipeline, autonomous scheduled tasks, and a knowledge-graph substrate for research workflows. Originally a sister/predecessor of `tuatha` (multi-tenant), Pantheon stays single-user and easy-to-install.

The user (Brent) runs Pantheon locally at `~/pantheon` against a small set of MCP connectors (YouTube transcript MCP, GitHub MCP, etc.) and uses it for thematic / vendor research over time — schedule a daily ingest, let it accumulate, analyze the graph at end of week.

## Architecture in three layers

1. **Memory.** SQLite for episodic / graph / file index (one DB each under `data/db/`). ChromaDB for semantic memory at `data/chroma/`. JSON for project metadata at `data/db/projects.json`. The `MemoryManager` orchestrates all four tiers; agents call `mgr.recall(query, tiers=[...])` to query across them.

2. **Source adapters.** The ingestion pipeline that turns "a URL or video_id" into a typed-topics-frontmatter markdown artifact + graph nodes/edges. Adapters live in `backend/sources/adapters/` and self-register at import time. Currently 28 adapters across 9 mechanisms (`youtube`, `blog`, `pdf`, `web`, `forum`, `podcast`, `github`, `cfr`, `malegis`). Each adapter declares its `source_type`, `bucket_aliases`, `extractor_strategy`, `auto_extract`, and `auto_link_similarity`. See `backend/sources/SOURCE_ADAPTERS.md` for the full design.

3. **Jobs.** Unified async job system in `backend/jobs/`. Job types: `autonomous_task`, `scheduled_job`, `coding_task`, `extraction`, `file_indexing`, `image_extraction`, `iteration_loop`. APScheduler fires schedules → `_enqueue_autonomous_job` creates a job row → `JobWorker` (asyncio task in the FastAPI process) polls and dispatches to the registered handler. Stall watchdog kills jobs idle for 5 min; total timeout configurable per-job.

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
│   ├── api/                     FastAPI routers (18 mounted routers)
│   │   ├── chat.py              REST + websocket chat. Both run resolve_explicit + resolve_auto.
│   │   ├── tasks.py             Schedule CRUD; run-now; rerun_job
│   │   ├── jobs.py              Jobs CRUD
│   │   ├── artifacts.py         Artifact CRUD + bulk export
│   │   ├── files.py             Workspace files CRUD + document conversions
│   │   ├── projects.py          Project metadata; reads/writes data/db/projects.json
│   │   ├── project_export.py    Export project as zip (artifacts, episodic, graph, semantic)
│   │   ├── project_import.py    Import a project zip
│   │   ├── personas.py          Persona CRUD (apollo, athena, zeus, ...)
│   │   ├── mcp.py               MCP server registry + port scanning
│   │   ├── mcp_oauth.py         MCP OAuth2 callback authentication
│   │   ├── connections.py       GitHub PAT connections
│   │   ├── conversations.py     Conversation history metadata updates
│   │   ├── llm_endpoints.py     /api/llm/{endpoints,routes,profiles,usage,probe} — endpoints + task-class routing
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
│   │       ├── cfr.py           2 adapters (section, part) — eCFR Versioner API → markdown
│   │       └── malegislature.py 7 adapters (general-law-section, general-law-chapter,
│   │                            session-law, bill, hearing, roll-call, committee-vote) —
│   │                            malegislature.gov public REST API → markdown
│   ├── memory/                  Memory tiers — episodic, semantic, graph, file_index
│   │   ├── manager.py           MemoryManager — orchestrates all tiers
│   │   ├── episodic.py          EpisodicMemory — chat history + task logs
│   │   ├── semantic.py          SemanticMemory — ChromaDB wrapper
│   │   ├── graph.py             GraphMemory — SQLite nodes + edges. add_edge is idempotent.
│   │   ├── chunker.py           Text chunking strategies (headings, paragraphs, fixed characters)
│   │   ├── file_indexer.py      FileIndexer — chunk + embed + extract entities to graph.
│   │   │                        _index_typed_topics_to_graph handles the canonical frontmatter shape.
│   │   ├── topic_embeddings.py  Topic-label embeddings keyed by (project_id, topic_type, label)
│   │   ├── working.py           WorkingMemory — per-conversation scratch workspace
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
│   │       ├── file_indexing.py      Workspace file indexer
│   │       ├── image_extraction.py   Vision + OCR + topic extraction for uploaded image artifacts
│   │       └── iteration_loop.py     Multi-turn execute/review loop with per-turn artifacts
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
│   ├── utils/                   Shared helpers
│   │   ├── autoresearch.py      Evolutionary self-improvement loop CLI runner
│   │   ├── document_converter.py Unified Pandoc/LibreOffice document format converter
│   │   └── vision.py            OCR and image analysis helper
│   ├── data/                    BUNDLED defaults — personas/, personality/. Tracked in git.
│   ├── tests/integration/       Pytest integration tests. Sparse coverage; expand as you go.
│   └── requirements.txt         Backend deps
├── frontend/                    Vite + React. package.json's "version" drives backend version too.
│   ├── src/
│   │   ├── pages/ArtifactsPage.jsx
│   │   ├── components/Chat.jsx, ChatTabs.jsx, Layout.jsx
│   │   ├── components/chat-tabs/ProjectTasksPanel.jsx (Tasks panel — schedules + jobs)
│   │   ├── components/settings/    LLM endpoints + model routing UI (EndpointCard, AddEndpointForm,
│   │   │                            EndpointList, ModelRouting, RoutingUsage)
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
cd frontend && npm ci && VITE_API_URL="" npm run build && cd ..  # if frontend changed
./stop.sh && pkill -f "uvicorn main:app" 2>/dev/null
find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
./start.sh && sleep 3 && curl -s http://localhost:8000/api/health
```

The curl at the end shows the version string — confirm it changed before declaring a deploy successful.

Use `npm ci` (not `npm install`) on the dev box: it installs exactly what `package-lock.json` pins and never rewrites it, so `git pull` doesn't conflict. `npm ci` fails if `package.json` and the lockfile disagree — whenever you change a dependency **or bump the version in package.json**, run `npm install --package-lock-only` (or `npm install <pkg>`) and commit the updated lockfile.

Run integration tests:

```bash
cd ~/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ -v
```

Currently ~410 tests (5 skipped). `tests/integration/conftest.py` lowers the vault KDF iteration count for speed. Expand them when fixing regressions.

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

## LLM endpoints + task-class routing

Pantheon's LLM configuration is **named endpoints + task-class routes + model profiles**.

- **Saved endpoint** — `{name, base_url, api_type, api_key}` stored once. `api_type` is `openai` (covers OpenAI-compat), `anthropic`, `ollama`, or `custom`. The API key lives in the vault keyed by `llm_endpoint_key__<name>`.
- **Task class** — what KIND of work a call is (`llm_config.models.TASK_CLASSES`): `agent` (tool-calling loop: chat, jobs, bots), `code` (coding_task + code chat turns; inherits agent), `quick` / `long_context` (chat router only; empty = never routed), `extract` (JSON extraction: ingest topics, memory entities, skill scans; inherits summarize), `summarize` (consolidation, notes, reports; inherits agent), `vision`, `image_gen`, `embed` (exactly one model, no fallback), `rerank`.
- **Route** — each class maps to an ordered `[{endpoint, model}, …]` list in vault `llm_routes`: primary first, then fallbacks. `RoutedProvider` (models/provider.py) tries them in order on 404/408/409/425/429/5xx/network errors (streams only fail over before the first chunk) and logs every attempt to `data/db/llm_calls.db` (`llm_config/usage.py`, 30-day retention; `GET /api/llm/usage`).
- **Model profile** — capabilities per `endpoint/model` (tools, vision, image_gen, embedding, tier fast/standard/frontier, context window). Seeded by regex guesses in `llm_config/known_models.py`, user edits stored in vault `llm_model_profiles` win. Used to warn when a route puts a model on a class it can't serve (e.g. a non-tool model on `agent`).

**Call sites ask by class:** `get_provider_for("extract")`, etc. The legacy getters remain as aliases: `get_provider()`→agent, `get_prefill_provider()`→summarize, `get_vision_provider()`→vision, `get_embedding_provider()`→embed, `get_reranker_provider()`→rerank. Pick the class that matches the work when adding a new LLM call. `reset_provider()` clears the per-class cache — every endpoint/route/profile mutation in the API router calls it.

**Migration.** Legacy flat keys → endpoints + 5-role mapping (`llm_config_migrated_v1`), then role mapping → routes once (`llm_routes_migrated_v1`): chat→agent **and** extract (ingest kept on the chat model), prefill→summarize, vision/embed/rerank carried over. The legacy `/api/llm/roles` endpoints still work and write the primary of the mapped class.

**Image generation.** `generate_image` agent tool → `image_gen` class → `ModelProvider.generate_image`, which supports both API styles: OpenAI `POST /images/generations` (`b64_json`/`url`) and chat-completions image mode (`modalities: ["image","text"]` + `image_config`, images in `choices[0].message.images[].image_url.url` — Abacus RouteLLM, OpenRouter, Gemini-style). It tries `/images/generations` first, falls back to chat mode on 400/404/405/422, and remembers the working style per base_url+model. Sizes: the tool accepts WxH, `W:H` or a named preset (`square_hd`, `landscape_16_9`, …); `/images/generations` gets WxH, chat mode gets `aspect_ratio` W:H, and if the backend rejects that with a 400/422 naming presets it retries once with the nearest preset (in `image_size` or `aspect_ratio`, whichever the error names) and remembers it per model (`_IMAGE_WANTS_PRESET`). URLs are fetched via `safe_http_get`. Images are saved as binary artifacts under `<project>/images/generated/<date>/` and the tool result carries `[DISPLAY:artifact://<id>]`, which Chat.jsx renders inline.

**Chat router (per-turn).** `llm_config/router.py` picks the class for each interactive chat turn (`api/chat.py::_stream_turn` → `_route_turn`), before the agent loop — the model never changes inside a tool loop. Rules, in order: session pin (`/model <class|auto>` or the chat input's picker, sent as `model_class`) → image the agent model can't see → history+message over 75% of the agent window (`long_context`) → skill `pantheon.model_class` → code fence/traceback/diff (`code`, sticky for `sticky_turns`) → short message with no tool-intent words (`quick`) → optional LLM classifier on the `extract` class (off by default, 3s cap) → `agent`. A rule only fires when the target class has its **own** route (inherited doesn't count) whose primary is tool-capable, so with nothing extra configured every turn stays on agent. Chat classes: `agent`, `quick`, `code`, `long_context`, `vision`. Pins live in process memory (single-user; reset on restart). Each turn emits a `model_route` WS event; `done` carries `route` incl. `served_model` (via the `models.provider.served_models` ContextVar, so fallbacks show); the route is stored in the assistant message's episodic metadata and logged to `route_decisions` in `llm_calls.db` (`GET /api/llm/router/decisions`). Config: vault `llm_router`, `GET/PUT /api/llm/router`, Settings → **Chat router**. Background jobs never go through the router.

**Agent self-knowledge.** `get_self_documentation` (utils/self_doc.py) renders the live routing table, routing warnings and the last 24h of LLM errors — keep it in sync when routing changes, or the agent will describe stale config.

**Frontend.** Settings: **Endpoints** (cards + add form), **Model routing** (`components/settings/ModelRouting.jsx` — per-class ordered models, capability badges, inline profile editor, warnings) and **Model usage** (`RoutingUsage.jsx`). API client: `llmApi.*`.

**Adding a task class.** Add it to `TASK_CLASSES` + the `TaskClass` literal in `llm_config/models.py`, handle it in `get_provider_for` if it has special semantics, and use `get_provider_for("<class>")` at the call site. The UI renders classes from `/api/llm/task-classes` automatically.

## MCP — protocol version + OAuth + structured outputs

**Protocol version.** `backend/mcp_client/client.py` advertises **`2025-11-25`** in the `initialize` handshake. Whatever version the server echoes back gets stored on `self.negotiated_protocol_version` and sent on every subsequent request as the `MCP-Protocol-Version` header (required by strict servers since spec 2025-06-18). If you bump the constant, also update the docstring at the top of the file.

**OAuth 2.1 (Protected Resource Metadata + DCR + PKCE + OIDC fallback).** Add a connection with `auth_type="oauth2"` and the manager persists a bare config; the user then clicks **Authorize** in the UI, which triggers `POST /api/mcp/connections/{name}/start-oauth`:

1. Unauth probe of the MCP URL — expects 401 with `WWW-Authenticate: Bearer resource_metadata="<url>"`
2. Fetch the PRM document (RFC 9728) for `authorization_servers` + `resource`
3. Fetch AS metadata: tries `/.well-known/oauth-authorization-server` (RFC 8414) first, falls back to `/.well-known/openid-configuration` (added to MCP in spec 2025-11-25 for OIDC providers)
4. Dynamic Client Registration (RFC 7591) — register a public client with `token_endpoint_auth_method=none`, redirect URI `http://localhost:8000/api/mcp/oauth/callback`. The AS may force-downgrade to a confidential client and return a `client_secret`; we honor it and store it in the vault.
5. Build authorize URL with PKCE S256 and the `resource` indicator (RFC 8707); state is stashed in `_pending` in `backend/mcp_client/oauth.py` with 10-min TTL
6. Frontend opens it in a new tab → user signs in → AS redirects to `/api/mcp/oauth/callback?code=…&state=…`
7. Callback handler matches state → exchanges code → persists tokens → reconnects MCP client

The callback path is in `_PUBLIC_PATHS` in `backend/main.py` because external authorization servers obviously can't carry Pantheon's `auth_password` Bearer token. Security comes from the PKCE `code_verifier` + `state` parameter, not Pantheon's auth layer.

Tokens live in the vault keyed by:
- `mcp_oauth_tokens__<name>` — `{access_token, refresh_token, expires_at, token_type, scope, issued_at}`
- `mcp_oauth_client_secret__<name>` — present only if DCR forced a confidential client

Per-connection config gains `auth_type` and an `oauth` block (issuer, token_endpoint, client_id, scopes, resource, registration_endpoint, prm_url). Connection list output includes `oauth_status: "ok" | "needs_auth"` so the frontend can decide whether to show the **Authorize** badge.

**Refresh.** Each OAuth-enabled `MCPClient` receives a `token_getter` async callback. It runs once before every request to populate `Authorization: Bearer …`. On HTTP 401, `_send_jsonrpc` calls the getter with `force_refresh=True` and retries the request once (the loop's `max_attempts` is bumped by 1 when a token_getter is present). The getter also pre-emptively refreshes when `expires_at` is within 60s of now. If refresh fails (no `refresh_token`, or AS rejects), the connection's `oauth_status` flips to `needs_auth` and the user has to click **Re-auth**.

**Structured tool outputs (spec 2025-06-18+).** `MCPClient.call_tool` now returns a dict: `{"text": str, "structured": dict|list|None, "is_error": bool}`. The manager's `_format_tool_result` renders text first, then appends any `structuredContent` inside a `<structured-output>…</structured-output>` block (capped at 50 KB) so the LLM gets both the human-readable summary and the typed payload. `outputSchema` from `tools/list` is preserved on `get_discovered_tools()` for UI inspection but is not forwarded to the LLM (OpenAI's function-call schema has no equivalent slot).

**Adding a connection that needs OAuth.** Frontend Add form has a radio: **API key** vs **OAuth 2.1**. Picking OAuth hides the API-key field; submitting creates the bare config and immediately calls `start-oauth`. After the user authorizes, the connection card shows "OAuth authorized ✓" and the user can run tools as normal.

## Conventions and gotchas

**Storage layers.** Two distinct things, NOT interchangeable:
- **Artifacts** — durable, indexed, searchable. SQLite + blob. Tools: `save_to_artifact`, `read_artifact`, `list_artifacts`, `update_artifact`, `save_transcript_artifact`. Bare paths get auto-prefixed with the project slug.
- **Workspace files** — ephemeral scratch on disk. Tools: `read_file`, `write_file`, `list_workspace_files`. Don't use these for anything you want to keep.

**MCP `save_*` tools are NOT artifact tools.** `mcp_SubDownload_save_to_library` writes to that MCP server's external storage, which Pantheon cannot see. Always use `save_to_artifact` (or `save_transcript_artifact` for video transcripts) for Pantheon persistence.

**Ingest dedup vs save_to_artifact.** `ingest_source` / `registry.ingest`: re-ingesting the same canonical path UPDATES the existing artifact (new version in `artifact_versions`); pass `extras={"force_new": true}` for a separate artifact. `save_to_artifact` is different: an existing path gets a `-1`, `-2`… suffix. Use `update_artifact` to revise in place.

**Graph idempotency.** `graph.add_edge` is idempotent on `(project_id, node_a_id, node_b_id, relationship)`. Re-running the typed-topics extractor doesn't pile parallel edges. Same for `add_node` on `(project_id, label)`.

**HTML→markdown.** `trafilatura.extract(output_format="html")` for cleaned article HTML, then `markdownify` for HTML→markdown. trafilatura's own markdown converter flattens lists into inline prose; markdownify preserves them.

**PDF extraction.** Default is `pdfplumber` (handles tables / structured layouts). `pypdf` is the faster fallback for prose. Image-only / scanned PDFs need OCR which isn't built yet.

**Topic-extraction failures are visible.** Every artifact saved through `registry.ingest()` gets an `extraction_status` block in its frontmatter showing `{strategy, ok, error?, raw_excerpt?, topic_count}`. If topics is empty, look there to see why.

**Path normalization for artifacts.** `save_to_artifact`, `list_artifacts`, `read_artifact`, and `index_artifact` all share the same path normalization: bare folder names get the project slug prepended automatically. Pass `path_prefix='NBJ/'` not `'default-project/NBJ/'`.

**Skill name slugify tolerance.** Both `/content_ingest_graph` and `/content-ingest-graph` resolve to `content-ingest-graph`. Don't worry about which separator the user types.

**Date parsing.** YouTube's MCP returns relative strings like `"4 months ago"`. The YouTube adapter accepts either `extras["published"]` (relative) or `extras["published_at"]` (ISO). When orchestrating an ingest after `mcp_SubDownload_search_youtube`, ALWAYS forward each video's `published` string so paths get real dates instead of `unknown-date/`.

**Job task timeout.** Default is 1800s (30 min) for autonomous_task. Pass `timeout_seconds` on `create_task` for batch ingests that need longer.

**Job terminal states.** The worker runs each handler as a supervised task: user cancel, total timeout, or the watchdog stalling the row all cancel it. A handler returning `{"status": "failed"|"error"}` is recorded FAILED and `{"status": "cancelled"}` CANCELLED — don't return those and expect "completed". `store.complete/fail/mark_cancelled` only transition rows that are still `running`. On worker shutdown the row stays `running` so orphan recovery re-queues it.

**Fire-and-forget tasks.** Use `utils.background.spawn(coro)`, not bare `asyncio.create_task`/`ensure_future` — the loop holds only weak refs.

**Episodic `get_history` returns the newest `limit` messages** (oldest-first order).

**Legacy `/api/settings` LLM keys** are read only by the one-shot migration; once `llm_config_migrated_v1` is set, writes to `llm_*`/`embedding_*`/etc. have no effect. Use `/api/llm/*`.

**Job heartbeats.** The autonomous_task handler emits a heartbeat on every tool call with the current plan step matched. The stall watchdog kills jobs idle for 5 min — the per-step heartbeat keeps it happy.

**parent_session_id.** When `create_task` is called from a chat session, that session_id is captured as `parent_session_id` and threaded through to the autonomous handler. On completion, the handler posts a "Task completed" message into that originating session so the user sees the result where they asked for it.

**DB path canonical location.** ALL SQLite stores live under `settings.db_dir` which resolves to `data/db/`. Don't use relative `data/foo.db` paths — they'll resolve to CWD-relative which on the dev box ends up at `backend/data/`.

**Auth is a random, expiring session in an HttpOnly cookie.** `/api/auth/login` issues a random token (hash stored in `data/db/auth_sessions.db`, `AUTH_SESSION_DAYS`, default 30) and sets the `pantheon_session` cookie (`HttpOnly; SameSite=Strict`). The browser never sees the token; `<img src>`, `<a download>`, `fetch()` and the WebSocket all authenticate via the cookie. API/script clients can send `Authorization: Bearer <token>` instead. Tokens are **never** accepted from the query string — don't add `?token=` support. Changing `AUTH_PASSWORD`/`SECRET_KEY` invalidates all sessions. `password_matches()` is the one password check (login, update gate).

**Vault KDF v2.** `SecretsVault` derives its key with PBKDF2-SHA256 (600k) over the full `VAULT_MASTER_KEY` and a random per-vault salt in `vault_meta`. v1 vaults (truncated key, fixed salt) are re-encrypted in one transaction on first open.

**WebSockets bypass the HTTP auth middleware.** `@app.middleware("http")` never runs for WebSocket scopes. Every WebSocket endpoint must call `api.auth.authorize_websocket(ws)` before `accept()` — it checks the session cookie/Bearer header and rejects cross-site `Origin`s.

**Host-exec tools are gated per context.** `run_command`, `code_execute` and `git_*` (`agent.tools.HOST_EXEC_TOOLS`) are hidden and refused unless `AgentCore(host_exec=True)`. Chat (`api/chat.py`) and `coding_task` pass `host_exec_allowed("interactive")`; autonomous/scheduled/iteration jobs and messaging bots pass `host_exec_allowed("background")`. `AGENT_HOST_EXEC=interactive|always|never` controls it. New `AgentCore` call sites default to no host exec.

**Untrusted names → paths.** Use `utils.paths` (`is_within`, `check_project_id`, `safe_filename`) — never `str(p).startswith(str(base))`. Conversion formats go through `document_converter.validate_format`.

**Outbound fetches go through `utils.net.safe_http_get`.** It blocks non-public addresses on every redirect hop and connects to the IP it validated (Host header + TLS SNI keep the real hostname), so DNS rebinding between check and connect doesn't work. Behind an HTTP(S) proxy it skips pinning (the proxy resolves). Don't add `httpx.get(model_chosen_url, follow_redirects=True)` or `trafilatura.fetch_url`. `ALLOW_PRIVATE_FETCH=true` opts out.

**Browser tools are guarded per request.** `browser_tools._guard_route` is installed on every Playwright context: all requests (subresources, fetch/XHR) to non-public hosts are aborted, and navigations are fetched without following redirects so a redirect to an internal host is refused before it's requested. Tools also re-check the page's final URL before returning content. `BROWSER_EXECUTABLE_PATH` points Playwright at a system Chromium.

**Stall detection needs progress signals.** Agent handlers wrap their run in `pinger_for(ctx, 30, max_quiet=AGENT_MAX_QUIET_SECONDS)` (15 min). The pinger only heartbeats while something calls `utils.progress.report_progress()` (agent rounds/stream chunks/tool results, ingest items, MCP responses) — a hung await goes quiet, the watchdog stalls the row, the worker cancels it. Long-running new code paths under a job should call `report_progress()`.

**Untrusted HTML/SVG rendering.** Workspace HTML renders via `SandboxedHtml` (srcDoc + `sandbox="allow-scripts"`, no `allow-same-origin`, no token in URL). Anything going into `innerHTML`/`dangerouslySetInnerHTML` goes through DOMPurify first.

**Git credentials never go in URLs.** Use `_git_auth_env(token)` (http.extraheader via `GIT_CONFIG_*` env) with the clean `https://github.com/...` URL.

**Messaging bot allowlists are deny-by-default.** Empty `telegram_allowed_chat_ids` / `slack_allowed_channel_ids` / `discord_allowed_guild_ids` / `matrix_allowed_room_ids` / `mattermost_allowed_channel_ids` = nobody.

**MCP OAuth refresh is serialized per connection** (`_REFRESH_LOCKS`); the client passes the rejected token so concurrent 401s refresh once. A refresh rejected by the AS (HTTP 400/401/403, `invalid_grant`) sets `refresh_failed` on the stored tokens → `oauth_status: needs_auth`. A 404 on a request with `Mcp-Session-Id` re-initializes the session and retries once.

**Embedding failures raise.** `ModelProvider.embed` no longer returns a zero vector; callers skip/fall back (semantic search → `[]`, episodic → LIKE search).

**Semantic deletes use metadata filters.** `SemanticMemory.delete_where(where)`; `strip_artifact` matches both `artifact_id` (embedder chunks) and `fm_artifact_id` (FileIndexer chunks). `index_text` deletes a path's old chunks before re-storing.

**Ingest embeds once.** `registry.ingest` relies on `index_artifact`; the generic embedder (`schedule_embed`) is only the fallback when that fails.

**Graph merges are one transaction** (`GraphMemory.merge_nodes`) and drop the absorbed label's topic embedding so it can't be resurrected by the next similarity pass.

**Adapters calling MCP tools** use `mgr.call_tool_raw()` (raw `{text, structured, is_error}`), never `execute_tool()` (LLM-formatted prose).

**Outbound HTTP to LLM/embedding/rerank/MCP endpoints uses `utils.http.pooled_client(timeout=…)`** (one keep-alive pool per event loop, closed on shutdown). Don't reintroduce `async with httpx.AsyncClient()` per request on hot paths.

**Batch embeddings.** `ModelProvider.embed_many` + `SemanticMemory.store_many`; the indexer and artifact embedder store chunks in one call. `embed()` memoizes short texts (queries, topic labels) in a 512-entry LRU.

**Frontend routes and heavy libs are lazy-loaded** (`React.lazy` pages, dynamic `import('mermaid')`, `import('jspdf')`). Keep new heavy deps behind dynamic imports.

**SQLite PRAGMAs.** Every long-lived store routes connections through `apply_sqlite_pragmas(conn)` in `backend/db_utils.py`, which sets `journal_mode=WAL`, `synchronous=NORMAL`, and `foreign_keys=ON`. Don't add a new SQLite store without calling this helper at its `_connect`/`_init_db` site — the WAL setting persists in the DB header but `synchronous=NORMAL` is per-connection and is where most of the write-throughput win comes from.

## Design rationale (decisions outside reviewers often misread)

These are deliberate architectural calls. If a code review recommends reversing one, the burden is on the recommendation, not the code.

**Memory recall is unconditional, not pattern-gated.** Every chat turn runs `mgr.recall` across episodic/semantic/graph. Reviewers sometimes suggest gating it on phrases like "what did we discuss" to save latency — don't. The whole product premise is implicit continuity: the user should be able to refer to last week's work without saying the magic words. Pattern-gating silently breaks the exact cases that matter most. If recall latency is the actual concern, lower `limit_per_tier` or cache within a turn — don't gate on phrasing.

**Skills are markdown recipes, not executable code.** A "skill" is `skill.json` + `instructions.md` that the agent reads as prompt context. There is no executor, no subprocess, no sandbox to harden. Recommendations to add WASM/process isolation for skills are confusing skills with arbitrary user code. See `backend/skills/`.

**No in-app conversation summarization.** Claude/Anthropic harness handles context compaction; the FastAPI app does not maintain its own summary turn. Adding a second layer in `AgentCore.from_session` duplicates work and risks lossy double-summarization. Persistent context lives in episodic memory + the recent-jobs block, not in a synthesized summary.

**Skill resolver is keyword-based, deliberately.** `skills/resolver.py` uses keyword scoring against installed-skill triggers — not per-turn similarity search. The reason: an embedding call per chat turn costs more latency than a static skill list does in tokens, and the keyword resolver gives deterministic, debuggable matches. The available-skills block in the system prompt is the right shape.

**Recent-jobs block is capped low.** `_build_recent_jobs_block` shows the last 5 jobs (24h window) — enough for "you started X earlier" continuity without bloating the system prompt. Don't raise it without a concrete reason; the value is anti-confabulation, not exhaustive history.

**Frontend settings is already componentized.** `frontend/src/components/settings/` contains EndpointCard, AddEndpointForm, EndpointList, ModelRouting, RoutingUsage. Reviewers who recommend "extract settings into separate files" are looking at stale state — verify against the current tree before acting.

**Single-user, single-process.** Pantheon does not have multi-tenant request fan-out, separate workers, or horizontal scaling. APScheduler + JobWorker share the FastAPI process by design. Recommendations that assume Pantheon needs the patterns of a multi-tenant SaaS (request-scoped DB pools, per-tenant isolation, queue/worker split) are misapplying tuatha's architecture here.

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
