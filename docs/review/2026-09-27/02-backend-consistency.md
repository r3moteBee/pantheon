# Pantheon backend — architecture / consistency review (read-only)

Scope: `/home/user/pantheon/backend` (45.5k lines of Python, 47 test files / 404 test functions) plus the root build/deploy scaffolding, checked against the intended architecture in `CLAUDE.md`. No files were modified. Every claim below was verified by reading code or by grep; where a count is approximate it says so.

Framing: the owner wants to keep the harness flexible and configurable. Nothing below recommends removing configurability for its own sake. The findings are (a) genuine duplication, (b) code that nothing reaches, (c) features that are silently broken, and (d) low-value complexity or drift between docs and code.

---

## (a) Summary table

| ID | Category | Finding | Impact | Effort | Files |
|----|----------|---------|--------|--------|-------|
| F1 | duplication / bug | `telegram_bot/` is an 11-line re-export shim over `messaging/adapters/telegram.py`; still imported from 3 places, and the telegram job sink imports a function the shim doesn't export, so the sink can never work | Med | S | `backend/telegram_bot/bot.py`, `backend/jobs/sinks/telegram_sink.py:16`, `backend/agent/tools.py:2936`, `backend/api/settings.py:332` |
| F2 | dead-code | Pre-jobs "task runs" subsystem survives: `tasks/autonomous.py` has zero importers; `tasks/runs.py` is written only by that dead module; 4 `/api/tasks/runs*` routes read a table `JobStore` already migrates away from; the frontend `taskRunsApi` export is never called | Med | S | `backend/tasks/autonomous.py`, `backend/tasks/runs.py`, `backend/api/tasks.py:140-177`, `backend/jobs/store.py:98-152`, `frontend/src/api/client.js:173-178` |
| F3 | dead-code / duplication | `scheduled_job` handler + `jobs/sinks/*` (≈314 lines): nothing creates a `scheduled_job` job — `schedule_scheduled_job()` has no callers, no agent tool exposes `output_sink`, and the only generic creator (`POST /api/jobs`) is never called by the UI. Functionally a strict subset of `autonomous_task` | Med | S–M | `backend/jobs/handlers/scheduled_job.py`, `backend/jobs/sinks/`, `backend/tasks/scheduler.py:354-425` |
| F4 | dead-code | `extraction` and `file_indexing` job handlers are registered but never enqueued; chat runs extraction via `spawn()` inline and `/files/index` runs `FileIndexer` inline | Low | S | `backend/jobs/handlers/extraction.py`, `backend/jobs/handlers/file_indexing.py`, `backend/api/chat.py:507,568,866`, `backend/api/files.py:370-384` |
| F5 | broken feature | `MemoryManager.working` (`memory/working.py`) is never populated on any real path, so `consolidate_session()` always returns "No messages to consolidate." — the `consolidate_memory` agent tool and `POST /memory/consolidate` are no-ops. The real working buffer is `AgentCore.working_memory` | High | M | `backend/memory/working.py`, `backend/memory/manager.py:147,522-528,595`, `backend/agent/core.py:169`, `backend/agent/tools.py:3144`, `backend/api/memory.py:354-361` |
| F6 | inconsistency / bug | `ArchivalMemory` is constructed by `MemoryManager` with default `base_dir="data"` (CWD-relative) while the API constructs it with `settings.data_dir` — the exact path gotcha CLAUDE.md warns about. Archival also duplicates what the artifact store does (durable markdown notes) | Med | S | `backend/memory/archival.py:22-26`, `backend/memory/manager.py:159,175`, `backend/api/memory.py:263-350` |
| F7 | dead-code | Legacy LLM plumbing in `api/settings.py`: 15 `llm_*/prefill_*/vision_*/embedding_*/reranker_*` request fields, their PUT branches, `/settings/models`, `/settings/test-connection`, and the `reset_provider()` key list. After `llm_config_migrated_v1` nothing reads those vault keys (only `llm_config/migration.py` does). The frontend never calls `listModels`/`testConnection`/`restartTelegram`. `/settings/restart-telegram` duplicates `/messaging/{adapter}/restart` | Med | S | `backend/api/settings.py:21-35,103-118,170-199,273-336,398-402` |
| F8 | inconsistency (layering) | Core/memory modules import runtime helpers from an API router module (`api.settings`) | Low | S | `backend/agent/core.py:344`, `backend/memory/file_indexer.py:388`, `backend/api/settings.py:73-96,151-157` |
| F9 | duplication | Two "run this job again" endpoints on two routers backed by two near-identical store methods: `POST /jobs/{id}/rerun` (tasks router, `JobStore.rerun`) vs `POST /jobs/{id}/retry` (jobs router, `JobStore.retry`) | Low | S | `backend/api/tasks.py:179-199`, `backend/api/jobs.py:74-81`, `backend/jobs/store.py:218,482` |
| F10 | duplication / dead column | Per-project settings live in `phase_g.db.project_settings` (persona, tone_weight, context_focus, skill_discovery) but the backend never reads tone_weight/context_focus/persona from it; `skill_discovery` is actually stored in the vault as `skill_discovery_<project>`; global `personality_weight`/`context_focus` live in the vault via `/settings`. Same knobs, two stores, one unread | Med | M | `backend/api/projects.py:385-442`, `backend/api/skills.py:181-199`, `backend/api/chat.py:290,632`, `backend/data/migrations/002_phase_g.sql:42` |
| F11 | stale scaffolding / docs | Root `VERSION` + `bump_version.sh` implement a superseded `YYYY-MM-DD-##` scheme and would corrupt the current `YYYY.MM.DD.HXX` package.json version; `BACKEND_BUILD_SUMMARY.txt`, `FILES_CREATED.txt`, `autoresearch_report.md`, `git_commit.sh` are build-session leftovers; `backend/README.md`, `REVIEW.md`, `docs/messaging-gateway-plan.md`, `CLAUDE.md` all disagree with the code on router/adapter counts, directories, and what's implemented | Low–Med | S | `VERSION`, `bump_version.sh`, `backend/README.md`, `REVIEW.md`, `docs/messaging-gateway-plan.md:3`, `CLAUDE.md` |
| F12 | config / inconsistency | `chroma_host` defaults to `"localhost"`, so every local start first tries an HTTP Chroma client, fails, logs a WARNING, and falls back to the embedded client (in two places). Docker: `docker compose build` without `deploy.sh` yields version `0.0.0-dev`; the frontend is served four different ways across backend/nginx/frontend-container/Caddyfile | Low | S–M | `backend/config.py:105`, `backend/memory/semantic.py:60-81`, `backend/memory/episodic.py:146-157`, `backend/main.py:64-96`, `backend/Dockerfile`, `docker-compose.yml`, `Caddyfile`, `nginx/nginx.conf` |
| F13 | config | 12 `Settings` env names missing from `.env.example`; 14 `os.getenv` reads outside `config.py` (job worker/watchdog/browser/sandbox tunables) that appear in neither `Settings` nor `.env.example`; `recall_token_budget` is defined and documented but never read | Low | S | `backend/config.py`, `.env.example`, `backend/jobs/worker.py:35-36`, `backend/jobs/watchdog.py:16-17`, `backend/jobs/recovery.py:26`, `backend/agent/browser_tools.py:27-58`, `backend/sandbox/__init__.py:28`, `backend/main.py:170` |
| F14 | inconsistency (storage) | 13 SQLite databases under `db_dir`; 11 stores apply `apply_sqlite_pragmas`, but `phase_g.db` (a real store) does not, and ~12 ad-hoc `sqlite3.connect` sites in routers/handlers bypass the store classes. `project_export.py` still probes legacy `data/episodic.db` paths | Low | S | `backend/api/projects.py:397`, `backend/api/project_export.py:60-100`, `backend/api/project_import.py:576,701`, `backend/api/conversations.py:103,128`, `backend/api/memory.py:490`, `backend/jobs/handlers/autonomous_task.py:630-636` |
| F15 | dead-code / contradiction | `skills/executor.py` (210 lines) + `SandboxBackend.execute_skill` have no callers; no bundled skill ships scripts; CLAUDE.md says "there is no executor". `scanner.py` (495 lines) largely scans for script behaviours that can't execute. `docs/mcp-registry-protocol.md` + `docs/examples/minimal-registry` specify an MCP registry client that has no implementation | Med | M | `backend/skills/executor.py`, `backend/sandbox/backend.py`, `backend/sandbox/subprocess_backend.py:33-54`, `backend/skills/scanner.py`, `docs/mcp-registry-protocol.md` |
| F16 | inconsistency | Tool error contract is implicit: tools return free-form strings (`Error: …` ×13, `Failed …` ×1, `No …` ×4) and `api/chat.py` detects tool errors with a regex on `error\b|failed\b`; 8 routers mix `HTTPException` with 200-status `{"error": …}` bodies | Low | M | `backend/agent/tools.py:1729,4170`, `backend/api/chat.py:142`, `backend/api/mcp_oauth.py`, `backend/api/skills.py`, `backend/api/artifacts.py` |
| F17 | inconsistency | Provider access is split: 36 call sites use legacy role getters (`get_provider()` ×20, etc.) vs 21 class-based `get_provider_for("…")`; `coding_task` routes to `"code"` while `iteration_loop` (also a coding loop) uses `get_provider()` → agent | Low | S | `backend/models/provider.py`, `backend/jobs/handlers/coding_task.py:78`, `backend/jobs/handlers/iteration_loop.py` |
| F18 | low-value complexity | Tavily-specific credit tracking baked into the generic MCP layer: `mcp_client/tavily_credits.py` (279 lines), 5 `/mcp/tavily/*` routes, 23 references in `manager.py`, 38 in `api/mcp.py` | Low | M | `backend/mcp_client/tavily_credits.py`, `backend/mcp_client/manager.py`, `backend/api/mcp.py` |
| F19 | inconsistency (jobs) | Two coding stories: `iteration_loop` instructs the agent to use GitHub-API tools (`github_write_files`, `github_create_branch`) with a branch-policy block, while `coding_task` instructs local-first `git_*` tools + `run_command`. Both are reachable; they disagree on toolchain | Low | M | `backend/jobs/handlers/iteration_loop.py:62-100,265-280`, `backend/jobs/handlers/coding_task.py:88-120` |
| F20 | test coverage | Large surfaces with zero tests: messaging (≈3.3k lines), MCP OAuth, watchdog, 5 of 7 job handlers, most routers, all of the skills registry/importer/scanner/editor, sandbox, `MemoryManager`, similarity/merge pipeline | Med | L | see Appendix E |

