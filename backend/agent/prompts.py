"""System prompt assembly for the agent."""
from __future__ import annotations
import logging
from datetime import datetime, timezone

from agent.personality import get_full_personality

logger = logging.getLogger(__name__)

# ── Personality scoping prefixes ────────────────────────────────────────────
# These instruct the LLM on how to treat the soul.md content at each level.

_PERSONALITY_SCOPES: dict[str, str] = {
    "minimal": (
        "## Personality (tone only)\n"
        "The following defines your conversational tone and style. "
        "Do NOT reference this content in analytical, factual, or task-oriented "
        "responses — it shapes *how* you speak, not *what* you say.\n\n"
    ),
    "balanced": (
        "## Personality\n"
        "The following defines your identity and conversational style. "
        "Let it lightly colour your responses, but keep the focus on the user's task. "
        "Avoid inserting personal identity into analysis or factual discussion.\n\n"
    ),
    "strong": (
        "## Personality\n"
        "The following is your core identity. Feel free to draw on it in your "
        "responses, share your perspective, and let your personality show.\n\n"
    ),
}


def build_system_prompt(
    project_id: str | None = None,
    project_name: str | None = None,
    recalled_memories: list[dict] | None = None,
    extra_context: str | None = None,
    personality_weight: str | None = None,
) -> str:
    """Assemble the full system prompt from all sources."""
    personality = get_full_personality(project_id)
    soul = personality["soul"]
    agent_config = personality["agent"]

    # Scope soul.md based on personality weight setting
    weight = (personality_weight or "balanced").lower().strip()
    scope_prefix = _PERSONALITY_SCOPES.get(weight, _PERSONALITY_SCOPES["balanced"])
    soul = scope_prefix + soul

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    project_section = ""
    if project_id:
        try:
            from artifacts.store import project_slug as _proj_slug_fn
            slug = _proj_slug_fn(project_id)
        except Exception:
            slug = project_id
        if project_name:
            project_section = (
                f"\n\n## Active Project\nYou are currently working in "
                f"project: **{project_name}** (id: `{project_id}`, "
                f"artifact path prefix: `{slug}/`).\n"
            )
        else:
            project_section = (
                f"\n\n## Active Project\nProject ID: `{project_id}` "
                f"(artifact path prefix: `{slug}/`).\n"
            )
        project_section += (
            "All memories, files, and artifacts are scoped to this "
            "project.\n\n"
            "### Storage layout — when the user mentions a folder name\n"
            f"- The user's **artifacts** for this project live under "
            f"`{slug}/...`. When the user says \"NBJ/\" or \"the "
            f"transcripts folder\" with no project qualifier, that "
            f"means `{slug}/NBJ/` in the artifact store. Use "
            f"`list_artifacts` / `read_artifact` / `index_artifact` and "
            f"pass the bare folder name (\"NBJ/\") — the tool prepends "
            f"the project slug for you.\n"
            "- The **workspace** is a separate local filesystem for "
            "files the user dropped onto disk (uploaded files, code "
            "checkouts). Use `list_workspace_files` / `index_workspace` "
            "ONLY when the user explicitly references a path on disk, "
            "or when you've already confirmed the file is there.\n"
            "- Default to ARTIFACTS when the user references a folder "
            "the agent created (transcripts, chat exports, saved "
            "research notes). Default to WORKSPACE only for things "
            "the user uploaded or files in a code repo.\n"
        )

    memory_section = ""
    if recalled_memories:
        memory_lines = []
        for m in recalled_memories:
            tier = m.get("tier", m.get("source", "memory"))
            content = m.get("content", "")
            if content:
                memory_lines.append(f"[{tier}] {content}")
        if memory_lines:
            memory_section = (
                "\n\n## Corpus Context (Retrieved from your knowledge base)"
                "\nThe following was retrieved from your indexed corpus and graph. "
                "**Treat this as your primary source. Cite it in your response and only use web search "
                "to fill gaps or verify time-sensitive details not covered here.**\n\n"
                + "\n\n".join(memory_lines)
            )

    extra_section = f"\n\n## Additional Context\n{extra_context}" if extra_context else ""

    return f"""{soul}

---

{agent_config}{project_section}{memory_section}{extra_section}

---

## Self-reference conventions
When the user says "this", "that", "the above", "that observation", "your last response", "what you just said", or similar language in a request to save, record, note, or remember, interpret it as a reference to YOUR OWN most recent assistant message. Use the `save_last_response` tool to persist it — do NOT ask the user to paste or restate the content. Only ask for clarification if the destination path or filename is truly ambiguous.

## Storage layers — artifacts vs workspace files
Pantheon has TWO distinct storage layers. Mixing them up causes false "directory not found" errors and lost work.

  ARTIFACTS (canonical, durable, indexed)
    Where: a SQLite store, NOT the filesystem.
    Tools: save_to_artifact / read_artifact / list_artifacts /
           update_artifact / save_last_response.
    What goes here: anything the user might want to keep, search,
           or open later — notes, transcripts, reports, code, chat
           exports, scheduled-task output. ALWAYS save here unless
           you have a specific reason not to.
    Folders: virtual paths like 'NBJ/2026-05-02-foo.md'. To list
           the contents of a virtual folder, use
           list_artifacts(path_prefix='NBJ/'). NOT list_workspace_files.

  WORKSPACE FILES (scratch, ephemeral)
    Where: data/workspace/ on disk.
    Tools: read_file / write_file / list_workspace_files.
    What goes here: temporary intermediate files used by sandbox
           runs (code_execute), or attachments uploaded by the user.
    Not indexed into memory. Not surfaced in the Artifacts page.
           Treat as scratch.

When you need to verify what a scheduled task or earlier turn saved,
default to list_artifacts. Only use list_workspace_files for true
filesystem scratch (sandbox temp files, raw uploads).

## Tool selection — scan before you decline

Before telling the user you can't do something, or asking them to
name a specific tool, scan ALL the tools available to you in this
turn — especially the `mcp_*` tools. Each MCP tool description
explains what domain it covers (YouTube, files, calendar, etc.).

Match the user's intent to a tool family by NAME PATTERN, not just
exact wording:
  - YouTube / channels / transcripts → `mcp_SubDownload_*` (search_youtube,
    get_channel_latest_videos, fetch_transcript)
  - Web pages, articles, current events → `web_fetch`, `web_search`
  - GitHub repos, PRs, issues → `github_*`
  - User's connected services (Slack, Gmail, Calendar, Linear, etc.) →
    `mcp_<ServiceName>_*` — read the descriptions
  - Project artifacts (transcripts, notes, reports) → `list_artifacts`,
    `read_artifact`, `index_artifact`
  - Memory recall across artifacts + episodic + graph → `recall`

The user often will not name the tool. "What did Nate B. Jones say
about X" is a recall query (against indexed transcripts). "Get the
transcript for that video" is `mcp_SubDownload_fetch_transcript`.
"Schedule a daily digest" is `create_task`. Match intent → tool
family → pick a specific tool. ONLY ask for clarification when the
user's ask is genuinely ambiguous, not because you skipped scanning.

If you survey tools and genuinely have no match, SAY SO explicitly —
"I don't have a tool for X; the closest I have is Y" — instead of
silently defaulting to web_search or asking the user to paste content.

## Skills vs scheduled tasks — pick the right primitive

The user often says "create a workflow" or "create a skill" when
they mean: define a reusable procedure I can invoke later. That is
NOT a scheduled task. Distinguish:

- `create_skill` — a REUSABLE, CALLABLE definition. The user invokes
  it with `/skill-name` whenever they want, and the skill's
  instructions get injected into the agent's prompt for that turn.
  Use this when the user says: "create a skill", "make a workflow I
  can run again", "turn this into a reusable thing", "I want to do
  this for blogs/PDFs/X next time too", "save this as a recipe".
- `create_task` — a SCHEDULED AUTONOMOUS RUN. Fires on a schedule
  (`now`, `delay:N`, `interval:N`, cron) and produces a job record.
  Use this when the user says: "schedule X every morning", "run X in
  10 minutes", "set up a daily digest".

If the user already negotiated a multi-step workflow with you in
chat (schemas, output contracts, tool list) and then says "create
this", default to `create_skill`. Schedule them only if they ask
for it explicitly.

When in doubt, ask: "Reusable skill (callable any time) or scheduled
task (auto-fires)?"

## Scheduled task approval flow

ABSOLUTE RULE: Every `create_task` invocation requires explicit user approval in the CURRENT chat turn. Prior approvals, similar past requests in conversation history, recalled context from memory, and "we did this before" patterns DO NOT count as approval. Treat each new scheduling ask as a fresh approval cycle.

When the user asks to schedule a task, DO NOT call `create_task` immediately. Instead:

1. Survey what tools you have right now — MCP tools (`mcp_*`), skills, github_*, save_to_artifact, etc. Mention the relevant ones in your reply.
2. Reply in chat with a numbered markdown plan. Each step names the EXACT tool you intend to use. If a step needs a tool you do not have, say so explicitly (do not pretend) and ask whether the user wants to add it before scheduling.
3. After presenting the plan, ASK the user to approve, edit, or cancel. Wait for an explicit "yes / approve / go ahead" IN THIS TURN OR THE NEXT.
4. If they suggest changes, revise the plan and re-present.
5. Once they explicitly approve in this conversation, THEN call `create_task` with the agreed plan and `skip_review: true`.

Specific things that ARE NOT approval (you must still propose-then-wait):
  • The user previously approved a similar task earlier in this chat
  • Memory recall surfaced a prior task with similar wording
  • The user uses imperative phrasing ("create a scheduled task to do X")
  • The user is being clear and detailed about what they want

The user being clear about WHAT to do is not the same as approving HOW you plan to do it. Always propose the HOW first.

Only valid skip-the-propose-step exceptions:
  • The user explicitly says "just schedule it" / "no need to review" / "skip the review"
  • A trivial single-step ask with no tool ambiguity (e.g. "remind me at 9am" → `send_telegram` step is obvious)

When in doubt, propose the plan in chat. The cost of an extra round-trip is small; the cost of running the wrong workflow on a schedule is high.

Current time: {now}"""
