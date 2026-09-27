# Pantheon codebase review — 2026-09-27

**Scope.** Full read-only review of `r3motebee/pantheon` at `cb4cc7e` (version `2026.09.27.H6`): security, consistency and duplication, documentation accuracy, the persona / personality / bundled-skills layer, the agent tool surface, and the frontend. Six focused sub-reviews were run in parallel; each finding below was verified against source and cites `file:line`. The full sub-reports with all evidence are in this folder:

| # | Sub-report | What it covers |
|---|---|---|
| 01 | [Security](01-security.md) | 19 findings + a "Verified OK" list of every CLAUDE.md security claim checked in code |
| 02 | [Backend consistency](02-backend-consistency.md) | 20 findings; router / config / SQLite / import-graph / test-coverage inventories |
| 03 | [Agent tools and prompt](03-agent-tools-and-prompt.md) | 17 findings; inventory of all 60 tools with token cost; system-prompt block list |
| 04 | [Personas, personality, skills](04-personas-personality-skills.md) | Persona runtime data flow, prompt assembly, bundled-skill table, streamlining options |
| 05 | [Frontend](05-frontend.md) | 16 findings; component import map, backend routes with no caller |
| 06 | [Documentation](06-documentation.md) | ~60 doc defects; proposed consolidated doc map |

**Baseline.** Integration suite run in a fresh venv: **422 passed, 6 skipped, 0 failed** (CLAUDE.md says ~425 / 5 skipped; close enough). 59 deprecation warnings, all from `config.py` using Pydantic v1-style `Field(env=…)` and `class Config` — harmless today because every field name matches its env var, but it is removed in Pydantic v3.

**Overall shape.** The core architecture (memory tiers, source adapters, unified jobs, LLM routing, vault, auth, SSRF guard) is sound and most of CLAUDE.md's security claims hold up in code. The problems are at the edges: leftovers from earlier phases that were never deleted, several features that look wired up but are silently inert, one real SSRF hole, a persona system that does far less than its UI implies, and documentation that disagrees with the code in ~60 places. Roughly a quarter of the fixed per-call token budget is spent on prompt and tool text that is duplicated, stale, or off-domain.

---

## 1. Top priorities (what to do first)

These are the items where the code is doing something other than what the user or the docs believe. Ordered by impact.

| P | ID | Finding | Why it matters | Effort |
|---|---|---|---|---|
| **P0** | SEC-1 | `sec/edgar` adapter fetches any model-supplied URL with raw `httpx`, no host check. Reachable from `ingest_source`, including in background jobs. | Defeats the SSRF guard the rest of the pipeline relies on: a prompt-injected transcript can make the agent read `127.0.0.1:8000/api/…`, ChromaDB, or any LAN device and save the result as an artifact. `backend/sources/adapters/sec_edgar.py:49-52` | S |
| **P0** | SEC-2 | Webhook sink and search-provider config accept a free-form vault key name and send that secret as a bearer token to a user-supplied URL. | Turns any authenticated request (or a stolen session cookie) into a vault dump: LLM keys, GitHub PATs, OAuth tokens. `backend/jobs/sinks/webhook_sink.py:23-29`, `backend/api/settings.py:415-424`, `POST /api/jobs` accepts any `job_type`/payload (`api/jobs.py:57-64`) | S |
| **P0** | SEC-3 | Personality endpoints build a filesystem path from an unvalidated `project_id`. | `PUT /api/personality/soul?project_id=../../..` writes outside the data dir. Fixed filename, so not arbitrary write, but it is the exact pattern CLAUDE.md forbids. `backend/agent/personality.py:75,98,108` | S |
| **P0** | SEC-4 | Default `VAULT_MASTER_KEY` / `SECRET_KEY` and the `.env.example` placeholder password are accepted with only a log warning. | With the default key, `vault.db` is decryptable by anyone holding the file. `start.sh` treats the placeholder password as unset for the bind decision while the backend accepts it as real. `backend/config.py:39-43`, `backend/main.py:110-120`, `start.sh:37` | S |
| **P1** | BUG-1 | `consolidate_memory` tool and `POST /api/memory/consolidate` are permanent no-ops. | `MemoryManager.working` is never populated on any real path (AgentCore keeps its own buffer), so `consolidate_session()` always returns "No messages to consolidate." The summarize+extract logic beneath it is unreachable. `backend/memory/manager.py:522-528`, `backend/agent/core.py:169` | M |
| **P1** | BUG-2 | Chat header toggles for memory recall, thread focus and persona presence only change UI state. | `ChatActions.jsx:126-142` calls store setters; the handlers that persist to the backend (`toggleMemoryRecall`, `cyclePersonalityWeight`, `cycleContextFocus` in `Chat.jsx:533-584`) are defined and never called. The backend reads these from the vault, so the buttons do nothing. | S |
| **P1** | BUG-3 | Project Settings "chat defaults" (persona, tone weight, context focus) write to a table nothing reads. | `phase_g.db.project_settings` has zero readers outside `api/projects.py`. The chat path applies the global vault values. Skill discovery is stored in two places; only the vault copy is used. Panel hint text is false. `ProjectSettingsPanel.jsx:200-235`, `backend/api/projects.py:405-442` | M |
| **P1** | BUG-4 | Telegram job sink can never work. | Imports `send_message_to` which does not exist anywhere; the sink always returns `unsupported`. `backend/jobs/sinks/telegram_sink.py:16` | S |
| **P1** | BUG-5 | `remember(tier="graph")` is offered in the tool schema but stores to semantic and reports "Stored in graph memory". | `MemoryManager.remember` has no graph branch. `backend/agent/tools.py:80-100`, `backend/memory/manager.py:189-213` | S |
| **P1** | BUG-6 | Tool results are never truncated before going back to the model. | `read_artifact`, `read_file` (incl. full PDF + OCR text), `github_read_file` and MCP text output are appended verbatim. One large transcript read blows the context on smaller local models. `backend/agent/core.py:555-559`; MCP caps only `structured` at 50 KB, not `text`. | S |
| **P1** | BUG-7 | Repo-work protocol in the system prompt tells the model to use `git_*` / `run_command` in contexts where those tools are hidden. | `build_system_prompt` emits it whenever a repo is bound, ignoring `host_exec`. Background jobs and bots get instructions for tools that return a refusal string. `backend/agent/prompts.py:101-133` vs `core.py:470-475` | S |
| **P1** | PERS-1 | Every new project silently gets a `soul.md` override (the Pan persona) that shadows global personality edits. | `Projects.jsx:11,22-24` defaults to `pan` and calls apply on create. `get_full_personality` prefers the override, so Settings → Personality → Identity edits have no effect on any UI-created project. Applying any non-Pan persona also **removes** the `## Key Commitments` block (no fabrication, flag uncertainty). | S |
| **P1** | ARCH-1 | Legacy `ArchivalMemory` writes to a CWD-relative `data/` while the API reads from `settings.data_dir`. | Agent writes and UI reads land in different directories. `backend/memory/archival.py:22-26`, `manager.py:159,175` vs `api/memory.py:263-350` | S |
| **P2** | DOC-1 | Documentation contradicts the code in ways that will mislead a new engineer. | CLAUDE.md says skills have "no executor, no subprocess, no sandbox" (`skills/executor.py` and `sandbox/` exist); `docs/messaging-gateway-plan.md` says messaging is not implemented (5 adapters shipped); `REVIEW.md` and `frontend/README.md` describe `?token=` / `localStorage.auth_token` auth (removed); `.env.example` says empty Discord allowlist allows all (deny-by-default); SOURCE_ADAPTERS.md's worked example uses the banned raw-httpx pattern. | S–M |

