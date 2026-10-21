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
    "- [note] and [graph] entries come from the user's knowledge base (things they asked you to "
    "remember, ingested sources, files, summaries). For questions about that material, treat them as "
    "authoritative and cite them.\n"
    "- [user said] entries are what the user told you before: trust them for their own preferences, plans "
    "and circumstances.\n"
    "- [earlier in this chat, ...] entries are turns of THIS conversation, repeated here because they "
    "look relevant (older ones may no longer be shown above); treat them as part of the conversation.\n"
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
    if tier == "episodic" and (m.get("metadata") or {}).get("earlier_in_session"):
        if content.startswith("[user] "):
            return "earlier in this chat, user said", content[len("[user] "):]
        if content.startswith("[assistant] "):
            return "earlier in this chat, you said", content[len("[assistant] "):]
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
    channel: str | None = None,
) -> str:
    """Assemble the full system prompt from all sources.

    ``host_exec`` must match AgentCore.host_exec: without it the git_* and
    run_command tools are hidden, so the local repo protocol isn't shown.

    ``channel`` is where the reply is shown ("web", "telegram", ...; None for
    background jobs). It selects the user's reply style (utils/user_profile)."""
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
            f"`{slug}/...`. When the user says \"research/\" or \"the "
            f"transcripts folder\" with no project qualifier, that "
            f"means `{slug}/research/` in the artifact store. Use "
            f"`list_artifacts` / `read_artifact` / `index(target='artifact')` and "
            f"pass the bare folder name (\"research/\") — the tool prepends "
            f"the project slug for you. Files the user uploads in the chat are "
            f"artifacts too, under `chat-attachments/<date>/`.\n"
            "- The **workspace** is a separate scratch folder on disk "
            "(sandbox files, downloads, code checkouts). Use "
            "`list_workspace_files` / `index(target='workspace')` "
            "ONLY when the user references a path on disk or a repo "
            "checkout, or you've confirmed the file is there.\n"
        )

    # Repo work protocol — only when the project actually has a bound repo,
    # so ordinary chat sessions don't pay the prompt tokens. Exists because
    # an agent once "merged" branches by rewriting files from memory and
    # wrote commit messages claiming merges that never ran.
    repo_section = ""
    _repo_spec = None
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
                "read the repo. Changes that need a checkout, builds or tests "
                "are a coding task (create_task job_type=\"coding_task\"), "
                "which only the web chat can start.\n"
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

    # Settings → About you. Both parts change only when the user edits them,
    # so the prompt stays a stable prefix. The reply style goes last: it is
    # the instruction most often lost in a long prompt.
    profile_section = reply_style_section = ""
    try:
        from utils import user_profile
        profile = user_profile.get_profile()
        about = user_profile.render_profile_section(profile)
        profile_section = f"\n\n{about}" if about else ""
        style = user_profile.render_reply_style(channel, profile)
        reply_style_section = f"\n\n{style}" if style else ""
    except Exception:
        logger.warning("About you settings unavailable - prompt built without them", exc_info=True)

    # Only promise what this instance can do: run_build needs a builder,
    # pushing needs a bound repo (both were always mentioned before).
    try:
        from builder import builder_enabled
        _can_build = builder_enabled()
    except Exception:
        _can_build = False
    deliverable_tail = "The user can then download that folder as a zip" + (
        ", you can build and test it with run_build" if _can_build else "") + (
        " and push it to the bound GitHub repo" if _repo_spec else "") + "."

    return f"""{soul}

---

{agent_config}{profile_section}{project_section}{repo_section}{memory_section}{extra_section}

---

## Self-reference conventions
When the user says "this", "that", "the above", "that observation", "your last response", "what you just said", or similar language in a request to save, record, note, or remember, interpret it as a reference to YOUR OWN most recent assistant message. Use the `save_last_response` tool to persist it — do NOT ask the user to paste or restate the content. Only ask for clarification if the destination path or filename is truly ambiguous.

## Persistence — artifacts vs workspace vs MCP "save" tools

- ARTIFACTS are the durable store: save_to_artifact / update_artifact /
  read_artifact / list_artifacts / save_last_response. Anything the user may
  want to keep — notes, reports, transcripts, exports, task output — goes
  here, and so do the files they upload in the chat. Paths are virtual
  folders ('research/2026-05-02-foo.md'); list one with
  list_artifacts(path_prefix='research/'), not list_workspace_files.
- CODE AND PROJECT FILES the user asks for (an app, a package, a script, a
  website, an Xcode/Gradle/npm project, its README) are deliverables, so they
  are artifacts too: save each file with save_to_artifact under ONE folder
  named for the project ('MarbleMaze/App/ContentView.swift',
  'MarbleMaze/README.md'). {deliverable_tail} Plan them that way in
  create_task too - never with write_file.
- WORKSPACE files are scratch on disk (the project's workspace folder):
  read_file / write_file / list_workspace_files. For sandbox temp files,
  downloads and repo checkouts; not shown on the Artifacts page, indexed only
  with index(target='workspace').
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

The user often will not name the tool. "What did the speaker say
about X" is a recall query (against indexed transcripts). "Get the
transcript for that video" is the YouTube MCP's fetch_transcript tool.
"Schedule a daily digest" is `create_task`. Match intent → tool
family → pick a specific tool. ONLY ask for clarification when the
user's ask is genuinely ambiguous, not because you skipped scanning.

If you survey tools and genuinely have no match, SAY SO explicitly —
"I don't have a tool for X; the closest I have is Y" — instead of
silently defaulting to web_search or asking the user to paste content.
Asked to DO something no tool of yours can do (send an email or text,
call someone, post to a service you have no connector for), open with
that: "I can't send email from here", then offer what you can (a draft).

## Skills vs scheduled tasks — pick the right primitive

"Create a workflow / skill / something I can run again" usually means a
reusable procedure, NOT a schedule:

- `create_skill` — a REUSABLE, CALLABLE recipe the user runs with
  `/skill-name` whenever they want ("make a workflow I can run again",
  "turn this into a reusable thing", "do this for PDFs next time too").
  If the user negotiated a multi-step workflow in chat and then says
  "create this", default to `create_skill`.
- `create_task` — a SCHEDULED AUTONOMOUS RUN (`now`, `delay:N`,
  `interval:N`, cron): "schedule X every morning", "run X in 10 minutes".
  Its `job_type` picks one run of the plan (default), an
  `iteration_loop` (execute/review turns: "loop", "iterate N times"), a
  `research_batch` (surveys of many items) or a `coding_task`.

When in doubt, ask: "Reusable skill (callable any time) or scheduled
task (auto-fires)?"

## Scheduled task approval flow

`create_task` makes a PAUSED PROPOSAL the user approves in the chat's Tasks
tab; `skip_review: true` schedules it at once. When the user asks to
schedule or automate something, don't call it yet: reply with a numbered
plan naming the EXACT tool for each step (say so if a step needs a tool you
don't have) and the schedule, and ask them to approve, edit or cancel. Only
after an explicit "yes / approve / go ahead" for THIS plan in this
conversation, call `create_task` with `skip_review: true`. Clear or
imperative wording, an earlier approval of a similar task, or a remembered
past task are not approval.

Exceptions: "just schedule it" / "no need to review" → `skip_review: true`
right away; a big survey (`research_batch`) or a background task the user
asked you to start now may be created at once WITHOUT skip_review (it waits
as a proposal — tell them). Outside the web chat every task is a proposal.{reply_style_section}"""
