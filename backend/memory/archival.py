"""Tier 5: Archival memory — file-based long-term storage for notes and summaries."""
from __future__ import annotations
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class ArchivalMemory:
    """Tier 5: File-based archival memory for personality and project summaries.
    
    This is the slowest but most permanent tier. Contents persist across
    sessions and are loaded into the system prompt for future conversations.
    """

    def __init__(self, project_id: str = "default", base_dir: str | None = None):
        from utils.paths import check_project_id
        self.project_id = check_project_id(project_id)
        if base_dir is None:
            # Always the configured data dir — a relative "data" resolved
            # against the CWD (backend/ on the dev box), so notes written by
            # the memory manager and read by the API landed in different places.
            from config import get_settings
            base_dir = str(get_settings().data_dir)
        self._base = Path(base_dir)
        self._personality_dir = self._base / "personality"
        self._projects_dir = self._base / "projects"

    @property
    def personality_dir(self) -> Path:
        return self._personality_dir

    @property
    def project_dir(self) -> Path:
        return self._projects_dir / self.project_id

    @property
    def notes_dir(self) -> Path:
        return self.project_dir / "notes"

    def _resolve_path(self, rel_path: str) -> Path:
        """Resolve a relative path safely within allowed directories."""
        # Allow personality/ and projects/ paths
        if rel_path.startswith("personality/"):
            base = self._base
        else:
            base = self.project_dir
            if rel_path.startswith(f"projects/{self.project_id}/"):
                rel_path = rel_path[len(f"projects/{self.project_id}/"):]
        
        target = (base / rel_path).resolve()
        allowed_bases = [
            self._personality_dir.resolve(),
            self.project_dir.resolve(),
        ]
        from utils.paths import is_within
        if not any(is_within(target, b) for b in allowed_bases):
            raise ValueError(f"Path outside allowed directories: {rel_path}")
        return target

    async def read_file(self, path: str) -> str:
        """Read a file from archival storage."""
        try:
            full_path = self._resolve_path(path)
            if not full_path.exists():
                return f"File not found: {path}"
            return full_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.error(f"Archival read error for {path}: {e}")
            return f"Error reading {path}: {e}"

    async def write_file(self, path: str, content: str) -> None:
        """Write content to an archival file."""
        full_path = self._resolve_path(path)
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")
        logger.info(f"Archival file written: {path}")

    async def append_note(self, content: str, filename: str | None = None) -> str:
        """Append a note to the project's notes directory."""
        self.notes_dir.mkdir(parents=True, exist_ok=True)
        if not filename:
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            filename = f"note_{timestamp}.md"
        note_path = self.notes_dir / filename
        header = f"# Note — {_now_str()}\n\n"
        if note_path.exists():
            existing = note_path.read_text(encoding="utf-8")
            note_path.write_text(existing + f"\n\n---\n\n{_now_str()}\n\n{content}", encoding="utf-8")
        else:
            note_path.write_text(header + content, encoding="utf-8")
        logger.info(f"Note appended: {filename}")
        return filename

    async def get_project_summary(self) -> str:
        """Read the project summary file."""
        summary_path = self.project_dir / "project_summary.md"
        if summary_path.exists():
            return summary_path.read_text(encoding="utf-8")
        return ""

    async def update_project_summary(self, content: str) -> None:
        """Update the project summary file."""
        summary_path = self.project_dir / "project_summary.md"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(content, encoding="utf-8")

    async def list_files(self, subpath: str = "") -> list[dict[str, Any]]:
        """List files in archival storage."""
        if subpath:
            try:
                target = self._resolve_path(subpath)
            except ValueError:
                return []
        else:
            target = self.project_dir
        
        if not target.exists():
            return []

        results = []
        for item in sorted(target.rglob("*")):
            if item.is_file():
                rel = str(item.relative_to(target))
                results.append({
                    "path": rel,
                    "size": item.stat().st_size,
                    "modified": datetime.fromtimestamp(item.stat().st_mtime, tz=timezone.utc).isoformat(),
                })
        return results

    async def list_notes(self) -> list[dict[str, Any]]:
        """List all notes for this project."""
        if not self.notes_dir.exists():
            return []
        notes = []
        for note_file in sorted(self.notes_dir.glob("*.md"), reverse=True):
            notes.append({
                "filename": note_file.name,
                "path": str(note_file.relative_to(self.project_dir)),
                "size": note_file.stat().st_size,
                "modified": datetime.fromtimestamp(
                    note_file.stat().st_mtime, tz=timezone.utc
                ).isoformat(),
            })
        return notes

    async def delete_file(self, path: str) -> bool:
        """Delete an archival file."""
        try:
            full_path = self._resolve_path(path)
            if full_path.exists() and full_path.is_file():
                full_path.unlink()
                return True
            return False
        except Exception as e:
            logger.error(f"Archival delete error: {e}")
            return False


def migrate_stray_notes() -> int:
    """Move archival notes written under a CWD-relative ``data/projects``
    (backend/data/ when run from backend/) into the configured data dir,
    where the Memory page reads them. Existing files are never overwritten.
    Returns the number of files moved."""
    import shutil
    from config import get_settings
    canonical = Path(get_settings().data_dir).resolve() / "projects"
    candidates = {Path(__file__).resolve().parents[1] / "data" / "projects",
                  Path.cwd().resolve() / "data" / "projects"}
    moved = 0
    for stray in candidates:
        if not stray.is_dir() or stray.resolve() == canonical:
            continue
        for note in stray.glob("*/notes/**/*"):
            if not note.is_file():
                continue
            dest = canonical / note.relative_to(stray)
            if dest.exists():
                logger.warning("archival: not moving %s — %s already exists", note, dest)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(note), str(dest))
            moved += 1
    if moved:
        logger.info("archival: moved %d stray note file(s) into %s", moved, canonical)
    return moved