---

## 2. Security

See [01-security.md](01-security.md) for all 19 findings and the verification list. The four P0 items are above. Remaining notable items:

| Sev | ID | Finding | Location |
|---|---|---|---|
| Med | SEC-5 | `python-multipart==0.0.9` predates the multipart DoS fix (0.0.18). Other 2024-era pins (`fastapi 0.111`, `httpx 0.27`, `cryptography 42.0.7`); parsers of untrusted input (`chromadb`, `trafilatura`, `PyMuPDF`, `pdfplumber`) are unpinned ranges. No `pip-audit`/`npm audit` anywhere. | `backend/requirements.txt` |
| Low | SEC-6 | `str.startswith` path checks (the pattern CLAUDE.md forbids) in skill executor and project import. Mitigated by other checks today. | `skills/executor.py:66`, `api/project_import.py:920` |
| Low | SEC-7 | ~15 host-fixed outbound fetches skip `safe_http_get` and follow redirects unguarded (github, cfr, forum, malegislature, sec_edgar adapters; financial tools; skill importer; search providers). Host is not model-controlled, so residual risk is open-redirect only. | see sub-report |
| Low | SEC-8 | Login throttle keys on `request.client.host`; behind the shipped nginx/Caddy everything is `127.0.0.1`, so ten wrong passwords lock everyone out and an attacker shares one bucket with the LAN. | `api/auth.py:151-170` |
| Low | SEC-9 | Skill security-override password compared with `!=`, not `hmac.compare_digest` (which `password_matches` already does). | `api/skills.py:711` |
| Low | SEC-10 | MCP debug/test endpoints return 10–14 chars of live API keys; Tavily key travels in the query string and is logged at INFO. | `api/mcp.py:275,343-358`, `mcp_client/client.py:124-170` |
| Low | SEC-11 | LibreOffice preview render lacks the throwaway profile the converter uses; office conversions can make unguarded network fetches. | `artifacts/preview.py:138-142` |
| Info | SEC-12 | `/docs`, `/redoc`, `/openapi.json` are public. Docker image runs as root. Slack/Discord allowlists are read once at adapter start (revoking a channel needs a restart). No "untrusted data" delimiter around tool output fed to the model. | `main.py:47-49`, `backend/Dockerfile`, `slack.py:115`, `discord.py:108` |

