"""research_batch handler - research a list of items one at a time, writing each finding down.

Asked for a 35-state Senate breakdown in one chat turn, the agent hit its lookup budget, then
answered from memory and saved 8 artifacts of invented data (2026-10-02). A 9B model does well
on ONE small question with a fresh context, so this job splits the survey:

  for each item:  a fresh agent turn with only web_search / web_fetch and a small lookup budget
                  answers the question for that item; the HANDLER saves the reply as
                  <project>/research/<topic>/<item>.md (facts from results with source URLs, "not found"
                  for the rest). The notes are the job's notebook: they survive restarts, and a
                  re-run skips items that already have one.
  then:           a summary turn with NO tools writes research/<topic>/summary.md from the notes
                  only - never from memory.

Payload shape:
    {
      "task_name": str,
      "topic": str,                 # names the folder: research/<topic-slug>/
      "items": [str, ...],          # e.g. the 50 states; at most MAX_ITEMS
      "item_question": str,         # template with {item}, e.g. "Who is running for US Senate in {item} in 2026?"
      "lookups_per_item": int,      # web_search + web_fetch calls per item, default 6 (1-15)
      "summary_instruction": str,   # optional: what the summary should look like
      "parent_session_id": str,     # chat to post the completion notice into
    }
"""
from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from jobs.context import AGENT_MAX_QUIET_SECONDS, JobContext, pinger_for
from jobs.handlers import register

logger = logging.getLogger(__name__)

MAX_ITEMS = 200
NOTE_CHARS_IN_SUMMARY = 1500
ITEM_PROMPT = (
    "You are researching ONE item of a larger survey.\n"
    "Survey: {topic}\nItem: {item}\nQuestion: {question}\n\n"
    "Use web_search and web_fetch (at most {budget} lookups) to answer the question for this item only. "
    "Search for the subject, not for names you expect. Then reply with a short markdown note:\n"
    "- only facts your results state about {item}, each followed by its source URL in parentheses\n"
    "- \"not found\" for any part of the question you could not confirm\n"
    "Do not use your own knowledge for anything that changes over time, and do not add background."
)
SUMMARY_PROMPT = (
    "Below are research notes, one per item, for the survey: {topic}.\n"
    "Write the summary from these notes ONLY. Keep anything marked \"not found\" as not found - do not fill it in. "
    "Mention which items had no findings. {instruction}\n\n{notes}"
)


def _slug(s: str, n: int = 60) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "-", (s or "").strip().lower()).strip("-")[:n] or "item"


@register("research_batch", default_timeout_seconds=14400,
          description="Research a list of items one at a time; one sourced note per item, then a summary.")
async def handle_research_batch(ctx: JobContext) -> dict[str, Any]:
    from agent.core import AgentCore
    from artifacts.store import get_store, project_slug
    from models.provider import get_provider_for

    pl = ctx.payload or {}
    topic = (pl.get("topic") or ctx.title or "research").strip()
    items = [str(i).strip() for i in (pl.get("items") or []) if str(i).strip()][:MAX_ITEMS]
    template = (pl.get("item_question") or "").strip()
    if not items or not template:
        return {"status": "failed", "error": "research_batch needs items and item_question"}
    budget = max(1, min(int(pl.get("lookups_per_item") or 6), 15))
    # same layout as save_to_artifact: <project-slug>/research/<topic>/
    folder = f"{project_slug(ctx.project_id)}/research/{_slug(topic)}"
    store = get_store()
    provider = get_provider_for("agent")
    notes: dict[str, str] = {}
    written = skipped = empty = 0

    for n, item in enumerate(items, 1):
        if ctx.cancel_requested():
            logger.info("research_batch %s: cancelled after %d item(s)", ctx.job_id[:8], n - 1)
            break
        path = f"{folder}/{_slug(item)}.md"
        existing = store.get_by_path(ctx.project_id, path)
        if existing and (existing.get("content") or "").strip():
            notes[item] = existing["content"]
            skipped += 1
            continue
        await ctx.heartbeat(progress=f"Item {n}/{len(items)}: {item}")
        question = template.replace("{item}", item)
        agent = AgentCore(
            provider=provider, memory_manager=None, project_id=ctx.project_id,
            session_id=f"research-{ctx.job_id[:8]}-{uuid.uuid4().hex[:6]}",
            only_tools={"web_search", "web_fetch"}, web_budget=budget,
        )
        try:
            async with pinger_for(ctx, 30, max_quiet=AGENT_MAX_QUIET_SECONDS):
                reply = await agent.run_autonomous(
                    ITEM_PROMPT.format(topic=topic, item=item, question=question, budget=budget))
        except Exception as e:
            logger.warning("research_batch %s: item %r failed: %s", ctx.job_id[:8], item, e)
            reply = f"not found (the lookup failed: {e})"
        reply = (reply or "").strip() or "not found"
        if reply.lower().startswith("not found"):
            empty += 1
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        content = (f"# {item}\n\n_Question: {question}_  \n_Researched {stamp} by job `{ctx.job_id[:8]}`._\n\n{reply}\n")
        store.create(project_id=ctx.project_id, path=path, content_type="text/markdown", content=content,
                     title=f"{topic}: {item}", tags=["research", _slug(topic, 40)],
                     source={"kind": "research_batch", "job_id": ctx.job_id})
        notes[item] = content
        written += 1
        ctx.update_result({"notes_written": written, "last_item": item})

    summary_id = None
    if notes:
        await ctx.heartbeat(progress="Writing the summary from the notes")
        joined = "\n\n".join(f"## {item} (note: {folder}/{_slug(item)}.md)\n{text[:NOTE_CHARS_IN_SUMMARY]}"
                             for item, text in notes.items())
        summarizer = AgentCore(provider=provider, memory_manager=None, project_id=ctx.project_id,
                               session_id=f"research-{ctx.job_id[:8]}-summary", only_tools=set())
        async with pinger_for(ctx, 30, max_quiet=AGENT_MAX_QUIET_SECONDS):
            summary = await summarizer.run_autonomous(SUMMARY_PROMPT.format(
                topic=topic, instruction=(pl.get("summary_instruction") or "").strip(), notes=joined))
        art = store.create(project_id=ctx.project_id, path=f"{folder}/summary.md", content_type="text/markdown",
                           content=f"# {topic} - summary\n\n{summary.strip()}\n\n_Built from {len(notes)} notes in "
                                   f"`{folder}/` by job `{ctx.job_id[:8]}`._\n",
                           title=f"{topic}: summary", tags=["research", _slug(topic, 40)],
                           source={"kind": "research_batch", "job_id": ctx.job_id})
        summary_id = art["id"]

    parent = pl.get("parent_session_id")
    if parent:
        try:
            from memory.episodic import EpisodicMemory
            await EpisodicMemory().save_message(
                session_id=parent, project_id=ctx.project_id, role="assistant",
                content=(f"**Research finished:** *{topic}* (job `{ctx.job_id[:8]}`)\n\n"
                         f"{written} new notes, {skipped} already there, {empty} with nothing found. "
                         f"Notes are in `{folder}/`; the summary is `{folder}/summary.md`."),
                metadata={"job_id": ctx.job_id, "kind": "research_batch_completion_notice"})
        except Exception:
            logger.debug("research_batch completion notice failed", exc_info=True)
    return {"status": "completed", "folder": folder, "items": len(items), "notes_written": written,
            "notes_reused": skipped, "nothing_found": empty, "summary_artifact_id": summary_id}
