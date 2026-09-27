# Pantheon agent tool surface — read-only review

Scope: `backend/agent/tools.py` (4,523 lines), `agent/core.py`, `agent/prompts.py`, `agent/browser_tools.py`, `agent/search_providers.py`, `api/chat.py`, `jobs/handlers/autonomous_task.py`, plus the other agent-loop consumers (`iteration_loop.py`, `scheduled_job.py`, `coding_task.py`, `tasks/autonomous.py`, `messaging/adapters/*`). All paths below are relative to `/home/user/pantheon/backend/`. Nothing was modified.

Headline numbers: **60 built-in tools** (`TOOL_SCHEMAS`, tools.py:76-1726) costing **~50.0 KB of JSON ≈ 12.5k tokens** on every LLM call, + 6 browser tools (1.9 KB) when `BROWSER_ENABLED`, + an uncapped number of MCP tools. Dispatcher is a single 2,440-line `if/elif` chain (tools.py:1729-4171). Static system prompt with default `soul.md`/`agent.md` is ~22 KB ≈ 5.5k tokens. So a first chat turn with no history is roughly **18k tokens of fixed overhead** before the user's message.

---

## 1. Findings summary

| ID | Issue | Impact | Effort | Where |
|----|-------|--------|--------|-------|
| F1 | **Tool results are never truncated before going back to the model.** `read_artifact`, `read_file` (incl. full PDF text + OCR), `github_read_file`, MCP text output are appended verbatim to `messages`. One 300 KB transcript read blows the context or the 4096 `max_tokens`-style budgets on smaller local models. | High | S | core.py:555-559; tools.py:2052-2100, 3343-3359, 3434-3437; mcp_client/manager.py:96-120 (only `structured` is capped at 50 KB, `text` is not) |
| F2 | **Repo-work protocol tells the model to use host-exec tools in contexts where they are hidden.** `build_system_prompt` emits the `git_sync_repo`/`run_command`/`git_merge`/`git_commit` protocol whenever the project has a bound repo, with no knowledge of `host_exec`. Autonomous, scheduled, iteration-loop and messaging-bot runs (all `host_exec_allowed("background")` → False by default) get instructions for tools that aren't in their schema list; calling them returns the refusal string. | High | S | prompts.py:101-133 vs core.py:470-475, 535-541; autonomous_task.py:259-266; iteration_loop.py:259-265 |
| F3 | **`remember(tier="graph")` is advertised but silently stores to semantic**, then reports "Stored in graph memory". The schema enum offers `graph`; `MemoryManager.remember` has no graph branch and falls to the `else` (warning + semantic). | Med | S | tools.py:80-100, 1770; memory/manager.py:189-213 |
| F4 | **Synthetic `context_loaded` tool events leak into metrics.** `core.chat` emits pre-recall as a fake `tool_call`/`tool_result` pair; `autonomous_task` and `iteration_loop` filter it, `_stream_turn` does not — every routed chat turn with recall hits is logged with `tool_calls` off by one, feeding routing-tuning stats. | Med | S | core.py:367-377; api/chat.py:193-198 (no filter) vs autonomous_task.py:434, iteration_loop.py:149 |
| F5 | **Error-string conventions are inconsistent and the router's error detector depends on them.** 25+ distinct prefixes (`Error:`, `X rejected:`, `X failed:`, `Refusing`, `Access denied`, `File not found`, `No repo bound`, `Unknown tool`, bare `X: msg`). `_TOOL_ERROR_RE = r"\s*(error\b|.{0,80}?\bfailed\b)"` misses `rejected`, `not found`, `Access denied`, `Refusing`, `skipped`, `Unknown tool` — so `tool_errors` in `route_decisions` under-counts. | Med | M | api/chat.py:142, 198; tools.py throughout (e.g. 2119, 2149, 2419, 2831, 3739, 4166) |
| F6 | **Stale / nonexistent tool references in prompts and descriptions**: `web_fetch` (does not exist), `save_chat_as_artifact` (an API route, not a tool), `list_skills` (not a tool), hardcoded `mcp_SubDownload_*` names in the generic prompt and in `create_task`'s example plan. | Med | S | prompts.py:230-233; browser_tools.py:260; tools.py:220, 2840; tools.py:250-262, 341-352; tools.py:2325 |
| F7 | **Default `agent.md` contradicts the current tool contract and is stale.** Tells the agent to write reports/analysis to workspace files ("Write files: Create reports…", "Store outputs in predictable locations so the user can find them") — the opposite of the "ALWAYS save to artifacts" rule; says iteration limit is 50 (it is 100), LLM timeout 120 s (300 s), "five tiers" incl. archival (unused), working memory "10-20 exchanges" (200 replayed). It is 8.6 KB ≈ 2.1k tokens per call. | Med | S | data/personality/agent.md ("File Workspace", "Performance and Limitations", "read_file() and write_file()" sections) vs prompts.py:211-236, core.py:21, 194 |
| F8 | **Six copies of the "run one turn" pump / eight copies of turn setup.** `core.chat` is the single LLM loop (good), but event consumption + agent construction + skill resolution + episodic save are re-implemented in: `_stream_turn`, the WS persona loop (which bypasses `_stream_turn` entirely — no route outcome, ignores `error` events, drops `done` metadata), REST `chat()`, `skill_accept`/`skill_decline` branches, `autonomous_task`, `iteration_loop._run_phase`, `scheduled_job`, `coding_task`, and five messaging adapters each with an inline `_run_agent` closure and its own explicit/auto skill resolution. | Med | M | api/chat.py:171-215, 247-345, 437-508, 510-575, 711-800; autonomous_task.py:425-495; iteration_loop.py:134-178; scheduled_job.py:84-90; coding_task.py:83-118; messaging/adapters/{discord:130-152, slack:128-148, matrix:123-143, mattermost:142-162, telegram:322-350} |
| F9 | **`save_transcript_artifact` is a superseded special case.** `ingest_source`'s own description says it "Replaces save_transcript_artifact". It hardcodes one MCP connection name and duplicates the path-normalize + UNIQUE-retry logic of `save_to_artifact`. 1.6 KB schema + 120 lines dispatch. | Med | S | tools.py:764-811, 2276-2393 vs 827-868 |
| F10 | **`create_task` schema is 6.0 KB ≈ 1.5k tokens** — 12 % of the whole tool budget — and its approval/plan/schedule prose is repeated a third time in the system prompt ("Scheduled task approval flow", "Skills vs scheduled tasks") and a fourth in the server-side guard's rejection text. | Med | S | tools.py:247-441; prompts.py:245-314; tools.py:2809-2818 |
| F11 | **Malformed tool-call JSON is swallowed as `{}`**; the model then gets `Error executing X: 'path'` (a KeyError) with no hint that its arguments were unparseable. | Low | S | models/provider.py:345-348, 413-417; tools.py:4168-4171 |
| F12 | **HOST_EXEC refusal lives only in `AgentCore`, not in `execute_tool`.** Anyone calling `execute_tool("run_command", …)` directly (tests do, 38 call sites) runs it. Not exploitable today, but the gate is one layer above where the effect happens. | Low | S | core.py:535-541 vs tools.py:1729-1755 |
| F13 | **MCP tool names truncated to 64 chars can't be resolved.** `get_openai_tool_schemas` truncates `mcp_<conn>_<tool>` to 64; `resolve_tool_call` requires the exact original suffix → `Unknown MCP tool`. Also, connection names containing `_` make prefix matching ambiguous (`mcp_a_b_c` ⇐ conn `a` tool `b_c` or conn `a_b` tool `c`); first client in dict order wins. No cap on MCP tool count. | Low | S | mcp_client/client.py:498-503; manager.py:557-572, 531-537 |
| F14 | **Dead / duplicate code in tools.py**: `_configured_search` (never called); `_ddg_search` (reachable only if `SearchProviderManager.search` itself raises; the manager has its own `_ddg`); `show_file` computes `suffix` and `caption` and uses neither; `save_to_artifact` returns literal `v{1}`; `TOOL_SCHEMAS` imported into core.py unused; `get_all_tool_schemas(project_id)` ignores its arg; `tasks/autonomous.py` is a whole legacy agent-runner referenced by nothing. | Low | S | tools.py:4441-4501, 4504-4523, 1833-1834, 3323, 50-54; core.py:14; tasks/autonomous.py |
| F15 | **Bare `httpx.AsyncClient()` in `analyze_company_financials`** — CLAUDE.md says hot-path outbound HTTP uses `pooled_client`/`safe_http_get`. Host is fixed (SEC) so no SSRF, but it's the convention. | Low | S | tools.py:3852, 3870 |
| F16 | **`save_last_response`'s no-history fallback tells the model to "call write_file directly"** — the one place the prompt says to use scratch storage for durable content. | Low | S | tools.py:2147-2151 |
| F17 | **"Storage layers" block says the workspace is `data/workspace/`**; per-project workspaces are `data/projects/<id>/workspace`. Minor factual drift. | Low | S | prompts.py:222 vs tools.py:4263-4270 |