**Verified sound** (worth knowing so nobody re-audits it): auth middleware and HttpOnly cookie session model, constant-time password check, WebSocket auth before accept, CORS, DNS-rebinding guard, `safe_http_get` pinning and redirect re-checks, browser route guard, path safety in files/workspace/download/skill-archive/project-import, host-exec gating enforced at both schema and dispatch, git credentials via `GIT_CONFIG_*`, all SQL parameterised, secrets never echoed by list endpoints, vault KDF v2, all five messaging allowlists deny-by-default, MCP OAuth PKCE/state/DCR handling, MCP port scan pinned to loopback, frontend sinks all DOMPurify'd or sandboxed.

---

## 3. Duplication, dead code and inert features

The backend carries several complete subsystems from earlier phases that nothing reaches. Deleting them is low-risk and removes real confusion (two "working memory" concepts, two job re-run endpoints, two coding workflows). Detail in [02-backend-consistency.md](02-backend-consistency.md).

### 3.1 Dead or unreachable (safe to delete)

| ID | What | Evidence | Lines |
|---|---|---|---|
| DEAD-1 | `backend/tasks/autonomous.py` (pre-jobs runner) | zero importers | 103 |
| DEAD-2 | `backend/tasks/runs.py` + 4 `/api/tasks/runs*` routes + `taskRunsApi` | written only by DEAD-1; `JobStore` already has a one-shot migration away from `task_runs.db`; frontend never calls it | ~250 |
| DEAD-3 | `scheduled_job` handler + `backend/jobs/sinks/` | nothing creates a `scheduled_job`; `schedule_scheduled_job()` has no callers; `create_task` only allows `autonomous_task`/`iteration_loop`. Functionally a subset of `autonomous_task` (+ misfire coalescing + output sink). | ~314 |
| DEAD-4 | `extraction` and `file_indexing` job handlers | registered, never enqueued; chat and `/files/index` run these inline | ~120 |
| DEAD-5 | `backend/telegram_bot/` shim | 11-line re-export; 3 lazy importers should point at `messaging.adapters.telegram` | 11 |
| DEAD-6 | `skills/executor.py` + `SandboxBackend.execute_skill` | no callers; no bundled skill ships scripts | 210 |
| DEAD-7 | Legacy LLM plumbing in `api/settings.py`: 15 `llm_*/prefill_*/…` fields, their PUT branches, `/settings/models`, `/settings/test-connection`, `/settings/restart-telegram` | only `llm_config/migration.py` reads those vault keys; frontend never calls the three routes | ~120 |
| DEAD-8 | Frontend: `FileRepository.jsx` (780 lines, hardened in the Sept security batch), `SourcesPage.jsx`, `FilesPage.jsx`, `PersonalityPage.jsx`, `chat-tabs/ProjectMcpPanel.jsx` (imports a nonexistent `projectMcpApi`), `chat-tabs/ProjectPersonalityPanel.jsx`; 34 `client.js` methods with no caller; `sourcesApi` | none imported anywhere | ~1,500 |
| DEAD-9 | In `tools.py`: `_configured_search`, `_ddg_search` (unreachable), unused locals in `show_file`, `TOOL_SCHEMAS` import in `core.py`, `get_all_tool_schemas(project_id)` ignores its arg | see 03 §2.7 | small |
| DEAD-10 | Root scaffolding: `VERSION`, `bump_version.sh` (writes the old `YYYY-MM-DD-NN` scheme and would corrupt the current `package.json` version), `git_commit.sh`, `BACKEND_BUILD_SUMMARY.txt`, `FILES_CREATED.txt`, `frontend/BUILD_SUMMARY.txt`, `backend/memory/VERIFICATION_REPORT.txt`, `autoresearch_report.md` | build-session leftovers; several contain `/sessions/…/mnt/outputs` paths | — |
| DEAD-11 | `.gitignore` lists `backend/data/` and `FILES_CREATED.txt` yet both are tracked; `data/personality/*.md` is tracked as a byte-identical copy of `backend/data/personality/*.md` (CLAUDE.md says `data/` is not in git) | `git ls-files` | — |

### 3.2 Duplicated concepts (pick one)

