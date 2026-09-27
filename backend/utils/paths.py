"""Path-safety helpers shared by the files API and agent tools."""
from __future__ import annotations

from pathlib import Path


def is_within(target: Path, base: Path) -> bool:
    """True when resolved ``target`` is ``base`` or inside it.

    Use instead of ``str(target).startswith(str(base))``, which lets a sibling
    such as ``workspace_old/`` pass a check for ``workspace``.
    """
    try:
        return target.resolve().is_relative_to(base.resolve())
    except (OSError, ValueError):
        return False


class InvalidProjectId(ValueError):
    """A project id that would escape projects_dir. main.py maps it to 400."""


def check_project_id(project_id: str) -> str:
    """Reject project ids that would escape ``projects_dir`` when joined."""
    if (
        not project_id
        or project_id in (".", "..")
        or "/" in project_id
        or "\\" in project_id
        or "\x00" in project_id
    ):
        raise InvalidProjectId(f"Invalid project id: {project_id!r}")
    return project_id


def safe_filename(name: str, default: str = "download") -> str:
    """Reduce an untrusted filename (URL segment, Content-Disposition) to a
    bare basename with no directory components."""
    name = (name or "").replace("\\", "/").split("/")[-1].strip().strip("\x00")
    if name in ("", ".", ".."):
        return default
    return name
