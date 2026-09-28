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


def _project_personality_dir(project_id: str) -> Path:
    """projects/<id>/personality, refusing ids that would escape projects_dir."""
    from utils.paths import check_project_id
    return settings.projects_dir / check_project_id(project_id) / "personality"


def load_project_personality(project_id: str) -> dict[str, str]:
    """Load per-project personality overrides if they exist."""
    project_dir = _project_personality_dir(project_id)
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
        path = _project_personality_dir(project_id) / "soul.md"
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path = settings.personality_dir / "soul.md"
    path.write_text(content, encoding="utf-8")


def save_agent_config(content: str, project_id: str | None = None) -> None:
    """Save agent.md globally or for a specific project."""
    if project_id:
        path = _project_personality_dir(project_id) / "agent.md"
        path.parent.mkdir(parents=True, exist_ok=True)
    else:
        path = settings.personality_dir / "agent.md"
    path.write_text(content, encoding="utf-8")


# ── Presets ─────────────────────────────────────────────────────────────────
# A preset (the persona JSONs) is a soul.md voice. Applying one writes it as
# the project's soul.md *plus* the Key Commitments from the global soul, so
# no preset drops "never fabricate / flag uncertainty".

_COMMITMENTS_HEADING = "## Key Commitments"
_FALLBACK_COMMITMENTS = (
    f"{_COMMITMENTS_HEADING}\n\n"
    "- **I will never fabricate information.** If I don't know something, I'll say so clearly.\n"
    "- **I will be transparent about uncertainty** — incomplete data is noted, not hidden.\n"
    "- **I will ask clarifying questions** when the task or the data is ambiguous.\n"
)


def _section(markdown: str, heading: str) -> str | None:
    """The ``heading`` section: from the heading up to the next heading or
    the first non-list paragraph after its list."""
    lines = markdown.splitlines()
    try:
        start = next(i for i, l in enumerate(lines) if l.strip() == heading)
    except StopIteration:
        return None
    out = [lines[start]]
    seen_item = False
    for l in lines[start + 1:]:
        s = l.strip()
        if s.startswith("#"):
            break
        if s.startswith(("-", "*")) or (seen_item and s and l.startswith((" ", "\t"))):
            seen_item = True
        elif s and seen_item:
            break
        out.append(l)
    return "\n".join(out).rstrip()


def key_commitments() -> str:
    """The Key Commitments section of the global soul (so user edits
    carry over), else the bundled one, else a minimal fallback."""
    for source in (load_soul(), _load_template("soul.md") or ""):
        sec = _section(source, _COMMITMENTS_HEADING)
        if sec:
            # Pan calls the user "my keeper"; presets have their own voice.
            return sec.replace("my keeper", "the user").replace("your keeper", "the user")
    return _FALLBACK_COMMITMENTS.rstrip()


def with_commitments(soul: str) -> str:
    if _COMMITMENTS_HEADING in soul:
        return soul
    return soul.rstrip() + "\n\n" + key_commitments() + "\n"


def clear_project_override(project_id: str) -> list[str]:
    """Delete a project's soul/agent overrides so it follows the global
    personality again. Returns the files removed."""
    removed = []
    d = _project_personality_dir(project_id)
    for fname in ("soul.md", "agent.md"):
        f = d / fname
        if f.exists():
            f.unlink()
            removed.append(fname)
    return removed


def _norm(text: str) -> str:
    return " ".join(text.split())


def migrate_persona_overrides(projects: dict, bundled_presets: dict[str, str]) -> dict[str, list[str]]:
    """One-shot fix for projects created while "Pan" was auto-applied:

    - a project soul.md that is exactly the global soul (or the bundled one)
      minus Key Commitments — i.e. untouched Pan — is removed, so the
      project follows the global personality (and your edits to it) again;
    - an untouched copy of another bundled preset gets its Key Commitments
      back.

    Anything the user edited is left alone. ``projects`` is projects.json;
    entries whose override is removed lose ``persona_id``."""
    report: dict[str, list[str]] = {"reverted": [], "commitments_added": []}
    pan_like = set()
    for source in (load_soul(), _load_template("soul.md") or ""):
        sec = _section(source, _COMMITMENTS_HEADING)
        if sec:
            pan_like.add(_norm(source.replace(sec, "")))
    presets = {_norm(v): k for k, v in bundled_presets.items() if v}
    for pid, meta in projects.items():
        try:
            f = _project_personality_dir(pid) / "soul.md"
        except Exception:
            continue
        if not f.exists():
            continue
        cur = f.read_text(encoding="utf-8")
        n = _norm(cur)
        if n in pan_like:
            f.unlink()
            if isinstance(meta, dict):
                meta.pop("persona_id", None)
            report["reverted"].append(pid)
        elif n in presets and _COMMITMENTS_HEADING not in cur:
            f.write_text(with_commitments(cur), encoding="utf-8")
            report["commitments_added"].append(pid)
    return report