| ID | Duplication | Recommendation |
|---|---|---|
| DUP-1 | `POST /jobs/{id}/rerun` (tasks router, `JobStore.rerun`) vs `POST /jobs/{id}/retry` (jobs router, `JobStore.retry`) | Keep `rerun` (superset), move it to the jobs router, alias or drop `retry`. |
| DUP-2 | Per-project knobs in `phase_g.db.project_settings` (unread) vs global vault `personality_weight`/`context_focus` vs vault `skill_discovery_<project>` | One store for project-scoped settings; `_build_agent` reads per-project with global fallback; drop the unread table. |
| DUP-3 | Two "working memory" concepts: `MemoryManager.working` (never populated) vs `AgentCore.working_memory` | Make `consolidate_session` read from episodic (as `run_extraction_on_recent` already does) and delete `memory/working.py`. |
| DUP-4 | `ArchivalMemory` (file notes) vs artifact store (durable indexed markdown) | Fold `remember(tier="archival")` into `save_to_artifact` under `notes/`; retire the module and its 7 routes. |
| DUP-5 | Two coding workflows: `iteration_loop` instructs GitHub-API tools (`github_write_files`, no tests can run); `coding_task` instructs local-first `git_*` + `run_command` | Pick local-first for both or make the branch-policy block toolchain-aware. Don't merge the handlers; they are different shapes. |
| DUP-6 | Six copies of the "run one turn" event pump and eight of turn setup: `_stream_turn`, WS persona loop (drops `error`/`done`, no route outcome), REST `chat()`, `skill_accept`/`skill_decline`, `autonomous_task`, `iteration_loop`, and five messaging adapters each with an inline `_run_agent` closure and its own skill resolution | One shared `run_turn()` used by all consumers. Fixes the metrics skew in DUP-7 as a side effect. |
| DUP-7 | Synthetic `context_loaded` tool events are filtered by `autonomous_task` and `iteration_loop` but not by `_stream_turn`, so every routed chat turn logs `tool_calls` off by one into routing-tuning stats | Filter in one place. `core.py:367-377`, `api/chat.py:193-198` |
| DUP-8 | Four tasks/jobs UIs: `TaskMonitor.jsx` (route `/tasks`, not in nav), `chat-tabs/ProjectTasksPanel.jsx`, `Settings.jsx` `GlobalTasksSection` and `TaskRunsSection` | `ProjectTasksPanel` is the most complete (approve/rerun/retry). Delete the other three. |
| DUP-9 | Persona apply in 3 UI places (one dead), persona browse in 2; `MCPConnections.jsx` mounted at two routes | See §5. |
| DUP-10 | Core modules import runtime settings from a router module (`agent/core.py:344`, `memory/file_indexer.py:388` → `api.settings`) | Move `is_memory_recall_enabled` / `get_active_chunk_settings` to `config.py` or a small `runtime_settings.py`. |
| DUP-11 | Tavily-specific credit tracking (279-line module, 5 routes, 61 references) inside the generic MCP layer | If Tavily is used daily, keep; otherwise generalise to the per-connection daily/monthly budget the search-provider manager already models. |
| DUP-12 | Provider access split: 36 legacy role getters vs 21 `get_provider_for("class")`; `iteration_loop` (a coding loop) uses `get_provider()` → agent while `coding_task` uses `code` | Mechanical cleanup to class-based calls. |

### 3.3 Inconsistencies worth one decision each

- **Tool error contract is a regex over prose.** Tools return free-form strings with 25+ prefixes (`Error:`, `X rejected:`, `Refusing`, `Access denied`, `Unknown tool`…). `api/chat.py:142` classifies errors with `_TOOL_ERROR_RE = r"\s*(error\b|.{0,80}?\bfailed\b)"`, so routing-tuning `tool_errors` under-counts. Adopt one envelope (`Error: <tool>: <msg>`) and match only that.
- **Eight routers mix `HTTPException` with 200-status `{"error": …}` bodies** (`artifacts`, `chat`, `mcp_oauth`, `skills`, `llm_endpoints`, `mcp`, `messaging`, `projects`).
- **Malformed tool-call JSON is swallowed as `{}`** so the model sees a `KeyError` with no hint its arguments were unparseable. `models/provider.py:345-348,413-417`.
- **HOST_EXEC refusal lives only in `AgentCore`**, not in `execute_tool`; direct callers (38 sites, mostly tests) bypass the gate. Move the check one layer down.
- **MCP tool names truncated to 64 chars cannot be resolved** back to the original; no cap on MCP tool count; connection names containing `_` make prefix matching ambiguous.
- **`chroma_host` defaults to `"localhost"`**, so every local start tries an HTTP Chroma client, fails, logs a WARNING and falls back to embedded, in two places. Default to `""`.
- **`phase_g.db` skips `apply_sqlite_pragmas`** and re-runs its migration script on every connect; ~12 ad-hoc `sqlite3.connect` sites in routers/handlers bypass the owning stores (e.g. `autonomous_task.py:630-636` writes `conversations.title` directly). `project_export.py` still probes legacy `data/episodic.db` paths.
- **Docker build produces version `0.0.0-dev`** unless `deploy.sh` copied `package.json` first; the frontend is served four different ways (backend static, frontend container, nginx, Caddyfile with a hard-coded `/path/to/pantheon`).
- **Config surface.** 12 `Settings` env names missing from `.env.example`, including `MATRIX_ALLOWED_ROOM_IDS` and `MATTERMOST_ALLOWED_CHANNEL_IDS` (deny-by-default, so those bots are silently dead for anyone following the example); 14 `os.getenv` reads outside `config.py` (job worker/watchdog/browser/sandbox tunables) declared nowhere; `recall_token_budget` is documented and never read.

---

## 4. Agent tool surface and system prompt

Detail and the full 60-tool inventory in [03-agent-tools-and-prompt.md](03-agent-tools-and-prompt.md).

**Fixed overhead per LLM call: ~18k tokens** before the user's message. Tool schemas are ~50 KB ≈ 12.5k tokens (60 tools, uncapped MCP tools on top); the static system prompt with default `soul.md`/`agent.md` is ~22 KB ≈ 5.5k tokens. This matters most on local models.

