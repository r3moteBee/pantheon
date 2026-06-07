"""autonomous_task handler — wraps the existing tasks/autonomous flow.

Payload shape:
    {
      "task_name": str,
      "description": str,        # the prompt for the agent loop
      "schedule": "now" | cron,  # informational
    }

Existing tasks/scheduler.schedule_agent_task() and
tasks/autonomous.run_autonomous_task() get wrapped so heartbeats and
result/error reporting flow through the jobs row.
"""
from __future__ import annotations

import logging
from typing import Any

from jobs.context import JobContext, pinger_for
from jobs.handlers import register

logger = logging.getLogger(__name__)


# ── Plan-step parsing + tool→step mapping ───────────────────────────────────

import re as _re

def _extract_plan_steps(plan: str) -> list[str]:
    """Extract numbered steps from a markdown plan. Returns [] if the plan
    isn't recognizably step-formatted.

    Recognizes lines starting with '1.', '2.', etc, optionally inside
    '   ' indentation. Captures up to the next numbered line or the end.
    """
    if not plan:
        return []
    lines = [ln.strip() for ln in plan.splitlines()]
    steps: list[str] = []
    current: list[str] = []
    for ln in lines:
        m = _re.match(r"^(\d+)\.\s+(.+)$", ln)
        if m:
            if current:
                steps.append(" ".join(current).strip())
            current = [m.group(2)]
        elif current and ln:
            current.append(ln)
    if current:
        steps.append(" ".join(current).strip())
    return steps


def _match_tool_to_step(tool_name: str, steps: list[str]) -> int | None:
    """Given a tool name, find the plan step that mentions it. Returns
    the 1-based step index or None if no match."""
    if not steps or not tool_name:
        return None
    lower = tool_name.lower()
    # Strip common prefixes for fuzzier match
    short = lower.replace("mcp_", "").replace("github_", "")
    for i, step in enumerate(steps, start=1):
        sl = step.lower()
        if lower in sl or short in sl:
            return i
    return None


def _format_progress(tool_count: int, tool_name: str,
                     step_idx: int | None, steps: list[str]) -> str:
    if step_idx and steps:
        step_text = steps[step_idx - 1][:60]
        return f"Step {step_idx}/{len(steps)} → {tool_name}: {step_text}"
    return f"Tool call #{tool_count}: {tool_name}"




async def _repo_snapshot(project_id: str) -> dict | None:
    """Ground-truth git state of the project's repo checkout. Computed by
    the harness so completion notices report verified numbers instead of
    the model's own arithmetic (observed: '40 deleted' for 44, '29
    branches' for 31)."""
    try:
        from agent.tools import _resolve_repo_checkout, _run_git_cmd
        cwd = _resolve_repo_checkout(project_id)
        if not cwd:
            return None
        snap: dict = {}
        _, head, _ = await _run_git_cmd(["rev-parse", "HEAD"], cwd, auto_init=False)
        snap["head"] = head.strip()
        _, branch, _ = await _run_git_cmd(["branch", "--show-current"], cwd, auto_init=False)
        snap["branch"] = branch.strip() or "(detached)"
        _, cnt, _ = await _run_git_cmd(["rev-list", "--count", "HEAD"], cwd, auto_init=False)
        snap["commit_count"] = int(cnt.strip()) if cnt.strip().isdigit() else 0
        code, ahead, _ = await _run_git_cmd(
            ["rev-list", "--count", f"origin/{snap['branch']}..HEAD"],
            cwd, auto_init=False)
        snap["ahead_of_origin"] = (int(ahead.strip())
                                   if code == 0 and ahead.strip().isdigit() else None)
        _, dirty, _ = await _run_git_cmd(["status", "--porcelain"], cwd, auto_init=False)
        snap["dirty"] = bool(dirty.strip())
        return snap
    except Exception:
        logger.debug("repo snapshot failed", exc_info=True)
        return None


