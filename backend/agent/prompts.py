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
            "Memories, files and artifacts are scoped to this project. A folder the user names (\"NBJ/\", "
            "\"the transcripts folder\") is an artifact folder: pass the bare name to `list_artifacts` / "
            "`read_artifact` / `index(target='artifact')` and the slug is added for you. Use the workspace "
            "(`list_workspace_files`, `index(target='workspace')`) only for files the user uploaded or a code "
            "checkout.\n"
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

## Saving
- "Save this / that / your last answer" means your own previous reply: use `save_last_response`. Never ask the
  user to paste it again.
- Artifacts are the durable store (save_to_artifact, update_artifact, read_artifact, list_artifacts): notes,
  reports, transcripts, task output. Paths are virtual folders ('notes/foo.md'); list one with
  list_artifacts(path_prefix='notes/').
- Workspace files (read_file, write_file, list_workspace_files) are scratch on disk for uploads and code
  checkouts; they are not indexed.
- MCP "save" / "upload" tools write to that outside service, which Pantheon cannot see. "Save" means
  save_to_artifact unless the user named the other service.

## Choosing tools
Before saying you can't do something, check every tool you have, including the mcp_* tools (each description
says which service it covers). The user rarely names the tool: "what did X say about Y" is `recall`, "get that
video's transcript" is the YouTube MCP, "every morning, ..." is `create_task`. If nothing fits, say so and name
the closest tool.

## Skills and tasks
- `create_skill` makes a reusable recipe the user runs later with /name ("make this a workflow I can rerun").
  When the user worked out a procedure with you and says "create this", that is a skill.
- `create_task` runs work in the background: later ("in 15 minutes", "tomorrow at 9", "next week", "remind me
  ..."), on a schedule ("every morning"), or too big for one reply (job_type research_batch for many items).
  When part of a request is for later, do the "now" part and call create_task for the later part in the same
  turn; when the user asks for it in this chat it starts without approval. A reminder is a create_task too. Tasks you decide on yourself, and plans
  that delete, overwrite, merge or push, wait for the user's approval in the Tasks tab; say so in your reply.
- Only say something is queued, scheduled or saved after the tool returned success in this turn."""