**Where the tokens go.** `create_task` alone is 6 KB (12% of the tool budget) and its approval/plan/schedule prose is repeated in two system-prompt blocks and the server-side guard text. Ten near-identical `github_*` schemas ≈ 4.9 KB. Every core concept (artifacts vs workspace, skills vs tasks, approval flow) is stated 3–4 times across tool descriptions, prompt blocks, and `agent.md`.

**Stale and contradictory prompt content.**
- Prompt and `browser_open` reference `web_fetch`, which does not exist (`prompts.py:233`, `browser_tools.py:260`). `save_chat_as_artifact` and `list_skills` are cited as tools; they are not.
- Hard-coded user-specific `mcp_SubDownload_*` names in the generic prompt and in `create_task`'s example plan.
- Default `agent.md` (8.6 KB, ~2.1k tokens per call) tells the agent to write reports to workspace files, the opposite of the "ALWAYS save to artifacts" rule; says iteration limit is 50 (it is 100), LLM timeout 120 s (300 s), describes an "Archival" tier CLAUDE.md calls unused.
- "Storage layers" block says the workspace is `data/workspace/`; it is `data/projects/<id>/workspace`.
- `save_last_response`'s fallback text recommends `write_file` for durable content.

**Recommended tool consolidations** (≈ −3.5k tokens, ~28%, no lost capability):
1. Retire `save_transcript_artifact` → `ingest_source` (its own description says it replaces it; hardcodes one MCP connection name; duplicates path-normalise logic).
2. Fold `start_coding_task` into `create_task(job_type="coding_task")`; its 984-char "STRICT SCOPE" text exists only to prevent confusion with `create_task`.
3. Collapse 10 `github_*` into `github(action=…)`; dispatch already shares auth setup.
4. Collapse `list/approve/reject/force_merge` into `merge_topics(action=…)`.
5. Drop `convert_document` (keep batch); merge `index_workspace` + `index_artifact` into `index(target=…)`.
6. Move the three SEC financial tools behind a skill calling the `sec/edgar` adapter.
7. Trim `create_task`'s description to what the prompt does not already say.
8. **Add a single result cap in `core.chat`** (e.g. 24–32 KB head/tail + "[N chars omitted]"). Highest-value single change.

**Do not merge:** artifact vs workspace families (rename to `artifact_*` / `workspace_*` and delete two of three prose blocks instead), `web_search` vs `browser_*` (but add `web_fetch` or stop citing it), `github_*` vs `git_*` (different availability), the jobs trio.

**Structural (do once):** registry/decorator dispatch with a `ToolContext` replacing the 2,440-line `if/elif` chain (`tools.py:1729-4171`; all branches are literal or prefix matches, so this is mechanical); split into `agent/tools/{artifacts,workspace,memory,ingest,jobs,git,github,finance,search}.py`; pass `host_exec` into `build_system_prompt`.

---

## 5. Personas, personality layer, bundled skills

Full analysis and three streamlining options in [04-personas-personality-skills.md](04-personas-personality-skills.md).

### 5.1 What a persona actually is

**A persona is a `soul.md` preset and nothing else.** `POST /personas/{id}/apply/{project}` copies the persona's `soul` string over the project's `personality/soul.md` and stamps `persona_id` in `projects.json` (`api/personas.py:192-226`). No tools, model, temperature, memory access, router class or skills change. Of the persona JSON's 10 fields, only `soul` reaches the LLM (`name`/`icon` appear as a prefix in group-chat mode). `traits`, `best_for`, `tagline`, `description`, `is_default` are UI-only; the backend never consults `is_default` (default is hard-coded `'pan'` in `Projects.jsx:11`).

Three disconnected persona mechanisms exist:
1. **Project persona** (apply → soul.md override). The only one that works.
2. **Group chat** (`active_personas` in conversation metadata). Only does anything with 2+ personas selected; one selected persona is silently ignored (`chat.py:711`). In group mode each persona replaces soul.md **and drops agent.md entirely** (`prompts.py:43-45`), losing all memory/tool guidance, and triples tokens per message.
3. **Project Settings "Default persona" + "Tone weight"** → `project_settings` table, read by nothing (BUG-3). Dead component `ProjectPersonalityPanel.jsx` also offers tone values (`focused/balanced/broad`) that don't match the backend enum (`minimal/balanced/strong`).

### 5.2 The nine personas

All nine share an identical skeleton (`# The Soul of <N>` → 4 bold traits → `## Working Style` → closing line); 33–43% of each one's word tokens are shared register. Pan is a verbatim prefix of the global `soul.md` minus the `## Key Commitments` block, so "no persona" and "Pan" are the same identity. Athena (plan first), Hermes (search immediately), Hephaestus (code first) and Zeus (don't hedge) contradict each other and Pan's "never speculate beyond evidence", which matters only in group mode.

**For thematic/vendor research, personas as built add roughly zero behavioural value.** Hermes and Mnemosyne are the only thematic fits, and their actionable content (cite sources, flag confidence, surface past context) already lives in `soul.md` + `agent.md`. The other six describe coding/tutoring/security/exec roles that never touch the ingest/recall/graph workflow. Applying any non-Pan persona **removes** the research-relevant Key Commitments.