def _repo_delta_line(before: dict | None, after: dict | None) -> str | None:
    """One-line harness-verified summary of what the run did to the repo."""
    if not after:
        return None
    parts = []
    if before and before.get("head") != after.get("head"):
        added = (after.get("commit_count") or 0) - (before.get("commit_count") or 0)
        parts.append(
            f"+{added} commit{'s' if added != 1 else ''} on {after['branch']} "
            f"({(before.get('head') or '')[:7]} → {(after.get('head') or '')[:7]})")
    elif before:
        parts.append(f"no new commits on {after.get('branch')}")
    ahead = after.get("ahead_of_origin")
    if ahead is not None:
        parts.append("fully pushed" if ahead == 0 else f"{ahead} unpushed")
    parts.append("working tree DIRTY" if after.get("dirty") else "tree clean")
    return "Repo: " + " · ".join(parts)


@register("autonomous_task", default_timeout_seconds=1800,
          description="Single-fire autonomous agent loop with a free-form prompt.")
async def handle_autonomous_task(ctx: JobContext) -> dict[str, Any]:
    pl = ctx.payload
    task_name = pl.get("task_name") or ctx.title or "Autonomous task"
    description = pl.get("description") or ctx.description or ""

    await ctx.heartbeat(progress="Spinning up agent…")
    ctx.update_result({"task_name": task_name})

    # Build the agent + memory the same way tasks/autonomous does, but
    # report progress + heartbeats along the way.
    import uuid
    session_id = f"autonomous-{ctx.job_id[:8]}-{uuid.uuid4().hex[:6]}"
    ctx.update_result({"session_id": session_id})
    await ctx.heartbeat(progress="Loading memory + agent…")

    from agent.core import AgentCore
    from memory.manager import create_memory_manager
    from memory.episodic import EpisodicMemory
    from models.provider import get_provider

    provider = get_provider()
    memory = create_memory_manager(
        project_id=ctx.project_id, session_id=session_id, provider=provider,
    )

    # ── Skill resolution (parity with the chat handler) ──────────────
    # Sources, in order of precedence:
    #   1. payload.skill_name (set explicitly via create_task)
    #   2. /<slug> at the start of the description (fallback for
    #      tasks scheduled by hand or by older clients)
    skill_context = None
    active_skill_name = None
    skill_name_pl = (pl.get("skill_name") or "").strip().lower() or None
    if not skill_name_pl:
        # Fallback: parse /<slug> from the description.
        try:
            from skills.resolver import resolve_explicit
            explicit, _rest = resolve_explicit(description or "")
            if explicit:
                skill_name_pl = explicit
        except Exception:
            pass
    if skill_name_pl:
        try:
            from skills.registry import get_skill_registry
            from skills.resolver import build_skill_context
            registry = get_skill_registry()
            sk = None
            for variant in (skill_name_pl,
                            skill_name_pl.replace("_", "-"),
                            skill_name_pl.replace("-", "_")):
                sk = registry.get(variant)
                if sk:
                    break
            if not sk:
                err = (f"skill {skill_name_pl!r} is not registered; "
                       f"task aborted before agent loop")
                logger.warning("autonomous_task %s: %s", ctx.job_id[:8], err)
                ctx.update_result({"error": err, "skill_name_requested": skill_name_pl})
                return {"status": "failed", "error": err, "session_id": session_id}

            # Validate MCP preconditions if the skill declares any.
            requires_mcp = []
            try:
                requires_mcp = list(getattr(sk.manifest, "requires_mcp", None)
                                    or getattr(sk, "requires_mcp", None) or [])
            except Exception:
                pass
            if requires_mcp:
                try:
                    from mcp_client.manager import get_mcp_manager
                    available_mcp = {
                        ((sp or {}).get("function") or {}).get("name")
                        for sp in (get_mcp_manager().get_all_tool_schemas() or [])
                        if (sp or {}).get("function")
                    }
                except Exception:
                    available_mcp = set()
                missing = [t for t in requires_mcp if t not in available_mcp]
                if missing:
                    err = (
                        f"skill {sk.name} requires MCP tools that are "
                        f"not currently registered: {missing}. "
                        f"Connect them via Settings → MCP servers and retry."
                    )
                    logger.warning("autonomous_task %s: %s",
                                   ctx.job_id[:8], err)
                    ctx.update_result({
                        "error": err,
                        "skill_name": sk.name,
                        "missing_mcp_tools": missing,
                    })
                    return {"status": "failed", "error": err,
                            "session_id": session_id}

            skill_context = build_skill_context(sk, project_id=ctx.project_id)
            active_skill_name = sk.name
            try:
                from skills import analytics as _sa
                _sa.record_fire(sk.name, source="scheduled")
            except Exception:
                pass
            logger.info("autonomous_task %s: skill activated: %s",
                        ctx.job_id[:8], sk.name)
            ctx.update_result({"active_skill_name": sk.name})
        except Exception as e:
            logger.exception("autonomous_task %s: skill resolution failed: %s",
                             ctx.job_id[:8], e)

    # ── Resolve project name for the system-prompt active-project block.
    project_name: str | None = None
    try:
        import json as _json
        from config import get_settings as _gs
        meta = _gs().db_dir / "projects.json"
        if meta.exists():
            data = _json.loads(meta.read_text() or "{}")
            row = data.get(ctx.project_id) if isinstance(data, dict) else None
            if isinstance(row, dict):
                project_name = row.get("name")
    except Exception:
        pass

    agent = AgentCore(
        provider=provider, memory_manager=memory,
        project_id=ctx.project_id, session_id=session_id,
        project_name=project_name,
        skill_context=skill_context,
        active_skill_name=active_skill_name,
    )

    episodic = EpisodicMemory()
    try:
        await episodic.log_task_event(
            task_id=ctx.job_id, event="started",
            project_id=ctx.project_id, task_name=task_name,
            details=f"Task description: {description}",
        )
    except Exception:
        logger.debug("episodic log_task_event failed", exc_info=True)

    # Run the agent loop. Wrap the whole call in pinger_for so the
    # watchdog stays happy even if the LLM round-trip blocks for minutes.
    plan = (pl.get("plan") or "").strip()
    plan_block = ""
    if plan:
        plan_block = (
            "\n\nEXECUTION PLAN (reviewed and approved by the user — "
            "follow it step by step; do not skip steps; if a step "
            "requires a tool you don't have, surface that explicitly "
            "rather than improvising):\n"
            f"{plan}\n"
        )

    # Task-ledger protocol — a compact, durable progress artifact that
    # (a) re-anchors instructions each cycle and (b) makes truncated or
    # killed runs resumable by a follow-up task with fresh context.
    # Ledgers live under the dedicated task-ledger/ folder and carry a
    # 'task-ledger' tag (also enforced harness-side in save_to_artifact)
    # so they group cleanly in the Artifacts UI and are tag-searchable.
    _ledger_slug = _re.sub(
        r"[^a-zA-Z0-9]+", "-", (task_name or "task").lower()).strip("-")[:60]
    ledger_rel = f"task-ledger/{_ledger_slug}.md"
    _legacy_ledger_rel = f"tasks/{_ledger_slug}-ledger.md"
    ledger_exists = False
    try:
        from artifacts.store import get_store, project_slug
        _store = get_store()
        _proj = project_slug(ctx.project_id)
        if _store.get_by_path(ctx.project_id, f"{_proj}/{ledger_rel}"):
            ledger_exists = True
        elif _store.get_by_path(ctx.project_id, f"{_proj}/{_legacy_ledger_rel}"):
            # In-flight ledger created before the task-ledger/ folder move —
            # keep resuming from the legacy path rather than forking.
            ledger_rel = _legacy_ledger_rel
            ledger_exists = True
    except Exception:
        logger.debug("ledger existence check failed", exc_info=True)

    from datetime import datetime, timezone
    _today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    if ledger_exists:
        ledger_block = (
            "\n\nTASK LEDGER PROTOCOL:\n"
            f"- A ledger from a previous run EXISTS at artifact path "
            f"'{ledger_rel}'. read_artifact it FIRST and RESUME from its "
            "'Next action:' line — do NOT redo subtasks marked done.\n"
            "- Update the ledger (save_to_artifact, same path) after EACH "
            "completed subtask, BEFORE starting the next.\n"
            "- Keep it accurate enough that a fresh agent could resume from "
            "it alone: one line per subtask "
            "(`- [pending|in-progress|done|skipped: reason] subtask`), key "
            "decisions, and a final 'Next action:' line.\n"
            "- Keep its YAML frontmatter current: set `status: complete` "
            "and 'Next action: none — task complete' when (and only when) "
            "everything is done.\n"
        )
    else:
        ledger_block = (
            "\n\nTASK LEDGER PROTOCOL (for multi-part work):\n"
            "- If this task decomposes into more than ~3 similar units of "
            f"work, FIRST create a ledger artifact at '{ledger_rel}' via "
            "save_to_artifact with tags=[\"task-ledger\"], beginning with "
            "EXACTLY this YAML frontmatter:\n"
            "  ---\n"
            "  type: task-ledger\n"
            f"  task: {task_name}\n"
            f"  job_id: {ctx.job_id}\n"
            "  status: active\n"
            f"  created: {_today}\n"
            "  tags: [task-ledger]\n"
            "  ---\n"
            "- Body: one line per subtask "
            "(`- [pending|in-progress|done|skipped: reason] subtask`), key "
            "decisions, and a final 'Next action:' line.\n"
            "- Update it (same path) after EACH completed subtask, BEFORE "
            "starting the next — not in batches at the end.\n"
            "- The ledger is the restart point if this run hits its "
            "iteration or time budget: keep it accurate enough that a "
            "fresh agent could resume from it alone.\n"
            "- When the task is COMPLETE: set frontmatter `status: complete` "
            "and 'Next action: none — task complete'. This is part of the "
            "task, not optional bookkeeping.\n"
            "- Single-step tasks don't need a ledger.\n"
        )

    full_prompt = (
        f"You are running an autonomous task: '{task_name}'\n"
        f"Job ID: {ctx.job_id}\n"
        f"Complete the task fully and save important results to memory."
        f"{plan_block}{ledger_block}\n\n"
        f"Task:\n{description}"
    )

    # Log which tools are available to this run so we can debug cases
    # where the agent didn't reach for an MCP tool the user expected.
    try:
        from agent.tools import get_all_tool_schemas
        tool_names = sorted([
            (((sp or {}).get("function") or {}).get("name"))
            for sp in get_all_tool_schemas(project_id=ctx.project_id) or []
            if (sp or {}).get("function")
        ])
        mcp_tools = [t for t in tool_names if t and t.startswith("mcp_")]
        non_mcp = [t for t in tool_names if t and not t.startswith("mcp_")]
        logger.info(
            "autonomous_task %s: %d tools available "
            "(%d MCP: %s; %d built-in/skill)",
            ctx.job_id[:8], len(tool_names), len(mcp_tools),
            ", ".join(mcp_tools[:8]) + ("…" if len(mcp_tools) > 8 else ""),
            len(non_mcp),
        )
        ctx.update_result({"available_tool_count": len(tool_names),
                           "mcp_tool_count": len(mcp_tools),
                           "mcp_tools_sample": mcp_tools[:20]})
        if not mcp_tools:
            logger.warning(
                "autonomous_task %s: NO MCP tools registered. If you "
                "expected an MCP server to be reachable, verify it is "
                "connected in Settings → Connections → MCP servers.",
                ctx.job_id[:8],
            )
    except Exception:
        logger.debug("tool inventory log failed", exc_info=True)

    # Persist the prompt as a user message so this session appears in
    # the chat history drawer (and so resume-from-session can replay
    # the prior context if the user clicks into it later).
    try:
        await memory.episodic.save_message(
            session_id=session_id, project_id=ctx.project_id,
            role="user", content=full_prompt,
            metadata={"job_id": ctx.job_id, "kind": "autonomous_task_prompt"},
        )
    except Exception:
        logger.debug("could not save user prompt to episodic", exc_info=True)

    # Replace agent.run_autonomous() with our own event-loop pump so we
    # can update the heartbeat progress with each tool call. That way the
    # Tasks UI shows the actual step the agent is on instead of a static
    # "Running agent loop…".
    plan_steps = _extract_plan_steps(plan)
    await ctx.heartbeat(
        progress=("Step 1/%d…" % len(plan_steps)) if plan_steps else "Running agent loop…"
    )

    full_response = ""
    tool_count = 0
    tool_usage: dict[str, int] = {}
    last_tool_name = None
    iterations = None
    truncated = False
    got_done = False
    last_error = None
    text_buf = ""
    text_deltas_since_tool = 0
    repo_before = await _repo_snapshot(ctx.project_id)
    # Per-task iteration budget (payload override; None → global default)
    try:
        max_iterations = int((ctx.payload or {}).get("max_iterations") or 0) or None
    except (TypeError, ValueError):
        max_iterations = None
    # Re-anchor text: the plan (or task description) plus the ledger rule —
    # re-injected every ~15 iterations by AgentCore so long runs don't lose
    # instruction fidelity under accumulated tool output.
    reanchor = (
        f"Task: '{task_name}'\n"
        + (f"Plan:\n{plan}\n" if plan else f"Description:\n{(description or '')[:800]}\n")
        + f"Ledger: keep '{ledger_rel}' updated after each completed subtask."
    )
    async with pinger_for(ctx, interval=30.0):
        async for event in agent.chat(full_prompt, stream=False,
                                      max_iterations=max_iterations,
                                      reanchor_text=reanchor):
            etype = event.get("type")
            if etype == "tool_call":
                if event.get("name") == "context_loaded":
                    continue  # synthetic pre-recall event, not a real tool
                tool_count += 1
                last_tool_name = event.get("name") or "?"
                tool_usage[last_tool_name] = tool_usage.get(last_tool_name, 0) + 1
                text_deltas_since_tool = 0
                step_idx = _match_tool_to_step(last_tool_name, plan_steps)
                progress = _format_progress(tool_count, last_tool_name, step_idx, plan_steps)
                await ctx.heartbeat(progress=progress)
            elif etype == "tool_result":
                name = event.get("name") or last_tool_name or "?"
                result = (event.get("result") or "")
                snippet = result.replace("\n", " ")[:80] if isinstance(result, str) else ""
                await ctx.heartbeat(
                    progress=f"✓ {name} → {snippet}" if snippet else f"✓ {name}"
                )
            elif etype in ("text_delta", "text"):
                # Capture model text. If no tools have fired yet, surface
                # the recent text so the UI shows the agent is alive
                # rather than appearing stuck on "Running agent loop…".
                chunk = event.get("content") or event.get("text") or ""
                if chunk:
                    text_buf += chunk
                    text_deltas_since_tool += 1
                    if tool_count == 0 and text_deltas_since_tool % 8 == 0:
                        # heartbeat once every 8 deltas with the tail of text
                        await ctx.heartbeat(
                            progress=f"thinking… {text_buf[-200:].strip()[-180:]}"
                        )
            elif etype == "done":
                got_done = True
                full_response = event.get("full_response", "") or ""
                iterations = event.get("iterations")
                truncated = bool(event.get("truncated"))
            elif etype == "error":
                last_error = event.get("message") or "unknown agent error"
                logger.warning(
                    "autonomous_task %s: agent error event: %s",
                    ctx.job_id[:8], event.get("message"),
                )

    result_text = full_response
    repo_after = await _repo_snapshot(ctx.project_id)
    repo_delta = _repo_delta_line(repo_before, repo_after)
    ctx.update_result({
        "tool_calls_observed": tool_count,
        "tool_usage": dict(sorted(tool_usage.items(),
                                  key=lambda kv: -kv[1])[:15]),
        "iterations_used": iterations,
        "truncated": truncated,
        "repo_before": repo_before,
        "repo_after": repo_after,
        "repo_delta": repo_delta,
    })
    if truncated:
        logger.warning(
            "autonomous_task %s: TRUNCATED at iteration cap (%s iterations) "
            "— the task did not finish; its last text is mid-work narration.",
            ctx.job_id[:8], iterations,
        )

    # Diagnostic: if the loop closed with no tool calls AND no final text,
    # log a warning so post-mortem is easier.
    if tool_count == 0 and not (result_text or "").strip():
        logger.warning(
            "autonomous_task %s: agent emitted no tool calls and no text. "
            "Likely causes: model refused to call tools, MAX_TOOL_ITERATIONS "
            "hit before any progress, or LLM provider returned empty.",
            ctx.job_id[:8],
        )
    elif tool_count == 0:
        logger.info(
            "autonomous_task %s: completed with NO tool calls. "
            "Final text length=%d. Plan may have been ignored.",
            ctx.job_id[:8], len(result_text or "")
        )

    # If the agent loop returned empty (e.g. hit MAX_TOOL_ITERATIONS or
    # ended on a tool-call turn), recover the last assistant message
    # from the saved conversation so the result row isn't blank.
    if not (result_text or "").strip():
        logger.warning(
            "autonomous_task %s: run_autonomous returned empty text. "
            "Falling back to the last assistant message in the session. "
            "Likely cause: max-iteration cap hit, or final tool result "
            "had no closing assistant turn.",
            ctx.job_id[:8],
        )
        try:
            history = await episodic.get_history(session_id=session_id, limit=200)
            assistant_msgs = [
                m for m in history if (m.get("role") == "assistant" and (m.get("content") or "").strip())
            ]
            if assistant_msgs:
                result_text = assistant_msgs[-1]["content"]
                ctx.update_result({"summary_recovered_from_history": True})
            else:
                # Last resort — surface tool-call activity so the user
                # sees what the agent actually did
                tool_msgs = [m for m in history if m.get("role") in ("tool", "assistant")]
                if tool_msgs:
                    snippet = "\n".join(
                        f"[{m.get('role')}] {(m.get('content') or '')[:200]}"
                        for m in tool_msgs[-8:]
                    )
                    result_text = (
                        f"(Agent loop produced no final text. "
                        f"Last 8 turns of activity:)\n\n{snippet}"
                    )
                    ctx.update_result({"summary_recovered_from_history": True,
                                       "summary_recovery_mode": "tool_trace"})
        except Exception as e:
            logger.debug("history fallback failed: %s", e)

    if ctx.cancel_requested():
        return {"status": "cancelled", "session_id": session_id,
                "summary": (result_text or "")[:200]}

    # The agent loop ended on a provider error (model crash, endpoint down)
    # without a closing 'done' turn. That is an ABORT, not a completion —
    # post an honest notice with the resume pointer and fail the job so the
    # Tasks UI doesn't show green. Incremental update_result data survives.
    if last_error and not got_done:
        ctx.update_result({"ended_on_error": last_error})
        _parent = (ctx.payload or {}).get("parent_session_id")
        if _parent and _parent != session_id:
            _ledger_note = ""
            try:
                from artifacts.store import get_store as _gs, project_slug as _psl
                if _gs().get_by_path(ctx.project_id,
                                     f"{_psl(ctx.project_id)}/{ledger_rel}"):
                    _ledger_note = (
                        f"\n\nThe task ledger at `{ledger_rel}` is current — "
                        "re-run a task with the same name to resume from its "
                        "'Next action'."
                    )
            except Exception:
                pass
            try:
                await memory.episodic.save_message(
                    session_id=_parent, project_id=ctx.project_id,
                    role="assistant",
                    content=(
                        f"⚠️ **Task ABORTED:** *{task_name}* "
                        f"(job_id `{ctx.job_id[:8]}`)\n\n"
                        f"The agent loop died on an LLM provider error, not "
                        f"by finishing:\n\n> {last_error}\n\n"
                        f"Work completed before the crash is intact."
                        + (f"\n\n_{repo_delta}_" if repo_delta else "")
                        + f"{_ledger_note}"
                    ),
                    metadata={"job_id": ctx.job_id,
                              "kind": "autonomous_task_abort_notice",
                              "task_session_id": session_id,
                              "task_name": task_name},
                )
            except Exception:
                logger.debug("abort notice post failed", exc_info=True)
        raise RuntimeError(f"LLM provider error ended the run: {last_error}")

    # Persist the final assistant message so the session is replayable
    # from chat history. Even when the recovery branch synthesized this
    # text, it's still useful to have something stored under the role.
    try:
        await memory.episodic.save_message(
            session_id=session_id, project_id=ctx.project_id,
            role="assistant",
            content=(result_text or "(no final assistant message produced)"),
            metadata={"job_id": ctx.job_id, "kind": "autonomous_task_response"},
        )
    except Exception:
        logger.debug("could not save assistant message to episodic", exc_info=True)

    # Set a useful conversation title so the chat history drawer shows
    # something more recognizable than 'Chat <id>'.
    try:
        import sqlite3
        ep_path = memory.episodic.db_path
        conn = sqlite3.connect(ep_path)
        conn.execute(
            "UPDATE conversations SET title = ? WHERE session_id = ? AND (title IS NULL OR title = '')",
            (task_name[:80], session_id),
        )
        conn.commit(); conn.close()
    except Exception:
        logger.debug("could not set conversation title", exc_info=True)

    try:
        await episodic.log_task_event(
            task_id=ctx.job_id, event="completed",
            project_id=ctx.project_id, task_name=task_name,
            details=f"Result: {(result_text or '')[:500]}",
        )
    except Exception:
        logger.debug("episodic log_task_event(completed) failed", exc_info=True)

    # Pipe a completion notice into the chat session that scheduled
    # this task (if any) so the user sees the result materialize where
    # they asked for it instead of in an orphan history-drawer chat.
    parent_session_id = (ctx.payload or {}).get("parent_session_id")
    if parent_session_id and parent_session_id != session_id:
        try:
            tc = int(tool_count or 0)
            it = int(iterations or 0)
            body = (result_text or "").strip()
            silent_failure = (tc == 0 and not body)
            if silent_failure:
                summary_for_parent = (
                    "⚠️ **NO OUTPUT — likely silent failure.** "
                    "The agent ran but emitted no tool calls and no text. "
                    "Possible causes: model decided the plan was satisfied "
                    "without doing anything, model returned empty, or the "
                    "plan was too vague. Check the Tasks tab → job "
                    "transcript for full diagnostics."
                )
            else:
                if len(body) > 1500:
                    body = body[:1500] + "…"
                summary_for_parent = body or "(agent made tool calls but emitted no final text — see Tasks tab for transcript)"
            if truncated:
                # Re-check ledger existence — the run may have created it.
                _ledger_note = ""
                try:
                    from artifacts.store import get_store as _gs, project_slug as _psl
                    if _gs().get_by_path(ctx.project_id,
                                         f"{_psl(ctx.project_id)}/{ledger_rel}"):
                        _ledger_note = (
                            f" A task ledger exists at artifact "
                            f"`{ledger_rel}` — a follow-up task will resume "
                            f"from it automatically (same task name)."
                        )
                except Exception:
                    pass
                summary_for_parent = (
                    f"⚠️ **TASK TRUNCATED — iteration cap reached at {it} "
                    "iterations.** The agent was cut off mid-work; the text "
                    "below is its last narration, NOT a completion report. "
                    "Queue a follow-up task to finish the remaining work "
                    "(you can raise the budget with `max_iterations` on "
                    f"create_task).{_ledger_note}\n\n" + summary_for_parent
                )
            # Harness-verified metrics — computed by the handler from the
            # event stream and git, never from the model's own narration.
            _top_tools = sorted(tool_usage.items(), key=lambda kv: -kv[1])[:5]
            _tools_part = (", ".join(f"{n}: {c}" for n, c in _top_tools)
                           if _top_tools else "")
            metrics_line = (
                f"_Harness-verified — tools: {tc} call{'s' if tc != 1 else ''}"
                + (f" ({_tools_part})" if _tools_part else "")
                + f" · iterations: {it}_"
            )
            if repo_delta:
                metrics_line += f"\n_{repo_delta}_"
            parent_msg = (
                f"**Task completed:** *{task_name}* "
                f"(job_id `{ctx.job_id[:8]}`)\n\n"
                f"{metrics_line}\n\n"
                f"{summary_for_parent}\n\n"
                f"_Run details: open the Tasks tab; full transcript "
                f"in chat history under the task name._"
            )
            await memory.episodic.save_message(
                session_id=parent_session_id, project_id=ctx.project_id,
                role="assistant", content=parent_msg,
                metadata={
                    "job_id": ctx.job_id,
                    "kind": "autonomous_task_completion_notice",
                    "task_session_id": session_id,
                    "task_name": task_name,
                },
            )
            logger.info(
                "Posted task-completion notice to parent session %s for job %s",
                parent_session_id, ctx.job_id,
            )
        except Exception:
            logger.debug(
                "Failed to post completion notice to parent session %s",
                parent_session_id, exc_info=True,
            )

    summary = (result_text or "")[:1000]
    if truncated:
        summary = (f"[TRUNCATED at {int(iterations or 0)} iterations — "
                   f"work incomplete] " + summary)
    return {
        "session_id": session_id,
        "task_name": task_name,
        "truncated": truncated,
        "repo_delta": repo_delta,
        "summary": summary,
    }
