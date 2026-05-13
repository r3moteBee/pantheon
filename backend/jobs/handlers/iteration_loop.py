"""iteration_loop handler — multi-turn execute/review loop.

Each turn runs two AgentCore.run_autonomous-style passes:
  1. EXECUTE phase — agent does concrete work (code, tool calls, artifacts).
  2. REVIEW phase — agent critiques the execute phase, surfaces gaps,
     and proposes the next step. Emits a STATUS line consumed by the loop.

Per-turn state is persisted to an artifact at
    iteration/<job_id>/turn-N.md
with frontmatter recording tool counts, last tool, files touched, and the
reviewer's STATUS verdict. The next turn reads the prior turn's artifact
back through the agent's normal memory recall (or via the prompt body).

Stop conditions:
  - max_turns reached
  - reviewer emits "STATUS: done"
  - two consecutive stalled turns (a stalled turn is one where the execute
    phase emits zero tool calls AND zero text, even after one retry with
    an explicit "you stopped early" nudge)
  - cancel_requested

Payload shape:
    {
      "task_name": str,
      "description": str,            # default execute_instruction if not set
      "topic": str,                  # optional; defaults to task_name
      "max_turns": int,              # default 10, clamped to [1, 50]
      "execute_instruction": str,    # per-turn execute prompt
      "review_instruction": str,     # per-turn review prompt
      "parent_session_id": str,      # for completion notice
      "plan": str,                   # optional high-level outline
    }
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from jobs.context import JobContext, pinger_for
from jobs.handlers import register

logger = logging.getLogger(__name__)


DEFAULT_REVIEW_INSTRUCTION = (
    "Review the previous execute phase. Be concrete:\n"
    "  1. What was actually accomplished (refer to specific files / artifacts).\n"
    "  2. Bugs, gaps, or steps that were skipped.\n"
    "  3. The single most important next step for the following turn.\n\n"
    "DO NOT keep iterating just because the loop has more turns budgeted. "
    "If the project has reached a coherent milestone (it builds, it runs "
    "end-to-end, the topic's stated goal is achieved, or further turns "
    "would just churn the same files), emit STATUS: done. Stopping early "
    "is fine and often better than burning turns on busywork.\n\n"
    "End your reply with exactly one of these two lines on its own:\n"
    "  STATUS: continue   (more turns needed)\n"
    "  STATUS: done       (project work for this loop is complete)"
)


def _branch_policy_block(branch_strategy: str, target_branch: str | None, turn: int) -> str:
    """Render the per-turn branch-policy instructions for the execute prompt."""
    if branch_strategy == "main":
        return (
            "\n\nBRANCH POLICY (strategy=main):\n"
            "  - Commit ALL changes directly to `main` via "
            "`github_write_files` with branch=\"main\".\n"
            "  - Do NOT create feature branches. Do NOT call "
            "`github_create_branch`.\n"
            "  - The next turn will see your code on main automatically."
        )
    if branch_strategy == "single_feature" and target_branch:
        block = (
            f"\n\nBRANCH POLICY (strategy=single_feature):\n"
            f"  - This entire loop runs on ONE branch: `{target_branch}`.\n"
            f"  - When WRITING files, pass branch=\"{target_branch}\" to "
            f"`github_write_files`. NEVER write to main and NEVER create "
            f"any other branch.\n"
            f"  - When READING files (`github_read_file`, "
            f"`github_list_directory`), pass ref=\"{target_branch}\" so "
            f"you see the cumulative state from prior turns. Reading "
            f"without ref shows main, which is stale relative to your work.\n"
        )
        if turn == 1:
            block += (
                f"  - This is turn 1. The branch may not exist yet — your "
                f"FIRST tool call should be "
                f"`github_create_branch(new_branch=\"{target_branch}\")` "
                f"to fork it from main. Then proceed with the work.\n"
            )
        return block
    # branch_per_turn: agent decides per turn (legacy behaviour)
    return (
        "\n\nBRANCH POLICY (strategy=branch_per_turn, LEGACY):\n"
        "  - Each turn creates its own feature branch. This produces "
        "branch sprawl unless you really want PR-per-turn. Consider "
        "using single_feature or main for new loops."
    )


def _extract_status(review_text: str) -> str:
    """Find STATUS: done | continue in the review output. Defaults to continue."""
    if not review_text:
        return "continue"
    for line in reversed(review_text.splitlines()):
        s = line.strip().lower()
        if s.startswith("status:"):
            verdict = s.split(":", 1)[1].strip()
            if verdict.startswith("done"):
                return "done"
            return "continue"
    return "continue"


def _prior_turn_summary(turn_results: list[dict]) -> str:
    """Render a compact context block of prior turns for the next prompt."""
    if not turn_results:
        return "(this is turn 1 — no prior turns yet)"
    lines = []
    for t in turn_results:
        tn = t["turn"]
        lines.append(
            f"Turn {tn}: {t['tool_count']} tool call{'s' if t['tool_count'] != 1 else ''}"
            + (f" (last: {t['last_tool']})" if t.get("last_tool") else "")
            + f" — reviewer STATUS: {t['status']}"
        )
        nxt = (t.get("next_step") or "").strip()
        if nxt:
            lines.append(f"  next-step (proposed by turn {tn} reviewer): {nxt[:200]}")
    return "\n".join(lines)


async def _run_phase(
    agent, prompt: str, ctx: JobContext, phase_label: str,
) -> dict[str, Any]:
    """Run one agent phase. Returns dict {tool_count, last_tool, text}."""
    tool_count = 0
    last_tool: str | None = None
    text_buf: list[str] = []

    async with pinger_for(ctx, interval=30.0):
        async for event in agent.chat(prompt, stream=False):
            etype = event.get("type")
            if etype == "tool_call":
                name = event.get("name") or "?"
                # Filter Pantheon's synthetic pre-recall event (emitted as a
                # tool_call from agent/core.py) so it doesn't mask real tools
                # in the metrics. It's memory loading, not agent action.
                if name == "context_loaded":
                    continue
                tool_count += 1
                last_tool = name
                await ctx.heartbeat(progress=f"{phase_label}: tool #{tool_count} {last_tool}")
            elif etype == "tool_result":
                name = event.get("name") or last_tool or "?"
                snippet = (event.get("result") or "")
                if isinstance(snippet, str):
                    snippet = snippet.replace("\n", " ")[:80]
                else:
                    snippet = ""
                await ctx.heartbeat(progress=f"{phase_label} ✓ {name} → {snippet}" if snippet else f"{phase_label} ✓ {name}")
            elif etype in ("text_delta", "text"):
                chunk = event.get("content") or event.get("text") or ""
                if chunk:
                    text_buf.append(chunk)
            elif etype == "done":
                full = event.get("full_response", "") or ""
                if full:
                    text_buf = [full]
            elif etype == "error":
                logger.warning("iteration_loop %s: %s phase error: %s",
                               ctx.job_id[:8], phase_label, event.get("message"))

    return {
        "tool_count": tool_count,
        "last_tool": last_tool,
        "text": "".join(text_buf).strip(),
    }


@register("iteration_loop", default_timeout_seconds=7200,
          description="Multi-turn execute/review loop with per-turn artifact state.")
async def handle_iteration_loop(ctx: JobContext) -> dict[str, Any]:
    pl = ctx.payload or {}
    task_name = pl.get("task_name") or ctx.title or "Iteration loop"
    description = pl.get("description") or ctx.description or ""
    topic = (pl.get("topic") or task_name).strip()
    max_turns = max(1, min(int(pl.get("max_turns", 10) or 10), 50))
    execute_instruction = (pl.get("execute_instruction") or description or
                           f"Continue developing '{topic}'. Use the available tools to make concrete progress.").strip()
    review_instruction = (pl.get("review_instruction") or DEFAULT_REVIEW_INSTRUCTION).strip()
    parent_session_id = pl.get("parent_session_id")
    plan = (pl.get("plan") or "").strip()
    branch_strategy = (pl.get("branch_strategy") or "single_feature").strip()
    if branch_strategy not in ("single_feature", "main", "branch_per_turn"):
        branch_strategy = "single_feature"
    if branch_strategy == "single_feature":
        target_branch: str | None = f"iteration/{ctx.job_id[:8]}"
    elif branch_strategy == "main":
        target_branch = "main"
    else:
        target_branch = None

    await ctx.heartbeat(progress=f"Booting iteration loop ({max_turns} turns max, strategy={branch_strategy})…")
    ctx.update_result({
        "task_name": task_name, "topic": topic,
        "max_turns": max_turns,
        "branch_strategy": branch_strategy,
        "target_branch": target_branch,
    })

    from agent.core import AgentCore
    from memory.manager import create_memory_manager
    from memory.episodic import EpisodicMemory
    from models.provider import get_provider
    from artifacts.store import get_store as get_artifact_store

    provider = get_provider()
    artifact_store = get_artifact_store()

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

    turn_results: list[dict[str, Any]] = []
    consecutive_stalls = 0
    stop_reason: str | None = None

    plan_block = ""
    if plan:
        plan_block = (
            "\n\nHIGH-LEVEL PLAN (informs every turn — do not redo work already "
            f"completed):\n{plan}\n"
        )

    for turn in range(1, max_turns + 1):
        if ctx.cancel_requested():
            stop_reason = "cancelled by user"
            break

        prior = _prior_turn_summary(turn_results)
        await ctx.heartbeat(progress=f"Turn {turn}/{max_turns}: execute phase")

        # ── EXECUTE PHASE ───────────────────────────────────────────────
        execute_session = f"iteration-{ctx.job_id[:8]}-t{turn}-exec-{uuid.uuid4().hex[:4]}"
        execute_memory = create_memory_manager(
            project_id=ctx.project_id, session_id=execute_session, provider=provider,
        )
        execute_agent = AgentCore(
            provider=provider, memory_manager=execute_memory,
            project_id=ctx.project_id, session_id=execute_session,
            project_name=project_name,
        )

        branch_policy = _branch_policy_block(branch_strategy, target_branch, turn)
        execute_prompt = (
            f"You are running turn {turn} of {max_turns} of an iterative "
            f"development loop on '{topic}'.\n\n"
            f"Prior turns:\n{prior}\n"
            f"{plan_block}"
            f"{branch_policy}\n\n"
            f"This turn's execute instruction:\n{execute_instruction}\n\n"
            f"Make at least one concrete tool call — do not stop after only "
            f"reading state. Write code, save artifacts, commit files, query "
            f"data; produce a tangible deliverable for this turn that the "
            f"reviewer can critique. End with a one-paragraph summary of what "
            f"you did and what you tried to accomplish."
        )
        try:
            ep = EpisodicMemory()
            await ep.save_message(
                session_id=execute_session, project_id=ctx.project_id,
                role="user", content=execute_prompt,
                metadata={"job_id": ctx.job_id, "kind": "iteration_execute_prompt",
                          "turn": turn},
            )
        except Exception:
            logger.debug("could not save execute prompt", exc_info=True)

        execute_result = await _run_phase(execute_agent, execute_prompt, ctx,
                                          phase_label=f"T{turn} exec")

        # Stall recovery
        if execute_result["tool_count"] == 0 and not execute_result["text"]:
            await ctx.heartbeat(progress=f"T{turn} exec stalled — retrying with nudge")
            nudge_extra = (
                "\n\n⚠️  IMPORTANT: your previous attempt produced no tool "
                "calls and no text. The loop CANNOT make progress without "
                "concrete tool use. Make at least one tool call now — pick "
                "the smallest plausible step (e.g. github_list_directory "
            )
            if target_branch and target_branch != "main":
                nudge_extra += f"with ref=\"{target_branch}\" "
            nudge_extra += (
                "to survey the repo, or save_to_artifact to record state) "
                "and execute it. Then summarise."
            )
            nudge_prompt = execute_prompt + nudge_extra
            retry_session = execute_session + "-retry"
            retry_memory = create_memory_manager(
                project_id=ctx.project_id, session_id=retry_session, provider=provider,
            )
            retry_agent = AgentCore(
                provider=provider, memory_manager=retry_memory,
                project_id=ctx.project_id, session_id=retry_session,
                project_name=project_name,
            )
            execute_result = await _run_phase(retry_agent, nudge_prompt, ctx,
                                              phase_label=f"T{turn} exec-retry")
            execute_result["retry_after_stall"] = True

        if execute_result["tool_count"] == 0 and not execute_result["text"]:
            consecutive_stalls += 1
            logger.warning("iteration_loop %s: turn %d stalled (consecutive=%d)",
                           ctx.job_id[:8], turn, consecutive_stalls)
            if consecutive_stalls >= 2:
                stop_reason = f"stalled at turn {turn} (2 consecutive empty turns)"
                # Still record the failed turn so the user sees it
                turn_results.append({
                    "turn": turn, "tool_count": 0, "last_tool": None,
                    "execute_text": "", "review_text": "",
                    "status": "stalled", "next_step": None,
                    "stalled": True,
                })
                break
        else:
            consecutive_stalls = 0

        # ── REVIEW PHASE ────────────────────────────────────────────────
        await ctx.heartbeat(progress=f"Turn {turn}/{max_turns}: review phase")
        review_session = f"iteration-{ctx.job_id[:8]}-t{turn}-rev-{uuid.uuid4().hex[:4]}"
        review_memory = create_memory_manager(
            project_id=ctx.project_id, session_id=review_session, provider=provider,
        )
        review_agent = AgentCore(
            provider=provider, memory_manager=review_memory,
            project_id=ctx.project_id, session_id=review_session,
            project_name=project_name,
        )
        review_prompt = (
            f"You are the REVIEWER for turn {turn} of {max_turns} in an "
            f"iterative development loop on '{topic}'.\n\n"
            f"The execute phase just ran. Here is what it produced:\n"
            f"  - tool calls: {execute_result['tool_count']}"
            + (f" (last: {execute_result['last_tool']})" if execute_result.get('last_tool') else "")
            + f"\n  - final summary text:\n{execute_result['text'][:2000] or '(empty)'}\n\n"
            f"Prior turn context:\n{prior}\n\n"
            f"{review_instruction}"
        )
        review_result = await _run_phase(review_agent, review_prompt, ctx,
                                         phase_label=f"T{turn} review")

        status = _extract_status(review_result["text"])
        next_step = ""
        # Lazy heuristic: lift the "next step" line if numbered
        for line in (review_result["text"] or "").splitlines():
            sl = line.strip()
            if sl.lower().startswith(("next step", "3.", "next:")):
                next_step = sl
                break

        # ── PERSIST TURN ARTIFACT ───────────────────────────────────────
        turn_md = (
            f"---\n"
            f"turn: {turn}\n"
            f"job_id: {ctx.job_id}\n"
            f"topic: {topic}\n"
            f"tools_called: {execute_result['tool_count']}\n"
            f"last_tool: {execute_result.get('last_tool') or ''}\n"
            f"status: {status}\n"
            f"retry_after_stall: {bool(execute_result.get('retry_after_stall'))}\n"
            f"---\n\n"
            f"# Turn {turn} — {topic}\n\n"
            f"## Execute phase\n\n"
            f"_Tool calls: {execute_result['tool_count']}"
            + (f", last: `{execute_result['last_tool']}`" if execute_result.get('last_tool') else "")
            + "_\n\n"
            f"{execute_result['text'] or '_(no text emitted)_'}\n\n"
            f"## Review phase\n\n"
            f"{review_result['text'] or '_(no review emitted)_'}\n"
        )
        artifact_path = f"iteration/{ctx.job_id[:8]}/turn-{turn:02d}.md"
        try:
            artifact_store.create(
                project_id=ctx.project_id,
                path=artifact_path,
                content=turn_md,
                content_type="text/markdown",
                title=f"Iteration {topic} — turn {turn}",
                tags=["iteration", topic.lower().replace(" ", "-")[:40]],
                source={"job_id": ctx.job_id, "kind": "iteration_turn",
                        "turn": turn},
            )
        except Exception:
            logger.exception("iteration_loop %s: failed to save turn-%d artifact",
                             ctx.job_id[:8], turn)

        turn_results.append({
            "turn": turn,
            "tool_count": execute_result["tool_count"],
            "last_tool": execute_result.get("last_tool"),
            "execute_text": execute_result["text"][:1000],
            "review_text": review_result["text"][:1000],
            "status": status,
            "next_step": next_step,
            "artifact_path": artifact_path,
        })
        ctx.update_result({"turns_completed": len(turn_results),
                           "last_turn_status": status})

        if status == "done":
            stop_reason = f"reviewer marked done at turn {turn}"
            break
    else:
        # for-else: ran to max_turns without break
        stop_reason = f"reached max_turns ({max_turns})"

    # ── POST COMPLETION TO PARENT SESSION ───────────────────────────────
    if parent_session_id:
        try:
            lines = [
                f"**Iteration loop completed:** *{task_name}* "
                f"(job_id `{ctx.job_id[:8]}`)",
                "",
                f"_Stop reason:_ {stop_reason}",
                f"_Turns:_ {len(turn_results)} / {max_turns}",
                "",
                "| Turn | Tools | Last tool | Status |",
                "|------|-------|-----------|--------|",
            ]
            for t in turn_results:
                lines.append(
                    f"| {t['turn']} | {t['tool_count']} | "
                    f"`{t.get('last_tool') or '—'}` | {t['status']} |"
                )
            lines.append("")
            if turn_results:
                final = turn_results[-1]
                lines.append("**Last review:**")
                lines.append("")
                lines.append((final["review_text"] or "_(no review)_")[:1500])
            else:
                lines.append("⚠️ No turns completed — check the Tasks tab for details.")
            lines.append("")
            lines.append(f"_Per-turn artifacts under_ `iteration/{ctx.job_id[:8]}/`")
            try:
                ep = EpisodicMemory()
                await ep.save_message(
                    session_id=parent_session_id, project_id=ctx.project_id,
                    role="assistant", content="\n".join(lines),
                    metadata={"job_id": ctx.job_id,
                              "kind": "iteration_loop_completion_notice"},
                )
            except Exception:
                logger.debug("episodic save (completion notice) failed", exc_info=True)
        except Exception:
            logger.exception("iteration_loop %s: could not post completion notice",
                             ctx.job_id[:8])

    return {
        "status": "completed" if stop_reason and "stalled" not in stop_reason else "failed",
        "stop_reason": stop_reason,
        "turns_completed": len(turn_results),
        "turns": turn_results,
    }