### 5.3 System prompt layering

The prompt assembles 16 blocks (ordered list with sizes in sub-report 04 §c). Static floor for an ordinary project turn ≈ 25 KB ≈ 6.4k tokens, of which soul+agent ≈ 47% and the always-on available-skills block ≈ 15%, dominated by 7 research-irrelevant bundled skills. "Who am I" is stated up to 4 times; memory guidance 3 times; the `minimal` scope prefix tells the model to mostly ignore the ~850 tokens of soul it just received.

### 5.4 Bundled skills

The skill **infrastructure** (`SkillEditor`, `SkillPicker`, scanner, registry, importer with 4 hub adapters, versioning, analytics) is solid and worth keeping. The **ten bundled skills** are thin prompt wrappers (1.7–3.2 KB each) with 47-line manifests whose fields are mostly never read (`auto_store`, `telemetry`, `evolution`, `schedulable.*`, `permissions.network_domains/file_paths` have zero runtime readers).

| Skill | Fit for research | Issue |
|---|---|---|
| web-research | Medium | steers to raw `web_search` before `recall`/`ingest_source`, contradicting agent.md "check memory first"; `auto_store` not implemented; triggers `investigate`, `look into`, `find out about` are everyday research verbs |
| knowledge-capture | Medium | overlaps agent.md memory rules; firing is desirable |
| summarize-conversation / daily-digest | Low | overlap each other and the recent-jobs block; digest says "store in workspace" (the anti-pattern); triggers `give me a summary`, `status update`, `catch me up` |
| autoresearch | Real machinery, off-domain | coding optimiser via `code_execute`, which is host-exec gated and refused in background jobs despite `schedulable: true` |
| code-review, explain-code, task-breakdown, draft-message, weather | Off-domain | triggers `what does this do`, `help me understand`, `announce` (⊂ "announcement"), `forecast for` (⊂ "revenue forecast for") will misfire once auto-discovery is on |

Resolver phrase matching is plain substring containment (`resolver.py:107`). Backend default discovery mode is `off`; the frontend store defaults to `'auto'` (`store/index.js:19`), a UI/backend mismatch.

### 5.5 Recommendation

**Personas: Option 1 from the sub-report.** Collapse personas into "soul presets" inside the existing Personality editor (which already has apply + Save-as-Persona). Remove the `/personas` page, group-chat mode, the dead `project_settings` persona/tone columns and both panels, `PersonalityPage.jsx`, `ProjectPersonalityPanel.jsx`. Stop auto-applying `pan` on project create. Make apply *append* Key Commitments rather than replace soul.md. Keep `agent.md` when `custom_soul` is set. ~600 frontend + ~140 backend lines removed; presets remain one click away; global edits propagate again.

Build **Option 2** (persona as a real project-level setting with `model_class`, default skills, tone, resolved at prompt-build time and honoured by jobs and bots) only when a concrete per-project difference beyond prose is wanted. Today nothing a persona does can't be done by editing the project's `soul.md`.

**Skills:** move `weather`, `draft-message`, `explain-code`, `code-review`, `task-breakdown`, `autoresearch` to `examples/` or ship them disabled. Keep `web-research` (rewritten corpus-first: `recall` → `ingest_source` → `web_search`), `knowledge-capture`, and one of digest/summarize. Tighten triggers to multi-word phrases. Cuts the always-on skills block from ~950 to ~300 tokens. Reconcile `agent.md`'s "File Workspace" section with the artifact rule and remove its hard-coded limits.

---

## 6. Frontend

Detail in [05-frontend.md](05-frontend.md). Security posture is good (cookie-only auth, DOMPurify on every sink, sandboxed iframe, `noopener` on every `_blank`).

| Impact | Finding |
|---|---|
| High | BUG-2 (chat-bar toggles inert) and BUG-3 (project settings write-only) above |
| Med | 7 orphaned files ≈ 1,500 lines (DEAD-8) |
| Med | Four tasks/jobs surfaces (DUP-8) |
| Med | `ChatTabs.jsx:10-11` statically imports `MemoryPage` and `ArtifactsPage`, pulling d3 and CodeMirror + 3 language packs into the default `/chat` chunk. The `React.lazy` split in `App.jsx` is real but the landing route defeats it. mermaid / jspdf are correctly dynamic. |
| Med | `Chat.jsx` is 1,306 lines, 24 `useState`, 33 store selectors, 11 components; natural seams: WS reducer, markdown factory, `Message`/`RouteBadge`, history drawer, save modal. Markdown is configured 5 different ways across 5 files (one without `remarkGfm`). |
| Low | Routes `/tasks`, `/memory`, `/mcp` registered but absent from nav; `/mcp` duplicates a Connections tab. |
| Low | 19 files read `e?.response?.data?.detail` although the axios interceptor already rewrites every rejection into `Error(detail)`, so that branch is always undefined. 37 `window.alert/confirm` calls alongside ~220 toasts. |
| Low | Tailwind `gray-850` (23×) and `gray-750` (8×) are used but never defined; they compile to nothing. |
| Low | `@codemirror/view` imported but not declared in `package.json`. |
| Low | Project-id fallback literal is `'default'` in 38 places and `'default-project'` in `ChatActions.jsx:45,65`. Skill-discovery mode is loaded twice per project switch with conflicting fallbacks. |
| Low | Broken `href="/artifacts?tab="` in job detail; raw `<a href>` instead of router `Link` forces a full reload that drops the module-level WebSocket. |
| Low | `Settings.jsx` is 1,559 lines; CLAUDE.md's "already componentized" claim holds only for the LLM area. RAG, Security, Secrets, SkillHubs, GlobalTasks, TaskRuns, Sandbox, SystemUpdate (~1,100 lines) are still inline. |

