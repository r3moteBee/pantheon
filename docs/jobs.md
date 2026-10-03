# Jobs and scheduled tasks

All background work runs as rows in `data/db/jobs.db`. A single in-process worker executes them. APScheduler (`data/db/scheduler.db`) only decides *when* to enqueue a row.

## Components (`backend/jobs/`)

| Module | Role |
|---|---|
| `store.py` | `JobStore`: `create`, `get`, `list`, `claim_next` (atomic queued→running, respects `scheduled_for`), `heartbeat`, `complete`, `fail`, `mark_cancelled`, `mark_stalled`, `cancel`, `rerun`, `stall_running`, `delete`. Failures and stalls also write an episodic task-log note. |
| `worker.py` | `JobWorker`: polls every `JOB_WORKER_POLL_SECONDS` (1s) and runs up to `JOB_WORKER_CONCURRENCY` jobs at once (default 2). `coding_task` and `iteration_loop` share the project's repo checkout, so only one of those runs per project at a time (`REPO_JOB_TYPES`). It supervises the handler task every `JOB_WORKER_SUPERVISE_SECONDS` (2s). |
| `watchdog.py` | `StallWatchdog`: every `JOB_STALL_CHECK_SECONDS` (60s) it marks `running` rows with no heartbeat for `JOB_STALL_TIMEOUT_SECONDS` (300s) as `stalled`. |
| `recovery.py` | `recover_orphaned_jobs()`: runs at startup, before the worker starts (see below). |
| `context.py` | `JobContext` (`heartbeat`, `touch`, `update_result`, `cancel_requested`), `pinger_for`, `AGENT_MAX_QUIET_SECONDS = 900`. |
| `handlers/` | One module per job type, each registered with `@register(job_type, default_timeout_seconds=…)`. `bootstrap.py` imports them at startup. |

## Job types

| Type | Default timeout | Created by | `POST /api/jobs`? |
|---|---|---|---|
| `autonomous_task` | 1800s | scheduler (`create_task` tool, `POST /api/tasks`, bot `/task`) | yes |
| `coding_task` | 1800s | `create_task(job_type="coding_task")` (legacy: `start_coding_task`) | yes |
| `iteration_loop` | 7200s | scheduler with `job_type="iteration_loop"` | yes |
| `image_extraction` | 300s | chat image upload (`api/chat.py`) | **no** |
| `autoresearch` | 7200s | `start_autoresearch` tool (web chat only) | **no** |
| `research_batch` | 14400s | scheduler with `job_type="research_batch"` (`create_task`) | **no** |

`CREATABLE_JOB_TYPES` in `api/jobs.py` is `("autonomous_task", "iteration_loop", "coding_task")`. Any other type returns 400, because internal handlers trust payloads the backend builds itself.

- **autonomous_task:** an agent loop with a free-form prompt, plus an optional skill and plan (below).
- **coding_task:** a GitHub sub-agent working on the project's bound repo. It works with `run_command` and the `git_*` tools (the `github` tool is a fallback), opens a PR, and returns `{branch, files_changed, pr_url, artifact_id, summary}`. It runs with `host_exec_allowed("interactive")`.
- **iteration_loop:** execute/review turns, each saved to `iteration/<first 8 chars of job_id>/turn-NN.md`. It stops on `max_turns` (default 10, clamped 1–50), reviewer `STATUS: done`, two stalled turns in a row, or a cancel.
- **research_batch:** a list researched one item at a time (`items`, `item_question` with `{item}`, `lookups_per_item` default 6). Each item gets a fresh agent turn with only `web_search`/`web_fetch` and no memory; the handler saves its reply as `<project>/research/<topic>/<item>.md` (sourced facts, "not found" for the rest). A re-run reuses existing notes, cancel stops between items. A summary turn with no tools writes `summary.md` from the notes only, and a completion notice is posted to the chat that proposed it.
- **autoresearch:** the evolutionary loop in `utils/autoresearch.py` on one workspace file: a baseline run of the benchmark command, then per round an LLM mutation, the benchmark again, and the change kept only if the metric improves (reverted otherwise). Progress per round, cancel between rounds, report saved as an artifact (`autoresearch/report-<job>.md`). The benchmark is a host command, so `start_autoresearch` is a host-exec tool that only queues from the web chat, and the job needs `host_exec_allowed("interactive")`.
- **image_extraction:** vision, OCR and topic extraction for an uploaded image artifact.

Memory extraction and file indexing run inline, not as jobs.

## Scheduling (`backend/tasks/scheduler.py`)