---

## (b) Findings in detail

### F1 — `telegram_bot/` shim and the broken telegram sink

`backend/telegram_bot/bot.py` is 11 lines:

```python
"""Telegram bot integration — backward-compatible shim.
All logic now lives in :mod:`messaging.adapters.telegram`. ..."""
from messaging.adapters.telegram import (  # noqa: F401
    start_telegram_bot, stop_telegram_bot, restart_telegram_bot, send_message_to_all,
)
```

It is not wired into `main.py` (startup goes through `messaging.gateway.get_messaging_gateway()` at `main.py:148-150`). Remaining importers, all lazy:

- `backend/agent/tools.py:2936` — `from telegram_bot.bot import send_message_to_all`
- `backend/api/settings.py:332` — `from telegram_bot.bot import restart_telegram_bot` (the `/settings/restart-telegram` endpoint, which the frontend never calls; `/api/messaging/{adapter}/restart` is the live equivalent)
- `backend/jobs/sinks/telegram_sink.py:16` — `from telegram_bot.bot import send_message_to_all, send_message_to`

The third is a latent bug: `send_message_to` does not exist in either the shim or `messaging/adapters/telegram.py` (only `send_message_to_all` at line 627), so the import raises and the sink always returns `{"sink": "telegram", "status": "unsupported", ...}`. The subsequent `"send_message_to" in dir()` check (line 26) is also always true once the import succeeds, so the branch logic wouldn't work even if the function existed.

