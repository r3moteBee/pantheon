"""Scheduled tasks and jobs: create_task, job status, reruns, coding tasks, Telegram."""
from __future__ import annotations

import logging
import re

from typing import Any
from agent.tools.registry import ToolContext, tool

logger = logging.getLogger(__name__)


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": (
                "Run work in the background: later, on a schedule, or too big for one reply. Starts right away "
                "when the user asked for it in this chat; otherwise, or when the plan deletes, overwrites, "
                "merges or pushes, it waits for the user's approval in the Tasks tab. Work you can do now (one "
                "image, one lookup) is not a task - do it."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short label, 3-7 words."
                    },
                    "description": {
                        "type": "string",
                        "description": (
                            "Everything the run needs to know; it sees only this, the plan and project memory."
                        )
                    },
                    "schedule": {
                        "type": "string",
                        "description": (
                            "'now', 'delay:N' (once, N minutes from now), 'interval:N' (every N minutes) or cron "
                            "('0 9 * * *'). Repeat only when the user asked for a repeat."
                        )
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Default 1800; 3600-7200 for big batches."
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "Round budget, default 100."
                    },
                    "skill_name": {
                        "type": "string",
                        "description": "Installed skill to run (its slug), instead of '/slug' in the description."
                    },
                    "plan": {
                        "type": "string",
                        "description": (
                            "Numbered steps, each naming the tool it uses. Say so if a step needs a tool you "
                            "don't have."
                        )
                    },
                    "skip_review": {
                        "type": "boolean",
                        "default": False,
                        "description": "True only when the user said not to review the plan."
                    },
                    "job_type": {
                        "type": "string",
                        "enum": ["autonomous_task", "iteration_loop", "coding_task", "research_batch"],
                        "default": "autonomous_task",
                        "description": (
                            "autonomous_task: run the plan once. research_batch: research a list of items (more "
                            "than ~5), a sourced note per item, then a summary; needs items and item_question. "
                            "iteration_loop: repeated execute/review turns ('loop', 'iterate N times'). "
                            "coding_task: a coding agent edits the bound repo and opens a PR - only for writing "
                            "code."
                        )
                    },
                    "coding_context": {
                        "type": "string",
                        "description": "coding_task: stack, file layout, conventions."
                    },
                    "branch_name": {
                        "type": "string",
                        "description": "coding_task: branch (auto if omitted)."
                    },
                    "base_branch": {
                        "type": "string",
                        "description": "coding_task: base branch."
                    },
                    "items": {
                        "type": "array", "items": {"type": "string"},
                        "description": "research_batch: the items, one note each."
                    },
                    "item_question": {
                        "type": "string",
                        "description": (
                            "research_batch: the question per item with {item}, e.g. 'What changed in {item} "
                            "this month?'"
                        )
                    },
                    "lookups_per_item": {
                        "type": "integer", "default": 6,
                        "description": "research_batch: web lookups per item (1-15)."
                    },
                    "max_turns": {
                        "type": "integer",
                        "default": 10,
                        "description": "iteration_loop: max turns."
                    },
                    "execute_instruction": {
                        "type": "string",
                        "description": "iteration_loop: per-turn instruction."
                    },
                    "review_instruction": {
                        "type": "string",
                        "description": "iteration_loop: per-turn review instruction."
                    },
                    "branch_strategy": {
                        "type": "string",
                        "enum": ["single_feature", "main", "branch_per_turn"],
                        "default": "single_feature",
                        "description": "iteration_loop with a repo: where turns commit."
                    }
                },
                "required": ["name", "description", "schedule", "plan"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_telegram",
            "description": "Send the operator a Telegram message.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string"}
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rerun_job",
            "description": "Re-run a finished job with the same payload; the original stays as history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "UUID or 8-char prefix."
                    }
                },
                "required": ["job_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_job_status",
            "description": (
                "Status of a background job, by job id or the 8-char schedule id from create_task (latest run). "
                "Check it before saying a job did or didn't run."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {"type": "string"}
                },
                "required": ["job_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_recent_jobs",
            "description": "Recent background jobs in this project ('what are you working on', 'did that finish').",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["queued","running","completed","failed","cancelled","stalled"]
                    },
                    "include_system": {
                        "type": "boolean",
                        "description": "Include extraction/indexing jobs.",
                        "default": False
                    },
                    "limit": {"type": "integer", "default": 10}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "start_coding_task",
            "description": (
                "Background coding agent that edits the bound GitHub repo and opens a PR. ONLY for "
                "authoring code (fix a bug, add a feature, refactor, open a PR) — never for ingest, "
                "research, summarizing, indexing or running a skill; do those inline or with "
                "create_task. First build coding_context with github_list_directory / "
                "github_read_file. Returns a job_id (get_job_status)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task_description": {
                        "type": "string",
                        "description": "Detailed description of what to build/fix/change."
                    },
                    "title": {
                        "type": "string",
                        "description": "Short title shown in the Tasks UI."
                    },
                    "coding_context": {
                        "type": "string",
                        "description": "Project context — tech stack, file layout, conventions."
                    },
                    "branch_name": {
                        "type": "string",
                        "description": "Optional explicit branch name; auto-generated otherwise."
                    },
                    "base_branch": {
                        "type": "string",
                        "description": "Optional override for the base branch; defaults to bound repo's default."
                    }
                },
                "required": ["task_description"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "start_autoresearch",
            "description": (
                "Background loop that mutates one workspace file, runs a benchmark after each change, keeps "
                "changes that improve the metric and saves a report. Web chat only. Agree the settings with the "
                "user first."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "target_file": {"type": "string", "description": "Workspace-relative file to optimise."},
                    "eval_cmd": {"type": "string", "description": "Benchmark command, e.g. 'python benchmark.py'."},
                    "metric": {"type": "string", "description": "Metric name it prints, e.g. 'time'."},
                    "direction": {"type": "string", "enum": ["min", "max"]},
                    "iterations": {"type": "integer", "description": "Default 10, max 50."},
                    "instructions": {"type": "string"},
                },
                "required": ["target_file", "eval_cmd", "metric", "direction"],
            },
        },
    },
]