`schedule_agent_task(name, description, schedule, project_id, plan, plan_status, parent_session_id, skill_name, timeout_seconds, job_type, extras)` adds an APScheduler job. When it fires, `_enqueue_autonomous_job` creates a queued row with `schedule_id` set.

| `schedule` | Meaning |
|---|---|
| `now` | One run, immediately |
| `delay:N` | One run, N minutes from now |
| `interval:N` | Every N minutes |
| 5-field cron (`0 9 * * *`) | Recurring |

APScheduler defaults: `coalesce=True`, `max_instances=1`, `misfire_grace_time=300`.

### Plan review

The `create_task` agent tool requires a `plan`. Unless `skip_review=true` **and** the turn is interactive (web chat), the schedule is created with `plan_status="proposed"` and **paused**. The user reviews it in the Tasks tab:

- `PATCH /api/tasks/{id}/plan`: edit the plan.
- `POST /api/tasks/{id}/approve`: set `approved` and resume. A one-shot whose time passed during review runs right away instead of being dropped as a misfire.
- `POST /api/tasks/{id}/run-now`: enqueue immediately. A recurring schedule keeps its timing; a one-shot is removed.

Background contexts (jobs, bots) can never skip review. `POST /api/tasks` creates approved tasks with no plan.

### Skill binding

`payload.skill_name`, or a leading `/slug` in the description, is resolved with underscore/hyphen tolerance. The job **fails fast** in three cases: the skill is unknown, it is `scan_blocked`, or its `requires_mcp` tool names are not all registered with the MCP manager. See [skills.md](skills.md).

## Timeouts, heartbeats, progress

- **Total timeout:** the job's `timeout_seconds`, or the handler default. `create_task` accepts `timeout_seconds` and `max_iterations` (agent rounds, 1–1000).
- **Stall:** 5 min without a heartbeat. Agent handlers wrap their run in `pinger_for(ctx, 30, max_quiet=AGENT_MAX_QUIET_SECONDS)`. The pinger heartbeats every 30s **only while** something inside has called `utils.progress.report_progress()` within the last 15 min. Agent rounds, stream chunks, tool results, ingest items and MCP responses all report progress. A hung await goes quiet, the watchdog stalls the row, and the worker cancels the handler. New long-running code under a job should call `report_progress()`.
- The autonomous handler also writes a heartbeat with a progress string on every tool call, matched to the current plan step.

## Terminal states

`queued → running → completed | failed | cancelled | stalled`. Every terminal write is guarded on `status='running'`, so a stalled row is never flipped back.

| Cause | Result |
|---|---|
| Handler returns normally | `completed` (with `result`, `session_id`, `artifact_id`, `pr_url`) |
| Returns `{"status": "failed"\|"error"}` or raises | `failed` |
| Returns `{"status": "cancelled"}`, or user `POST /api/jobs/{id}/cancel` | `cancelled` (a queued job is cancelled directly; a running one sets `cancel_requested`) |
| Total timeout | `failed` ("Handler timed out after Ns") |
| Watchdog | `stalled` |
| Worker shutdown | row left `running` for orphan recovery |

`max_attempts` is stored, but the worker never retries on its own.

## Rerun and restart recovery

`POST /api/jobs/{id}/rerun` copies a finished job (type, payload, title, timeout, `schedule_id`) into a new queued row with `parent_job_id` pointing at the original. Active jobs return 400.

At startup, orphaned `running` rows are marked `stalled`. `autonomous_task` orphans are then auto-rerun up to `JOB_AUTO_REQUEUE_MAX` (2) times, counted in `payload.auto_requeue_count`. Other types stay stalled for a manual rerun.

## Task ledger

For multi-part work, the autonomous handler tells the agent to keep a ledger artifact at `task-ledger/<task-slug>.md`, tagged `task-ledger`. Older runs used `tasks/<slug>-ledger.md`. If a ledger already exists, the prompt says to read it first and resume from it. This is what makes auto-requeue and reruns safe.

## Completion notice

`create_task` from chat records `parent_session_id`. On finish, `autonomous_task` posts a "Task completed" assistant message into that session, and `iteration_loop` posts "Iteration loop completed" with the stop reason and turn count. `image_extraction` also reports into the chat session it came from. An autonomous run with no tool calls and no text is flagged as a likely silent failure.

## API

- `/api/jobs`: list (filters: project, type, status, `started_within_hours`, `include_system`), get, create, cancel, rerun, delete.
- `/api/tasks`: schedule CRUD, `approve`, `PATCH …/plan`, `run-now`, logs.