`docs/messaging-gateway-plan.md:160` planned the shim as "during transition"; the transition is complete.

**Recommendation:** repoint the three imports at `messaging.adapters.telegram`, delete `backend/telegram_bot/`, and either add a real `send_message_to(chat_id, text)` to the Telegram adapter or drop the sink (see F3).

### F2 — Legacy task-runs subsystem

- `backend/tasks/autonomous.py` (103 lines, `run_autonomous_task`) has **zero importers** anywhere in `backend/` (import-graph scan, Appendix C). It is the pre-jobs execution path.
- `backend/tasks/runs.py` (187 lines, `task_runs.db`) is written only by `tasks/autonomous.py` (`start_run/complete_run/fail_run`) and read by `api/tasks.py:140-177` (`GET/DELETE /tasks/runs*`, `POST /tasks/runs/{id}/cancel`).
- `backend/jobs/store.py:98-152` contains `_migrate_from_task_runs()` — a one-shot import of `task_runs.db` rows into `jobs.db`, i.e. the jobs system already considers task_runs legacy.
- Frontend: `client.js:173-178` exports `taskRunsApi`; `Settings.jsx:4` imports it; `grep -c "taskRunsApi\." Settings.jsx` = 0. The comment at `Settings.jsx:1007` ("uses /api/tasks/runs") is stale — that dashboard uses `projectsApi`/`jobsApi`.

**Recommendation:** delete `tasks/autonomous.py`, `tasks/runs.py`, the four `/tasks/runs*` routes, the `taskRunsApi` export, and (after confirming the migration has run on the dev box) the `_migrate_from_task_runs` block. Leave the SQL migration file alone.

### F3 — `scheduled_job` handler + sinks are unreachable

`tasks/scheduler.py:366 schedule_scheduled_job()` is the only creator of `job_type="scheduled_job"` and has no callers (`grep -rn schedule_scheduled_job` → only its definition and its own APScheduler target at line 357). The agent's `create_task` tool only allows `["autonomous_task", "iteration_loop"]` (`agent/tools.py:372,2857`). `POST /api/jobs` (`api/jobs.py:57`) accepts an arbitrary `job_type`, but `jobsApi.create` is never called from the frontend.

Handler body comparison (`scheduled_job.py:69-117` vs `autonomous_task.py:139-265`): both build `create_memory_manager(...)`, `AgentCore(host_exec=host_exec_allowed("background"), ...)`, wrap `agent.run_autonomous(prompt)` in `pinger_for(...)`. `scheduled_job` adds only (a) misfire coalescing within `interval_seconds` and (b) routing output to a sink. `autonomous_task` adds skill resolution, plan injection, the task ledger, completion notices, repo delta. The sinks package (`jobs/sinks/__init__.py` + 3 sinks, 197 lines) exists only for this handler, and its telegram sink is broken (F1).

**Recommendation:** either (preferred, keeps the capability) add optional `output_sink` + coalescing to `autonomous_task` and delete `scheduled_job` + `jobs/sinks`, or delete both outright. Also drop `scheduled_job` from the job-type lists in `CLAUDE.md`/`README.md`.

### F4 — `extraction` / `file_indexing` handlers never enqueued

`grep -rn 'job_type="extraction"\|job_type="file_indexing"'` finds no creators. Only `image_extraction` is enqueued (`api/chat.py:942`). Extraction runs inline via `spawn(_run_background_extraction(...))` (`api/chat.py:507,568,866`) and `/files/index` calls `FileIndexer` directly (`api/files.py:380-384`). `jobs/store.py:307` even filters these two types out of listings by default. The handlers are small (≈60 lines each) but they are two more job types to document and reason about.

