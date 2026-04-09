"""Skill sharing — package a skill as a portable tar.gz archive.

The archive contains the skill directory contents (manifest, instructions,
any scripts/assets) but excludes the versioning store and caches. Recipients
can extract into their own `data_dir/skills/` and re-import.
"""
from __future__ import annotations

import io
import logging
import tarfile
from pathlib import Path

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_EXCLUDES = {".versions", "__pycache__", ".DS_Store"}


def _skill_dir(skill_name: str) -> Path:
    return settings.data_dir / "skills" / skill_name


def export_skill_targz(skill_name: str) -> bytes:
    src = _skill_dir(skill_name)
    if not src.is_dir():
        raise FileNotFoundError(f"Skill not found: {skill_name}")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        def _filter(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
            parts = info.name.split("/")
            for part in parts:
                if part in _EXCLUDES or part.endswith(".pyc"):
                    return None
            # Keep basic metadata but strip ownership for portability
            info.uid = 0
            info.gid = 0
            info.uname = "skill"
            info.gname = "skill"
            return info
        tar.add(src, arcname=skill_name, filter=_filter)
    return buf.getvalue()
