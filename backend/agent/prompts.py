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


# ── Recalled memory ──────────────────────────────────────────────────────────
# Recall is automatic and relevance-ranked, so what comes back is a mix: the
# user's knowledge base (things they asked us to remember, ingested sources,
# file chunks, summaries), things the user said in earlier chats, and our own
# earlier replies. They deserve different trust. Framing everything as "your
# primary source, only search to fill gaps" made the agent repeat its own stale
# answers instead of searching (measured: 7/14 "latest version of X" questions
# answered from memory with outdated versions).
MEMORY_LABELS = {
    "semantic": "note",
    "graph": "graph",
    "archival": "archive",
}
MEMORY_GUIDANCE = (
    "A user message may start with a <context> block that Pantheon adds, not the user: the current time and "
    "entries retrieved automatically from this project's memory because they may be relevant to that message.\n"
    "- [note], [graph] and [archive] entries come from the user's knowledge base (things they asked you to "
    "remember, ingested sources, files, summaries). For questions about that material, treat them as "
    "authoritative and cite them.\n"
    "- [user said] entries are what the user told you before: trust them for their own preferences, plans "
    "and circumstances.\n"
    "- [your earlier reply] entries are your own past answers. They may be wrong or out of date: never "
    "repeat one as fact without checking it.\n"
    "- Memory does not replace tools: for anything time-sensitive (latest versions, prices, news, current "
    "status) or anything the user asks you to search or look up, use your tools even when a memory "
    "seems to answer it.\n"
    "- Ignore entries that are not relevant to the current message."
)


def _memory_label(m: dict) -> tuple[str, str]:
    """(label, content) for one recalled item; episodic items carry their role as a "[role] " prefix."""
    tier = m.get("tier", m.get("source", "memory"))
    content = m.get("content", "") or ""
    if tier == "episodic":
        if content.startswith("[user] "):
            return "user said", content[len("[user] "):]
        if content.startswith("[assistant] "):
            return "your earlier reply", content[len("[assistant] "):]
    return MEMORY_LABELS.get(tier, tier), content


def render_turn_context(recalled_memories: list[dict] | None, now: str | None = None,
                        omitted_messages: int = 0) -> str:
    """The <context> block AgentCore puts in front of the new user message:
    current time + this turn's recalled memory, labelled by provenance.

    Everything that changes per turn lives here, after the conversation
    history, so the tools + system prompt + history stay a stable prefix and
    the model server reuses its KV cache for them (measured: the first call
    of a turn recomputed ~3.3K tokens when memories sat mid-system-prompt).
    How to treat the block is explained once, in the system prompt
    (MEMORY_GUIDANCE)."""
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = []
    for m in recalled_memories or []:
        label, content = _memory_label(m)
        if content.strip():
            lines.append(f"[{label}] {content}")
    body = f"Current time: {now}"
    if omitted_messages > 0:
        # Without this a model asked "did I mention X earlier?" denies it
        # when X was in a turn the history budget dropped.
        body += (f"\nEarlier in this conversation: {omitted_messages} older messages are not shown here; "
                 "relevant parts appear under Recalled memory when found.")
    if lines:
        body += "\nRecalled memory:\n" + "\n\n".join(lines)
    return f"<context>\n{body}\n</context>\n\n"


