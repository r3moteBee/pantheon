# CLAUDE.md — Pantheon agent harness

Working context for coding agents (and people) changing the Pantheon codebase: conventions, gotchas and the decisions behind them. Read it before making changes. The incidents and measurements behind many rules are in [docs/design-notes.md](docs/design-notes.md); user-facing behaviour is in [docs/USAGE.md](docs/USAGE.md).

## What Pantheon is

A single-user agent harness with persistent memory, a source-adapter ingestion pipeline, background jobs and a knowledge-graph substrate for research workflows. A sister/predecessor of `tuatha` (multi-tenant); Pantheon stays single-user and easy to install.

The user (Brent) runs it at `~/pantheon` against a few MCP connectors (YouTube transcripts, GitHub, …) and uses it for thematic / vendor research over time: schedule a daily ingest, let it accumulate, analyze the graph at the end of the week.

## Architecture in three layers

1. **Memory.** `MemoryManager` (`memory/manager.py`) fronts the stores; see "Memory, recall and history". SQLite stores live under `data/db/` (episodic.db, graph.db, file_index.db, artifacts.db, jobs.db, vault.db, …), ChromaDB at `data/chroma/` (or a server via `CHROMA_HOST`), project metadata in `data/db/projects.json`.
2. **Source adapters.** Turn "a URL or video_id" into a markdown artifact with typed-topics frontmatter plus graph nodes/edges. 29 adapters across 10 mechanisms (`youtube`, `blog`, `pdf`, `web`, `forum`, `podcast`, `github`, `cfr`, `malegis`, `sec`) in `backend/sources/adapters/`, self-registered at import. See `backend/sources/SOURCE_ADAPTERS.md`.
3. **Jobs.** `backend/jobs/`: APScheduler fires a schedule → `tasks.scheduler._enqueue_autonomous_job` creates a job row → `JobWorker` (an asyncio task in the FastAPI process) dispatches it to the handler registered for its type. Details in [docs/jobs.md](docs/jobs.md) and "Jobs" below.

## Directory layout

Directories only — read the code for files. [docs/tools.md](docs/tools.md) lists every agent tool.

```
~/pantheon/
├── backend/
│   ├── main.py            FastAPI app; mounts the routers under /api; version from frontend/package.json
│   ├── config.py          Settings (Pydantic); settings.db_dir is canonical
│   ├── db_utils.py        apply_sqlite_pragmas — every SQLite store uses it
│   ├── security_log.py    Security audit log
│   ├── agent/             AgentCore (core.py), tools/, prompts.py, history, context fit, freshness, output filter…
│   ├── api/               One router per area (backend/README.md lists them) + project export/import/purge helpers
│   ├── artifacts/         Artifact store (SQLite + blobs), previews, conversions, embedder
│   ├── builder/           run_build: builds/tests in throwaway pods (PANTHEON_BUILDER=kubernetes)
│   ├── data/              BUNDLED defaults (soul presets in personas/, personality/, migrations). Tracked.
│   ├── integrations/      GitHub API client, server-side artifact push
│   ├── jobs/              JobStore, JobWorker, watchdog, recovery, handlers/ (one per job type)
│   ├── llm_config/        Endpoints, task-class routes, model profiles, probe, chat router, tuning, usage
│   ├── mcp_client/        MCP client (protocol, OAuth, budgets) + connection manager
│   ├── memory/            Episodic, semantic, graph, file index, archival — see memory/README.md
│   ├── messaging/         Gateway + Telegram/Slack/Discord/Matrix/Mattermost adapters
│   ├── models/            ModelProvider / RoutedProvider, model discovery
│   ├── sandbox/           code_execute / run_command sandbox (subprocess or Firecracker; PANTHEON_SANDBOX)
│   ├── secrets/           Encrypted vault (data/db/vault.db)
│   ├── skills/            Skill registry, resolver, editor, importer/exporter, scanner, publisher
│   ├── sources/           Source-adapter registry, extractors, similarity — SOURCE_ADAPTERS.md
│   ├── tasks/             APScheduler integration (scheduler.py)
│   ├── utils/             safe_http_get, http pool, paths, progress, background, chat_settings, user_profile, self_doc…
│   └── tests/integration/ Pytest suite
├── frontend/              Vite + React. package.json "version" is THE app version.
│   └── src/               pages/, components/ (chat/, chat-tabs/, settings/), api/client.js, store/
├── data/                  RUNTIME data, untracked except data/personality/soul.md (see below)
├── deploy/kubernetes/     builder.yaml (locked-down build namespace)
├── docs/                  User/reference docs; docs/archive/ holds old plans and specs
├── scripts/               rotate_vault_key.py, gen_tools_doc.py, one-off migrations
├── skills/                Bundled skills (skill.json + instructions.md)
├── start.sh / stop.sh     Lifecycle scripts
└── deploy.sh / update.sh  Install / pull + rebuild
```

At startup `main.py` copies `backend/data/personality/{soul,agent}.md` into `data/personality/` when a file there is missing or empty, and refreshes an `agent.md` the user never edited from the bundled one (`agent/personality.refresh_default_agent_md`, by known shipped hashes or a hash marker); user edits are kept. `data/personality/agent.md` is not tracked. The agent reads the `data/personality/` copies, which Settings → Personality edits.

## Deploy / build / test workflow

The venv lives at `~/pantheon/.venv`. The user runs deploy commands himself — do NOT ssh and run them.

```bash
cd ~/pantheon && git pull
~/pantheon/.venv/bin/pip install -r backend/requirements.txt   # if deps changed
cd frontend && npm ci && VITE_API_URL="" npm run build && cd ..  # if frontend changed
./stop.sh && pkill -f "uvicorn main:app" 2>/dev/null
find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
./start.sh && sleep 3 && curl -s http://localhost:8000/api/health
```

The curl shows the version string — confirm it changed before declaring a deploy successful.

Use `npm ci` (not `npm install`): it installs exactly what `package-lock.json` pins and never rewrites it, so `git pull` doesn't conflict. It fails if `package.json` and the lockfile disagree — whenever you change a dependency **or bump the version**, run `npm install --package-lock-only` (or `npm install <pkg>`) and commit the lockfile.

```bash
cd ~/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ -v
```

About 700 test functions (704 on 2026-10-21). `conftest.py` lowers the vault KDF iteration count for speed. `test_autonomous_skill_resolution.py` is the canary for the autonomous-task path. Add tests when fixing regressions. No CI: tests run manually.

## Versioning

