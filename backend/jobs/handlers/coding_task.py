"""coding_task handler — long-form coding work in the background.

Payload shape:
    {
      "task_description": str,    # what to build / fix / change
      "coding_context": str,      # tech stack / file layout / conventions
      "branch_name": str,         # optional explicit branch; else auto
      "base_branch": str,         # optional override; else repo default
    }

Resolves the project's bound repo via api/connections.get_project_repo_for_tools.
Builds an agent loop with:
  * code_execute (sandbox)
  * github_read_file / github_list_directory
  * github_create_branch / github_write_files / github_create_pr
A coding-specific system prompt that includes the supplied
coding_context and the repo's owner/repo/branch.

On completion: opens a PR (if files were changed), saves a one-page
summary as a chat-export artifact, writes a memory note pointing at
the PR, and returns:
    {session_id, branch, files_changed, pr_url, artifact_id, summary}
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from jobs.context import JobContext, pinger_for
from jobs.handlers import register

logger = logging.getLogger(__name__)


def _slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return s[:60] or "task"


@register("coding_task", default_timeout_seconds=1800,
          description="Background coding agent — branch, edit, commit, open PR.")
async def handle_coding_task(ctx: JobContext) -> dict[str, Any]:
    pl = ctx.payload or {}
    task = pl.get("task_description") or ctx.description or ""
    if not task:
        return {"status": "skipped", "reason": "no task_description"}

    coding_context = pl.get("coding_context") or ""
    desired_branch = pl.get("branch_name") or f"agent/{_slug(ctx.title or task)[:40]}-{ctx.job_id[:6]}"
    base_branch_override = pl.get("base_branch")

    # Resolve repo binding for the project
    await ctx.heartbeat(progress="Resolving repo binding…")
    from api.connections import get_project_repo_for_tools, get_token, mark_used, mark_error
    spec = get_project_repo_for_tools(ctx.project_id)
    if not spec:
        return {"status": "failed",
                "error": f"No repo bound to project {ctx.project_id}. "
                         "Bind one in chat → Repository tab before starting a coding_task."}
    token = get_token(spec["connection_id"])
    if not token:
        return {"status": "failed",
                "error": "Bound connection has no stored token."}
    owner, repo = spec["owner"], spec["repo"]
    base_branch = base_branch_override or spec["default_branch"] or "main"

    # Build agent + memory
    session_id = f"coding-{ctx.job_id[:8]}-{uuid.uuid4().hex[:6]}"
    ctx.update_result({"session_id": session_id})
    await ctx.heartbeat(progress="Loading memory + agent…")

    from agent.core import AgentCore
    from memory.manager import create_memory_manager
    from models.provider import get_provider
    provider = get_provider()
    memory = create_memory_manager(
        project_id=ctx.project_id, session_id=session_id, provider=provider,
    )
    agent = AgentCore(
        provider=provider, memory_manager=memory,
        project_id=ctx.project_id, session_id=session_id,
    )

    # Coding-specific system prompt — opinionated and concrete
    sys_extra = (
        f"You are a background coding agent working on repository {owner}/{repo}.\n"
        f"Base branch: {base_branch}\n"
        f"Working branch: {desired_branch} (will be created if it does not exist)\n\n"
        "Available tools include: code_execute (run Python/Node), "
        "github_read_file, github_list_directory, github_create_branch, "
        "github_write_files (atomic multi-file commit), github_create_pr.\n\n"
        "PROCESS:\n"
        "  1. Read enough of the repo to understand the change. Use "
        "     github_list_directory + github_read_file. Don't guess at "
        "     code you haven't read.\n"
        "  2. Run github_create_branch to make the working branch from "
        f"     {base_branch}. Skip if the branch already exists.\n"
        "  3. Make changes via github_write_files (one atomic commit per "
        "     logical change). Use code_execute to validate Python/JS "
        "     syntax before committing.\n"
        "  4. When done, call github_create_pr to open a PR from "
        f"     {desired_branch} into {base_branch}. Title should be "
        "     concise, body should summarize the change and any tests.\n"
        "  5. Reply with a short summary of what you did and the PR URL.\n"
    )
    if coding_context.strip():
        sys_extra += f"\nPROJECT CONTEXT:\n{coding_context}\n"

    full_prompt = (
        f"{sys_extra}\n\n"
        f"TASK:\n{task}\n"
    )

    await ctx.heartbeat(progress="Running coding agent loop…")
    async with pinger_for(ctx, interval=30.0):
        result_text = await agent.run_autonomous(full_prompt) or ""

    if ctx.cancel_requested():
        return {"status": "cancelled", "session_id": session_id,
                "summary": (result_text or "")[:400]}

    # Try to extract a PR URL the agent reported
    pr_url = None
    m = re.search(r"https?://github\.com/[^\s)\]]+/pull/\d+", result_text or "")
    if m:
        pr_url = m.group(0)
    mark_used(spec["connection_id"])

    # Save a summary artifact
    await ctx.heartbeat(progress="Saving summary artifact…")
    artifact_id = None
    try:
        from artifacts.store import get_store, project_slug
        from artifacts import embedder
        proj = project_slug(ctx.project_id)
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        body = (
            f"# Coding task: {ctx.title or _slug(task)}\n\n"
            f"_Job `{ctx.job_id[:8]}` on {today}_\n\n"
            f"**Repo:** `{owner}/{repo}` · **Base:** `{base_branch}` · **Branch:** `{desired_branch}`\n\n"
            f"## Task\n\n{task}\n\n"
            + (f"## PR\n\n{pr_url}\n\n" if pr_url else "")
            + f"## Agent summary\n\n{result_text or '(no summary returned)'}\n"
        )
        a = get_store().create(
            project_id=ctx.project_id,
            path=f"{proj}/coding/{today}-{_slug(ctx.title or task)}.md",
            content=body,
            content_type="text/markdown",
            title=f"Coding: {ctx.title or _slug(task)}",
            tags=["coding-task"] + (["pr-opened"] if pr_url else []),
            source={"kind": "coding_task", "job_id": ctx.job_id,
                    "owner": owner, "repo": repo, "branch": desired_branch,
                    "pr_url": pr_url},
            edited_by=f"job:{ctx.job_id[:8]}",
        )
        artifact_id = a["id"]
        embedder.schedule_embed(artifact_id, ctx.project_id, immediate=True)
    except Exception as e:
        logger.exception("coding_task: artifact save failed: %s", e)

    return {
        "status": "ok",
        "session_id": session_id,
        "owner": owner, "repo": repo,
        "branch": desired_branch,
        "base_branch": base_branch,
        "pr_url": pr_url,
        "artifact_id": artifact_id,
        "summary": (result_text or "")[:1500],
    }
