# Pantheon documentation audit (read-only) — 2026-09-27

Scope: every `.md`/`.txt` doc in the repo vs. the code at HEAD `cb4cc7e`. All counts below were produced by grep/wc/ls on this checkout. `pytest` is not installed in this container, so the test total is estimated from source (404 `def test_` + ~24 parametrize expansions ≈ 428 items; 5 `MALEGIS_LIVE`-gated tests skipped) — consistent with CLAUDE.md's "~425 (5 skipped)".

Severity = impact on a new engineer onboarding. **High** = will lead them to write wrong/unsafe code or believe a feature exists/doesn't; **Med** = wrong facts that cost time; **Low** = stale numbers / cosmetics.

## 1. Summary table

| ID | Doc | Type | Sev | One-line |
|---|---|---|---|---|
| C1 | CLAUDE.md | wrong | Med | "18 mounted routers" → 19 |
| C2 | CLAUDE.md (+README, REVIEW, backend/README, SOURCE_ADAPTERS) | stale | Med | "28 adapters / 9 mechanisms" → 29 / 10; `sec/edgar` missing everywhere |
| C3 | CLAUDE.md | stale | Low | "tools.py 1500+ lines" → 4523 |
| C4 | CLAUDE.md | missing | High | Directory tree omits ~60 real files/dirs incl. 5 of 19 routers, `messaging/`, `sandbox/`, `jobs/sinks/`, 8 of 12 `skills/*.py` |
| C5 | CLAUDE.md §Design rationale + REVIEW.md | wrong | High | "Skills… no executor, no subprocess, no sandbox" — `skills/executor.py`, `sandbox/` (Subprocess+Firecracker) exist and run skill scripts |
| C6 | CLAUDE.md | stale | Med | Versioning section ignores that `VERSION` + `bump_version.sh` implement a conflicting scheme and `main.py:89` still falls back to `VERSION` |
| C7 | CLAUDE.md | missing | High | No coverage of messaging gateway, sandbox, search-provider chain, SEC/financial tools, system updater, sinks, skills security pipeline, `security_log.py`, `tasks/runs.py` |
| C8 | CLAUDE.md | stale | Low | `api/settings.py` described as "legacy flat-config CRUD"; also hosts secrets, security-log, search-providers, restart-telegram routes |
| C9 | CLAUDE.md | stale | Low | `components/settings/` list omits `ChatRouter.jsx`; says prompts.py appends recent-jobs block (it's `agent/core.py:31`) |
| R1 | README.md | stale | Med | LLM config described as "roles (chat, prefill, embedding, vision, rerank)" + "Settings → LLM Endpoints"; now task-class routes, tab is "LLMs" |
| R2 | README.md | wrong | Med | "Type `/create-skill` in chat" — no such skill; `create_skill` is an agent tool |
| R3 | README.md + .env.example | stale | Med | Leads with flat `LLM_*` env keys that are seed-only after `llm_config_migrated_v1` |
| R4 | README.md | wrong | Low | "api/ (19 endpoints)" → 19 routers, 244 routes |
| V1 | REVIEW.md | wrong | High | "`/raw` now accepts `?token=`" — contradicts CLAUDE.md & code (no token param; cookie auth) |
| V2 | REVIEW.md | stale | Med | "199 passed, 5 skipped", "MA Legislature 142 tests" → ~425 / 67 defs |
| V3 | REVIEW.md | wrong | Med | "deleted 7 dead scaffolding artifacts" — they are all still present |
| V4 | REVIEW.md | stale | Low | version `2026.05.24.H4`, tools.py "~3000 lines", shipped-log ends 2026-05-24 (50 commits ago) |
| B1 | backend/README.md | wrong | Med | Tree lists `secret_storage/`, `plugins/` — don't exist |
| B2 | backend/README.md | wrong | Med | Router list names `sources` (none) and omits `messaging` |
| B3 | backend/README.md | wrong | High | "messaging gateway is planned" — shipped with 5 adapters + `/api/messaging/*` |
| B4 | backend/README.md | stale | Med | `llm_role_mapping`, `ROLES`, `RoleMapping.jsx` → `llm_routes`, `TASK_CLASSES`, `ModelRouting.jsx` |
| B5 | backend/README.md vs README.md | duplicate/conflict | Low | "don't invoke uvicorn directly" vs README dev section that does |
| F1 | frontend/README.md | stale | Med | `/files`, `/sources`, `/personality` are redirects; `SourcesPage.jsx` dead & mis-described; `/memory`,`/tasks`,`/mcp` not in sidebar |
| F2 | frontend/README.md | stale | Med | `RoleMapping`, `RoleMappingRow` components don't exist; role-mapping wording |
| F3 | frontend/README.md | wrong | High | "Bearer token from `localStorage.auth_token`… URLs append `?token=`" — code removes that token and uses HttpOnly cookie |
| Q1 | frontend/QUICKSTART.md | stale | Med | "Role Mapping… chat/prefill/vision/embed/rerank", "assigned to the chat role" |
| Q2 | frontend/QUICKSTART.md | duplicate | Low | ~80% overlaps frontend/README.md |
| M1 | backend/memory/{INDEX,MANIFEST,QUICK_START,README,SUMMARY}.md + VERIFICATION_REPORT.txt | duplicate/stale | High | Five overlapping generator-output docs ("Agent-Harness", `/sessions/friendly-happy-maxwell/…`, `cp -r memory/` install steps) |
| M2 | memory docs | wrong | Med | DB paths `data/episodic.db`, `data/graph.db` → `data/db/…` (config.py:155-160) — contradicts CLAUDE.md gotcha |
| M3 | memory docs | stale | Low | Line counts & API listings miss ~25 methods, `ContextBudget`, and 5 whole modules |
| S1 | SOURCE_ADAPTERS.md | stale | Med | "28 across 9" table; `sec/edgar` missing |
| S2 | SOURCE_ADAPTERS.md | wrong | Med | §"What adapters do NOT own" says topic extraction is skill-driven, adapter writes `topics: []` — contradicted by its own §Resolved decisions and `registry.py` |
| S3 | SOURCE_ADAPTERS.md | stale | Med | Pipeline step 5 documents only 50-variant collision retry; default is in-place update (registry.py:196-217) |
| S4 | SOURCE_ADAPTERS.md | wrong | High | Worked example uses raw `httpx.AsyncClient().get(url)` — the exact SSRF pattern CLAUDE.md bans (`safe_http_get`) |
| U1 | docs/USAGE.md | wrong | Med | Lists `web_fetch` tool — not in `TOOL_SCHEMAS` (60 tools) |
| U2 | docs/USAGE.md | wrong | Med | "Settings → Web Search Provider Chain" → Connections page `SearchProvidersTab`; default chain claim not matched by code defaults |
| U3 | docs/USAGE.md | missing | Med | Telegram only; Discord/Slack/Matrix/Mattermost, `/chat` cmd, `/model` pin, `/skill` invocation undocumented |
| SF1 | docs/SECURITY_FEATURES.md | stale | Low | Accurate for skills (verified), but "as of 2026-04-06"; misses sandbox layer, auth sessions, KDF v2, SSRF guard, host-exec gating; contradicted by CLAUDE.md:349 |
| MG1 | docs/messaging-gateway-plan.md | wrong | High | Banner (edited 2026-09-26) says "not yet implemented — no backend/messaging/, no /api/messaging/*" — all exist |
| MR1 | docs/mcp-registry-protocol.md + examples/minimal-registry | wrong | High | Describes `GenericRegistryAdapter` + "Settings → MCP → Registries" as built-in — nothing implemented (skill registry is; MCP registry is not) |
| SP1 | docs/SKILLS_FEATURE_PLAN.md | stale | Low | 996-line plan, all 5 phases marked complete Apr-2026 — historical |
| SU1 | docs/superpowers/plans+specs (15 files, ~13k lines) | stale | Low | All implemented (except pub/sub phase-2 "parking lot") — historical checklists |
| AF1 | docs/api/artifacts-feed.md | stale | Low | "Authentication: bearer token" — primary is session cookie; params verified correct |
| X1 | BACKEND_BUILD_SUMMARY.txt, FILES_CREATED.txt, frontend/BUILD_SUMMARY.txt, backend/memory/VERIFICATION_REPORT.txt | stale | Med | Generator logs with `/sessions/…/mnt/outputs` paths; reference non-existent files (`BACKEND_BUILD_COMPLETE.md`, `telegram/bot.py`, "Emotional memory") |
| X2 | autoresearch_report.md | stale | Low | Run output (0 successful mutations) committed at root |
| X3 | VERSION + bump_version.sh | wrong | Med | Old `YYYY-MM-DD-NN` scheme; script seds `main.py`/`package.json` with patterns that no longer match |
| E1 | .env.example | wrong | High | "Leave empty to allow all guilds" — discord.py:120 is deny-by-default |
| E2 | .env.example | missing | Med | Settings fields absent: `MATRIX_ALLOWED_ROOM_IDS`, `MATTERMOST_ALLOWED_CHANNEL_IDS` (deny-by-default → bots silently dead), `CONTEXT_FOCUS`, `FILE_CHUNK_STRATEGY`, `PERSONALITY_WEIGHT`, legacy `EMBEDDING_/PREFILL_/RERANKER_*` |
| E3 | .env.example | missing | Med | `os.environ`-read vars absent: `BROWSER_ENABLED/HEADLESS/EXECUTABLE_PATH/WS_URL`, `PANTHEON_SANDBOX`, `FC_DIR`, `JOB_WORKER_POLL_SECONDS`, `JOB_WORKER_SUPERVISE_SECONDS`, `JOB_STALL_TIMEOUT_SECONDS`, `JOB_STALL_CHECK_SECONDS`, `JOB_AUTO_REQUEUE_MAX` |
| E4 | .env.example | stale | Low | `BIND_HOST`, `INSTALL_BROWSER`, `INSTALL_OFFICE` are installer/start-script vars, not `Settings` fields — unlabeled |
| P1 | backend/agent/prompts.py | wrong | Med | System prompt tells the model to use `web_fetch` (prompts.py:233) — no such tool |
| P2 | prompts.py | stale | Low | Hard-codes user-specific `mcp_SubDownload_*` names; says workspace is `data/workspace/` (per-project is `data/projects/<id>/workspace`, files.py:37) |
| D1 | memory/extraction.py:7 | stale | Low | "Uses the prefill/curation model (Nemotron Nano…)" → `get_provider_for("extract")` (l.111) |
| D2 | api/connections.py:3 | stale | Low | "Refactored from api/sources.py" (gone); frontend keeps dead `sourcesApi` + orphan `SourcesPage.jsx` |
| D3 | api/settings.py:1 | stale | Low | "model config, endpoint management" — moved to `api/llm_endpoints.py` |
| D4 | jobs/handlers/autonomous_task.py:1,11 | stale | Low | "wraps tasks/autonomous.run_autonomous_task()" — no longer imports it |
| D5 | jobs/handlers/scheduled_job.py | stale | Low | Sink kinds "email | sms" — only artifact/telegram/webhook registered |
| D6 | memory/episodic.py:59, graph.py:35, file_indexer.py:284 | code≠doc | Low | Relative `data/*.db` fallbacks survive despite CLAUDE.md "never relative" |
| N1 | (none) | missing | High | Subsystems with zero documentation anywhere — see §2.N |

## 2. Findings in detail

### CLAUDE.md

**C1 — router count.** Claim: `api/ FastAPI routers (18 mounted routers)` (l.30). Code: 19 `app.include_router(...)` calls, `backend/main.py:290-308` (auth, chat, files, memory, personality, projects, settings, mcp, mcp-oauth, skills, tasks, personas, system, connections, artifacts, conversations, jobs, llm, messaging). Fix: "19" and add the five missing files to the tree (`auth.py`, `memory.py`, `messaging.py`, `personality.py`, `system.py`).

**C2 — adapter count.** Claim (l.15): "28 adapters across 9 mechanisms (youtube… malegis)". Code: 29 `register_adapter(` calls; 10th mechanism `sec/edgar` at `backend/sources/adapters/sec_edgar.py:29`, imported in `adapters/__init__.py:16`. Same stale number in README.md, REVIEW.md (×2), backend/README.md, SOURCE_ADAPTERS.md. Fix: "29 adapters across 10 mechanisms (…, `sec`)"; add `sec_edgar.py 1 adapter (edgar) — SEC Archives / ticker+form lookup` to the tree.

**C3 — tools.py size.** Claim l.27 "1500+ lines". `wc -l backend/agent/tools.py` = 4523; 60 tool schemas. Fix: "4500+ lines, 60 tools" or drop the number.

**C4 — directory tree vs reality.** Nothing in the tree is nonexistent (only `data/*` runtime dirs are absent, which is expected). Real entries not in the tree:
- Root: `Makefile`, `docker-compose.yml`, `Caddyfile`, `nginx/`, `scripts/` (`setup_firecracker.sh`, 2 migrations), `skills/` (10 bundled skills — tree only shows `data/skills/`), `update.sh`, `uninstall.sh`, `setup_options.sh`, `bump_version.sh`, `git_commit.sh`, `VERSION`, `.env.example`.
- `backend/`: `Dockerfile`, `pytest.ini`, `db_utils.py`, `security_log.py`, `messaging/` (gateway + 5 adapters), `sandbox/` (3 backends), `integrations/github.py`, `telegram_bot/` (shim).
- `backend/api/`: `auth.py`, `memory.py`, `messaging.py`, `personality.py`, `system.py`.
- `backend/agent/`: `personality.py`, `search_providers.py`.
- `backend/artifacts/`: `conversions.py`, `embedder.py`, `preview.py`.
- `backend/jobs/`: `context.py` (JobContext, `pinger_for`), `recovery.py`, `sinks/` (artifact/telegram/webhook), `handlers/bootstrap.py`.
- `backend/skills/`: `analytics.py`, `executor.py`, `exporter.py`, `importer.py`, `publisher.py`, `registries_config.py`, `scanner.py`, `versioning.py` (tree shows only 4 of 12).
- `backend/tasks/`: `autonomous.py`, `runs.py`.
- `backend/mcp_client/`: `client.py`, `oauth.py`, `tavily_credits.py` (text mentions first two).
- `backend/llm_config/`: `known_models.py`, `router.py`, `tuning.py`, `usage.py` (text mentions them; tree doesn't).
- `backend/models/discovery.py`; `backend/utils/`: `background.py`, `http.py`, `net.py`, `paths.py`, `progress.py`, `self_doc.py`.
- `backend/sources/adapters/sec_edgar.py`; `backend/tests/benchmark_chunker.py`.
- Frontend: `components/settings/ChatRouter.jsx`, `RouterTuning.jsx`; 14 pages (tree lists 1).
Fix: regenerate the tree from `find` and annotate; or shrink it to top-level dirs and rely on per-package READMEs.

**C5 — "no executor / no sandbox" is false.** Claim (l.349): "There is no executor, no subprocess, no sandbox to harden. Recommendations to add WASM/process isolation for skills are confusing skills with arbitrary user code." Code: `backend/skills/executor.py` runs skill `scripts/` via `asyncio.create_subprocess_exec` with an env allowlist (`executor.py:32-33`); `backend/sandbox/{subprocess_backend,firecracker_backend}.py` wrap it (`subprocess_backend.py:45`, `firecracker_backend.py:82-88`); `code_execute` tool uses `sandbox.get_sandbox` (`tools.py:3174`); `docs/SECURITY_FEATURES.md §5` documents the executor in detail. REVIEW.md repeats the claim ("Sandbox skills with WASM — they're markdown, not code"). Fix: rewrite the bullet: "Skills are primarily markdown recipes; optional `scripts/` run through `skills/executor.py` under `sandbox/` (subprocess default, Firecracker opt-in via `PANTHEON_SANDBOX`). Don't propose *another* isolation layer; point at `sandbox/`."

**C6 — versioning.** Convention text is correct (package.json `2026.09.27.H6`, `main.py:64-99`). But `VERSION` contains `2026-04-05-01` and `bump_version.sh` writes that format and `sed`s `backend/main.py` for `version="YYYY-MM-DD-NN"` (l.49-59) and `package.json` for `"YYYY.MM.DD"` (l.80-86) — neither pattern exists any more. `main.py:89` still falls back to `VERSION`. Fix: delete `VERSION` and `bump_version.sh` (and fallback #3 in `_resolve_app_version`), or add one sentence: "`VERSION`/`bump_version.sh` are legacy; do not use."

**C7 — undocumented subsystems** (grep of all docs): see §2.N.

**C8** `api/settings.py` also serves `/settings/security-log`, `/secrets/*`, `/settings/search/providers*`, `/settings/restart-telegram` (route list). **C9** `_build_recent_jobs_block` is `agent/core.py:31`, not prompts.py; `components/settings/` also has `ChatRouter.jsx`.

Verified correct in CLAUDE.md: all 9 tool names cited exist in `TOOL_SCHEMAS`; all cited API paths exist (`/api/llm/{endpoints,routes,profiles,usage,probe,task-classes,router,router/decisions,router/tuning,router/simulate,router/apply,router/feedback}`, `/api/mcp/connections/{name}/start-oauth`, `/api/mcp/oauth/callback`, `/api/auth/login`, `/api/health`); every named symbol (`host_exec_allowed`, `pinger_for`, `spawn`, `served_models`, `_TOOL_ERROR_RE`, `looks_like_correction`, `MIN_TURNS/MIN_CALLS`, `_IMAGE_WANTS_PRESET`, `_PUBLIC_PATHS`, `_REFRESH_LOCKS`, `apply_sqlite_pragmas`, `password_matches`, `authorize_websocket`, `get_provider_for`, `reset_provider`, `TASK_CLASSES`, `_enqueue_autonomous_job`, `_index_typed_topics_to_graph`, `schedule_embed`, `_guard_route`, `validate_format`, `_git_auth_env`, `call_tool_raw`, `pooled_client`, `embed_many`, `store_many`, `merge_nodes`, `delete_where`, `strip_artifact`, `create_blank_skill`, `resolve_explicit/auto`, `negotiated_protocol_version`, `2025-11-25`) resolves. "Things explicitly NOT done yet" — all six still not done (no merge-proposal UI in `frontend/src`; `pdf.py:144` only warns about scanned PDFs; no playwright in `web.py`/`blog.py`; `forum.py:246` still `raw_payload`; no `.github/`; registry still global). The React.lazy / dynamic-import claims hold (`App.jsx:9-11`, `Mermaid.jsx:8`, `svgExport.js:74`). Deny-by-default allowlists hold (`discord.py:120`).

### README.md

**R1.** "Map different models to distinct roles (chat, prefill, embedding, vision, rerank)" and "Settings → LLM Endpoints … assign them to roles". Code: task classes in `llm_config/models.py:72` (`agent, code, quick, long_context, extract, summarize, vision, image_gen, embed, rerank`), routes in vault `llm_routes`; Settings tab id `llms`, label "LLMs" (`Settings.jsx:1453`), sub-sections "Endpoints", "Model routing", "Model usage", "Chat router", "Routing tuning". Fix wording accordingly.
**R2.** "Type `/create-skill` in chat" — bundled skills are `autoresearch, code-review, daily-digest, draft-message, explain-code, knowledge-capture, summarize-conversation, task-breakdown, weather, web-research`; `create_skill` is a tool the agent calls when asked ("make this a reusable skill"). Fix: "Ask the agent to create a skill (it calls `create_skill`)".
**R3.** Configuration section presents `LLM_BASE_URL/LLM_API_KEY/LLM_MODEL/LLM_PREFILL_MODEL/EMBEDDING_MODEL` as the primary config. CLAUDE.md: "Legacy `/api/settings` LLM keys are read only by the one-shot migration; once `llm_config_migrated_v1` is set, writes … have no effect." Fix: label these "first-run seed values; afterwards configure in Settings → LLMs".
**R4.** "api/ … (19 endpoints)" → "19 routers".

### REVIEW.md

**V1 (security).** "2026-05-21 | Artifact downloads | Fixed auth: `/raw` endpoint now accepts `?token=`". Code: no `token` parameter anywhere in `backend/api/artifacts.py`; `client.js:10` `withCredentials: true`, `:14-15` deletes any legacy `localStorage.auth_token`, `rawUrl` (l.213) has no token; CLAUDE.md: "Tokens are never accepted from the query string — don't add `?token=` support." Fix: delete the row or amend to "superseded by HttpOnly session cookie (2026-09)".
**V2.** "Last run: 199 passed, 5 skipped… MA Legislature is 142 tests alone" → `test_malegislature_adapters.py` has 67 test functions (+5 live); whole suite ≈425. 
**V3.** "Docs | Full README rewrite; deleted 7 dead scaffolding artifacts" — `BACKEND_BUILD_SUMMARY.txt`, `FILES_CREATED.txt`, `frontend/BUILD_SUMMARY.txt`, `backend/memory/VERIFICATION_REPORT.txt`, `VERSION`, `bump_version.sh`, the five memory docs all exist and were never deleted in this repo's history (all added in `1b02292`, no `D` entries).
**V4.** Version `2026.05.24.H4` (now `2026.09.27.H6`); "tools.py (~3000 lines)" (4523); "What shipped recently" stops 2026-05-24 — 50 commits later it omits auth sessions/KDF v2, SSRF/XSS batches, chat router phases 1-3, messaging gateway, SEC/financial tools, system updater. Fix: REVIEW.md should stop carrying a changelog (link `git log`), keep only orientation + pointers.

### backend/README.md

**B1.** Tree lists `secret_storage/  Storage backend for the vault` and `plugins/  Plugin loader (sources + tools)` — neither directory exists (`ls backend`). Delete both lines.
**B2.** "19 routers … `sources` … `llm_endpoints`" — there is no `sources` router (`api/sources.py` was refactored into `connections.py`, see its docstring); `messaging` is missing. Also states "`project_export` / `project_import` mount on the `projects` router" — correct.
**B3.** "`telegram_bot/  Telegram bot (still in place; messaging gateway is planned)`". Code: `backend/messaging/{gateway,base,models,channel_store}.py`, `messaging/adapters/{telegram,discord,slack,matrix,mattermost}.py`, `api/messaging.py` (9 routes: `/messaging/status`, `/channels`, `/mappings…`, `/default-project`, `/{adapter}/restart`), `frontend/src/components/MessagingSettings.jsx` (Settings → Channels tab). `telegram_bot/bot.py` docstring: "backward-compatible shim. All logic now lives in messaging.adapters.telegram". Fix: replace with "`messaging/` unified gateway (Telegram/Discord/Slack/Matrix/Mattermost); `telegram_bot/` is a re-export shim".
**B4.** "Stored in the vault under `llm_endpoint_key__<name>` and `llm_role_mapping`… To add a role: update `llm_config.models.ROLES` and … `RoleMapping.jsx`". Code: `llm_routes` + `llm_model_profiles`; `TASK_CLASSES` (`models.py:72`); `ModelRouting.jsx`. `RoleMapping.jsx` doesn't exist. Fix: point to CLAUDE.md "Adding a task class".
**B5.** "don't invoke `uvicorn` directly" vs README.md "Run Dev Servers Locally: `../.venv/bin/uvicorn main:app --reload`". Pick one.

### frontend/README.md

**F1.** Page table: `App.jsx:78` `files → /artifacts`, `:82` `sources → /connections`, `:85` `personality → /settings` are `<Navigate>` redirects, not pages. `SourcesPage.jsx` is not imported by `App.jsx` (dead file) and is a legacy GitHub-connections form, not "Source-adapter ingestion UI" (there is no ingestion UI; ingestion is agent-tool driven). `/memory`, `/tasks`, `/mcp` are routable but absent from the sidebar (`Layout.jsx:21-27`: Chat, Artifacts, Skills, Personas, Connections, Projects, Settings); Memory/Tasks live as Chat tabs (`ChatTabs.jsx:19-24`), MCP inside Connections (`ConnectionsPage.jsx:4`). Fix: rewrite table from `App.jsx` + `Layout.jsx`; delete `SourcesPage.jsx` and `sourcesApi` (`client.js:134-147`, "legacy aliases").
**F2.** `settings/ … RoleMapping, RoleMappingRow` → `EndpointCard, AddEndpointForm, EndpointList, ModelRouting, RoutingUsage, ChatRouter, RouterTuning`. Settings row "role mapping (chat / prefill / vision / embed / rerank)" → task-class routing.
**F3 (security).** "Bearer-token auth is added by an Axios interceptor based on `localStorage.auth_token`. For endpoints hit by bare HTML tags… URLs append `?token=…` — see `artifactsApi.rawUrl` and `filesApi.downloadUrl`." Code: `client.js:10` `withCredentials: true`; `:14-15` `localStorage.removeItem('auth_token')` with comment "Earlier builds kept a (non-expiring) token in localStorage; drop it."; `rawUrl` returns a bare URL. Fix: "Auth is an HttpOnly `pantheon_session` cookie; nothing in JS holds a token."
**F4.** `npm install` vs CLAUDE.md `npm ci` rule.

### frontend/QUICKSTART.md

**Q1.** Steps 3 and Troubleshooting: "Under **Role Mapping**, assign… `chat`, `prefill`, `vision`, `embed`, `rerank`", "Confirm a model is assigned to the `chat` role" → Model routing / `agent` class. **Q2.** Setup, dev server, build, "Add a New Page", "Make an API Call", state examples all duplicate frontend/README.md. Fix: fold the 4 unique bits (first-time UI walkthrough, troubleshooting) into frontend/README.md and delete QUICKSTART.md.

### backend/memory/*.md (INDEX, MANIFEST, QUICK_START, README, SUMMARY) + VERIFICATION_REPORT.txt

**M1.** Generator output, not maintained docs: titles "Agent-Harness Memory System" / "Agent-Harness 5-Tier Memory System"; `SUMMARY.md:238` `/sessions/friendly-happy-maxwell/pantheon/backend/memory/`; `INDEX.md:222` and `QUICK_START.md:8` `cp -r memory/ /path/to/pantheon/backend/` (installing the package into itself); `SUMMARY.md:257` "ready for immediate integration into the pantheon project". Each of the five repeats the tier diagram, `MemoryManager(...)` example and file tree.
**M2.** `MANIFEST.md:246-248`, `QUICK_START.md:106-108`, `SUMMARY.md:167-169`, `README.md:63,146` say `data/episodic.db`, `data/graph.db`. Code: `config.py:155-160` `db_dir/episodic.db`, `db_dir/graph.db` (= `data/db/`); `merge_proposals.py:59` `db_dir/merge_proposals.db`; `file_indexer.py:282` `db_dir/file_index.db`. Directly contradicts the CLAUDE.md "DB path canonical location" gotcha.
**M3.** `INDEX.md:47-52`/`SUMMARY.md:11-47` line counts (working 103, episodic 320, semantic 188, graph 351, archival 156, manager 266) vs actual 103/595/433/634/156/762; `chunker.py`(189), `extraction.py`(317), `file_indexer.py`(972), `merge_proposals.py`(221), `topic_embeddings.py`(185) absent. `MANIFEST.md:73-146` API omits `EpisodicMemory.get_session_project_id/get_conversation/get_recent_messages`, `SemanticMemory.store_many/get_metadata/delete_where/strip_artifact/list_by_model/reembed_stale`, `GraphMemory.get_paths/merge_nodes/strip_artifact/edges_for_nodes/nodes_mentioned_in`, `MemoryManager.run_extraction_on_recent/index_workspace_file/index_workspace_directory/index_artifact`, `ContextBudget`, and `__init__` params `context_budget/embedding_model/embedding_batch_fn` (`manager.py:117-160`).
Fix: delete INDEX/MANIFEST/QUICK_START/SUMMARY/VERIFICATION_REPORT; replace README.md with a ≤80-line package README (tiers table already in backend/README.md, DB paths, `recall` provenance format from CLAUDE.md "Memory tier semantics", pointer to `file_indexer._index_typed_topics_to_graph`).

### backend/sources/SOURCE_ADAPTERS.md

**S1.** "Current state (2026-05-09): 28 across 9" — add `sec` row (`sec_edgar.py`, 1 adapter, `bucket_aliases ("sec","edgar")`, template `sec/{cik}/{published_at}/{form_type}-{slug}.md`).
**S2.** §"What adapters do NOT own": "Topic extraction. Topic labeling is an LLM step (the skill orchestrates it). Adapter writes empty `topics: []` initially; skill updates the artifact frontmatter once topics are extracted". Contradicted by §"Resolved design decisions §1" in the same file and by `registry.ingest` (runs `extractor_strategy` inline before save; `base.py:109-115` `extractor_strategy`, `auto_extract`). Fix: delete the stale paragraph.
**S3.** Pipeline step 5: "retries up to 50 numbered variants on UNIQUE-path collisions". Code `registry.py:196-226`: default path re-ingest **updates the existing artifact** (new version); the 50-variant retry (`:228`) only applies to new paths / `force_new`. CLAUDE.md gotcha "Ingest dedup vs save_to_artifact" documents this correctly. Fix: add the dedup sentence.
**S4.** Worked example `BlogAnnouncement.fetch`: `async with httpx.AsyncClient() as client: r = await client.get(req.identifier, timeout=30)`. CLAUDE.md: "Outbound fetches go through `utils.net.safe_http_get`… Don't add `httpx.get(model_chosen_url, follow_redirects=True)`"; also "don't reintroduce `async with httpx.AsyncClient()` per request". Real `blog.py` presumably follows the rule; the doc teaches the banned pattern. Fix: rewrite example with `from utils.net import safe_http_get`.
Verified: attribute names (`base.py:95-120`), 6 extractor names (`extraction.py:110,172,313,433,556,661`), `batch_ingest`/`stop_on_error`, `mgl_citations` consumer still pending (no hits in `file_indexer.py`).

### docs/USAGE.md

**U1.** "`web_fetch` — plain HTTP GET for static pages" and prompt mapping "Web pages… → `web_fetch`, `web_search`". `TOOL_SCHEMAS` has no `web_fetch` (60 names, list in audit notes); only `web_search`, `download_file`, and browser tools. Fix: remove; if fetching is wanted, that's a code task.
**U2.** "Configure the chain in **Settings → Web Search Provider Chain**" → UI is `Connections` page → `SearchProvidersTab` (`ConnectionsPage.jsx:6`, `connections/SearchProvidersTab.jsx`); "default: Brave → SearXNG → DuckDuckGo" — the default provider list in `agent/search_providers.py:47-58` contains `searxng` and `ddg` entries (Brave is a supported *type*, `:33`, added by the user). Fix: correct location; describe defaults from `_default_providers`.
**U3.** §8 covers Telegram only; code has Discord/Slack/Matrix/Mattermost adapters and the Channels settings tab; Telegram `/chat` command (`telegram.py:499`) not in the table; chat `/model <class|auto>` pin and `/skill-name` invocation not documented anywhere user-facing. Fix: rename §8 "Messaging channels", add a slash-command subsection to §3.
Verified: `save_last_response(history_count, mode)`, `index_workspace(force)`, `consolidate_memory`, `create_task` schedules, `send_telegram`, `BROWSER_HEADLESS`, `--with-browser`, browser tool names (code also has `browser_close`), `uninstall.sh --purge` (script exists), `minimal` personality weight (`config.py:116`).

### docs/SECURITY_FEATURES.md

**SF1.** Content verified accurate for the skills pipeline: `.scan_results` (`registry.py:186`), quarantine, override password, executor env allowlist (`executor.py:32-33`), `data/logs/security.log` (`security_log.py:39`), all 20 event methods exist (`auth_login_*`, `secret_set/deleted`, `settings_updated`, `skill_*`), all 8 endpoints exist. Gaps: header says "as of 2026-04-06"; no mention of `sandbox/` (which now wraps the executor), auth sessions, vault KDF v2, `safe_http_get`, browser `_guard_route`, host-exec gating, WebSocket auth — all of which live only in CLAUDE.md gotchas. Fix: retitle "Skills security" or extend into the single security doc and have CLAUDE.md link to it; reconcile with CLAUDE.md:349 (C5).

### docs/messaging-gateway-plan.md

**MG1.** Line 3 (file last touched 2026-09-26): "⚠️ Proposed — not yet implemented… no `backend/messaging/` gateway, Discord adapter, or `/api/messaging/*` routes exist yet." All exist (see B3). The plan's proposed layout matches what shipped, plus Slack/Matrix/Mattermost. Fix: flip banner to "Implemented (see backend/messaging/); kept for design rationale" and move to `docs/archive/`; write a short current-state doc (adapters, `messaging_channel_mappings` vault key from `channel_store.py:4-6`, deny-by-default allowlists, `/api/messaging/*`).

### docs/mcp-registry-protocol.md + docs/examples/minimal-registry/

**MR1.** Claims Pantheon has a "built-in `GenericRegistryAdapter`", discovers `/.well-known/pantheon-mcp-registry.json`, and the UI has "Settings → MCP → Registries → Add Registry". Code: no `GenericRegistryAdapter`, no `pantheon-mcp-registry.json`, no "registr" string in `backend/mcp_client/`, `backend/api/mcp.py`, or `MCPConnections.jsx`. Only the **skill** registry protocol is implemented (`skills/importer.py:639 GenericSkillRegistryAdapter`, `:646 DISCOVERY_PATH="/.well-known/pantheon-skill-registry.json"`, `/api/skills/registries*`, UI "Hubs / Add Hub"). Fix: mark mcp-registry-protocol.md "Status: Proposed — not implemented" (its "Draft" status is read as "implemented but unstable"), or move to `docs/archive/proposals/`. `docs/skill-registry-protocol.md` is accurate.

### docs/SKILLS_FEATURE_PLAN.md, docs/superpowers/**

**SP1.** Phases 1–5 all `[x]` with completion assessments dated 2026-04-05..08; still the only place the skills runtime (importer, hubs, versioning, publisher, analytics, chaining) is described. **SU1.** 8 plans + 7 specs (2026-05-08..19), every feature verified present (`llm_config/`, `malegislature.py`, `store/index.js` active-project, `components/help/`, `/artifacts/feed`, move/duplicate endpoints, `image_extraction.py`); `2026-05-17-publisher-subscriber-phase2-notes.md` is an explicit parking lot. Fix: `git mv docs/superpowers docs/archive/superpowers`, `git mv docs/SKILLS_FEATURE_PLAN.md docs/archive/`, and extract the shipped-behaviour paragraphs (skills runtime) into a real `docs/skills.md`.

### docs/api/artifacts-feed.md

**AF1.** "Authentication … bearer token in the `Authorization` header" — still accepted for scripts, but the primary mechanism is the session cookie (CLAUDE.md "Auth"). Params (`limit` default 500, `1 ≤ limit ≤ 5000` at `artifacts.py:197`, `after_id` pairing) verified. Low.

### Root/stray files

**X1.** `BACKEND_BUILD_SUMMARY.txt` ("TARGET DIRECTORY: /sessions/friendly-happy-maxwell/mnt/outputs/pantheon/backend/", "7 modules, 35+ endpoints", `telegram/bot.py`, references `BACKEND_BUILD_COMPLETE.md`/`QUICK_REFERENCE.md` which don't exist), `FILES_CREATED.txt` (describes a README with "Procedural / Emotional / Personality Memory" tiers; notes about `/sessions/bold-funny-ride/` permission constraints), `frontend/BUILD_SUMMARY.txt`, `backend/memory/VERIFICATION_REPORT.txt` — all generator transcripts from the first scaffold. Delete. **X2.** `autoresearch_report.md` — output of `utils/autoresearch.py` (5 iterations, 0 successful mutations) — belongs in `data/` or nowhere. **X3.** `VERSION` + `bump_version.sh` — see C6.

### .env.example vs backend/config.py

Method: `grep -oE '^#?\s*[A-Z_]+=' .env.example` vs field names of `class Settings` (54 fields; `extra="ignore"`, so unknown keys are harmless).
- **E1 (wrong, security):** line 96 "Leave empty to allow all guilds the bot is in" — `messaging/adapters/discord.py:120` "Deny by default — an empty allowlist is not 'allow all'"; CLAUDE.md agrees. Fix the comment (and check the Slack/Matrix/Mattermost comments).
- **E2 (missing Settings fields):** `MATRIX_ALLOWED_ROOM_IDS`, `MATTERMOST_ALLOWED_CHANNEL_IDS` (both deny-by-default → an operator following .env.example gets a bot that ignores everyone), `CONTEXT_FOCUS`, `FILE_CHUNK_STRATEGY`, `PERSONALITY_WEIGHT`, `EMBEDDING_BASE_URL`, `EMBEDDING_API_KEY`, `PREFILL_BASE_URL`, `PREFILL_API_KEY`, `RERANKER_MODEL`, `RERANKER_BASE_URL`, `RERANKER_API_KEY` (last seven are legacy/seed-only — say so or omit deliberately).
- **E3 (missing, read via `os.environ`):** `BROWSER_ENABLED`, `BROWSER_HEADLESS`, `BROWSER_EXECUTABLE_PATH`, `BROWSER_WS_URL`, `PANTHEON_SANDBOX`, `FC_DIR`/`FIRECRACKER_DIR`, `JOB_WORKER_POLL_SECONDS`, `JOB_WORKER_SUPERVISE_SECONDS`, `JOB_STALL_TIMEOUT_SECONDS`, `JOB_STALL_CHECK_SECONDS`, `JOB_AUTO_REQUEUE_MAX`. (`AGENT_HOST_EXEC`, `ALLOW_PRIVATE_FETCH`, `AUTH_SESSION_DAYS`, `ALLOWED_HOSTS` are present.)
- **E4:** `BIND_HOST`, `INSTALL_BROWSER`, `INSTALL_OFFICE` are consumed by `start.sh`/`deploy.sh`, not `Settings` — add a "used by scripts only" comment.
- **R3 again:** the LLM block presents flat keys as the config surface.

### backend/agent/prompts.py (system prompt)

**P1.** l.233 "Web pages, articles, current events → `web_fetch`, `web_search`" — `web_fetch` does not exist; the model is being told to call a tool it doesn't have (browser_tools.py:260 description also says "Use when web_fetch fails"). **P2.** l.~228-240 hard-code `mcp_SubDownload_*` and "Nate B. Jones" examples — user-specific, in a generic prompt (fine for single-user, but a new engineer won't know why). l.~200 "WORKSPACE FILES — Where: data/workspace/ on disk" — per-project workspace is `data/projects/<project_id>/workspace` (`api/files.py:37`); `data/workspace` is only the default-project fallback (`:39`). Verified correct: `create_task` params `job_type/max_turns/execute_instruction/review_instruction/skip_review/timeout_seconds/skill_name` all in schema (`tools.py:370-410`); "Skills vs scheduled tasks" section present as CLAUDE.md says; `save_last_response`, `save_to_artifact`, `update_artifact`, `list_artifacts`, `index_artifact`, `recall`, `create_skill`, `send_telegram`, `git_*`, `github_*`, `run_command` all exist. Not mentioned but exist: `ingest_source`/`batch_ingest_sources`/`list_source_adapters` (the ingestion pipeline is invisible to the model unless a skill names it), `/model` pin, `generate_image`, `convert_document`, financial tools.

### Inline docstrings (spot check)

D1 `memory/extraction.py:7` "prefill/curation model (Nemotron Nano or similar)" → `get_provider_for("extract")` (`:111`). D2 `api/connections.py:3` "Refactored from api/sources.py" — file gone; note frontend leftovers. D3 `api/settings.py:1` "model config, endpoint management, secrets" → endpoints moved to `llm_endpoints.py`; file also owns search providers/security log. D4 `jobs/handlers/autonomous_task.py:1,11` "wraps tasks/autonomous.run_autonomous_task()" — no import of `tasks.autonomous`; builds `AgentCore` itself (`:138`). D5 `jobs/handlers/scheduled_job.py` payload doc lists sinks `email | sms`; only `artifact`, `telegram`, `webhook` registered (`jobs/sinks/__init__.py:49`). D6 `memory/episodic.py:59`, `graph.py:35`, `file_indexer.py:284` keep relative `data/*.db` fallbacks (only when `config` import fails) — contradicts the CLAUDE.md gotcha; harmless at runtime but a landmine for anyone copying the pattern. Accurate docstrings worth pointing new engineers at: `jobs/worker.py`, `jobs/recovery.py`, `jobs/sinks/__init__.py`, `sandbox/__init__.py`, `messaging/channel_store.py`, `security_log.py`, `merge_proposals.py`, `topic_embeddings.py`, `api/llm_endpoints.py`, `api/skills.py` (route-ordering note).

### N. Subsystems with no documentation anywhere (grep over all docs)

| Subsystem | Code | Doc hits |
|---|---|---|
| Messaging gateway (5 adapters, channel→project mapping, Channels tab) | `backend/messaging/`, `api/messaging.py`, `MessagingSettings.jsx` | only the plan that denies it exists; CLAUDE.md mentions "messaging bots" allowlists in one gotcha |
| Sandbox layer (Subprocess/Firecracker, `PANTHEON_SANDBOX`, `scripts/setup_firecracker.sh`) | `backend/sandbox/` | "firecracker" appears in zero docs |
| Search provider chain internals (`SearchProviderManager`, `data/db/search_usage.json`, `/api/settings/search/*`) | `agent/search_providers.py` | USAGE.md user-level only (wrong UI location) |
| Job output sinks + `scheduled_job` payload | `jobs/sinks/`, `handlers/scheduled_job.py` | none |
| SEC EDGAR adapter + financial tools (`analyze_company_financials`, `analyze_earnings_call`, `compare_company_strategy_and_risks`) | `sources/adapters/sec_edgar.py`, `agent/tools.py` | none |
| System updater (`/api/system/update/{check,execute}`, Settings → System Update, `update.sh`) | `api/system.py` | none |
| Task run ledger (`TaskRunStore`, `/api/tasks/runs*`, approve/plan endpoints) | `tasks/runs.py`, `api/tasks.py` | none |
| Chat router / `/model` pin (user-facing) | `llm_config/router.py` | CLAUDE.md only (dense engineer notes) |
| Project export/import + 3-layer import scanner | `api/project_export.py`, `project_import.py`, `ProjectPortability.jsx` | one-liners in CLAUDE.md tree |
| Skills runtime as shipped (hubs/registries, importer, versioning, exporter, publisher, analytics, chaining, editor endpoints) | `backend/skills/*` (12 files, 40 `/api/skills/*` routes) | only inside the historical SKILLS_FEATURE_PLAN.md + SECURITY_FEATURES.md |
| Security audit log | `security_log.py`, `/api/settings/security-log`, `SecurityLog.jsx` | SECURITY_FEATURES.md only |
| Agent tool inventory (60 tools) | `agent/tools.py` | ~20 tools named across docs; no reference list; e.g. `convert_document`, `batch_convert_documents`, `download_file`, `show_file`, `force_merge`, `reject_merge`, `link_concepts`, `create_graph_node`, `start_coding_task`, `list_recent_jobs`, `rerun_job`, `github_*`, `git_*` |
| `integrations/github.py`, `models/discovery.py`, `mcp_client/tavily_credits.py` (`/api/mcp/tavily/*`), `artifacts/{conversions,preview,embedder}.py` | — | none |
| Deployment surface: `docker-compose.yml`, `Caddyfile`, `nginx/`, `setup_options.sh`, `uninstall.sh`, `update.sh`, `git_commit.sh` | root | README covers `deploy.sh`+`make` only; CLAUDE.md none |

## 3. Proposed consolidated doc map

**Keep as canonical (fix in place):**
- `CLAUDE.md` — engineer working context. Apply C1–C9; shrink the tree to directories + one-line purpose (per-package READMEs hold file lists); add short sections for messaging, sandbox, search chain, sinks, system updater, task runs, SEC/financial tools; fix the "no executor/sandbox" rationale; note `VERSION`/`bump_version.sh` removal.
- `README.md` — product/install. Apply R1–R4; configuration section → "first run seeds from `.env`; then Settings → LLMs".
- `backend/README.md` — backend layout + router list generated from `main.py`. Apply B1–B5; link to `docs/` pages instead of restating LLM config.
- `frontend/README.md` — absorb QUICKSTART's unique bits; apply F1–F4; page table from `App.jsx`/`Layout.jsx`.
- `backend/sources/SOURCE_ADAPTERS.md` — apply S1–S4 (it is otherwise the best-maintained design doc).
- `docs/USAGE.md` — apply U1–U3; add "Slash commands" (`/skill`, `/model`) and "Messaging channels" sections.
- `docs/SECURITY_FEATURES.md` → rename `docs/security.md`; keep skills sections, add sandbox layer, auth sessions/KDF, SSRF guard, host-exec gating, WebSocket auth (lift from CLAUDE.md gotchas, leave one-line pointers there).
- `docs/skill-registry-protocol.md` + `docs/examples/minimal-skill-registry/` — accurate; keep.
- `docs/api/artifacts-feed.md` — keep; fix AF1.
- `.env.example` — apply E1–E4; group into "app Settings" / "read directly by modules" / "installer & scripts".

**New docs to write (small, from code):**
- `docs/messaging.md` (adapters, allowlists, channel mappings vault keys, `/api/messaging/*`).
- `docs/skills.md` (runtime as shipped: layout, `/skill` invocation, hubs/registries, scan gate, versioning, publish).
- `docs/tools.md` (the 60 agent tools grouped: storage, memory/graph, ingest, jobs/tasks, git/github, code exec, media, finance, messaging) — could be generated from `TOOL_SCHEMAS`.
- `docs/jobs.md` (job types, sinks, recovery/watchdog, task-run ledger) — or fold into backend/README.md.
- `backend/memory/README.md` (single ≤80-line replacement for the five files).

**Delete:**
- `backend/memory/{INDEX,MANIFEST,QUICK_START,SUMMARY}.md`, `backend/memory/VERIFICATION_REPORT.txt` (after writing the replacement README).
- `BACKEND_BUILD_SUMMARY.txt`, `FILES_CREATED.txt`, `frontend/BUILD_SUMMARY.txt`, `autoresearch_report.md`.
- `VERSION`, `bump_version.sh` (+ the `VERSION` fallback in `main.py:_resolve_app_version`).
- `frontend/QUICKSTART.md` (after merge into frontend/README.md).
- Frontend dead code surfaced by the audit: `src/pages/SourcesPage.jsx`, `sourcesApi` in `client.js`.

**Move to `docs/archive/` (historical, mark status at top):**
- `docs/superpowers/**` (all 15 plans/specs; keep `publisher-subscriber-phase2-notes.md` visible in a `docs/proposals/` if it's still wanted).
- `docs/SKILLS_FEATURE_PLAN.md` (after extracting shipped behaviour into `docs/skills.md`).
- `docs/messaging-gateway-plan.md` (banner → "Implemented; historical design").
- `docs/mcp-registry-protocol.md` + `docs/examples/minimal-registry/` → `docs/proposals/` with "Status: Proposed — no implementation yet" (MR1).
- `REVIEW.md` — either delete (CLAUDE.md + README cover it) or cut to the "Start here" + "Design rationale pointers" + test command, dropping the changelog, test tallies, version string and the `?token=` row.