def build_system_prompt(
    project_id: str | None = None,
    project_name: str | None = None,
    extra_context: str | None = None,
    personality_weight: str | None = None,
    host_exec: bool = True,
) -> str:
    """Assemble the full system prompt from all sources.

    ``host_exec`` must match AgentCore.host_exec: without it the git_* and
    run_command tools are hidden, so the local repo protocol isn't shown."""
    personality = get_full_personality(project_id)
    soul = personality["soul"]
    agent_config = personality["agent"]

    # Scope soul.md based on personality weight setting
    weight = (personality_weight or "balanced").lower().strip()
    scope_prefix = _PERSONALITY_SCOPES.get(weight, _PERSONALITY_SCOPES["balanced"])
    soul = scope_prefix + soul

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
            f"`list_artifacts` / `read_artifact` / `index(target='artifact')` and "
            f"pass the bare folder name (\"NBJ/\") — the tool prepends "
            f"the project slug for you.\n"
            "- The **workspace** is a separate local filesystem for "
            "files the user dropped onto disk (uploaded files, code "
            "checkouts). Use `list_workspace_files` / `index(target='workspace')` "
            "ONLY when the user explicitly references a path on disk, "
            "or when you've already confirmed the file is there.\n"
            "- Default to ARTIFACTS when the user references a folder "
            "the agent created (transcripts, chat exports, saved "
            "research notes). Default to WORKSPACE only for things "
            "the user uploaded or files in a code repo.\n"
        )

    # Repo work protocol — only when the project actually has a bound repo,
    # so ordinary chat sessions don't pay the prompt tokens. Exists because
    # an agent once "merged" branches by rewriting files from memory and
    # wrote commit messages claiming merges that never ran.
    repo_section = ""
    if project_id:
        try:
            from api.connections import get_project_repo_for_tools
            _repo_spec = get_project_repo_for_tools(project_id)
        except Exception:
            _repo_spec = None
        if _repo_spec and not host_exec:
            repo_section = (
                f"\n\n## Repository\n"
                f"This project is bound to GitHub repo "
                f"`{_repo_spec['owner']}/{_repo_spec['repo']}` (default branch "
                f"`{_repo_spec['default_branch']}`). Local git and shell tools "
                "are not available in this context — use the `github` tool to "
                "read or open PRs, or create_task(job_type=\"coding_task\") for "
                "changes that need a checkout, builds or tests.\n"
            )
        elif _repo_spec:
            repo_section = (
                f"\n\n## Repository work protocol\n"
                f"This project is bound to GitHub repo "
                f"`{_repo_spec['owner']}/{_repo_spec['repo']}` (default branch "
                f"`{_repo_spec['default_branch']}`).\n"
                "- Start local repo work with `git_sync_repo` — it clones/"
                "updates the checkout and fetches all remote branches.\n"
                "- Merge branches ONLY with `git_merge`. Never simulate a "
                "merge by copying file contents between branches or "
                "rewriting files from memory.\n"
                "- On merge conflicts, edit only the conflicted regions "
                "(between the <<<<<<< and >>>>>>> markers) in the files "
                "git_merge lists, leave everything else untouched, then "
                "`git_commit` to conclude the merge.\n"
                "- VALIDATE before every commit: run the repo's compiler/"
                "linter/tests via `run_command` (e.g. `npx tsc --noEmit`, "
                "`pytest`, `npm test`) and confirm it passes.\n"
                "- NEVER pass `--no-verify` to git commit. The checkout's "
                "pre-commit hook (installed by git_sync_repo) rejects "
                "unresolved conflict markers — if it fires, fix the "
                "conflict, don't bypass the hook.\n"
                "- Report only what tool outputs confirm. git_commit and "
                "git_merge name the branch they acted on — if it doesn't "
                "match your intent, stop and correct course instead of "
                "narrating success.\n"
            )

    # Static: the per-turn memories and time go in the user message
    # (render_turn_context), so this prompt is identical from turn to turn.
    memory_section = "\n\n## Recalled memory\n" + MEMORY_GUIDANCE

    extra_section = f"\n\n## Additional Context\n{extra_context}" if extra_context else ""

    return f"""{soul}

---

{agent_config}{project_section}{repo_section}{memory_section}{extra_section}

---

## Self-reference conventions
When the user says "this", "that", "the above", "that observation", "your last response", "what you just said", or similar language in a request to save, record, note, or remember, interpret it as a reference to YOUR OWN most recent assistant message. Use the `save_last_response` tool to persist it — do NOT ask the user to paste or restate the content. Only ask for clarification if the destination path or filename is truly ambiguous.

## Persistence — artifacts vs workspace vs MCP "save" tools

- ARTIFACTS are the durable store: save_to_artifact / update_artifact /
  read_artifact / list_artifacts / save_last_response. Anything the user may
  want to keep — notes, reports, transcripts, exports, task output — goes
  here. Paths are virtual folders ('NBJ/2026-05-02-foo.md'); list one with
  list_artifacts(path_prefix='NBJ/'), not list_workspace_files.
- WORKSPACE files are scratch on disk (the project's workspace folder):
  read_file / write_file / list_workspace_files. For sandbox temp files and
  raw uploads only; not indexed, not shown on the Artifacts page.
- MCP "save" / "library" / "upload" tools (mcp_*_save_*, …) write to that
  external service, which Pantheon cannot see. When the user says "save",
  use save_to_artifact unless they named the other service; never report
  an MCP save as a Pantheon save, and don't substitute one for a skill's
  save_to_artifact step.

## Tool selection — scan before you decline

Before telling the user you can't do something, or asking them to
name a specific tool, scan ALL the tools available to you in this
turn — especially the `mcp_*` tools. Each MCP tool description
explains what domain it covers (YouTube, files, calendar, etc.).

Match the user's intent to a tool family by NAME PATTERN, not just
exact wording:
  - YouTube / channels / transcripts → the YouTube MCP tools
    (`mcp_*_search_youtube`, `mcp_*_fetch_transcript`, …)
  - Read a web page now → `web_fetch`; find pages → `web_search`;
    keep a source (indexed + graph) → `ingest_source`
  - GitHub repos, PRs, issues → `github` (action=…)
  - User's connected services (Slack, Gmail, Calendar, Linear, etc.) →
    `mcp_<ServiceName>_*` — read the descriptions
  - Project artifacts (transcripts, notes, reports) → `list_artifacts`,
    `read_artifact`, `index`
  - Memory recall across artifacts + episodic + graph → `recall`

The user often will not name the tool. "What did Nate B. Jones say
about X" is a recall query (against indexed transcripts). "Get the
transcript for that video" is the YouTube MCP's fetch_transcript tool.
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

  `create_task` accepts a `job_type` arg:
    - `autonomous_task` (default) — single agent run that executes the
      plan once and stops.
    - `iteration_loop` — multi-turn execute/review loop. Each turn the
      agent does work, then a separate review phase critiques it and
      proposes the next step. Per-turn state lives in artifacts at
      `iteration/<job_id>/turn-N.md`. Use when the user asks for a
      "loop", "iterate N times", a "generator/reviewer loop", or any
      self-perpetuating multi-turn development. Pair with `max_turns`
      (default 10), `execute_instruction`, and `review_instruction`.
      The loop stops at max_turns, when the reviewer emits
      `STATUS: done`, or after two consecutive stalled turns.

If the user already negotiated a multi-step workflow with you in
chat (schemas, output contracts, tool list) and then says "create
this", default to `create_skill`. Schedule them only if they ask
for it explicitly.

When in doubt, ask: "Reusable skill (callable any time) or scheduled
task (auto-fires)?"

## Scheduled task approval flow

ABSOLUTE RULE: Every `create_task` invocation requires explicit user approval in the CURRENT chat turn. Prior approvals, similar past requests in conversation history, recalled context from memory, and "we did this before" patterns DO NOT count as approval. Treat each new scheduling ask as a fresh approval cycle.

When the user asks to schedule a task, DO NOT call `create_task` immediately. Instead:

1. Survey what tools you have right now — MCP tools (`mcp_*`), skills, `github`, save_to_artifact, etc. Mention the relevant ones in your reply.
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

When in doubt, propose the plan in chat. The cost of an extra round-trip is small; the cost of running the wrong workflow on a schedule is high."""