Backend routes with no frontend caller: 12 with no client method at all, 34 whose client method is never invoked (list in sub-report Appendix B). Whether these are script-only or dead is a product decision; `/api/system/self-doc` is one of them.

---

## 7. Documentation

Detail and a proposed consolidated doc map in [06-documentation.md](06-documentation.md). ~60 defects; the ones that actively mislead:

| Sev | Doc | Problem |
|---|---|---|
| High | CLAUDE.md §Design rationale, REVIEW.md | "Skills… no executor, no subprocess, no sandbox" — `skills/executor.py` and `sandbox/{subprocess,firecracker}` exist. |
| High | docs/messaging-gateway-plan.md:3 | Banner (edited 2026-09-26) says messaging is not implemented. Five adapters, gateway, 9 routes and a settings tab exist. |
| High | REVIEW.md, frontend/README.md, docs/api/artifacts-feed.md | Describe `?token=` URLs and `localStorage.auth_token`; auth is an HttpOnly cookie and query tokens are explicitly refused. |
| High | .env.example:90,96 | "Leave empty to allow all users/guilds" — every adapter is deny-by-default. |
| High | backend/sources/SOURCE_ADAPTERS.md | Worked example uses raw `httpx.AsyncClient().get(url)`, the banned SSRF pattern (and the exact bug in SEC-1). Also contradicts itself on who owns topic extraction; documents only the collision-retry path, not the default in-place update. |
| High | docs/mcp-registry-protocol.md + examples/minimal-registry | Describes a `GenericRegistryAdapter` and "Settings → MCP → Registries" as built-in; nothing is implemented (the *skill* registry is). |
| High | backend/memory/{INDEX,MANIFEST,QUICK_START,README,SUMMARY}.md | Five overlapping generator-output docs with wrong DB paths (`data/episodic.db` vs `data/db/`), wrong line counts, ~25 missing methods and 5 missing modules. Replace with one ≤80-line README. |
| Med | CLAUDE.md | 18 routers → 19; 28 adapters / 9 mechanisms → 29 / 10 (`sec/edgar` missing everywhere); `tools.py` "1500+ lines" → 4,523; directory tree omits ~60 real files/dirs incl. `messaging/`, `sandbox/`, `jobs/sinks/`, 5 of 19 routers, 8 of 12 `skills/*.py`; `data/` is described as untracked but `data/personality/*.md` is tracked; versioning section ignores the conflicting `VERSION`/`bump_version.sh`. |
| Med | README.md, backend/README.md, frontend/README.md, QUICKSTART.md | Still describe "role mapping" (chat/prefill/vision/embed/rerank) and `RoleMapping.jsx`; superseded by task-class routes and `ModelRouting.jsx`. README says "type `/create-skill`" (no such skill). backend/README lists `secret_storage/`, `plugins/`, a `sources` router that don't exist. |
| Med | docs/USAGE.md, prompts.py | Cite `web_fetch` (not a tool). Search chain is under Connections, not Settings. Only Telegram is documented of five messaging adapters; `/model` pin and `/chat` command undocumented. |

**Subsystems with zero documentation anywhere:** messaging gateway, sandbox (`PANTHEON_SANDBOX`), search-provider chain internals, job sinks, SEC EDGAR adapter + financial tools, system updater, task-run ledger, project export/import scanner, the skills runtime as shipped (45 routes, 12 modules), security audit log, and a reference list of the 60 agent tools (~20 are named across all docs).

**Proposed doc map:** keep and fix CLAUDE.md (shrink tree to directories), README.md, backend/README.md (router list generated from `main.py`), frontend/README.md (absorb QUICKSTART), SOURCE_ADAPTERS.md, USAGE.md, `docs/security.md` (expand SECURITY_FEATURES), skill-registry-protocol.md, .env.example (grouped into app settings / module-read env / installer vars). Add small `docs/messaging.md`, `docs/skills.md`, `docs/tools.md` (generate from `TOOL_SCHEMAS`), `docs/jobs.md`. Delete the memory docs, root scaffolding, QUICKSTART. Move `docs/superpowers/**`, SKILLS_FEATURE_PLAN, messaging-gateway-plan (banner flipped) to `docs/archive/`; mark mcp-registry-protocol as a proposal. Cut REVIEW.md to "start here" pointers or delete it.

---

## 8. Test coverage