@tool('rerun_job')
async def _tool_rerun_job(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    from jobs.store import get_store
    jid = (tool_args.get("job_id") or "").strip()
    if not jid:
        return "rerun_job rejected: job_id is required."
    store = get_store()
    target = store.get_or_none(jid)
    # Allow 8-char prefix.
    if not target and len(jid) >= 4:
        cand = [r for r in store.list(limit=200) if r["id"].startswith(jid)]
        if len(cand) == 1:
            target = cand[0]
        elif len(cand) > 1:
            return f"rerun_job: prefix {jid!r} matches {len(cand)} jobs; provide more characters."
    if not target:
        return f"rerun_job: no job with id {jid!r}."
    try:
        new_job = store.rerun(target["id"])
    except ValueError as e:
        return f"rerun_job rejected: {e}"
    return (
        f"Rerun queued.\n"
        f"  new_job_id: {new_job['id']}\n"
        f"  from_job_id: {target['id']}\n"
        f"  type: {new_job['job_type']}\n"
        f"  title: {new_job.get('title') or '(untitled)'}\n\n"
        f"Worker picks it up on the next poll. Track via "
        f"get_job_status({new_job['id'][:8]!r})."
    )



# What the model does after proposing a task. Without the last sentence it
# proposed a 35-state election survey and then did it inline from memory in
# the same turn (2026-10-02), saving 8 artifacts of invented data.
PROPOSED_NEXT_STEP = (
    "Tell the user the plan is queued for review and ask them to read the chat or open the Tasks tab to approve "
    "or edit. The schedule will not fire until they approve. Do NOT also do the task's work now in this chat - "
    "the task does it once approved; end your reply after telling the user."
)


@tool('create_task')
async def _tool_create_task(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    session_id = ctx.session_id
    interactive = ctx.interactive
    effective_project = ctx.effective_project
    if tool_args.get("job_type") == "coding_task":
        # Coding work goes straight to the coding agent (what
        # start_coding_task always did); the plan joins the brief.
        brief = (tool_args.get("description") or "").strip()
        if (tool_args.get("plan") or "").strip():
            brief += "\n\nPlan:\n" + tool_args["plan"].strip()
        return await _tool_start_coding_task(ctx, "start_coding_task", {
            "task_description": brief,
            "title": tool_args.get("name"),
            "coding_context": tool_args.get("coding_context"),
            "branch_name": tool_args.get("branch_name"),
            "base_branch": tool_args.get("base_branch"),
        })
    from tasks.scheduler import schedule_agent_task
    plan_text = (tool_args.get("plan") or "").strip()
    skip_review = bool(tool_args.get("skip_review", False))
    # Only a person in the web chat can have approved the plan.
    # Background runs (jobs, bots) may be steered by injected
    # content, so their tasks always land as proposals.
    if skip_review and not interactive:
        skip_review = False
    # A task the user asked for in this chat message runs without the review step;
    # review stays for the agent's own initiative and for plans that can lose data
    # (agent/task_intent.py).
    auto_approved = None
    if not skip_review and ctx.user_requested_task:
        from agent.task_intent import review_reason
        held = review_reason(tool_args)
        if held:
            logger.info("create_task: requested in chat but held for review - %s", held)
        else:
            skip_review = auto_approved = True

    # SERVER-SIDE GUARDRAIL: reject create_task without a plan, and
    # reject skip_review=true unless the user explicitly approved
    # the plan in the current conversation. The agent's system
    # prompt should already enforce this, but the model
    # sometimes ignores it — refusal here is the backstop.
    if not plan_text:
        return (
            "create_task rejected: a plan is required. "
            "Reply to the user with a numbered markdown plan "
            "naming the exact tools you intend to use per step, "
            "ask them to approve or edit, and only call "
            "create_task again AFTER they explicitly approve in "
            "this chat. Do not infer approval from prior "
            "context, memory recall, or similar past requests."
        )

    # A repeating schedule nobody asked for runs forever: asked to research something "in the
    # background", the agent scheduled it interval:60 (2026-10-08).
    from tasks.scheduler import is_recurring_schedule
    sched = (tool_args.get("schedule") or "now").strip()
    if interactive and ctx.user_message and is_recurring_schedule(sched):
        from agent.task_intent import asks_to_repeat
        if not asks_to_repeat(ctx.user_message):
            return (f"create_task rejected: schedule {sched!r} repeats, but the user did not ask for anything "
                    "recurring. For a single background run use 'now'; for one run later use 'delay:N' (N minutes "
                    "from now). Call create_task again with that schedule.")

    plan_status = "approved" if skip_review else "proposed"
    skill_name = (tool_args.get("skill_name") or "").strip().lower() or None
    # A job type given as a skill ("research_batch") is the job type: told the skill was not
    # registered and to create it, the agent made a skill named research_batch (2026-10-08).
    if skill_name and skill_name.replace("-", "_") in ("autonomous_task", "iteration_loop", "research_batch", "coding_task"):
        tool_args = {**tool_args, "job_type": skill_name.replace("-", "_")}
        skill_name = None
    # Validate the skill exists at scheduling time so the user
    # gets immediate feedback instead of a runtime failure.
    if skill_name:
        from skills.registry import get_skill_registry
        _reg = get_skill_registry()
        # Tolerate underscore<->hyphen drift the same way
        # resolve_explicit does.
        resolved = None
        for variant in (skill_name, skill_name.replace("_", "-"), skill_name.replace("-", "_")):
            sk = _reg.get(variant)
            if sk:
                resolved = sk.name
                break
        if not resolved:
            return (
                f"create_task rejected: skill {tool_args.get('skill_name')!r} "
                f"is not registered. Pick a slug from the available-skills list in "
                f"your instructions, or leave skill_name out (the plan alone drives the task). "
                f"Do not create a skill just to make this call work."
            )
        skill_name = resolved
    timeout_seconds = tool_args.get("timeout_seconds")
    if timeout_seconds is not None:
        try:
            timeout_seconds = int(timeout_seconds)
        except (TypeError, ValueError):
            timeout_seconds = None
    max_iterations = tool_args.get("max_iterations")
    if max_iterations is not None:
        try:
            max_iterations = max(1, min(int(max_iterations), 1000))
        except (TypeError, ValueError):
            max_iterations = None
    job_type = (tool_args.get("job_type") or "autonomous_task").strip()
    if job_type not in ("autonomous_task", "iteration_loop", "research_batch"):
        return (
            f"create_task rejected: job_type {job_type!r} is not "
            f"recognized. Use 'autonomous_task' (default), "
            f"'iteration_loop' or 'research_batch'."
        )
    extras: dict | None = None
    if job_type == "research_batch":
        items = tool_args.get("items")
        if isinstance(items, str):
            items = [i.strip() for i in re.split(r"[,\n]", items) if i.strip()]
        question = (tool_args.get("item_question") or "").strip()
        if not items or not isinstance(items, list) or not question:
            return ("create_task rejected: research_batch needs items (a list, e.g. the state names) and "
                    "item_question (with {item} where each item goes).")
        if "{item}" not in question:
            question += " ({item})"
        try:
            per_item = max(1, min(int(tool_args.get("lookups_per_item") or 6), 15))
        except (TypeError, ValueError):
            per_item = 6
        extras = {"topic": tool_args.get("name") or "research", "items": [str(i) for i in items][:200],
                  "item_question": question, "lookups_per_item": per_item}
    if job_type == "iteration_loop":
        try:
            max_turns = int(tool_args.get("max_turns", 10))
        except (TypeError, ValueError):
            max_turns = 10
        max_turns = max(1, min(max_turns, 50))
        branch_strategy = (tool_args.get("branch_strategy") or "single_feature").strip()
        if branch_strategy not in ("single_feature", "main", "branch_per_turn"):
            return (
                f"create_task rejected: branch_strategy "
                f"{branch_strategy!r} not recognized. Use "
                f"'single_feature' (default), 'main', or "
                f"'branch_per_turn'."
            )
        extras = {
            "max_turns": max_turns,
            "topic": tool_args.get("name", "iteration"),
            "execute_instruction": (tool_args.get("execute_instruction") or "").strip() or None,
            "review_instruction": (tool_args.get("review_instruction") or "").strip() or None,
            "branch_strategy": branch_strategy,
        }
        if timeout_seconds is None:
            timeout_seconds = 7200
    if max_iterations:
        extras = {**(extras or {}), "max_iterations": max_iterations}
    task_id = await schedule_agent_task(
        name=tool_args.get("name", "task"),
        description=tool_args.get("description", ""),
        schedule=tool_args.get("schedule", "now"),
        project_id=effective_project,
        plan=plan_text,
        plan_status=plan_status,
        parent_session_id=session_id,
        skill_name=skill_name,
        timeout_seconds=timeout_seconds,
        job_type=job_type,
        extras=extras,
    )
    if auto_approved:
        return (
            f"Task scheduled (the user asked for it in this chat, so it runs without the review step).\n"
            f"  schedule_id: {task_id}  (SCHEDULE id, not a job run id)\n"
            f"  name: {tool_args.get('name')}\n"
            f"  schedule: {tool_args.get('schedule')}\n\n"
            f"Tell the user in one line what you queued and when it runs. Do NOT also do the task's "
            f"work now in this chat - the task does it."
        )
    if skip_review:
        return (
            f"Task scheduled (review skipped — assumes the user "
            f"already approved the plan in this chat).\n"
            f"  schedule_id: {task_id}  (SCHEDULE id, not a "
            f"job run id)\n"
            f"  name: {tool_args.get('name')}\n"
            f"  schedule: {tool_args.get('schedule')}\n\n"
            f"To check whether the schedule actually fired, "
            f"call list_recent_jobs() or "
            f"get_job_status(job_id={task_id!r}) — that "
            f"resolves the schedule to its most recent job "
            f"run.\n\n"
            f"If the user did NOT explicitly approve, immediately "
            f"acknowledge that and ask them to confirm or cancel."
        )
    return (
        f"Task PROPOSED — paused, awaiting your approval.\n"
        f"  schedule_id: {task_id}  (this is the SCHEDULE id, "
        f"not a job run id)\n"
        f"  name: {tool_args.get('name')}\n"
        f"  schedule: {tool_args.get('schedule')}\n\n"
        f"NOTE: Each time the schedule fires it produces a "
        f"separate JOB with its own UUID. To check whether it "
        f"actually ran, call list_recent_jobs() or "
        f"get_job_status(job_id={task_id!r}) — the latter "
        f"will resolve the schedule to its most recent run.\n\n"
        + PROPOSED_NEXT_STEP
    )



@tool('send_telegram')
async def _tool_send_telegram(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    try:
        from messaging.adapters.telegram import send_message_to_all
        await send_message_to_all(tool_args["message"])
        return "Telegram message sent."
    except Exception as e:
        return f"Telegram send failed: {e}"



@tool('get_job_status')
async def _tool_get_job_status(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    from jobs.store import get_store
    jid = (tool_args.get("job_id") or "").strip()
    store = get_store()
    j = store.get_or_none(jid)
    via_schedule = False
    if not j and jid:
        # Fall back: maybe the caller passed a schedule_id
        # (the 8-char hex id returned by create_task) instead
        # of a full job UUID. Look up the most recent run.
        j = store.find_latest_for_schedule(jid)
        via_schedule = bool(j)
    if not j:
        # Also try matching a job UUID by 8-char prefix
        # (e.g. agent quotes a truncated id).
        if jid and len(jid) >= 4:
            matches = store.list(limit=200)
            cand = [r for r in matches if r["id"].startswith(jid)]
            if len(cand) == 1:
                j = cand[0]
    if not j:
        return (
            f"No job or schedule found for id {jid!r}. "
            f"If this looks like a schedule_id (8-char hex "
            f"from create_task), the schedule may not have "
            f"fired yet — call list_recent_jobs to see what "
            f"actually ran, or check the Tasks tab to "
            f"confirm the schedule is approved and active."
        )
    lines = [
        f"job_id: {j['id']}"
        + (f"  (resolved from schedule_id {jid!r})" if via_schedule else ""),
        f"schedule_id: {j.get('schedule_id') or '(none)'}",
        f"type: {j['job_type']}  status: {j['status'].upper()}",
        f"title: {j.get('title') or ''}",
        f"started: {j.get('started_at') or '(not yet)'}",
        f"completed: {j.get('completed_at') or '(still running)'}",
    ]
    if j.get("progress"):
        lines.append(f"progress: {j['progress']}")
    if j.get("error"):
        lines.append(f"error: {j['error']}")
    if j.get("pr_url"):
        lines.append(f"pr_url: {j['pr_url']}")
    if j.get("artifact_id"):
        lines.append(f"artifact_id: {j['artifact_id']}")
    if j.get("session_id"):
        lines.append(f"session_id: {j['session_id']}")
    if j.get("result"):
        summary = (j["result"] or {}).get("summary") if isinstance(j["result"], dict) else None
        if summary:
            lines.append(f"summary: {summary[:500]}")
    return "\n".join(lines)



@tool('list_recent_jobs')
async def _tool_list_recent_jobs(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    effective_project = ctx.effective_project
    from jobs.store import get_store
    include_sys = bool(tool_args.get("include_system", False))
    statuses = [tool_args["status"]] if tool_args.get("status") else None
    from datetime import timedelta
    runs = get_store().list(
        project_id=effective_project,
        statuses=statuses, include_system=include_sys,
        started_within=timedelta(hours=72),
        limit=int(tool_args.get("limit") or 10),
    )
    if not runs:
        return "(no recent jobs in this project)"
    out = []
    for r in runs:
        line = f"#{r['id'][:8]}  [{r['job_type']}]  {r['status'].upper()}  {r.get('title') or ''}"
        if r.get("progress") and r["status"] == "running":
            line += f"  ({r['progress'][:100]})"
        if r.get("error"):
            line += f"  ERR: {r['error'][:120]}"
        if r.get("pr_url"):
            line += f"  {r['pr_url']}"
        out.append(line)
    return "\n".join(out)



@tool('start_coding_task')
async def _tool_start_coding_task(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    # A coding task runs with host exec (run_command, git_*) as if a person had
    # asked for it, so only a person in the web chat can start one. Background
    # runs (jobs, messaging bots) may be steered by content they read; for other
    # tasks create_task turns their requests into proposals, but coding tasks
    # have no proposal state, so they are refused here.
    if not ctx.interactive:
        return ("start_coding_task refused: coding tasks run commands on the host and can only be "
                "started from the web chat. Tell the user what you would like the coding agent to do "
                "so they can start it there.")
    effective_project = ctx.effective_project
    from jobs.store import get_store
    title = tool_args.get("title") or "Coding task"
    description = tool_args.get("task_description") or ""
    payload = {
        "task_description": description,
        "coding_context": tool_args.get("coding_context") or "",
        "branch_name": tool_args.get("branch_name"),
        "base_branch": tool_args.get("base_branch"),
    }
    j = get_store().create(
        job_type="coding_task",
        project_id=effective_project,
        title=title,
        description=description[:200],
        payload=payload,
    )
    return (
        f"Coding task queued.\n"
        f"  job_id: {j['id']}\n"
        f"  title: {title}\n"
        f"You can call get_job_status(job_id={j['id'][:8]!r}) "
        f"or list_recent_jobs() to check progress. The user can "
        f"watch it in the chat Tasks tab."
    )




@tool('start_autoresearch')
async def _tool_start_autoresearch(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    # The benchmark is a shell command on the host: same rule as coding tasks.
    from agent.tools import host_exec_allowed
    if not ctx.interactive:
        return ("start_autoresearch refused: the loop runs its benchmark command on the host and can only be "
                "started from the web chat.")
    if not host_exec_allowed("interactive"):
        return "start_autoresearch refused: host commands are disabled here (AGENT_HOST_EXEC=never)."
    from agent.tools.workspace import _get_workspace_base
    workspace = _get_workspace_base(ctx.effective_project)
    target = (workspace / (tool_args.get("target_file") or "")).resolve()
    if not target.is_relative_to(workspace) or not target.is_file():
        return f"start_autoresearch: {tool_args.get('target_file')!r} is not a file in the project workspace."
    from jobs.store import get_store
    payload = {k: tool_args.get(k) for k in ("target_file", "eval_cmd", "metric", "direction", "iterations", "instructions")}
    j = get_store().create(job_type="autoresearch", project_id=ctx.effective_project,
                           title=f"Autoresearch: {payload['target_file']}",
                           description=f"{payload['metric']} ({payload.get('direction') or 'min'}) via {payload['eval_cmd']}"[:200],
                           payload=payload)
    return (f"Autoresearch queued.\n  job_id: {j['id']}\n"
            f"Progress shows per round in the Tasks tab; get_job_status(job_id={j['id'][:8]!r}) reports it. "
            "When it finishes, the report is saved as an artifact (report_artifact_id in the job result).")
