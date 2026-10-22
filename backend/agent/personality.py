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


# sha256 of every agent.md Pantheon has shipped (backend/data/personality/agent.md
# and the copy once tracked at data/personality/agent.md). A data-dir agent.md
# with one of these hashes is an unedited default. Installs only ever got the
# file copied when it was missing, so they kept reading an old guide after
# updates; refresh_default_agent_md replaces such copies and keeps user edits.
SHIPPED_AGENT_MD_SHA256 = frozenset({
    "23b4c4f785027b84b5391d5de0b41e64d47a40603eefcac744ff4886edb357df",
    "3c6590caedfb1d720938865972bdc19350d6cf809a67e28a5c6fc5ecb4d1f9df",
    "7a9b2b87bd32a491a91ab20329f7325ceae07d9e4e69b2f025d93b8abbd9b195",
    "b20382ffe5e78c5413e4b52b0fdff32294994e9250aa307ecb6dfe520e426360",
    "b8879f51858cbaaf60be9a0012b5f4515a13acc7ce505b916612601f6e18ce9c",
    "c1d4a976f1f4df6e5a7bfe5deb4064507777269b6dfef63b94b95f8b41dfcc63",
    "c7e7736e190a0756d2d45b97c8412d8b496dbae3eb7e1ae56be6421301d6b8b8",
    "d3d0824a9b2182d4ad67a466ad43721f13bbf21df2b801e7f1525bd2ea917046",
    "e064186477389e8705256b5117276237256c36f81cf22cff248aba4f45e629c0",
    "eb633794aeaf00d39ce11957ed6e7fe26fbe2ea446267638beacda7b9371621e",
    "f05a9859a47358b71a4e40a418681f7478e40d28ada4921eabe1348433750afe",
    "f63e54cec0376dd04c88f9bd3f6ebc3440a3824bb290e1a3ce11a983c9a0de1c",
})
_AGENT_MD_MARKER = ".agent.md.bundled-sha256"   # hash of the default we last copied


def _sha256(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(data).hexdigest()


def refresh_default_agent_md(dest_dir: Path, bundled: Path) -> str:
    """Bring DATA_DIR/personality/agent.md up to the bundled default unless the
    user edited it. Returns what happened: "copied", "refreshed", "current",
    "kept-edited" or "no-bundled"."""
    if not bundled.is_file():
        return "no-bundled"
    new = bundled.read_bytes()
    dest = dest_dir / "agent.md"
    marker = dest_dir / _AGENT_MD_MARKER
    dest_dir.mkdir(parents=True, exist_ok=True)
    if not dest.exists() or not dest.read_text(encoding="utf-8").strip():
        dest.write_bytes(new)
        marker.write_text(_sha256(new), encoding="utf-8")
        return "copied"
    cur = _sha256(dest.read_bytes())
    if cur == _sha256(new):
        marker.write_text(cur, encoding="utf-8")
        return "current"
    last = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
    if cur in SHIPPED_AGENT_MD_SHA256 or cur == last:
        dest.write_bytes(new)
        marker.write_text(_sha256(new), encoding="utf-8")
        return "refreshed"
    return "kept-edited"


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