ONE source of truth: `"version"` in `frontend/package.json`, read by `utils/version.app_version()` (`/api/health`, the FastAPI app version, `get_self_documentation`). Format `YYYY.MM.DD.HXX` (e.g. `2026.05.04.H1`); bump on every push, incrementing the H suffix within a day (history has both H9→Ha and H9→H10; never reuse a string). The user checks it to confirm a deploy picked up new code.

## Source adapters

Pattern `<mechanism>/<genre>` (`youtube/keynote`, `pdf/datasheet`, `blog/announcement`). Each `sources/base.py::SourceAdapter` declares `source_type` (unique), `display_name`, `bucket_aliases`, `requires_mcp` (validated when scheduled tasks fire), `artifact_path_template`, `extractor_strategy` (default `llm_default`), `auto_extract` (default True) and `auto_link_similarity` (default False).

`registry.ingest(IngestRequest)`: fetch → extract topics → save artifact → `index_artifact` (chunks + embeds the body, builds the typed-topics graph) → optional similarity. `batch_ingest(reqs)` isolates per-item failures. Extractors (`sources/extraction.py`): `llm_default`, `llm_announcement`, `llm_structured_specs`, `llm_research_paper`, `llm_changelog`, `noop`.

- **Ingest dedup vs save_to_artifact.** Re-ingesting the same canonical path UPDATES the artifact (new version); `extras={"force_new": true}` makes a separate one. `save_to_artifact` on an existing path makes a `-1`, `-2`… copy unless `overwrite=true`; `update_artifact` revises in place.
- **Ingest embeds once.** `registry.ingest` relies on `index_artifact`; the generic embedder (`artifacts/embedder.schedule_embed`) is only the fallback.
- **Extraction failures are visible** in each ingested artifact's `extraction_status` frontmatter (`{strategy, ok, error?, raw_excerpt?, topic_count}`).
- **Dates.** The YouTube MCP returns relative strings ("4 months ago"). The adapter takes `extras["published"]` (relative) or `extras["published_at"]` (ISO, wins). Always forward each video's `published` from `search_youtube`, or paths land in `unknown-date/`.
- **HTML→markdown:** trafilatura (`output_format="html"`) then `markdownify` (`sources/util.py`); trafilatura's own markdown flattens lists.
- **PDF:** `pdfplumber` by default, `pypdf` fallback. The PDF adapter has no OCR: under 100 characters of text it fails with "may be image-only / scanned". Workspace indexing and `read_file` do fall back to the vision model for blank pages (`memory/file_indexer._ocr_pdf_pages`).
- **Adapters calling MCP tools** use `mgr.call_tool_raw()` (raw `{text, structured, is_error}`), never `execute_tool()`, and find the tool with `MCPManager.find_tool`.

## Skills vs scheduled tasks

Different things; the agent must not confuse them, and the system prompt keeps them apart ("Skills vs scheduled tasks" in `agent/prompts.py`). Don't merge them.

- **Skill** — slug + `instructions.md`, prompt context only. Invoked with `/skill-name` in chat or `skill_name` on `create_task`. Created with `create_skill`.
- **Scheduled task** — a one-shot or recurring run (`now`, `delay:N`, `interval:N` minutes, or 5-field cron), optionally bound to a skill. Created with `create_task`.

The autonomous_task handler resolves `payload.skill_name` with underscore↔hyphen tolerance, checks `requires_mcp` against the live MCP manager (`missing_tools`) and fails fast when tools are offline instead of running a doomed loop.

## Jobs

- **Types and default timeouts** (`@register` in `jobs/handlers/`): `autonomous_task` 1800 s, `coding_task` 1800 s, `iteration_loop` 7200 s, `autoresearch` 7200 s, `research_batch` 14400 s, `image_extraction` 300 s. `create_task` takes `timeout_seconds`. `POST /api/jobs` accepts only `CREATABLE_JOB_TYPES` (autonomous_task, iteration_loop, coding_task); `research_batch` comes from `create_task`, `autoresearch` from the web-chat `start_autoresearch` tool, `image_extraction` from chat uploads. Memory extraction and file indexing run inline, not as jobs.
- **Approval.** `create_task` needs a plan and lands paused with `plan_status="proposed"` unless `skip_review=true` in an interactive turn; jobs and bots can never skip review. `create_task(job_type="coding_task")` has no proposal state, so it is refused outside interactive chat.
- **Terminal states.** The worker runs each handler as a supervised task; user cancel, total timeout or a watchdog stall cancels it. A handler returning `{"status": "failed"|"error"}` is recorded FAILED and `{"status": "cancelled"}` CANCELLED — don't return those and expect "completed". `store.complete/fail/mark_cancelled` only transition rows still `running`. On worker shutdown the row stays `running` so orphan recovery re-queues it.
- **Stall detection needs progress signals.** The watchdog stalls a running job after 300 s without a heartbeat (`JOB_STALL_TIMEOUT_SECONDS`). Agent handlers wrap their run in `pinger_for(ctx, 30, max_quiet=AGENT_MAX_QUIET_SECONDS)` (900 s), which only heartbeats while something calls `utils.progress.report_progress()` (agent rounds, stream chunks, tool results, ingest items, MCP responses) or `ctx.heartbeat()`. A hung await goes quiet and gets stalled. New long-running code under a job must call `report_progress()`.
- **Concurrency.** `JOB_WORKER_CONCURRENCY` (default 2). `REPO_JOB_TYPES` (`coding_task`, `iteration_loop`) never run two at a time per project (`claim_next(exclusive_types=…, busy_projects=…)`).
- **parent_session_id.** `create_task` from chat records the session; the handler posts the result (or an ABORTED notice after a provider error) back into it.
- **Retired `scheduled_job`.** Gone; `tasks.scheduler._enqueue_scheduled_job` survives only as a logging no-op so APScheduler jobs persisted by older builds still load.
- **Fire-and-forget tasks.** Use `utils.background.spawn(coro)`, not bare `asyncio.create_task`/`ensure_future` — the loop holds only weak refs.

## LLM endpoints + task-class routing

Configuration is **named endpoints + task-class routes + model profiles** (`llm_config/`, API `/api/llm/*`).