Suite is green and fast (21 s) but concentrated on adapters, artifacts, LLM routing, and a few tool subsets. Zero coverage on: `messaging/*` (≈3.3k lines, deny-by-default allowlists), `mcp_client/oauth.py` (537 lines of PKCE/DCR/refresh), `jobs/watchdog.py`, 5 of 7 job handlers, `memory/manager.py` (recall/budget/rerank), similarity + merge pipeline (rewrites graph edges), the whole `skills/` package except models/registry, `sandbox/*`, and most routers (`tasks`, `jobs`, `projects`, `project_export/import`, `memory`, `skills`, `personas`, `personality`, `settings`, `mcp_oauth`, `messaging`). `agent/tools.py` dispatch is covered only for git/financial/local-coding/precommit/ledger/multimodal/self-doc subsets. Full map in [02-backend-consistency.md](02-backend-consistency.md) Appendix E.

Two tests worth adding with the P0 fixes: (a) `ingest_source` with a loopback URL is refused for **every** registered adapter; (b) the webhook sink and search-provider config reject vault keys outside their namespace.

---

## 9. Suggested work packages

Grouped so each can be one PR and validated by the existing suite plus the tests named above.

**WP-1 Security hotfix (P0, ~1 day).** SEC-1 (`safe_http_get` + SEC host allowlist in `sec_edgar.py`), SEC-2 (namespaced vault keys for webhook/search; validate `job_type` in `POST /api/jobs`; `safe_http_post`), SEC-3 (`check_project_id` in `agent/personality.py`), SEC-4 (refuse to start or refuse non-loopback bind on default key / placeholder password), SEC-9 (`compare_digest`), bump `python-multipart`. Add the two tests above.

**WP-2 Fix the inert features (P1, ~2 days).** BUG-2 (wire `ChatActions` to the persisting handlers), BUG-3 + DUP-2 (one store for project-scoped settings, read with global fallback, drop unread table/panels), BUG-1 + DUP-3 (consolidate from episodic, delete `working.py`), BUG-4 (add `send_message_to` or drop the sink with DEAD-3), BUG-5 (implement graph tier or remove it from the enum), ARCH-1 (pass `settings.data_dir` or retire archival per DUP-4), BUG-7 (pass `host_exec` into `build_system_prompt`), DUP-7 (filter `context_loaded` in `_stream_turn`).

**WP-3 Delete dead code (P1, ~1 day, mostly deletions).** DEAD-1 through DEAD-11, DUP-1, DUP-8. Confirm the dev box's `task_runs.db` has been migrated before removing `_migrate_from_task_runs`. Fix `.gitignore` to match reality. Bump version.

**WP-4 Prompt and tool budget (P2, ~2–3 days).** BUG-6 (result cap in `core.chat`), the eight tool consolidations in §4, stale prompt references (`web_fetch`, `mcp_SubDownload_*`, workspace path), reconcile `agent.md` with the artifact rule and remove hard-coded limits, one tool-error envelope + matching `_TOOL_ERROR_RE`. Then, as a separate PR, the registry/decorator dispatch refactor and `agent/tools/` split.

**WP-5 Personas and skills (P2, ~1–2 days).** §5.5: soul presets in the Personality editor, remove Personas page and group mode, stop auto-applying `pan`, apply appends Key Commitments, keep `agent.md` under `custom_soul`, prune bundled skills to three, tighten triggers, align frontend/backend discovery default.

**WP-6 Docs (P2, ~1 day).** §7 doc map. Highest value: fix the six High rows, regenerate CLAUDE.md's tree and counts, replace the five memory docs, fix `.env.example` (allowlist wording + 12 missing vars + 14 module-read vars), write `docs/tools.md` from `TOOL_SCHEMAS`.

**WP-7 Frontend hygiene (P3, ~1–2 days).** Lazy-load `MemoryPage`/`ArtifactsPage` in `ChatTabs`, split `Chat.jsx` along the seams named in the sub-report, one markdown config, define or remove `gray-750/850`, declare `@codemirror/view`, replace `alert` with toasts, standardise `'default'` fallback and error extraction, remove `/tasks` `/memory` `/mcp` routes or add them to nav.

**WP-8 Consistency and config (P3, ~1 day).** `chroma_host` default, `phase_g.db` pragmas, route ad-hoc SQLite through stores, `EpisodicMemory.set_conversation_title`, move runtime-setting readers out of `api.settings`, class-based provider getters everywhere, Pydantic v2 `Field(alias=…)`/`model_config`, declare the 14 stray env vars in `Settings`, fix Docker version resolution, decide one frontend serving path.

**WP-9 Tests (ongoing).** Messaging allowlists, OAuth refresh/401 path, watchdog, `consolidate_session`, merge pipeline edge rewrite, tool dispatch for the remaining ~45 tools.

---

## 10. What was not determined

- Whether any external automation (outside the frontend) uses `POST /api/jobs`, `/tasks/runs*`, `/settings/models`, `/settings/test-connection`, or the 46 other routes with no frontend caller.
- Whether the dev box's `task_runs.db` has already been migrated into `jobs.db`.
- Whether Tavily credit thresholds (DUP-11) and `remember(tier="archival")` (DUP-4) are actively used.
- Bundle sizes (no `node_modules` in the review checkout; the static-import analysis is from the import graph).
