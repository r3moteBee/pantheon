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
    if project_id and project_name:
        project_section = f"\n\n## Active Project\nYou are currently working in project: **{project_name}** (id: {project_id})\nAll memories, files, and context are scoped to this project."
    elif project_id:
        project_section = f"\n\n## Active Project\nProject ID: {project_id}"

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

Current time: {now}"""