- **Endpoint** — `{name, base_url, api_type, api_key}`; `api_type` is `openai` (any OpenAI-compatible), `anthropic`, `ollama` or `custom`. Key in the vault as `llm_endpoint_key__<name>`.
- **Task class** (`llm_config.models.TASK_CLASSES`): `agent` (tool loop: chat, jobs, bots), `code` (inherits agent), `quick` / `long_context` (chat router only; empty = never routed), `extract` (JSON extraction; inherits summarize), `summarize` (inherits agent), `vision`, `image_gen`, `embed` (exactly one model), `rerank`.
- **Route** — per class, an ordered `[{endpoint, model}, …]` in vault `llm_routes`. `RoutedProvider` fails over on 404/408/409/425/429/5xx/network errors (streams only before the first chunk) and logs every attempt to `data/db/llm_calls.db` (30-day retention; `GET /api/llm/usage`).
- **Model profile** — capabilities per `endpoint/model`. Precedence: user edits (vault `llm_model_profiles`) → what the endpoint advertises (vault `llm_model_advertised`, from `store.refresh_advertised()` at startup and the Probe button; parsed by `probe.capabilities_from_entry`) → regex guesses in `llm_config/known_models.py`. Used to warn when a route puts a model on a class it can't serve.
- **Call sites ask by class:** `get_provider_for("extract")`. Legacy getters remain as aliases (`get_provider()`→agent, `get_prefill_provider()`→summarize, `get_vision_provider()`, `get_embedding_provider()`, `get_reranker_provider()`). `reset_provider()` clears the per-class cache; every endpoint/route/profile mutation in the API calls it, and cached providers are rechecked against the stored route every `LLM_ROUTE_RECHECK_SECONDS` (10).
- **Adding a task class:** add it to `TASK_CLASSES` + the `TaskClass` literal, handle special semantics in `get_provider_for`, call `get_provider_for("<class>")`. The UI renders classes from `/api/llm/task-classes`.
- **Legacy keys.** Flat keys → endpoints + 5-role mapping (`llm_config_migrated_v1`) → routes (`llm_routes_migrated_v1`; chat→agent **and** extract, prefill→summarize). `/api/llm/roles` still works. Legacy vault keys are read only by `llm_config/migration.py`; `/api/settings` no longer accepts or returns them.
- **Image generation/editing.** `generate_image` → `image_gen` class → `ModelProvider.generate_image`: tries OpenAI `POST /images/generations` first, falls back to chat-completions image mode (`modalities`, `image_config`; images in `choices[0].message.images[]`) on 400/404/405/422, and remembers the working style per base_url+model. Sizes accept WxH, `W:H` or a preset; a 400/422 naming presets retries once with the nearest one (`_IMAGE_WANTS_PRESET`). `source_image` edits: chat mode adds an `image_url` part, images mode uses `POST /images/edits`. Output is saved under `<project>/images/generated/<date>/` and the result carries `[DISPLAY:artifact://<id>]` (rendered by `components/chat/ToolCallBlock.jsx`).
- **Chat router (per turn).** `llm_config/router.py` picks the class for each interactive chat turn before the agent loop (`api/chat.py::_route_turn` → `router.decide`); the model never changes inside a tool loop, and background jobs never go through the router. Rules in order: session pin (`/model <class|auto>` or the picker's `model_class`) → image the agent model can't see → history+message over 75% of the agent window (`long_context`) → skill `pantheon.model_class` → code fence/traceback/diff (`code`, sticky for `sticky_turns`, default 3) → time-sensitive or follow-up-of-fresh (`fresh` → agent) → short, non-action, non-push-back message (`quick`; `_TOOL_INTENT_RE` + `_NEEDS_AGENT_RE`) → optional LLM classifier (off, 3 s cap) → `agent`. A rule fires only when the target class has its **own** route whose primary is tool-capable, so with nothing extra configured every turn stays on agent. Pins live in process memory. Each turn emits `model_route`; `done` carries `route` incl. `served_model`; decisions are logged to `route_decisions` in `llm_calls.db` (`GET /api/llm/router/decisions`). Config: vault `llm_router`, `GET/PUT /api/llm/router`. Why `quick` is scoped the way it is: design notes.
- **Routing tuning.** Each routed turn records its outcome on its `route_decisions` row (latency, iterations, tool calls/errors via `tool_results.is_error`, truncated, stream_error, `corrected`, 👍/👎 `rating`). `llm_config/tuning.py`: `stats()`, `recommendations()` (need `MIN_TURNS` 15 / `MIN_CALLS` 20), `apply_recommendation(id)` (never automatic), `simulate(patch)` (replays recent chat messages through `decide()`, no LLM calls). API `GET /api/llm/router/tuning`, `POST /api/llm/router/{simulate,apply,feedback}`. Warn/info suggestions also appear in self-doc.
- **Agent self-knowledge.** `get_self_documentation` (utils/self_doc.py) = a short "How Pantheon works" guide filled with this instance's real numbers (MAX_TOOL_ITERATIONS, AGENT_WEB_BUDGET, TOOL_RESULT_MAX_CHARS, LLM_MAX_TOKENS, sandbox mode, build toolchains) + the CALLING context (interactive or not, host exec, builder, browser, repo binding, MCP tools) + instance state (routing with local/external endpoints, routing warnings, 24h LLM error counts, tuning suggestions, search chain, MCP, messaging, skills, version, an allowlist of settings). Secrets never appear: settings only from `SETTINGS_SHOWN` (an allowlist — the old substring mask printed the first/last 4 chars of vault_master_key / secret_key / llm_api_key), URLs reduced to their host, no provider error texts. The tool runs it with `asyncio.to_thread`. AgentCore reads it before round 1 for self-description questions and short capability questions (`freshness.asks_about_capabilities`: "Can you compile Swift?", "Where do uploads go?", "What's the most tool steps you can take?") — left to the model it read it 5 times in 40 such questions and answered from general LLM knowledge. When you change a limit, a gate, routing or where things are stored, update the guide text in `_guide()` (tests in test_self_doc.py / test_self_knowledge_texts.py).
- **Frontend.** Settings → LLMs (`components/settings/LlmSettings.jsx`): Endpoints, Model routing, Chat router, Model usage, Routing tuning. API client `llmApi.*`.

## Agent loop: tools, thinking and grounding

- **Tool dispatch is a registry.** `agent/tools/__init__.py::execute_tool` applies the host-exec gate, routes `mcp_*` to the MCP manager, then `registry.resolve(name)` (exact names, then the `browser_`/`github_`/`git_` prefixes) with a `ToolContext`. Unknown names return `Unknown tool: …`; exceptions return `Error executing …`. Workspace/git helpers live in `agent/tools/workspace.py` and handlers call them as `_ws.<name>`, so tests patch `agent.tools.workspace.<name>`. A test asserts every schema has a handler and vice versa.
- **Consolidated tools; old names are hidden aliases.** The model sees the built-in tools in `_ORDER` (49 today; `docs/tools.md` is the generated list), plus browser tools when enabled and MCP tools. `github(action=…)`, `merge_topics(action=…)`, `index(target=artifact|workspace)`, `create_task(job_type="coding_task")`, `batch_convert_documents` and `ingest_source` replace older tools. Retired names are in `LEGACY_TOOLS`: their handlers stay registered (skills, plans and job prompts that name them keep working) but their schemas aren't sent. A test keeps them out of the prompt.
- **Host-exec tools are gated per context.** `agent.tools.HOST_EXEC_TOOLS` = `run_command`, `code_execute`, the six `git_*` tools and `start_autoresearch` (it queues a job that runs a benchmark on the host). They are hidden unless `AgentCore(host_exec=True)`, and `execute_tool(..., host_exec=False)` refuses them (tests calling them directly pass `host_exec=True`). Chat and `coding_task` pass `host_exec_allowed("interactive")`; autonomous/iteration jobs and bots pass `host_exec_allowed("background")`. `AGENT_HOST_EXEC=interactive|always|never` (default interactive). New `AgentCore` call sites default to no host exec. `build_system_prompt(host_exec=…)` shows the local git/run_command protocol only where those tools exist.
- **Bad tool arguments aren't run.** `models.provider.parse_tool_args` marks non-JSON/non-object arguments with `args_error`; AgentCore returns an error to the model instead of calling the tool with `{}`. A call cut off by the output cap gets `CUT_OFF_ARGS_ERROR` (split it into smaller calls).
- **Textual tool calls are recovered** (`agent/text_tool_calls.py`): ReAct, `name`/`arguments`, `<tool_call>` tags, fenced JSON, Gemma `tool_code`, Qwen3 XML — when the WHOLE reply is calls to known tools. Streamed replies starting with `{`, `[`, ``` or `<tool_call` are held until the round ends.
- **Tool results are capped for the model, not the UI.** `agent/tool_results.cap` trims to `TOOL_RESULT_MAX_CHARS` (24000; head + tail); the `tool_result` event keeps the full text. `tool_results.is_error` is the one failure check — reuse it.
- **A turn's tool loop stays inside the context window** (`agent/context_fit.py`). Before every round (and the finalize rounds) `fit_messages` estimates the prompt (4 chars/token, plus tool schemas, minus a reply reserve and margin) against the agent model's profile `context_window` (nothing happens when unknown) and shrinks OLDER tool results first: head+tail excerpt (6000, then 2000 chars), then a stub; the two newest are only excerpted (floor 1000). Messages are replaced, never removed, so every call keeps a result. A real overflow (`context_overflow()`) recalibrates and retries once. Tests: `test_context_fit.py`.
- **Output cap and endings are reported, not silent.** `LLM_MAX_TOKENS` (16384) is the cap; streams ask for usage (`LLM_STREAM_USAGE`) and `_effective_finish` reports `length` / `empty`. AgentCore nudges once on an empty reply mid-task (`EMPTY_REPLY_NOTICE`), shows `EMPTY_REPLY_NOTE` otherwise, ends a length-stopped reply with `CUT_OFF_NOTE`, and a run that hits `MAX_TOOL_ITERATIONS` (100) with work pending ends with `STEP_LIMIT_NOTE`. Tests: `test_output_limits.py`.
- **`web_fetch` reads a page without keeping it** (`safe_http_get` → trafilatura → markdown, `max_chars` 20000, ≤ 60000; `compact_links` drops link URLs from long pages); `ingest_source` keeps sources. The bundled agent guide is `backend/data/personality/agent.md` (short; detailed rules live in `agent/prompts.py`) — keep tool names in it real (a test checks).
- **Thinking is opt-in (`AGENT_THINKING`, default off).** It sends `chat_template_kwargs: {"enable_thinking": true}` on agent-class rounds only, via `extra_body` (may set request options like `tool_choice`, never `model`/`messages`/`stream`/`tools`; only passed when set, so fakes without the parameter keep working). Providers return `reasoning_content` as `reasoning`. A round with no tool calls and no text but some reasoning gets `_finalize_from_reasoning`: one more call, thinking off, `tool_choice: "none"`, same tools (for the KV cache). Thinking stops once the turn has tool results (`AGENT_THINKING_AFTER_TOOLS`, default off), except round 1 after a pre-search.
- **Time-sensitive questions are looked up (`agent/freshness.py`, `AGENT_FORCE_SEARCH`, default on).** When `needs_fresh_facts` matches, a round 1 that made no web call has its text dropped and a `web_search` for the user's words added. `unknown_entities` (capitalised/camel-case names that aren't Pantheon's own tools or skills) adds "What is X?". With `AGENT_PRE_SEARCH` (default on) that search runs before round 1; pasted URLs (up to 3, outside code fences) are fetched first instead (`AGENT_PREFETCH_URLS`); non-version time-sensitive queries get the month and year appended. `about_self` questions are never forced to the web; `wants_self_description` calls `get_self_documentation` before round 1. Short follow-ups inherit the lookup (`followup_needs_fresh`), long instruction-style messages get a model-written query (`_standalone_query`, dated). In interactive chat `AGENT_WEB_BUDGET` (12) caps web calls per turn. Don't use `tool_choice: "required"` for any of this (llama.cpp ignores it with Qwen templates).
- **Forced-search precision** (`freshness.py`; eval in docs/design-notes.md). `needs_fresh_facts` is suppressed in a coding context (`coding_context`, unless it asks for a latest/current version or release), for the user's own past and things and creative writing (`personal_context`), and for general explanations / word problems without an explicit time word. "still"/"anymore" count only next to an office/status word; `won` doesn't match "won't" (also in the router's `_NEEDS_AGENT_RE`). `unknown_entities` skips `WELL_KNOWN` long-established names unless versioned ("Python 3.15"). A new trigger word must come with a negative example in `tests/integration/test_freshness.py`.
- **Surveys become research tasks.** `is_survey_request` ("5 states at a time", "all 50 states") gets `SURVEY_NOTE`, a 4-lookup budget and a wrap-up round where only `create_task` may run; if the model still only describes the task, `_propose_research_batch` files `create_task(job_type="research_batch")` itself. The `research_batch` handler runs each item in a fresh `AgentCore(only_tools={"web_search","web_fetch"}, memory_manager=None)` and itself saves `<project>/research/<topic>/<item>.md`, then a no-tool summary turn writes `summary.md`.
- **Version questions get release tables** (`agent/release_facts.py`): a version-intent `web_search` naming an endoflife.date product gets that product's table prepended (cached; `SEARCH_RELEASE_FACTS=false` disables; it sends the product name out), else `github_release_facts` appends the most-starred matching repo's newest **stable** release (unauthenticated by default, optional `GITHUB_TOKEN`).
- **Answers built on web results end with sources** (`agent/sources.py`, `ANSWER_SOURCES`): if the answer cites no URL, up to 2 result URLs whose text contains the answer's HEADLINE fact are appended.
- **Outside content is fenced data** (`tool_results.for_model`). Web/browser/MCP results (`UNTRUSTED_PREFIXES`) reach the model inside `<untrusted_content tool="…">` with chat-template control tokens (`<|im_start|>`, `[INST]`, …) defused first.
- **Remote images in replies are shown as text** (`agent/output_filter.py`): `AgentCore.chat` filters every `text_delta`, `full_response` and the working-memory copy so `![alt](http…)` becomes "*(image not shown …)*" unless the user's own message contained the URL.

## MCP — protocol, OAuth, budgets

- **Protocol.** `mcp_client/client.py` advertises `MCP_PROTOCOL_VERSION = "2025-11-25"`, stores the version the server echoes (`negotiated_protocol_version`) and sends it as `MCP-Protocol-Version` on every request. If you bump the constant, update the docstring at the top of the file. A 404 with `Mcp-Session-Id` re-initializes once.
- **OAuth 2.1.** `auth_type="oauth2"` connections authorize via `POST /api/mcp/connections/{name}/start-oauth`: unauth probe (401 + `resource_metadata`) → PRM (RFC 9728) → AS metadata (RFC 8414, falling back to OIDC discovery) → DCR (RFC 7591, public client, redirect `http://localhost:8000/api/mcp/oauth/callback`; a forced confidential client's secret goes to the vault) → authorize URL with PKCE S256 + `resource` (RFC 8707), state in `oauth._pending` (10-min TTL) → `/api/mcp/oauth/callback` exchanges the code and reconnects. The callback is in `_PUBLIC_PATHS`; PKCE + `state` protect it. Vault keys: `mcp_oauth_tokens__<name>` (`{access_token, refresh_token, expires_at, …}`, + `refresh_failed`), `mcp_oauth_client_secret__<name>`. The connection config carries `auth_type` and an `oauth` block (issuer, token_endpoint, client_id, scopes, resource, registration_endpoint, prm_url); connections report `oauth_status: "ok" | "needs_auth"`.
- **Refresh.** `manager._make_oauth_token_getter` refreshes within 60 s of expiry; on 401 `_send_jsonrpc` force-refreshes with the rejected token and retries once. Refreshes are serialized per connection (`_REFRESH_LOCKS`) and rotation-safe. A refresh the AS rejects (400/401/403, `invalid_grant`), or an expired token without `refresh_token`, sets `needs_auth`; network errors leave tokens alone.
- **Structured outputs.** `MCPClient.call_tool` returns `{"text", "structured", "is_error"}`; `_format_tool_result` appends `structuredContent` in a `<structured-output>` block (50 KB cap). `outputSchema` is kept for the UI, not sent to the LLM.
- **Names.** Always build MCP tool names with `mcp_client.client.tool_function_name` (`mcp_<conn>_<tool>`, hashed past 64 chars); `resolve_tool_call` reverses it. Find tools by what they do with `MCPManager.find_tool` (pattern, bare name, or a prefixed name from a renamed connection); `missing_tools` checks `requires_mcp` the same way.
- **MCP `save_*` tools are NOT artifact tools** — they write to the MCP server's own storage. Pantheon persistence is `save_to_artifact` / `ingest_source`.
- **Budgets** (`mcp_client/budget.py`, `data/db/mcp_usage.db`). Every MCP call is metered; `cfg["budget"] = {daily, monthly, costs?, preset?}` (0 = unlimited), pacing note past 80%, 1 per call unless a preset (`tavily`) or `costs` rule says otherwise. Over the limit the call is refused and search tools with a `query` fall back to `web_search`; `call_tool_raw` raises. API `GET/PUT /api/mcp/connections/{name}/budget` (for Tavily GET also returns the service's own `/usage`), `POST …/budget/reset`. `budget.migrate_tavily` ran once (vault `mcp_budget_migrated_v1`) to move the old `tavily_*_limit` keys onto the Tavily connection. MCP servers are enabled globally (per-project enablement was removed).

## Security conventions

Full list in [docs/security.md](docs/security.md). The rules code changes most often trip over:

- **Auth is a random, expiring session in an HttpOnly cookie** (`pantheon_session`, `SameSite=Strict`; hash in `data/db/auth_sessions.db`, `AUTH_SESSION_DAYS` 30) or `Authorization: Bearer`. Tokens are **never** accepted from the query string — don't add `?token=`. `password_matches()` is the one password check. With `AUTH_PASSWORD` empty only IP literals, single-label and private-TLD hosts or `ALLOWED_HOSTS` are served (421 otherwise).
- **WebSockets bypass the HTTP middleware.** Every WebSocket endpoint calls `api.auth.authorize_websocket(ws)` before `accept()`.
- **Public default secrets → loopback only** (`insecure_defaults()`, `remote_blocked`; `ALLOW_INSECURE_DEFAULTS=true` overrides). Change the vault key only with `scripts/rotate_vault_key.py`. Vault KDF v2: PBKDF2-SHA256 (600k) over the full key + per-vault salt; v1 vaults are re-encrypted on first open. Changing the KDF without a migration makes every secret unreadable.
- **Secrets never follow user-set URLs by name.** Search providers may only reference `*_api_key` / `search_key__*` keys (`is_search_key_name`; LLM keys excluded).
- **Untrusted names → paths** only through `utils.paths` (`is_within`, `check_project_id` → 400, `safe_filename`) — never `str(p).startswith(str(base))`. Conversion formats go through `document_converter.validate_format`.
- **Outbound fetches go through `utils.net.safe_http_get`** (blocks non-public addresses on every redirect hop, pins the validated IP). Don't add `httpx.get(model_chosen_url, follow_redirects=True)` or `trafilatura.fetch_url`. `ALLOW_PRIVATE_FETCH=true` opts out.
- **Browser tools** are guarded per request (`browser_tools._guard_route`); `BROWSER_ENABLED`, `BROWSER_EXECUTABLE_PATH`.
- **Untrusted HTML/SVG:** workspace HTML renders in `SandboxedHtml` (no `allow-same-origin`); anything for `innerHTML`/`dangerouslySetInnerHTML` goes through DOMPurify.
- **Git credentials never go in URLs** — `_git_auth_env(token)` (`http.extraheader` via `GIT_CONFIG_*`).
- **Skills are scanned before they reach the prompt** (static layers at registry load, full scan in `create_skill`, `scanner.INSTRUCTION_PATTERNS` for injection phrasing).
- **Messaging allowlists are deny-by-default** — empty = nobody.

## Other conventions and gotchas

- **Artifacts vs workspace.** Not interchangeable:
  - **Artifacts** — durable, versioned, embedded for recall, downloadable, buildable (`run_build`) and pushable to GitHub. Tools: `save_to_artifact`, `read_artifact`, `list_artifacts`, `update_artifact`, `delete_artifact` (sources: `ingest_source`). Chat uploads (`POST /api/chat/attach`) land here too. `save_to_artifact`, `list_artifacts`, `read_artifact` and `index(target="artifact")` share one path normalization: bare folder names get the project slug prepended — pass `path_prefix='research/'`, not `'default-project/research/'`.
  - **Workspace** — files on disk (`data/workspace` for the default project, `data/projects/<id>/workspace` otherwise) for `read_file`, `write_file`, `run_command`, `code_execute`, `download_file`, the repo checkout and bot `/note` captures. Not indexed unless someone runs `index(target="workspace")`. Deliverables (code, documents the user asked for) belong in artifacts: `write_file` mirrors a code/document file outside scratch folders (`tmp/`, `build/`, hidden, `repos/`, …) to an artifact at the same path (`files._mirror_to_artifact`), `create_task` rewrites plan steps that write deliverables with `write_file` (`tasks.fix_deliverable_steps`, "PLAN ADJUSTED"), and `POST /api/files/to-artifacts` copies a workspace folder over.
- **DB paths.** ALL SQLite stores live under `settings.db_dir` (`data/db/`). Relative `data/foo.db` paths resolve against CWD, which on the dev box is `backend/data/`.
- **SQLite PRAGMAs.** Every long-lived store calls `apply_sqlite_pragmas(conn)` (`db_utils.py`: WAL, `synchronous=NORMAL`, `foreign_keys=ON`) at its `_connect`/`_init_db`. `synchronous=NORMAL` is per-connection, so a new store that skips the helper loses most of the write-throughput win.
- **Project delete and export cover every per-project store** (`api/project_purge.py`). `inventory()` counts and `purge()` removes episodic tables, graph, artifacts + versions + previews (blobs only when unreferenced), file index, merge proposals, phase_g tables, the sources.db repo binding, jobs (409 while one runs; queued ones are cancelled), APScheduler jobs, both Chroma collections (by collection name; a local dir only when empty, because project `episodic-foo`'s semantic dir is project `foo`'s episodic dir), project files, vault keys and skill state. `GET /projects/{id}/delete-preview` feeds `DeleteProjectModal`. **A new per-project store must be added to project_purge.py and to export/import.** Import gives artifacts fresh ids, checks blob sha256, skips existing paths, and replaces row ids (and chat session ids) another project owns. The default project's workspace is `data/workspace` (`project_export.workspace_dir`).
- **Store-owned SQL.** Code that needs another component's data calls that store's method (`EpisodicMemory.set_conversation_title` / `merge_conversation_metadata` / `delete_conversation` / `project_stats`, `GraphMemory.edges_along_path` / `count_nodes`), never its DB file. Export and self-doc open DBs read-only (`project_export._connect_ro`). Runtime-setting readers live in `utils/runtime_settings.py`, not in routers.
- **Config.** `config.Settings` fields map to upper-cased env vars. `CHROMA_HOST` empty = embedded Chroma. `LLM_*`, `EMBEDDING_MODEL`, `EMBEDDING_BASE_URL`/`EMBEDDING_API_KEY` are bootstrap fallbacks used only when no route exists (`_fallback_embedder` never sends the LLM key to the embedding host). Job/browser/sandbox/builder knobs (`JOB_*`, `BROWSER_*`, `PANTHEON_SANDBOX`, `FC_*`, `PANTHEON_BUILDER`, `BUILDER_*`, `TOOL_RESULT_MAX_CHARS`, `MAX_TOOL_ITERATIONS`) are read with `os.getenv`, not Settings.
- **Outbound HTTP to LLM/embedding/rerank/MCP endpoints uses `utils.http.pooled_client(timeout=…)`** (one keep-alive pool per event loop). Don't reintroduce `async with httpx.AsyncClient()` per request on hot paths.
- **Chat settings: one read path** (`utils/chat_settings.py`). `tone_weight` (minimal|balanced|strong; global vault `personality_weight`), `context_focus` (broad|balanced|focused), `memory_recall` (global `memory_recall_enabled`): per-project override in phase_g.db `project_settings` (NULL = inherit) over the global vault values. `skill_discovery` (off|suggest|auto) stays in vault `skill_discovery_<project>`. `AgentCore.chat` reads `effective(project_id)`; the UI writes `PUT /api/projects/{id}/settings` (`null` clears) and never keeps its own copy.
- **About you: one read/write path** (`utils/user_profile.py`, `data/db/user_profile.db`, global). `about_you` text, `reply_length` (short|default|detailed) and `formatting` (auto|markdown|plain), each `{"global", "channels"}` per channel (`CHANNELS`: web + the five adapters), plus writing voices. `build_system_prompt(channel=…)` puts `render_profile_section` right after agent.md and `render_reply_style(channel)` LAST; both change only when the user edits Settings, so the prefix stays cacheable. Chat passes `channel="web"`, each adapter its platform name, jobs nothing (no reply style; a test checks every entry point). `formatting` applies to the messaging apps only; `auto` = plain text where the app doesn't render Markdown (`RENDERS_MARKDOWN`), and `run_autonomous` strips leftover Markdown (`utils/plain_text.markdown_to_plain`). Non-default styles are repeated next to the question (`reply_style_note`). Voices are read only through the `writing_voice` tool, never pasted into the prompt. UI Settings → About you; API `/api/about-you`, `/api/writing-voices`.
- **Personality presets (was "personas").** A preset is only a `soul.md` voice (`backend/data/personas/` or `data/personas/`; `/api/personas`). `POST /personas/{id}/apply/{project}` writes `personality.with_commitments(soul)` (appends the global Key Commitments). `POST /personality/reset?project_id=` drops a project's override (without it, restores the bundled files). Projects follow global by default. Group chat (`active_personas`) and `custom_soul` are gone; there is no Personas page (`/personas` redirects).
- **Firecracker mounts the workspace.** `SandboxConfig.workspace_dir` becomes an ext4 disk at `/workspace` and is mirrored back after the run; exit codes come from a console marker (`_parse_console`).
- **Frontend conventions.**
  - **Markdown:** always `components/Markdown.jsx` (remark-gfm + mermaid); pass a `components` override, never configure ReactMarkdown directly.
  - **API errors:** the axios interceptor rejects with an `Error` whose `.message` is the readable API error (plus `.status`, `.data`, `.response`). Use `e.message`.
  - **Notifications:** `addNotification({type, message})` toasts, not `alert()`.
  - **Colors:** `brand` and the in-between grays (250/350/750/850) are defined in `tailwind.config.js`; an undefined step silently generates no CSS.
  - **Links:** router `<Link>` for internal links; a raw `<a href>` reloads the page and drops the chat WebSocket.
  - **Lazy loading:** pages are `React.lazy`; heavy libs via dynamic `import()`.

## Design rationale (decisions outside reviewers often misread)

Deliberate calls. If a review recommends reversing one, the burden is on the recommendation.

- **Memory recall is not pattern-gated.** Every chat turn recalls (unless `memory_recall` is off for the project), filtered by relevance, not phrasing. The product premise is implicit continuity; gating on "what did we discuss" silently breaks the cases that matter. For latency, lower `limit_per_tier` or `PRE_RECALL_TIMEOUT_SECONDS` / `RERANK_TIMEOUT_SECONDS`.
- **Skills are markdown recipes, not executable code.** `skill.json` + `instructions.md`, read as prompt context. No skill executor, no subprocess; `backend/sandbox/` isolates `code_execute`, not skills. Imported skills are scanned because their text becomes prompt.
- **No in-app conversation summarization.** History is trimmed by token budget in whole blocks (`agent/history.py`) and dropped turns come back through recall. A synthesized summary turn would rewrite the prompt prefix and risk lossy double-summarization. `consolidate_memory` is the explicit path into semantic/graph.
- **Skill resolver is keyword-based** (`skills/resolver.py`): an embedding call per turn costs more latency than a static skill list costs in tokens, and keyword matches are deterministic and debuggable.
- **Recent-jobs block is capped low** (`_build_recent_jobs_block`: last 5 jobs, 24 h). Its value is anti-confabulation, not history. It is appended last in the system prompt because it changes with job state.
- **Builds run outside Pantheon** (`builder/`, `agent/tools/build.py`, [docs/builds.md](docs/builds.md)). `run_build` sends a folder of artifacts to a throwaway pod with no network and returns PASSED/FAILED, error lines and a log excerpt. Kubernetes is the only backend; a build is refused unless a NetworkPolicy in `BUILDER_NAMESPACE` selects every pod and allows no egress, and `deploy/kubernetes/builder.yaml` locks the namespace down on its own because anything in Pantheon's pod can use its token. Hidden unless `PANTHEON_BUILDER` is set. Swift is Swift-on-Linux (no SwiftUI/UIKit). Tests: `test_builder.py`.
- **Artifacts push to GitHub server-side** (`integrations/github_push.py`, `api/github_push.py`, `PushToGitHubModal.jsx`). One commit through the Git Data API from the store: blobs whose git sha already match are skipped (an identical push makes no commit and reuses the open PR), default branch only with `allow_default_branch`, `mirror` deletes only under a non-empty `dest_dir`, credential-looking files need `confirm_sensitive` (409 otherwise), `artifact_pushes` (sources.db) records the last push. The agent's `github(action="push_artifacts")` is interactive-only, bound repo only, new branch + PR, no mirror, refuses credential-looking files. Tests: `test_github_push.py`.
- **Deliverables are artifacts, not workspace files** (see "Artifacts vs workspace"). The bundled `ios-app` skill lays an app out as `<App>/<App>Core` (Swift package, XCTest — what `run_build` checks), `<App>/App` (SwiftUI) and an XcodeGen `project.yml`, never a hand-written pbxproj. `create_task` attaches a registered skill the plan names as `/slug` when `skill_name` is unset. Tests: `test_deliverables.py`.
- **Frontend is componentized.** `components/Settings.jsx` is a tab shell over `components/settings/`; `components/Chat.jsx` composes `components/chat/`. Verify against the current tree before recommending a split.
- **Single-user, single-process.** APScheduler + JobWorker share the FastAPI process by design. Multi-tenant patterns (request-scoped DB pools, per-tenant isolation, queue/worker split) are tuatha's architecture, not Pantheon's.

## Memory, recall and history

- **Episodic** — chat history, task logs and notes (`episodic.db`). `get_history` returns the newest `limit` messages, oldest first.
- **Semantic** — embedded chunks from artifacts and indexed workspace files, plus `remember(tier="semantic")` notes; topic-label embeddings too (`metadata.kind=topic_node`, never recalled).
- **Graph** — typed nodes + edges (source / video / topic / person / concept; PRODUCES, DISCUSSES, FEATURES_SPEAKER, SEMANTICALLY_SIMILAR_TO).
- **Working** — the in-process conversation (`AgentCore.working_memory`); not persisted. `remember(tier="working")` from old callers becomes a session-tagged episodic note.
- **Archival** — markdown notes + project summary under `<data_dir>/projects/<id>/notes`, managed in Memory → Archival (`/api/memory/archival/*`). Recall does **not** search it and no agent tool reads it. `migrate_stray_notes()` moves notes an older build wrote under a CWD-relative `data/`.

- **Recall searches semantic, episodic and graph** (`mgr.recall` default tiers; the `recall` tool's `tiers` enum is the same three). Pre-recall in `AgentCore.chat` asks for 5 per tier, adds 1-hop graph context, reranks, and trims to `ContextBudget.recall_budget` (4000 tokens). The `recall` tool renders provenance: `[semantic/artifact] … ↳ source: <path> id=… tags=[…]`, `[semantic/file:foo.md]`, `[episodic session=abc12345 ts=…]`, `[graph:concept]`.
- **Writing memory.** `consolidate_session()` (tool `consolidate_memory`, `POST /api/memory/consolidate?session_id=`) summarises + extracts from the session's recent messages; it needs a real session id. `remember(tier="graph")` runs the conversation extractor (`min_messages=1`) on the text.
- **Graph idempotency.** `add_edge` is idempotent on `(project_id, node_a_id, node_b_id, relationship)`, `add_node` on `(project_id, label)`. `GraphMemory.merge_nodes` is one transaction and drops the absorbed label's topic embedding.
- **Embeddings.** `ModelProvider.embed` raises on failure (no zero vector); callers fall back (semantic → `[]`, episodic → LIKE). Batch with `embed_many` + `SemanticMemory.store_many`; `embed()` memoizes short texts (512-entry LRU). Deletes use metadata filters (`delete_where`; `strip_artifact` matches `artifact_id` and `fm_artifact_id`); `index_text` deletes a path's old chunks first.
- **Pre-recall skips what the prompt already carries** (`in_context=`: the new message plus the history actually sent). Chat saves the user message before the agent runs, so without this the top hit was the question itself. Turns dropped by the history budget aren't in that set, so recall can bring them back.
- **Pre-recall has a relevance floor.** `_rerank` maps scores to 0-1 (`_as_probabilities`; cross-encoder logits are sigmoid-mapped, raw kept in `rerank_raw`). `RECALL_MIN_RELEVANCE` (0.05) drops reranked items below it; small talk gets nothing. No floor when the rerank failed or timed out, and none for the `recall` tool. Keep the floor low (design notes).
- **History has a token budget** (`agent/history.py`). Each turn rebuilds the agent from the newest 200 messages and sends those within `HISTORY_TOKEN_BUDGET` (0 = a quarter of the agent window, clamped 2K-24K; <0 disables). Older messages drop in whole blocks of 20 aligned to their absolute position (`working_offset`), so the prefix only changes every ~10 turns. Never slide the window one turn at a time.
- **Earlier turns come back by similarity, not rerank.** Past `SESSION_RECENT_MESSAGES` (6) messages, or when the budget dropped some, `recall(session_fallback=…)` adds up to 2 of this conversation's messages (not the newest 6) with similarity >= `RECALL_SESSION_MIN_SIMILARITY` (0.45), labelled `[earlier in this chat, …]` — also when they're still in the prompt but far back.
- **Per-turn context goes in the user message, not the system prompt.** `prompts.render_turn_context` renders recalled items and the time as a `<context>` block before the new message, labelled `[note]` (semantic), `[user said]` / `[your earlier reply]` (episodic), `[graph]`, `[earlier in this chat, …]`. `MEMORY_GUIDANCE` in the system prompt explains it once. Don't put anything per-turn into `build_system_prompt`.
- **Recall ranking + scoping** (eval: docs/design-notes.md). `recall` never returns `kind=topic_node` vectors (bare topic labels for the similarity pipeline). Graph matching is whole-word (`graph.label_in_text`: "AI" doesn't match "said"); `search_nodes` finds labels named inside a question, then word overlap. Graph context is added before the reranker, so it is judged and floored like everything else. Episodic search returns plain similarity; recency is applied once, in `_apply_context_focus`. The budget packer skips an item that doesn't fit instead of stopping.
- **Deletes reach the vectors.** `EpisodicMemory.delete_message` / `delete_conversation` drop the Chroma vectors; the conversation endpoint also drops facts extracted from it (`session_id` + `source=auto_extraction`). Episodic search skips and cleans up vectors whose row is gone. Job prompts (`metadata.kind` `*_prompt`, or "You are running an autonomous task" / "turn N of M") stay in SQLite but are never indexed or recalled.
- **One index per artifact.** The debounced embedder doesn't re-chunk when `index_artifact` chunks of the same content hash exist; recall collapses overlapping chunks of one artifact; `index_text` chunks start with "<doc title> › <section heading>".

## Cross-artifact similarity + merge proposals

With `auto_link_similarity=True`, after `index_artifact` `sources/similarity.py` embeds each topic label (`kind=topic_node`), finds type-compatible neighbours (concept↔concept, technology↔framework, vendor↔organization, market↔market_segment) above 0.86 (`DEFAULT_LINK_THRESHOLD`), adds `SEMANTICALLY_SIMILAR_TO` edges both ways, and queues a merge proposal above 0.92 (`DEFAULT_MERGE_THRESHOLD`, `merge_proposals.db`). Merges are NEVER auto-applied: `merge_topics(action="list")`, then `merge_topics(action="approve", proposal_id, canonical_label)` rewrites edges, deletes the deprecated node and marks the proposal `merged` (idempotent). Backfill: `link_topic_similarity(path_prefix="youtube-transcripts/")`.

## Things explicitly NOT done yet

- JS-rendered pages fail trafilatura (add a Playwright fallback when a real failure shows up).
- OCR for the PDF source adapter (scanned PDFs fail `ingest_source`; workspace indexing and `read_file` use the vision model).
- CI (tests run manually).
- Real-traffic shakedown of the forum / podcast / github adapters (smoke-tested only).
- Reddit OAuth: public `.json` endpoints get 403'd from non-residential IPs; workaround `extras['raw_payload']`. Plan: a Reddit app, vault credentials, `oauth.reddit.com` with a bearer token.
- Per-project source-adapter scoping (global registry today).
- A UI for merge-proposal review (agent tool only).

## Working style

- Brent prefers concise responses. Skip preambles.
- He runs deploy commands himself; output the command, don't ssh.
- After code changes, give him the rebuild command — don't pretend the change is live.
- If something seems suspect (an empty job result, stale UI state), grep the code before guessing — Pantheon has many small layers.
- When a fix is structural, say so even if it means more work; he'd rather pay the architectural cost once.
- Don't pile new features on top of broken ones; fix the foundation first.

## Common dev tasks

**Add an agent tool:**
1. Pick the domain module in `backend/agent/tools/` (memory, files, artifacts, web, sources, tasks, skills, images, code, build, github, git, finance, writing) or add one (import it in `agent/tools/__init__.py`).
2. Add the schema to its `SCHEMAS` and the name to `_ORDER` (the order the model sees).
3. Write `@tool("name") async def _tool_name(ctx: ToolContext, tool_name, tool_args)` — `ctx` carries `memory_manager`, `project_id`/`effective_project`, `session_id`, `last_assistant_text`, `interactive`, `host_exec`. Host-exec tools also go in `HOST_EXEC_TOOLS`.
4. Regenerate `docs/tools.md`: `.venv/bin/python scripts/gen_tools_doc.py` (a test fails if it's stale).
5. Bump the version (+ lockfile) and restart the backend.

**Add a source adapter:** create `backend/sources/adapters/<name>.py`, subclass `SourceAdapter` (or a `_*AdapterBase` such as `_BlogAdapterBase`, `_PDFAdapterBase`) implementing `fetch()` and the class attrs, call `register_adapter(YourClass())` at import, import the module in `adapters/__init__.py`, optionally add an extractor.

**Add an extractor strategy:** subclass `LLMDefaultExtractor` (JSON recovery + diagnostics), set `name`, parse into `ExtractedFields(topics, speakers, claims, status, frontmatter_additions)`, `register_extractor(YourClass())`, reference it via `extractor_strategy`.

**Add a per-project store:** add it to `api/project_purge.py` and to project export/import.