**Recommendation:** either route the inline paths through the job system (you'd gain heartbeats/cancel/visibility for long index runs) or delete the handlers.

### F5 — `consolidate_memory` is a silent no-op

`memory/manager.py:147` creates `self.working = WorkingMemory(...)`. The only writer is `remember(tier="working")` (`manager.py:190`), which is reachable from the `remember` agent tool and `POST /memory/store` but nothing on the chat path calls it (`grep -rn "\.working\.add_message\|mgr\.working"` outside `manager.py` → nothing). `AgentCore` keeps its own conversation buffer (`agent/core.py:169 self.working_memory: list[dict]`).

`consolidate_session()` (`manager.py:522-528`):

```python
messages = self.working.get_messages(as_dicts=True)
if not messages:
    return "No messages to consolidate."
```

So the `consolidate_memory` tool (`agent/tools.py:3144`) and `POST /memory/consolidate` (`api/memory.py:354-361`) always return that string. The summarisation + extraction logic below it (lines 530-600) is well-built but unreachable. Meanwhile `run_extraction_on_recent()` (`manager.py:607`, used by chat) does the extraction half correctly from episodic.

**Recommendation (structural):** make `consolidate_session` read the session's recent messages from episodic (as `run_extraction_on_recent` already does) and delete `memory/working.py`; or make `MemoryManager.working` *the* working buffer and have `AgentCore` use it. Two "working memory" concepts is the root cause.

### F6 — `ArchivalMemory` path bug and overlap with artifacts

`memory/archival.py:22-26`:

```python
def __init__(self, project_id: str = "default", base_dir: str | None = None):
    self._base = Path(base_dir or "data")
```

`memory/manager.py:159,175` call `ArchivalMemory(project_id=project_id)` with no `base_dir`, so `remember(tier="archival")` from the agent writes under `<CWD>/data/projects/<id>/notes` (i.e. `backend/data/…` on the dev box — the same class of bug CLAUDE.md calls out for DB paths). `api/memory.py:271,285,304,318,333,348` pass `base_dir=str(s.data_dir)`, so the seven `/memory/archival/*` routes read from a different directory than the agent writes to.

Beyond the bug: archival is "file-based long-term notes" — the artifact store already provides durable, indexed, versioned markdown with recall integration. CLAUDE.md already labels archival "mostly unused".

**Recommendation:** at minimum pass `settings.data_dir` in the manager. Better: fold `remember(tier="archival")` into `save_to_artifact` under a `notes/` folder and retire the module + 7 routes.

### F7 — Legacy LLM plumbing in `api/settings.py`

What's dead post-migration (verified by `grep -rn 'get_secret("llm_\|prefill_\|vision_\|embedding_\|reranker_'` → only `llm_config/migration.py` and `api/settings.py` itself):

- `SettingsUpdate` fields lines 21-35 (15 fields) and their PUT branches lines 170-199
- `_get_effective_settings` keys lines 103-118
- `GET /settings/models` (273-284) — reads the legacy `llm_base_url` vault key
- `GET /settings/test-connection` (287-325) — works only because legacy getters alias the new classes
- `POST /settings/restart-telegram` (328-336) — duplicate of `/messaging/{name}/restart`
- `PUT /secrets/{key}` reset list (398-402)

Frontend usage check: `settingsApi.listModels/testConnection/restartTelegram` have no callers in `frontend/src` (only defined in `client.js:298-303`).

What's live and should stay: messaging tokens/allowlists (used by `MessagingSettings.jsx` and read by adapters via the vault), `memory_recall_enabled`, `personality_weight`, `context_focus`, `file_chunk_*` (Chat.jsx, Settings.jsx), security log, `/secrets`, search providers.

**Recommendation:** strip the LLM section (~120 lines, 3 endpoints), delete `SettingsUpdate.llm_*` fields, and rename the module's docstring — "model config, endpoint management" is no longer what it does.

### F8 — Core imports from the API layer

`agent/core.py:344 from api.settings import is_memory_recall_enabled` and `memory/file_indexer.py:388 from api.settings import get_active_chunk_settings`. These are runtime-setting readers that happen to live in a router module. Move them to `config.py` (or a small `runtime_settings.py`) so `agent/` and `memory/` don't depend on `api/`.

### F9 — `rerun` vs `retry`

`api/tasks.py:179 POST /jobs/{job_id}/rerun` → `JobStore.rerun()` (`jobs/store.py:218`, allows any terminal status, copies schedule binding, used by orphan recovery). `api/jobs.py:74 POST /jobs/{job_id}/retry` → `JobStore.retry()` (`jobs/store.py:482`, failed/stalled/cancelled only). Two routers, two methods, one concept. Keep `rerun` (superset), make `retry` an alias or delete it, and move the route onto the jobs router.

### F10 — Two homes for per-project knobs

`api/projects.py:385-442` owns `phase_g.db.project_settings` with columns `persona, tone_weight, context_focus, skill_discovery`. Readers of those columns outside that file: none (`grep -rn "tone_weight\|project_settings"` → only `api/projects.py`; `skill_discovery` hits are all vault reads of `skill_discovery_<project>` in `api/skills.py:186-197`, `api/chat.py:290,632`, `messaging/adapters/discord.py:415`). The frontend writes tone/context here (`ProjectSettingsPanel.jsx:104`) but the chat path applies the *global* vault `personality_weight`/`context_focus` from `/settings`. So the per-project values are stored and never applied, and the `skill_discovery` column is shadowed by the vault.

**Recommendation:** pick one store for project-scoped settings (the vault already holds `skill_discovery_<project>`; or `projects.json`), have `_build_agent` read per-project overrides with global fallback, and drop the unread columns/table.

### F11 — Stale scaffolding and documentation drift

- `VERSION` = `2026-04-05-01`; `bump_version.sh` writes that format, seds `version="YYYY-MM-DD-##"` literals in `backend/main.py` and `Layout.jsx`/`LoginPage.jsx` that no longer exist (Layout fetches `/api/health`), and rewrites `package.json` to `YYYY.MM.DD` — running it would replace `2026.09.27.H6` with `2026.09.27`, breaking the H-suffix convention. `main.py:_resolve_app_version` only reaches `VERSION` as a third fallback.
- `BACKEND_BUILD_SUMMARY.txt` (334 lines, references `/sessions/friendly-happy-maxwell/mnt/outputs/…`), `FILES_CREATED.txt`, `autoresearch_report.md` (a generated run report) and `git_commit.sh` (a workaround for `.git/index.lock`) are build-session artifacts.
- `backend/README.md` lists `secret_storage/` and `plugins/` directories that don't exist, says "19 routers" but names `sources` (no such router) and omits `messaging`, describes "role mapping" (superseded by task-class routes), and says `telegram_bot/` is the Telegram implementation. `REVIEW.md` repeats "19 API routers".
- `docs/messaging-gateway-plan.md:3` says "⚠️ Proposed — not yet implemented … no `backend/messaging/` gateway … exist yet" — all of it exists (5 adapters, gateway, 9 routes).
- `CLAUDE.md`: says 18 routers (actual 19 — Appendix A), 28 adapters across 9 mechanisms (actual 29/10 — `sec_edgar` is registered in `adapters/__init__.py:16`), `tools.py` "1500+ lines" (4523), job types list omits nothing but includes the unreachable `scheduled_job`.

**Recommendation:** delete `VERSION`, `bump_version.sh`, the two `.txt` files, `autoresearch_report.md`, `git_commit.sh`; fix the three docs; drop the `VERSION` fallback in `_resolve_app_version`.

### F12 — Chroma default and Docker/serving layout

- `config.py:105 chroma_host: str = "localhost"`. `semantic.py:63-81` and `episodic.py:148-157` try `chromadb.HttpClient(...).heartbeat()` first and fall back to `PersistentClient` with a WARNING. Local mode (the documented deployment) therefore logs a spurious connection failure on first use per process and pays a connect timeout. Defaulting to `""` makes embedded the default; compose already sets `CHROMA_HOST=chromadb` explicitly.
- `backend/Dockerfile` context is `./backend` and does not copy `frontend/package.json` or `VERSION`; `main.py:81` looks for `backend/package.json`, which only exists if `deploy.sh:965` (`cp frontend/package.json backend/package.json`) ran. `make build` / `docker compose build` produce a backend reporting `0.0.0-dev`.
- Frontend serving: backend SPA static (`main.py:325-343`), a separate `frontend` nginx container (`frontend/Dockerfile`), the root `nginx` service proxying to both, and a `Caddyfile` with a hard-coded `/path/to/pantheon/frontend/dist`. The compose `frontend` + `nginx` pair is redundant with the backend's own static serving.
- Compose `depends_on: searxng` but nothing sets `SEARCH_URL` to it inside compose (deploy.sh may). Compose mounts `./skills:/skills:ro`; that does resolve correctly because `skills/registry.py:34` computes `<repo>/skills` = `/skills` inside the container.

### F13 — Config surface

`Settings` fields with no reader: `recall_token_budget` (0 uses; documented in `.env.example` as "Max tokens of recalled context"). Fields read only via `getattr`/property: `auth_session_days`, `allow_private_fetch`, `cors_origins` — fine.

Env names in `config.py` missing from `.env.example`: `CONTEXT_FOCUS, EMBEDDING_API_KEY, EMBEDDING_BASE_URL, FILE_CHUNK_STRATEGY, MATRIX_ALLOWED_ROOM_IDS, MATTERMOST_ALLOWED_CHANNEL_IDS, PERSONALITY_WEIGHT, PREFILL_API_KEY, PREFILL_BASE_URL, RERANKER_API_KEY, RERANKER_BASE_URL, RERANKER_MODEL`. (`BIND_HOST`, `INSTALL_OFFICE`, `INSTALL_BROWSER` in `.env.example` are script/build vars, not `Settings` — fine but worth a comment.)

Direct `os.getenv` outside `config.py` (14 sites): `JOB_WORKER_ENABLED` (`main.py:170`), `JOB_STALL_CHECK_SECONDS`/`JOB_STALL_TIMEOUT_SECONDS` (`jobs/watchdog.py:16-17`), `JOB_WORKER_POLL_SECONDS`/`JOB_WORKER_SUPERVISE_SECONDS` (`jobs/worker.py:35-36`), `JOB_AUTO_REQUEUE_MAX` (`jobs/recovery.py:26`), `BROWSER_ENABLED/HEADLESS/WS_URL/EXECUTABLE_PATH` + UA (`agent/browser_tools.py:27-58`), `PANTHEON_SANDBOX` (`sandbox/__init__.py:28`), `FIRECRACKER_DIR`/`FC_DIR` (`sandbox/firecracker_backend.py:58`). None are in `Settings` or `.env.example`, so they're undiscoverable. Keep them configurable — just declare them.

Note also the `llm_*`… `reranker_*` fields in `Settings` (15 fields, lines 12-36) are only consumed by the one-shot migration and `api/settings.py`; once F7 is done they can go too, with `.env.example` pointing users to Settings → Endpoints.

### F14 — SQLite consistency

13 databases (Appendix D). `apply_sqlite_pragmas` is applied in 11 store modules. Gaps:

- `api/projects.py:397 _phase_g_connect()` — a real store (`project_settings`) opened without pragmas; it also `executescript`s the migration on every request.
- Ad-hoc connections that bypass the owning store: `api/project_import.py:576,701`, `api/project_export.py:73,101,119,162`, `api/conversations.py:103,128`, `api/memory.py:490`, `api/projects.py:308,330`, `utils/self_doc.py:85`, `jobs/handlers/autonomous_task.py:630-636` (writes `conversations.title` directly instead of through `EpisodicMemory`).
- `api/project_export.py:66-67,94-95` still probe `"data/episodic.db"` and `data_dir/"episodic.db"` as legacy fallbacks.
- `scheduler.db` is an APScheduler SQLAlchemy jobstore (pragmas not applicable).

**Recommendation:** add a `set_conversation_title` method to `EpisodicMemory`, route the export/import readers through the stores or at least through `apply_sqlite_pragmas`, and drop the legacy path probes.

### F15 — Skill executor, scanner scope, and the MCP-registry spec

- `skills/executor.py` (210 lines, `execute_script`) is only referenced by `sandbox/subprocess_backend.py:46` and `sandbox/firecracker_backend.py:88`, both inside `SandboxBackend.execute_skill`. `grep -rn "\.execute_skill(\|\.execute_script("` outside `sandbox/` and `skills/executor.py` → nothing. Only `execute_inline` (the `code_execute` tool, `agent/tools.py:3183,3221`) is used. No bundled skill has a `scripts/` dir or `*.py`/`*.sh` (`find ../skills -name '*.py' -o -name '*.sh'` → none). This matches CLAUDE.md's "Skills are markdown recipes… There is no executor" — except the executor exists.
- `skills/scanner.py` (495 lines) is wired (`/skills/scan/*`, quarantine, `sec_log`), and the UI uses it heavily. But much of layer 1/2 targets script behaviour (shell injection, `os.environ`, eval/exec) that can't run. It still has value as import hygiene for prompt-injection-shaped instructions; the recommendation is to trim the script-oriented rules, not remove scanning.
- `docs/mcp-registry-protocol.md` (304 lines) + `docs/examples/minimal-registry/server.py` describe an MCP *server* registry protocol. There is no client for it in `mcp_client/` or `api/mcp.py` (`grep -n registry api/mcp.py` → 0). `skills/importer.py` explicitly says MCP registries are out of scope for skills. It is a spec with no implementation.

The rest of the skills stack is real: 45 routes in `api/skills.py`, 51 corresponding calls in `client.js:381-475`; importer has 4 built-in hub adapters (`skill_md`, `github`, `clawhub`, `local`) plus configurable generic registries; publisher/exporter/versioning/analytics are all reached from routes.

**Recommendation:** delete `skills/executor.py` and the `execute_skill` abstract method (keep `execute_inline` and the sandbox backends), mark `mcp-registry-protocol.md` as unimplemented or remove it, and trim scanner rules to what matters for markdown skills.

### F16 — Error and logging conventions

Logging is consistent: `logging.basicConfig` once in `main.py:36`, zero `print()` in runtime code, 371 `%`-style vs 54 f-string logger calls (minor).

Routers: ~200 `raise HTTPException` sites, but eight files also return 200-status `{"error": …}` bodies (`artifacts` 2, `chat` 2, `mcp_oauth` 4, `skills` 3, `llm_endpoints` 1, `mcp` 1, `messaging` 1, `projects` 1). `main.py` middleware returns `{"error": …}` JSON with proper status codes, which is fine.

Agent tools: `execute_tool()` returns `str` (`agent/tools.py:1729-1737`); errors are free-form (`return f"Error: …"` ×13, `f"Failed …"` ×1, `"No …"` ×4, plus the catch-all `f"Error executing {tool_name}: {e}"` at line 4170). `api/chat.py:142` then classifies tool errors for routing-tuning metrics with `_TOOL_ERROR_RE = r"\s*(error\b|.{0,80}?\bfailed\b)"` — a regex over prose is the de-facto contract, and it will misfire on legitimate content that starts with "Error handling in…".

**Recommendation:** adopt one tool-error envelope (e.g. always `Error: ` prefix, or return a small dataclass and stringify at the boundary) and have `_TOOL_ERROR_RE` match only that. Decide once whether routers signal failure via status or body and fix the eight outliers.

### F17 — Provider access split

`get_provider()` ×20, `get_embedding_provider()` ×5, `get_vision_provider()` ×5, `get_prefill_provider()` ×3, `get_reranker_provider()` ×3 vs `get_provider_for("…")` ×21. CLAUDE.md says the legacy getters are aliases and call sites should ask by class. Two jobs that do coding work disagree: `coding_task.py:78 get_provider_for("code")`, `iteration_loop.py` uses `get_provider()`. Mechanical cleanup.

### F18 — Tavily in the generic MCP layer

`mcp_client/tavily_credits.py` (279 lines), 5 routes (`/mcp/tavily/usage|thresholds|reset-daily|reset-monthly|test-direct`), 23 `tavily` references in `mcp_client/manager.py`, 38 in `api/mcp.py`. This is one vendor's pricing table living inside the connection manager. If the owner uses Tavily daily it earns its keep; otherwise generalise to a per-connection call budget (which the search-provider manager in `agent/search_providers.py` already models with `daily_limit`/`monthly_limit`) and delete the vendor module.

### F19 — Jobs: frank assessment of overlap

- **`scheduled_job` vs `autonomous_task`**: merge (F3). `scheduled_job` is a subset plus two small features.
- **`iteration_loop` vs `coding_task`**: different shapes, both legitimate. `iteration_loop` (478 lines) is a generic execute/review loop with per-turn artifacts and stall recovery; `coding_task` (186 lines) is a single run with a local checkout and a PR at the end. Don't merge. But they encode two incompatible coding workflows: `iteration_loop._branch_policy_block` (lines 62-100) tells the agent to use `github_write_files`/`github_create_branch`/`github_read_file(ref=…)` (API-only, no tests can run), while `coding_task` (lines 88-120) says "local-first — work in a real checkout so you can run the tests" via `git_sync_repo`/`run_command`/`git_commit`/`git_push_pr` and calls the GitHub API tools "a fallback for trivial single-file edits". Pick the local-first story for both, or make the branch-policy block toolchain-aware.
- **`extraction` / `file_indexing`**: unreachable (F4).
- Handler registration is complete: all 7 modules in `jobs/handlers/` are imported by `bootstrap.py` and each carries `@register`.

### F20 — Test coverage gaps

See Appendix E. Summary: 47 files, 404 `def test_`. Untested surfaces with the most runtime risk: `messaging/*` (5 adapters + gateway + channel store, ≈3.3k lines, deny-by-default allowlists), `mcp_client/oauth.py` (537 lines of PKCE/DCR/refresh), `jobs/watchdog.py`, `jobs/handlers/{scheduled_job,coding_task,extraction,file_indexing}` (and `iteration_loop` beyond truncation), `memory/manager.py` (recall/budget/rerank), `sources/similarity.py` + `memory/merge_proposals.py` (merge rewrites edges), the whole `skills/` package except models/registry, `api/{tasks,jobs,projects,project_export,project_import,memory,skills,personas,personality,settings,mcp_oauth,messaging}`.

---

## (c) Appendices

### Appendix A — Router inventory (`backend/main.py:290-313`)

19 `include_router` calls, all `prefix="/api"`, plus the WebSocket at `/ws/chat`:

| # | Router module | Tag | Routes | Notes |
|---|---------------|-----|--------|-------|
| 1 | `api/auth.py` | auth | 4 | |
| 2 | `api/chat.py` | chat | 5 (incl. `/ws/chat`) | REST `/chat` and WS share `_build_agent`/`_stream_turn`/`_route_turn` |
| 3 | `api/files.py` | files | 13 | |
| 4 | `api/memory.py` | memory | 28 | 7 archival routes (F6); `/memory/consolidate` no-op (F5) |
| 5 | `api/personality.py` | personality | 6 | soul.md/agent.md |
| 6 | `api/projects.py` | projects | 12 | mounts `project_export`/`project_import`; `/projects/{id}/settings` (F10) |
| 7 | `api/settings.py` | settings | 15 | legacy LLM section dead (F7) |
| 8 | `api/mcp.py` | mcp | 15 | 5 are `/mcp/tavily/*` (F18) |
| 9 | `api/mcp_oauth.py` | mcp-oauth | 3 | |
| 10 | `api/skills.py` | skills | 45 | all but ~3 called from `client.js` |
| 11 | `api/tasks.py` | tasks | 14 | 4 `/tasks/runs*` dead (F2); `/jobs/{id}/rerun` lives here (F9) |
| 12 | `api/personas.py` | personas | 6 | |
| 13 | `api/system.py` | system | 4 | sandbox health, update check/execute, self-doc |
| 14 | `api/connections.py` | connections | 9 | GitHub PAT + project repo binding |
| 15 | `api/artifacts.py` | artifacts | 25 | |
| 16 | `api/conversations.py` | conversations | 6 | |
| 17 | `api/jobs.py` | jobs | 6 | `/jobs/{id}/retry` (F9) |
| 18 | `api/llm_endpoints.py` | llm | 20 | `/llm/roles` legacy shim retained by design |
| 19 | `api/messaging.py` | messaging | 9 | |

Router files not mounted: none (all 19 `router = APIRouter()` modules are mounted; `project_export.py`/`project_import.py` attach to the projects router). Doc drift: CLAUDE.md says 18; `backend/README.md` and `REVIEW.md` say 19 but list `sources` (doesn't exist) and omit `messaging`.

Duplicate/overlapping routes: `/jobs/{id}/rerun` vs `/jobs/{id}/retry`; `/settings/restart-telegram` vs `/messaging/{name}/restart`; `/tasks/runs*` vs `/jobs*`; `/settings` (global tone/context) vs `/projects/{id}/settings` (per-project tone/context, unread); `/settings/models` + `/settings/test-connection` vs `/llm/probe` + `/llm/endpoints`.

### Appendix B — Config inventory

`Settings` fields and non-test usage counts (dot-access; `config.py` excluded):

| Field | Uses | Note |
|-------|------|------|
| `llm_base_url`, `llm_api_key`, `llm_model`, `llm_prefill_model`, `prefill_base_url`, `prefill_api_key`, `llm_vision_model`, `vision_base_url`, `vision_api_key`, `embedding_base_url`, `embedding_api_key`, `reranker_model`, `reranker_base_url`, `reranker_api_key` | 3–5 each | only `llm_config/migration.py` + `api/settings.py` — dead after migration (F7) |
| `embedding_model` | 10 | includes live embed calls; verify before removing |
| `vault_master_key` 1, `secret_key` 2, `auth_password` 9, `allowed_hosts` 1 | | live |
| `auth_session_days` | 0 direct | read via `getattr` in `api/auth.py:26` — live |
| `telegram_*`, `discord_*`, `slack_*`, `matrix_*`, `mattermost_*` | 2–4 each | live (adapters + settings) |
| `app_env` 1, `log_level` 1 | | live |
| `cors_origins` | 0 direct | via `cors_origins_list` property — live |
| `agent_host_exec` 2, `allow_private_fetch` (getattr in `utils/net.py:29`) | | live |
| `search_url` 7, `search_api_key` 3 | | live |
| `chroma_host` 5, `chroma_port` 4 | | live; default `"localhost"` questionable (F12) |
| `extraction_interval` 3 | | live (`api/chat.py`) |
| `recall_token_budget` | **0** | unused |
| `personality_weight` 3, `context_focus` 4, `auto_index_uploads` 1, `file_chunk_size` 4, `file_chunk_overlap` 4, `file_chunk_strategy` 3 | | live |
| `data_dir` 36 | | live |

Missing from `.env.example`: `CONTEXT_FOCUS, EMBEDDING_API_KEY, EMBEDDING_BASE_URL, FILE_CHUNK_STRATEGY, MATRIX_ALLOWED_ROOM_IDS, MATTERMOST_ALLOWED_CHANNEL_IDS, PERSONALITY_WEIGHT, PREFILL_API_KEY, PREFILL_BASE_URL, RERANKER_API_KEY, RERANKER_BASE_URL, RERANKER_MODEL`.
In `.env.example` but not `Settings`: `BIND_HOST` (start.sh), `INSTALL_OFFICE`, `INSTALL_BROWSER` (Docker build args).
Env read outside `config.py` and absent from both: `JOB_WORKER_ENABLED, JOB_STALL_CHECK_SECONDS, JOB_STALL_TIMEOUT_SECONDS, JOB_WORKER_POLL_SECONDS, JOB_WORKER_SUPERVISE_SECONDS, JOB_AUTO_REQUEUE_MAX, BROWSER_ENABLED, BROWSER_HEADLESS, BROWSER_WS_URL, BROWSER_EXECUTABLE_PATH, PANTHEON_SANDBOX, FIRECRACKER_DIR/FC_DIR` (see F13 for file:line).

### Appendix C — Module import graph: zero non-test importers

Method: for each `backend/**/*.py`, search every other module for `from X import`, `import X`, `from <parent> import <name>`, and bare dotted references. Excludes `main.py`, `jobs/handlers/*` (registered via `bootstrap.py`), `sources/adapters/*` (registered via `adapters/__init__.py`), `jobs/sinks/*` (imported by `bootstrap_sinks` via `__import__`), tests.

| Module | Importers | Verdict |
|--------|-----------|---------|
| `tasks/autonomous.py` | none | dead (F2) |
| `utils/autoresearch.py` | tests only | CLI runner invoked by the `autoresearch` bundled skill via `run_command` (`skills/autoresearch/instructions.md:39`) — intentional, but only reachable when host-exec is on |
| `telegram_bot/bot.py` | 3 lazy imports | shim (F1) |
| `memory/working.py` | `memory/manager.py` only | constructed, never populated (F5) |
| `memory/archival.py` | `memory/manager.py`, `api/memory.py` | reachable, path bug (F6) |
| `skills/executor.py` | `sandbox/*` only, via an uncalled method | dead (F15) |
| `jobs/sinks/*` | `bootstrap_sinks` only | reachable only via dead `scheduled_job` (F3) |
| `jobs/handlers/scheduled_job.py`, `extraction.py`, `file_indexing.py` | bootstrap only | registered, never enqueued (F3, F4) |
| `models/discovery.py` | `api/settings.py`, `models/provider.py` | live via `provider.list_models` |
| `db_utils.ClosingConnection` | 23 uses | live |

Everything else has at least one live importer. Handler registration: all 7 files in `jobs/handlers/` (`autonomous_task, scheduled_job, coding_task, extraction, file_indexing, image_extraction, iteration_loop`) are imported by `bootstrap_handlers()` and each has `@register(...)`.

### Appendix D — SQLite databases

All paths resolve under `settings.db_dir` unless noted.

| DB file | Owner | Pragmas | Notes |
|---------|-------|---------|-------|
| `episodic.db` | `memory/episodic.py` | yes | also opened directly by `api/conversations.py:103,128`, `api/projects.py:308`, `api/project_export.py`, `api/project_import.py`, `jobs/handlers/autonomous_task.py:632` (no pragmas) |
| `graph.db` | `memory/graph.py` | yes | also `api/memory.py:490`, `api/projects.py:330`, export/import |
| `vault.db` | `secrets/vault.py` | yes | |
| `scheduler.db` | APScheduler SQLAlchemy jobstore (`tasks/scheduler.py:27`) | n/a | |
| `artifacts.db` | `artifacts/store.py` | yes | migration `001_artifacts.sql` |
| `jobs.db` | `jobs/store.py` | yes | migration `003_jobs.sql`; imports `task_runs.db` once |
| `task_runs.db` | `tasks/runs.py` | yes | legacy (F2); table in `002_phase_g.sql` |
| `auth_sessions.db` | `api/auth.py:49` | yes | |
| `sources.db` | `api/connections.py:41` | yes | GitHub connections + repo bindings |
| `phase_g.db` | `api/projects.py:394` | **no** | `project_settings`; re-runs migration script per connect |
| `llm_calls.db` | `llm_config/usage.py:42` | yes | 30-day retention |
| `file_index.db` | `memory/file_indexer.py:282` | yes | |
| `merge_proposals.db` | `memory/merge_proposals.py:58` | yes | |

Read-only ad-hoc opens without pragmas: `utils/self_doc.py:85`, `api/project_export.py:73,101,119,162`. Legacy path probes: `api/project_export.py:66-67,94-95`.

### Appendix E — Test coverage map

| Test file | Modules exercised |
|-----------|-------------------|
| test_agent_multimodal_artifact | agent.core, artifacts.store |
| test_artifact_* (7 files) | artifacts.store, api.artifacts (via main), memory.graph |
| test_auth_and_vault | api.auth, secrets.vault |
| test_autonomous_skill_resolution | jobs.handlers.autonomous_task, skills.registry, skills.models, mcp_client, tasks |
| test_autoresearch | utils.autoresearch |
| test_browser_guard | utils.net, agent.browser_tools (guard only) |
| test_cfr_adapter, test_malegislature_*, test_phase_b_adapters, test_sec_edgar | sources.registry, sources.adapters.{cfr,malegislature,forum,github,sec_edgar}, sources.base |
| test_chat_attach_artifacts | api.chat attach, jobs.store, artifacts.store |
| test_chat_router, test_router_tuning, test_model_routing, test_llm_endpoints | llm_config.{router,tuning,store,models,known_models,migration}, api.llm_endpoints, api.chat (routing), models.provider |
| test_chunking_strategies, test_memory_ingest_fixes, test_memory_strip_artifact | memory.{chunker,file_indexer,graph,semantic,topic_embeddings}, sources.adapters.youtube |
| test_connections_schema | api.connections |
| test_conversations_endpoint | api.conversations, memory.episodic |
| test_document_converter | utils.document_converter, api.files |
| test_financial_tools, test_git_tools, test_git_merge_tool, test_local_coding_tools, test_precommit_hook, test_ledger_reanchor | agent.tools (subsets), agent.prompts |
| test_harness_metrics | jobs.handlers.autonomous_task helpers |
| test_image_extraction | jobs.handlers.image_extraction, jobs.store, jobs.context |
| test_integrations_fixes | agent.tools, artifacts.conversions, mcp_client.{client,manager} |
| test_iteration_truncation | agent.core |
| test_job_lifecycle, test_orphan_recovery | jobs.{store,worker,context,recovery}, tasks.scheduler (indirect) |
| test_mcp_port_scanner | api.mcp (scanner only) |
| test_search_providers | agent.search_providers |
| test_security_hardening | api.auth, api.chat, agent.core/tools, utils.{net,paths,document_converter} |
| test_self_doc | utils.self_doc |
| test_system_updater | api.system (update), api.auth |

**Zero coverage:** `messaging/*` (gateway, channel_store, base, 5 adapters), `mcp_client/oauth.py`, `mcp_client/tavily_credits.py`, `jobs/watchdog.py`, `jobs/handlers/{scheduled_job,coding_task,extraction,file_indexing}`, `jobs/sinks/*`, `api/{tasks,jobs,projects,project_export,project_import,memory,skills,personas,personality,settings,mcp_oauth,messaging}`, `skills/{importer,scanner,editor,publisher,exporter,versioning,analytics,executor,registries_config,resolver}`, `sandbox/*`, `integrations/github.py`, `memory/{manager,archival,working,merge_proposals,extraction}`, `sources/{extraction,similarity,util}`, `tasks/{runs,autonomous}`, `telegram_bot`, `security_log.py`. `agent/tools.py` dispatch is covered only for git/financial/local-coding/precommit/ledger/multimodal/self-doc tool subsets (~60 tools total).

### Appendix F — Things checked that are fine

- `memory/extraction.py` (conversation entity extraction) vs `sources/extraction.py` (artifact topic extraction) are genuinely different pipelines with a naming collision only; both already route through `get_provider_for("extract")`.
- `integrations/github.py` is live (`api/connections.py`, `agent/tools.py:3391,3757`).
- `security_log.py` is live (6 importers).
- `sandbox/` `execute_inline` is live via `code_execute`; `firecracker_backend.py` is opt-in via `PANTHEON_SANDBOX` — legitimate configurability.
- Compose `./skills:/skills` mount matches `skills/registry.py:34` path arithmetic inside the container.
- `deploy.sh`, `update.sh`, `start.sh`, `stop.sh`, `setup_options.sh`, `Makefile`, `scripts/*` are current (npm ci, cookie auth, BIND_HOST) — only `VERSION`/`bump_version.sh`/`git_commit.sh` and the two `.txt` files are stale.
- Route/topic similarity pipeline (`sources/similarity.py`, `memory/topic_embeddings.py`, `memory/merge_proposals.py`) is on the real ingest path via `registry.ingest`.

### Not determined

- Whether any external/automation client (outside the frontend) uses `POST /api/jobs`, `/tasks/runs*`, `/settings/models`, or `/settings/test-connection`; the review only checked `frontend/src`.
- Whether the dev box's `task_runs.db` has already been migrated into `jobs.db` (needed before deleting `_migrate_from_task_runs`).
- Whether the owner actively uses Tavily credit thresholds (F18) or the `remember(tier="archival")` path (F6).