---

## 2. Details and evidence

### 2.1 Inventory sanity (task 1)

* All 60 schema entries have a dispatch branch; no orphan schemas. Verified by cross-referencing the `"name":` lines (tools.py:80-1707) with the `elif tool_name ==` / `in (...)` / `startswith(...)` lines (tools.py:1756-4123). Full table in Appendix A.
* Dispatch branches without a top-level schema: only the `tool_name.startswith("browser_")` branch (tools.py:2270-2274), whose schemas live in `browser_tools.py:257-330` and are appended conditionally. Not a bug, just a split.
* Prefix branches (`github_` 3386, `git_` 3532) catch unknown names and return `Unknown github tool` / `Unknown git tool` (3522, 3813); artifact group has `Unknown artifact tool` (3384) which is unreachable because the outer `in (...)` tuple is exhaustive.

### 2.2 Overlap clusters (task 2)

**A. Artifacts vs workspace** — `save_to_artifact / update_artifact / read_artifact / list_artifacts` (1372-1442; 3236-3384) vs `read_file / write_file / list_workspace_files` (170-232; 2052-2122), plus the workspace-only satellites `show_file`, `download_file`, `convert_document`, `batch_convert_documents`, `index_workspace`, and artifact-only `index_artifact`.
The distinction is real and the model needs it: `read_file` does PDF extraction + vision OCR (2058-2090) and reaches git checkouts; `run_command`/`git_*` only see disk; artifacts are what `recall` indexes. Merging the two families into one tool with a `store=` flag would not remove the concept the model has to hold. What *is* redundant:
  * `index_workspace` already auto-pivots to artifact indexing when the path is not on disk (2963-3003). The boundary is blurred in code but not in the schema → merge into `index(target=…)` or drop the pivot.
  * `convert_document` is `batch_convert_documents` with a one-element list (1909-1954 vs 1956-2050). Drop the singular.
  * The confusion is being fought with ~3.7 KB of prose across three prompt blocks (prompts.py:72-96, 176-209, 211-236) plus every tool description. A naming convention (`artifact_read`, `workspace_read`, …) would carry the same information at near-zero token cost. That is a rename, not a merge.

