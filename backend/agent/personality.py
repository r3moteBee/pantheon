"""Load and manage agent personality files (soul.md, agent.md)."""
from __future__ import annotations
import logging
from pathlib import Path

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Bundled template directory (shipped with the package)
_TEMPLATE_DIR = Path(__file__).parent.parent / "data" / "personality"


def _load_template(fname: str) -> str | None:
    """Return the bundled template content, or None if not found."""
    path = _TEMPLATE_DIR / fname
    if path.exists():
        content = path.read_text(encoding="utf-8")
        if content.strip():
            return content
    return None


def load_soul() -> str:
    """Load the global soul.md personality file.

    Priority order:
    1. Live data-dir soul.md (if it exists AND has content)
    2. Bundled template soul.md
    3. Hard-coded minimal default
    """
    soul_path = settings.personality_dir / "soul.md"
    if soul_path.exists():
        content = soul_path.read_text(encoding="utf-8")
        if content.strip():
            return content
        logger.warning("soul.md exists at %s but is empty — falling back to template", soul_path)
    else:
        logger.warning("soul.md not found at %s — falling back to template", soul_path)

    template = _load_template("soul.md")
    if template:
        return template

    return "You are a helpful, curious, and honest AI assistant."


def load_agent_config() -> str:
    """Load the global agent.md configuration file.

    Priority order:
    1. Live data-dir agent.md (if it exists AND has content)
    2. Bundled template agent.md
    3. Hard-coded minimal default
    """
    agent_path = settings.personality_dir / "agent.md"
    if agent_path.exists():
        content = agent_path.read_text(encoding="utf-8")
        if content.strip():
            return content
        logger.warning("agent.md exists at %s but is empty — falling back to template", agent_path)
    else:
        logger.warning("agent.md not found at %s — falling back to template", agent_path)

    template = _load_template("agent.md")
    if template:
        return template

    return "Use your tools and memory to assist the user effectively."


def load_project_personality(project_id: str) -> dict[str, str]:
    """Load per-project personality overrides if they exist."""
    project_dir = settings.projects_dir / project_id / "personality"
    result: dict[str, str] = {}
    for fname in ["soul.md", "agent.md"]:
        fpath = project_dir / fname
        if fpath.exists():
            result[fname] = fpath.read_text(encoding="utf-8")
    return result


def get_full_personality(project_id: str | None = None) -> dict[str, str]:
    """Return merged personality for a given project (project overrides global)."""
    soul = load_soul()
    agent = load_agent_config()
    if project_id:
        overrides = load_project_personality(project_id)
        soul = overrides.get("soul.md", soul)
        agent = overrides.get("agent.md", agent)
    return {"soul": soul, "agent": agent}


def save_soul(content: str, project_id: str | None = None) -> None:
    """Save soul.md globally or for a specific project."""
    if project_id:
        path = settings.projects_dir / project_id / "personality" / "soul.md"
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path = settings.personality_dir / "soul.md"
    path.write_text(content, encoding="utf-8")


def save_agent_config(content: str, project_id: str | None = None) -> None:
    """Save agent.md globally or for a specific project."""
    if project_id:
        path = settings.projects_dir / project_id / "personality" / "agent.md"
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path = settings.personality_dir / "agent.md"
    path.write_text(content, encoding="utf-8")
