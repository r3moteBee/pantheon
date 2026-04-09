"""Skill versioning and rollback.

Every edit to a user skill (via the editor) snapshots the current state of
the skill directory to `data_dir/skills/.versions/{skill_name}/{stamp}/`
before writing. Users can list past versions, preview file contents at a
given version, and restore any prior version (which itself snapshots the
current state first, so restore is non-destructive).

History is capped per-skill to `MAX_VERSIONS_PER_SKILL` (oldest pruned).

Storage layout:
    data_dir/skills/.versions/
        my-skill/
            20260408T153012Z_edit/
                skill.json
                instructions.md
                ...
                _meta.json     # {label, timestamp, note}
            20260408T160145Z_restore/
                ...
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

MAX_VERSIONS_PER_SKILL = 20
_VERSIONS_ROOT = settings.data_dir / "skills" / ".versions"
_USER_SKILLS_DIR = settings.data_dir / "skills"


def _skill_versions_dir(skill_name: str) -> Path:
    return _VERSIONS_ROOT / skill_name


def _skill_dir(skill_name: str) -> Path:
    return _USER_SKILLS_DIR / skill_name


def _stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _is_version_dir(p: Path) -> bool:
    return p.is_dir() and (p / "_meta.json").is_file()


def snapshot_skill(skill_name: str, label: str = "edit", note: str = "") -> str | None:
    """Copy the current skill dir into the versions store. Returns version id."""
    src = _skill_dir(skill_name)
    if not src.is_dir():
        return None
    versions_dir = _skill_versions_dir(skill_name)
    versions_dir.mkdir(parents=True, exist_ok=True)

    version_id = f"{_stamp()}_{label}"
    dst = versions_dir / version_id
    if dst.exists():
        # Same-second collision — append counter
        i = 1
        while (versions_dir / f"{version_id}_{i}").exists():
            i += 1
        version_id = f"{version_id}_{i}"
        dst = versions_dir / version_id

    try:
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(".versions", "__pycache__", "*.pyc"))
    except Exception as e:
        logger.warning("snapshot_skill(%s) failed: %s", skill_name, e)
        return None

    meta = {
        "version_id": version_id,
        "skill": skill_name,
        "label": label,
        "note": note,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    (dst / "_meta.json").write_text(json.dumps(meta, indent=2), "utf-8")
    _prune_old_versions(skill_name)
    return version_id


def _prune_old_versions(skill_name: str) -> None:
    versions_dir = _skill_versions_dir(skill_name)
    if not versions_dir.is_dir():
        return
    versions = sorted(
        (p for p in versions_dir.iterdir() if _is_version_dir(p)),
        key=lambda p: p.name,
    )
    while len(versions) > MAX_VERSIONS_PER_SKILL:
        oldest = versions.pop(0)
        try:
            shutil.rmtree(oldest)
        except Exception as e:
            logger.warning("Failed to prune old version %s: %s", oldest, e)


def list_versions(skill_name: str) -> list[dict[str, Any]]:
    versions_dir = _skill_versions_dir(skill_name)
    if not versions_dir.is_dir():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(versions_dir.iterdir(), reverse=True):
        if not _is_version_dir(p):
            continue
        try:
            meta = json.loads((p / "_meta.json").read_text("utf-8"))
        except Exception:
            meta = {"version_id": p.name}
        file_count = sum(1 for f in p.rglob("*") if f.is_file() and f.name != "_meta.json")
        out.append({**meta, "file_count": file_count})
    return out


def _safe_version_path(skill_name: str, version_id: str) -> Path:
    if "/" in version_id or ".." in version_id or version_id.startswith("."):
        raise ValueError(f"Invalid version id: {version_id}")
    target = _skill_versions_dir(skill_name) / version_id
    if not _is_version_dir(target):
        raise FileNotFoundError(f"Version not found: {version_id}")
    return target


def list_version_files(skill_name: str, version_id: str) -> list[dict[str, Any]]:
    root = _safe_version_path(skill_name, version_id)
    files = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.name != "_meta.json":
            files.append({
                "path": str(p.relative_to(root)),
                "size": p.stat().st_size,
            })
    return files


def read_version_file(skill_name: str, version_id: str, rel_path: str) -> str:
    root = _safe_version_path(skill_name, version_id)
    if ".." in rel_path or rel_path.startswith("/"):
        raise ValueError(f"Invalid path: {rel_path}")
    target = (root / rel_path).resolve()
    if root.resolve() not in target.parents:
        raise ValueError(f"Path escapes version directory: {rel_path}")
    if not target.is_file():
        raise FileNotFoundError(f"File not found in version: {rel_path}")
    return target.read_text("utf-8", errors="replace")


def restore_version(skill_name: str, version_id: str) -> dict[str, Any]:
    """Restore a skill directory to a prior version.

    Safety: snapshots current state first (label='pre_restore'), then wipes
    the skill dir and copies the version contents back in.
    """
    src = _safe_version_path(skill_name, version_id)
    dst = _skill_dir(skill_name)
    if not dst.is_dir():
        raise FileNotFoundError(f"Skill not found: {skill_name}")

    pre_snapshot = snapshot_skill(skill_name, label="pre_restore", note=f"before restoring {version_id}")

    # Wipe current skill dir (preserving .versions if it lived here — it doesn't, it's at data_dir/skills/.versions)
    for item in dst.iterdir():
        try:
            if item.is_dir():
                shutil.rmtree(item)
            else:
                item.unlink()
        except Exception as e:
            logger.warning("restore_version wipe failed for %s: %s", item, e)

    # Copy version contents (skip _meta.json)
    for item in src.iterdir():
        if item.name == "_meta.json":
            continue
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target)
        else:
            shutil.copy2(item, target)

    # Record the restore as its own version marker
    restored_snapshot = snapshot_skill(skill_name, label="restored", note=f"restored from {version_id}")
    return {
        "restored_from": version_id,
        "pre_restore_snapshot": pre_snapshot,
        "restored_snapshot": restored_snapshot,
    }