**B. `save_transcript_artifact` vs `ingest_source` vs `save_to_artifact`** — `ingest_source` (827-868) is the canonical path and says so. `save_transcript_artifact` (764-811; 2276-2393) hardcodes `mcp_SubDownload_fetch_transcript` (2325), re-implements path normalization and UNIQUE-retry (2336-2370) that `save_to_artifact` already has, and skips topic extraction. Retire it; keep `save_to_artifact` for free-form content.

**C. Memory** — `recall` is the only search entry point (no `search_memory`/`semantic_search` duplicates; good). `remember` overlaps `create_graph_node`+`link_concepts` only nominally because its `graph` tier is a lie (F3). The merge-proposal quartet `list_merge_proposals / approve_merge / reject_merge / force_merge` (514-614; 2585-2701) is 2.5 KB of schema for a rare maintenance flow with near-identical args → one `merge_topics(action=list|approve|reject|force, …)`. `consolidate_memory`, `extract_topics`, `link_topic_similarity` are distinct enough to keep.

**D. Web** — `web_search` (233-246; 2267-2268 → `search_providers`) and 6 `browser_*` tools. The prompt and `browser_open`'s description both route "web pages / when web_fetch fails" to a `web_fetch` tool that doesn't exist (prompts.py:233, browser_tools.py:260). The nearest thing is `download_file` → `read_file` (two calls, disk round-trip). Either add a real `web_fetch` (`safe_http_get` + `trafilatura` are already in the codebase) or fix the references. Two search implementations exist in tools.py but only the manager path is live (F14).

**E. Tasks/jobs** — `create_task` already has a mode parameter (`job_type` ∈ autonomous_task|iteration_loop, 338-357). `start_coding_task` (1317-1371; 3147-3171) is functionally `create_task(job_type="coding_task", schedule="now")` minus the approval gate; its 984-char "STRICT SCOPE" description exists solely to stop the model confusing it with `create_task`. Folding it into `job_type` removes that confusion structurally. `get_job_status / list_recent_jobs / rerun_job` could be one `jobs(action=…)` but the saving is small (~250 tok) and the three descriptions carry genuinely different guidance (schedule_id vs job_id resolution, 1020-1044); low priority.

**F. GitHub API (10) vs local git (6, host-exec)** — Two `create_branch`s, two `create_pr`s. The split is meaningful: `github_*` work in background contexts where `git_*` are hidden, and `coding_task`'s prompt already frames the API tools as fallback (coding_task.py:93-100). But 10 near-identical `github_*` schemas (≈4.9 KB) are a clear `github(action=read_file|list_directory|…)` candidate — the dispatch is already a single block sharing auth resolution (3386-3531).

**G. Skills** — only `create_skill`. No overlap; the missing `list_skills` referenced at 2840 is covered by the available-skills prompt block.

**H. Financial trio** — `analyze_company_financials`, `compare_company_strategy_and_risks`, `analyze_earnings_call` (1649-1725; 3815-4165): 2.2 KB schema, 350 lines of dispatch, hardcoded to SEC EDGAR + `summarize` class. Domain-specific enough that it belongs behind a skill + the `sec/edgar` adapter rather than in every call's tool list.

