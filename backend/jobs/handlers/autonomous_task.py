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




@register("autonomous_task", default_timeout_seconds=600,
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
    agent = AgentCore(
        provider=provider, memory_manager=memory,
        project_id=ctx.project_id, session_id=session_id,
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

    full_prompt = (
        f"You are running an autonomous task: '{task_name}'\n"
        f"Job ID: {ctx.job_id}\n"
        f"Complete the task fully and save important results to memory."
        f"{plan_block}\n\n"
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
    last_tool_name = None
    async with pinger_for(ctx, interval=30.0):
        async for event in agent.chat(full_prompt, stream=False):
            etype = event.get("type")
            if etype == "tool_call":
                tool_count += 1
                last_tool_name = event.get("name") or "?"
                step_idx = _match_tool_to_step(last_tool_name, plan_steps)
                progress = _format_progress(tool_count, last_tool_name, step_idx, plan_steps)
                await ctx.heartbeat(progress=progress)
            elif etype == "tool_result":
                # Surface tool completion + any error
                name = event.get("name") or last_tool_name or "?"
                result = (event.get("result") or "")
                snippet = result.replace("\n", " ")[:80] if isinstance(result, str) else ""
                await ctx.heartbeat(
                    progress=f"✓ {name} → {snippet}" if snippet else f"✓ {name}"
                )
            elif etype == "done":
                full_response = event.get("full_response", "") or ""
            elif etype == "error":
                logger.warning(
                    "autonomous_task %s: agent error event: %s",
                    ctx.job_id[:8], event.get("message"),
                )

    result_text = full_response
    ctx.update_result({"tool_calls_observed": tool_count})

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

    return {
        "session_id": session_id,
        "task_name": task_name,
        "summary": (result_text or "")[:1000],
    }
