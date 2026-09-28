"""Scheduled tasks and jobs: create_task, job status, reruns, coding tasks, Telegram."""
from __future__ import annotations

from typing import Any
from agent.tools.registry import ToolContext, tool


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "create_task",
            "description": (
                "Schedule an autonomous background task. Unless skip_review=true it is created "
                "PAUSED with a proposed plan; it runs only after the user approves it in the Tasks tab. "
                "In the plan, name the exact tools each step uses (look at the tools you actually have — "
                "mcp_*, github, save_to_artifact, ingest_source…); if a step needs a tool you lack, "
                "say so in the plan rather than pretending."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Short label for the Tasks list, 3-7 words, e.g. 'Daily PR digest'."
                    },
                    "description": {
                        "type": "string",
                        "description": "What the agent should do, in full — the background run sees only this, the plan and project memory."
                    },
                    "schedule": {
                        "type": "string",
                        "description": (
                            "One-shot: 'now' or 'delay:N' (N minutes from now). Recurring: 'interval:N' "
                            "(every N minutes) or a cron expression ('0 9 * * *' = daily 9am). "
                            "'in 2 minutes' is delay:2, not interval:2."
                        )
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Max run time, default 1800. Use 3600-7200 for batch ingests of 20+ items."
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "Agent-loop round budget, default 100. Raise (e.g. 300) for long multi-step work, with timeout_seconds."
                    },
                    "skill_name": {
                        "type": "string",
                        "description": (
                            "Skill slug to drive the task (its instructions are loaded, like /<slug> in chat). "
                            "Use this instead of writing '/slug' in the description. Required MCP connectors are checked at start."
                        )
                    },
                    "plan": {
                        "type": "string",
                        "description": (
                            "Required. Numbered markdown steps, each naming its tool, e.g. "
                            "'1. List the channel's latest videos with `mcp_<server>_get_channel_latest_videos`. "
                            "2. Ingest each with `ingest_source`.'"
                        )
                    },
                    "skip_review": {
                        "type": "boolean",
                        "default": False,
                        "description": "True only when the user explicitly said not to review the plan."
                    },
                    "job_type": {
                        "type": "string",
                        "enum": ["autonomous_task", "iteration_loop", "coding_task"],
                        "default": "autonomous_task",
                        "description": (
                            "'autonomous_task' (default): run the plan once. 'iteration_loop': repeated "
                            "execute→review turns (for 'loop', 'iterate N times', generator/reviewer work); "
                            "each turn is saved as iteration/<job_id>/turn-N.md. 'coding_task': a background "
                            "coding agent edits the bound GitHub repo and opens a PR — ONLY for authoring code "
                            "(fix, feature, refactor), never for ingest or research; starts now, no plan review. "
                            "Put the stack and file layout in coding_context (read them with the github tool first)."
                        )
                    },
                    "coding_context": {
                        "type": "string",
                        "description": "coding_task only: tech stack, file layout, conventions."
                    },
                    "branch_name": {
                        "type": "string",
                        "description": "coding_task only: branch to work on (auto-named if omitted)."
                    },
                    "base_branch": {
                        "type": "string",
                        "description": "coding_task only: base branch (repo default if omitted)."
                    },
                    "max_turns": {
                        "type": "integer",
                        "default": 10,
                        "description": "iteration_loop only: max turns (stops early when the reviewer says STATUS: done)."
                    },
                    "execute_instruction": {
                        "type": "string",
                        "description": "iteration_loop only: per-turn execute instruction (default: the description)."
                    },
                    "review_instruction": {
                        "type": "string",
                        "description": "iteration_loop only: per-turn review instruction (default: find gaps, pick the next step, emit STATUS: continue|done)."
                    },
                    "branch_strategy": {
                        "type": "string",
                        "enum": ["single_feature", "main", "branch_per_turn"],
                        "default": "single_feature",
                        "description": (
                            "iteration_loop with a bound repo: 'single_feature' (default) commits every turn to "
                            "iteration/<job_id>; 'main' commits to main; 'branch_per_turn' makes a branch per turn."
                        )
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
            "description": "Send a message to the operator via Telegram. Use for important updates, task completions, or when you need human input on a long-running task.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Message to send"}
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "rerun_job",
            "description": (
                "Re-run a finished job (completed / failed / stalled / "
                "cancelled) with the exact same payload, title, "
                "schedule_id, and timeout. Creates a NEW job entry "
                "linked back to the original via parent_job_id; the "
                "original record stays as audit history.\n\n"
                "Useful when:\n"
                "  - the user says 'run yesterday\'s research task again'\n"
                "  - a recent ingest looks incomplete and you want to "
                "redo the same work\n"
                "  - a failed task got fixed (e.g. MCP reconnected) "
                "and the user wants to retry without rebuilding the "
                "schedule\n\n"
                "Accepts the full job UUID or an 8-char prefix."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "job_id": {
                        "type": "string",
                        "description": "Full UUID or 8-char prefix from list_recent_jobs."
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
                "Get the current state of a background job. Pass either "
                "a job UUID (full id from list_recent_jobs) OR a "
                "schedule_id (the short 8-char hex id returned by "
                "create_task — refers to the schedule, not a single "
                "run). When given a schedule_id, this returns the most "
                "recent run of that schedule. Use this when the user "
                "asks about a task you started earlier — READ THE "
                "ACTUAL STATUS before claiming the job did/didn't run. "
                "Returns status, progress text, error, result, "
                "session_id, artifact_id, and pr_url where applicable."
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
            "description": (
                "List recent background jobs for the active project. Use "
                "this when the user asks 'what are you working on' / "
                "'is anything still running' / 'did that finish'. Filter "
                "by status when needed. By default omits system job types "
                "(extraction, file_indexing) so the list is user-relevant."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["queued","running","completed","failed","cancelled","stalled"],
                        "description": "Optional status filter."
                    },
                    "include_system": {
                        "type": "boolean",
                        "description": "Include extraction/file_indexing rows. Default false.",
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

    plan_status = "approved" if skip_review else "proposed"
    skill_name = (tool_args.get("skill_name") or "").strip().lower() or None
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
                f"your instructions (or create it with create_skill), then retry."
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
    if job_type not in ("autonomous_task", "iteration_loop"):
        return (
            f"create_task rejected: job_type {job_type!r} is not "
            f"recognized. Use 'autonomous_task' (default) or "
            f"'iteration_loop'."
        )
    extras: dict | None = None
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
        f"Tell the user the plan is queued for review and ask "
        f"them to read the chat or open the Tasks tab to approve "
        f"or edit. The schedule will not fire until they approve."
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