**Token estimate** (Appendix C): 50,023 chars ≈ 12.5k tokens at 4 chars/token (14.3k at 3.5). Realistic wins: retire `save_transcript_artifact` (−400 tok), fold `start_coding_task` into `create_task` (−350 net), `github_*`→1 (−700), merge quartet→1 (−450), convert pair→1 (−230), index pair→1 (−250), financial→skill (−550), trim `create_task` prose that duplicates the system prompt (−600). Total ≈ **−3.5k tokens (~28 %)** without removing any capability the model actually uses.

### 2.3 Dispatch structure (task 3)

* `execute_tool` spans tools.py:1729-4171. Shape: one `try:` (1753) → MCP prefix short-circuit (1760-1768) → `if/elif` chain on `tool_name` (1756-4123) → `else: Unknown tool` (4165-4166) → `except Exception: "Error executing {name}: {e}"` (4168-4171). Four nested sub-dispatchers (artifact 3259-3384, github 3392-3522, git 3539-3813, save_last_response mode 2166-2210).
* Every branch consumes only `tool_args`, `effective_project`, `project_id`, `session_id`, `memory_manager`, `last_assistant_text`, `interactive` — a six-field `ToolContext` covers all of them. A `@tool("name", schema={...})` registry that co-locates schema + handler and rebuilds `TOOL_SCHEMAS` in registration order is low-risk because: (a) all branches are literal-equality or prefix matches; (b) the outer try/except becomes the registry wrapper; (c) `execute_tool(...)`'s signature can stay as the facade — tests call it 38 times across 8 files (`tests/integration/test_git_tools.py`, `test_local_coding_tools.py`, `test_financial_tools.py`, `test_security_hardening.py`, …); (d) external imports of private helpers (`_safe_workspace_path` from browser_tools.py:232, `_run_git_cmd`/`_resolve_repo_checkout` from autonomous_task.py:85, `_web_search` from manager.py:595/629) must be re-exported from the package `__init__`. Natural split: `agent/tools/{artifacts,workspace,memory,ingest,jobs,git,github,finance,search}.py`.
* Return type is always `str`; no dict returns. Error prefixes are inconsistent (F5). No branch raises deliberately; exceptions are caught at 4168.
* Truncation: none at the loop level (F1). Per-tool caps that do exist: `recall` 10×400 chars (1808), `list_artifacts` default limit 20 (3368), `git_merge` hunks 4000 (3703), `git_commit` marker list 1500 (3739), `browser_read` 8000 (browser_tools.py:207), `run_command`/`code_execute` rely on the sandbox's 1 MB cap, MCP `structured` 50 KB (manager.py:118-119) but MCP `text` unbounded.

### 2.4 Gating (task 4)

* **HOST_EXEC_TOOLS** (tools.py:28-32: `code_execute, run_command, git_sync_repo, git_status, git_create_branch, git_merge, git_commit, git_push_pr`). Schema filtering: core.py:470-475. Dispatch refusal: core.py:535-541 (before `execute_tool`). `host_exec_allowed(context)` (tools.py:35-47) reads `AGENT_HOST_EXEC` (config.py:88, default `interactive`). Callers: chat.py:81-83 and coding_task.py:82-87 pass `"interactive"`; autonomous_task.py:260, scheduled_job.py:84, iteration_loop.py:260/315/348, tasks/autonomous.py:62, all five messaging adapters pass `"background"`. Both halves are correct; the gap is F12 (refusal not inside `execute_tool`) and F2 (prompt unaware of it).
* **Browser**: added in `get_all_tool_schemas` (tools.py:55-60) iff `browser_enabled()` (env `BROWSER_ENABLED`, browser_tools.py:26-27); dispatch re-checks at tools.py:2270-2274. Consistent.
* **MCP**: `get_all_tool_schemas` (tools.py:61-70) → `MCPManager.get_all_tool_schemas` (manager.py:531-537) → per-client `get_openai_tool_schemas(excluded_tools)` (client.py:485-514). Namespacing by `mcp_<connection>_<tool>` prefix; per-connection `excluded_tools` list; no max count; no dedup across connections; 64-char truncation breaks resolution (F13). Built-in collision is avoided only by convention (no built-in starts with `mcp_`). Dispatch: tools.py:1760-1768 → `manager.execute_tool` (582-672), which also hides a Tavily-specific fallback to `_web_search`.

### 2.5 System prompt (task 5)

Assembly order in `build_system_prompt` (prompts.py:35-316) then two appends in `AgentCore.chat` (core.py:389-416). Sizes in Appendix B. Problems:

* **Duplicates tool descriptions**: "Storage layers" (211-236) ≈ `read_file`/`write_file`/`list_workspace_files`/`save_to_artifact` descriptions (tools.py:172-232, 1374-1376) ≈ project_section storage layout (72-96). "Persistence boundary" (176-209) ≈ `save_to_artifact` description. "Skills vs scheduled tasks" (245-283) ≈ `create_skill` (665-690) + `create_task.job_type` (338-357) + `_build_available_skills_block` (core.py:111-124) + `start_coding_task` (1319-1340). "Scheduled task approval flow" (285-314) ≈ `create_task` description (250-270) + guard text (tools.py:2809-2818). Recent-jobs IMPORTANT footer (core.py:85-91) ≈ `get_job_status` description (1022-1044). Each concept is stated 3-4 times.
* **Contradictions**: F2 (repo protocol vs hidden tools), F7 (`agent.md` says write outputs to workspace), F16 (`write_file` fallback), F3 (`graph` tier). Also `create_task` description says the task "only fires after they click Approve in the Tasks tab" (252-255) while the prompt's flow ends with `skip_review: true` after chat approval (298) — both are valid paths but read as inconsistent.
* **Nonexistent tools**: `web_fetch`, `save_chat_as_artifact`, `list_skills` (F6).
* **Stale**: `agent.md` numbers (F7); `mcp_SubDownload_*` baked into a generic prompt (prompts.py:230-232, tools.py:250-262).
* **Recent-jobs block** (core.py:30-93): last 5 jobs, 24 h window, `include_system=False`, errors/progress cut at 200 chars, plus a ~330-char fixed footer. **Available-skills block** (core.py:96-139): up to 30 skills, description first line cut at 160 chars, 6 triggers each, ~350-char fixed header; worst case ≈ 9 KB. Both are appended after `build_system_prompt` returns, so `build_system_prompt` callers elsewhere (none found) would miss them.

### 2.6 Agent loop (task 6)

* `MAX_TOOL_ITERATIONS` env, default 100 (core.py:21); per-call `max_iterations` (core.py:290); `create_task` clamps 1..1000 (tools.py:2851).
* Pre-recall: `limit_per_tier=5`, 4 s timeout, emitted as synthetic `context_loaded` tool events (core.py:339-377) — F4.
* Streaming (480-499) vs non-streaming (500-517) differ only in that non-streaming re-emits `tool_call` events post hoc to mirror streaming; both then share tool execution (523-559). Malformed args → `{}` silently (provider.py:345-348, 413-417) — F11.
* Tool errors go back as an ordinary `role: tool` message with the full string (555-559); host-exec refusal string at 535-541; unknown tool → `Unknown tool: X` (4166); exception → `Error executing X: …` (4171).
* Re-anchor every `reanchor_every=15` iterations (561-573) and a "10 left" notice (574-583); `truncated` flag (584-593). `run_autonomous` (606-623) wraps `chat(stream=False)`.
* Working memory keeps only user text + final assistant text (327, 595-597); intermediate tool traffic is dropped between turns (documented at 178-181).
* Duplication (F8): the LLM loop itself is not duplicated — every consumer calls `agent.chat`/`run_autonomous`. What is duplicated is (1) the event pump: `_stream_turn` (chat.py:171-215), persona loop (chat.py:788-793, drains only text/tool events, ignores `error`/`done` payload, no `_finish_route` outcome), `autonomous_task` (425-495), `iteration_loop._run_phase` (134-178); (2) turn setup: provider+memory+`AgentCore`+skill resolve+episodic save appears in REST `chat()` (247-345), WS main (596-855), `skill_accept` (437-508), `skill_decline` (510-575), and each of the five messaging adapters (each also re-implements explicit/auto skill resolution, e.g. telegram.py:352-370). `tasks/autonomous.py` is a sixth, unreferenced runner.

### 2.7 Dead code (task 7)

See F14. Additionally: `interactive` (tools.py:1736) is consulted only by `create_task` (2800-2802); `httpx` module import is live (download_file, financials). `sec_edgar.py` adapter exists but CLAUDE.md's "28 adapters / 9 mechanisms" list omits it (doc drift, not code).

---

## 3. Recommendation — what to merge, what to keep

**Worth doing (clear win, low risk):**
1. Retire `save_transcript_artifact` → `ingest_source(source_type="youtube/…")`. Delete 1.6 KB schema + 120 lines.
2. Fold `start_coding_task` into `create_task(job_type="coding_task")`; keep the approval bypass as a documented property of that job_type. Deletes the whole "STRICT SCOPE" paragraph.
3. Collapse the 10 `github_*` tools into one `github(action=…)`; dispatch already shares setup.
4. Collapse `list/approve/reject/force_merge` into `merge_topics(action=…)`.
5. Drop `convert_document` (keep batch), drop `index_workspace`'s artifact pivot and merge with `index_artifact` into `index(target=artifact|workspace)`.
6. Move the three financial tools behind a skill that calls the `sec/edgar` adapter, or make them MCP.
7. Cut `create_task`'s description to what the system prompt does not already say (or vice-versa — pick one home per rule).
8. Add a single result cap in `core.chat` (e.g. 24-32 KB with head/tail + "…[N chars omitted; re-read with offset]") — this is the highest-value single change in the file.

**Not worth merging (model needs the distinction):**
* Artifact vs workspace families. The semantics differ (durable/indexed vs disk/OCR/git). Rename for clarity (`artifact_*` / `workspace_*`) and delete two of the three prose blocks instead.
* `web_search` vs `browser_*` — different mechanisms; but add `web_fetch` or stop referencing it.
* `github_*` vs `git_*` — different availability (API works when host-exec is off).
* `remember`/`recall`/`create_graph_node`/`link_concepts` — fine as-is once the `graph` tier lie is fixed (either implement it via `GraphMemory.add_node` or drop it from the enum).
* `get_job_status`/`list_recent_jobs`/`rerun_job` — small, and the descriptions carry real disambiguation.

**Structural (do once):** registry/decorator dispatch with a `ToolContext`; move the host-exec check into `execute_tool`; pass `host_exec` into `build_system_prompt` so the repo protocol is only emitted when the tools exist; make `_stream_turn` the only event pump (persona loop and messaging adapters should call it or a shared `run_turn()`); normalise error returns to a single `Error: <tool>: <msg>` shape and make `_TOOL_ERROR_RE` match it.

---

## Appendix A — Tool inventory (60 built-in + 6 browser)

Categories: artifact, workspace, memory, graph, ingest, task, skill, mcp, host-exec, browser, search, image, self-doc, github, git, finance, other. Gate column: `HE` = in `HOST_EXEC_TOOLS` (hidden unless `AgentCore(host_exec=True)`); `BR` = only when `BROWSER_ENABLED`; `INT` = behaviour depends on `interactive`; `MCP` = requires a live MCP tool; `REPO` = requires a bound GitHub repo; `CFG` = requires configured route/provider.

| # | Tool | Schema line | Dispatch line | Category | Purpose (one line) | Gate |
|---|------|-------------|---------------|----------|--------------------|------|
| 1 | remember | 80 | 1756 | memory | Store text in working/episodic/semantic/(graph*) tier | — (*graph tier not implemented) |
| 2 | recall | 104 | 1771 | memory | Search semantic+episodic+graph, 10 hits × 400 chars | — |
| 3 | create_graph_node | 133 | 1810 | graph | Add node by type/label | — |
| 4 | link_concepts | 157 | 1819 | graph | Add edge between labels | — |
| 5 | read_file | 173 | 2052 | workspace | Read scratch file; PDF text + vision OCR | — |
| 6 | write_file | 193 | 2102 | workspace | Write scratch file | — |
| 7 | list_workspace_files | 215 | 2108 | workspace | List scratch dir | — |
| 8 | web_search | 236 | 2267 | search | Provider chain Brave→SearXNG→DDG | — |
| 9 | create_task | 250 | 2796 | task | Schedule autonomous_task / iteration_loop (proposed unless approved) | INT (skip_review forced off in background) |
| 10 | send_telegram | 446 | 2934 | other | Broadcast to Telegram allowlist | CFG |
| 11 | index_workspace | 460 | 2942 | memory | Index scratch file/dir; auto-pivots to artifacts | — |
| 12 | link_topic_similarity | 482 | 2550 | graph | Backfill SEMANTICALLY_SIMILAR_TO + merge proposals | — |
| 13 | list_merge_proposals | 517 | 2585 | graph | List proposals | — |
| 14 | approve_merge | 542 | 2607 | graph | Execute proposal (irreversible) | — |
| 15 | reject_merge | 572 | 2643 | graph | Mark proposal rejected | — |
| 16 | force_merge | 592 | 2662 | graph | Merge two labels bypassing queue | — |
| 17 | rerun_job | 618 | 2703 | task | Clone a finished job as new run | — |
| 18 | get_self_documentation | 649 | 2733 | self-doc | Render live config/routing doc | — |
| 19 | create_skill | 666 | 2740 | skill | Scaffold skill.json + instructions.md | — |
| 20 | index_artifact | 733 | 3009 | memory | Index artifact by id or prefix | — |
| 21 | save_transcript_artifact | 767 | 2276 | ingest | Fetch YT transcript via hardcoded MCP tool and save | MCP (`mcp_SubDownload_fetch_transcript`) |
| 22 | list_source_adapters | 815 | 2395 | ingest | List registered adapters | — |
| 23 | ingest_source | 830 | 2411 | ingest | Canonical adapter ingest | MCP (per adapter) |
| 24 | batch_ingest_sources | 872 | 2458 | ingest | ingest_source over a list | MCP (per adapter) |
| 25 | extract_topics | 904 | 2502 | ingest | Dry-run extractor on artifact/text | CFG (extract class) |
| 26 | save_last_response | 942 | 2124 | artifact | Save last N messages (verbatim/summarize/research/custom) as artifact | CFG (summarize class for transforms) |
| 27 | show_file | 967 | 1828 | workspace | Emit `[DISPLAY:workspace://…]` for UI | — |
| 28 | download_file | 982 | 1850 | workspace | HTTP(S) → scratch file via safe_http_get | — |
| 29 | generate_image | 998 | 1847 | image | image_gen class → binary artifact + `[DISPLAY:artifact://…]` | CFG (image_gen route) |
| 30 | get_job_status | 1023 | 3062 | task | Job or schedule status | — |
| 31 | list_recent_jobs | 1048 | 3116 | task | Jobs last 72 h | — |
| 32 | consolidate_memory | 1078 | 3141 | memory | Summarize + extract session | — |
| 33 | code_execute | 1089 | 3173 | host-exec | Sandbox python/node/bash snippet | HE |
| 34 | run_command | 1129 | 3200 | host-exec | Shell in repo checkout / workspace | HE |
| 35 | github_list_connections | 1163 | 3392 | github | Diagnostic list of PATs + binding | — |
| 36 | github_read_file | 1171 | 3434 | github | GH API read | REPO |
| 37 | github_list_directory | 1186 | 3438 | github | GH API ls | REPO |
| 38 | github_list_branches | 1201 | 3443 | github | GH API branches | REPO |
| 39 | github_list_pulls | 1215 | 3459 | github | GH API PRs | REPO |
| 40 | github_create_branch | 1231 | 3475 | github | GH API branch | REPO |
| 41 | github_delete_branch | 1246 | 3483 | github | GH API delete (refuses default) | REPO |
| 42 | github_write_files | 1260 | 3490 | github | GH API atomic multi-file commit | REPO |
| 43 | github_create_pr | 1287 | 3502 | github | GH API PR | REPO |
| 44 | github_merge_pr | 1305 | 3514 | github | GH API merge (squash default) | REPO |
| 45 | start_coding_task | 1320 | 3147 | task | Enqueue coding_task job (no approval gate) | REPO (at run time) |
| 46 | save_to_artifact | 1375 | 3259 | artifact | Create artifact; -N suffix on collision; auto-index | — |
| 47 | update_artifact | 1395 | 3324 | artifact | New version in place | — |
| 48 | read_artifact | 1411 | 3343 | artifact | Read by id/path (full content, uncapped) | — |
| 49 | list_artifacts | 1426 | 3361 | artifact | Filter by tag/type/prefix/search (limit 20) | — |
| 50 | convert_document | 1446 | 1909 | workspace | Pandoc/LibreOffice single convert | CFG (binaries) |
| 51 | batch_convert_documents | 1475 | 1956 | workspace | Glob/dir batch convert | CFG (binaries) |
| 52 | git_sync_repo | 1505 | 3539 | git | Clone/fetch bound repo + pre-commit hook | HE, REPO |
| 53 | git_status | 1534 | 3622 | git | porcelain status | HE |
| 54 | git_create_branch | 1546 | 3628 | git | checkout -b / checkout | HE |
| 55 | git_merge | 1564 | 3639 | git | --no-ff merge with conflict hunks | HE |
| 56 | git_commit | 1597 | 3718 | git | add + conflict-marker guard + commit | HE |
| 57 | git_push_pr | 1626 | 3752 | git | push + create PR | HE, REPO |
| 58 | analyze_company_financials | 1652 | 3815 | finance | SEC XBRL common-size/ratios/growth | — (bare httpx) |
| 59 | compare_company_strategy_and_risks | 1679 | 4068 | finance | 10-K comparison via summarize class | CFG (summarize) |
| 60 | analyze_earnings_call | 1707 | 4123 | finance | Transcript file → LLM briefing | CFG (summarize) |
| B1 | browser_open | browser_tools.py:257 | 2270 → browser_tools.py:334 | browser | goto with SSRF guard | BR |
| B2 | browser_read | 268 | 336 | browser | innerText, 8000 chars | BR |
| B3 | browser_click | 280 | 338 | browser | click selector | BR |
| B4 | browser_type | 290 | 340 | browser | fill (+Enter) | BR |
| B5 | browser_screenshot | 305 | 342 | browser | full-page PNG to workspace | BR |
| B6 | browser_close | 317 | 344 | browser | close project context | BR |
| M* | mcp_<conn>_<tool> | manager.py:531 / client.py:485 | tools.py:1760 | mcp | Passthrough; text + `<structured-output>` | MCP; per-connection `excluded_tools`; no count cap |

Schemas with no dispatch: **0**. Dispatch branches with no schema in `TOOL_SCHEMAS`: `browser_*` (schemas elsewhere), `mcp_*` (dynamic). Unreachable: `Unknown artifact tool` (3384).

---

## Appendix B — System prompt blocks (order of assembly, static sizes)

Measured from literal strings; dynamic parts noted. 1 token ≈ 4 chars.

| # | Block | Source | Static chars | ≈ tokens | Condition |
|---|-------|--------|-------------:|---------:|-----------|
| 1 | Personality scope prefix | prompts.py:13-32 | 200-250 | ~60 | always (minimal/balanced/strong) |
| 2 | `soul.md` | data/personality/soul.md (default) | 3,407 | ~850 | always (or `custom_soul`) |
| 3 | `agent.md` | data/personality/agent.md (default) | 8,557 | ~2,140 | unless `custom_soul` |
| 4 | Active Project + Storage layout | prompts.py:57-96 | ~1,150 | ~290 | `project_id` set |
| 5 | Repository work protocol | prompts.py:101-133 | 1,618 | ~400 | project has bound repo (ignores host_exec — F2) |
| 6 | Corpus Context (recalled memories) | prompts.py:135-148 | 354 + hits | ~90 + (5/tier × ≤~400) | recall returned hits |
| 7 | Additional Context (skill instructions) | prompts.py:150 | 25 + skill | — | skill active |
| 8 | Self-reference conventions | prompts.py:170-174 | 468 | ~117 | always |
| 9 | Persistence boundary — Pantheon vs MCP save | prompts.py:176-209 | 1,355 | ~338 | always |
| 10 | Storage layers — artifacts vs workspace | prompts.py:211-236 | 1,350 | ~337 | always |
| 11 | Tool selection — scan before you decline | prompts.py:238-271 | 1,528 | ~382 | always |
| 12 | Skills vs scheduled tasks | prompts.py:245-283 | 1,914 | ~478 | always |
| 13 | Scheduled task approval flow | prompts.py:285-314 | 2,022 | ~505 | always |
| 14 | Current time | prompts.py:316 | ~30 | ~8 | always |
| 15 | RECENT BACKGROUND JOB ACTIVITY | core.py:30-93 | ~330 footer + ≤5 × ~150 | ~80-270 | project has jobs in last 24 h |
| 16 | Available skills | core.py:96-139 | ~350 header + ≤30 × ~250 | ~90-1,900 | project has skills |

Static tail (8-14): 8,732 chars ≈ 2,180 tok. Typical total with defaults, project set, no repo, no skills: ≈ 22 KB ≈ 5.5k tokens. Plus tool schemas (Appendix C) ≈ 12.5k → **~18k fixed tokens per call**.

---

## Appendix C — Tool-schema token cost

Computed by `ast.literal_eval` of `TOOL_SCHEMAS` and `len(json.dumps(schema))` per entry (script at scratchpad `tokcost.py`).

* 60 tools, **50,023 chars** total → **12,505 tokens** at 4 chars/tok (14,292 at 3.5).
* Browser tools: 6, 1,936 chars ≈ 480 tok (conditional).
* MCP: unbounded; each server's full `inputSchema` is forwarded verbatim.

Top 15 by size (chars: total / description / parameters):

| Tool | Total | Desc | Params |
|------|------:|-----:|-------:|
| create_task | 5,970 | 975 | 4,881 |
| save_last_response | 2,094 | 514 | 1,476 |
| create_skill | 1,857 | 893 | 859 |
| ingest_source | 1,761 | 667 | 996 |
| start_coding_task | 1,709 | 984 | 611 |
| save_transcript_artifact | 1,623 | 807 | 693 |
| save_to_artifact | 1,258 | 639 | 510 |
| generate_image | 1,229 | 320 | 799 |
| code_execute | 1,082 | 476 | 513 |
| link_topic_similarity | 1,031 | 459 | 463 |
| convert_document | 969 | 217 | 655 |
| run_command | 964 | 422 | 445 |
| extract_topics | 947 | 319 | 533 |
| batch_convert_documents | 914 | 118 | 692 |
| index_artifact | 906 | 408 | 398 |

Family subtotals: github_* 10 tools ≈ 4,880 chars; git_* 6 ≈ 3,540; merge quartet 4 ≈ 2,460; finance 3 ≈ 2,210; artifact 4 ≈ 2,660; workspace core 3 ≈ 1,790; jobs 3 ≈ 2,250.

Estimated savings from the merges recommended in §3 (items 1-7): ≈ 12.5k → ≈ 9k tokens per call.
