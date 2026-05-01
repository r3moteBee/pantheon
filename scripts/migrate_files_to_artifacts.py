"""Phase E migration — move workspace files into artifacts.

Idempotent. Records every action in data/db/migration_log/files_to_artifacts.json.

Run via: python scripts/migrate_files_to_artifacts.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from config import get_settings  # noqa: E402
from artifacts.store import ArtifactStore, MAX_TEXT_BYTES, is_text_type  # noqa: E402

logger = logging.getLogger("migrate_files_to_artifacts")
logging.basicConfig(level=logging.INFO, format="%(message)s")

TEXT_EXTS = {".md", ".markdown", ".txt", ".text", ".csv", ".tsv",
             ".json", ".yaml", ".yml", ".py", ".js", ".ts", ".tsx", ".jsx",
             ".html", ".htm", ".xml", ".log", ".sh", ".rst"}
BINARY_EXTS_PREVIEWABLE = {
    ".pdf", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg",
    ".docx", ".xlsx", ".pptx",
}
SKIP_DIRS = {"__pycache__", "node_modules", ".git"}
MAX_BLOB_BYTES = 100 * 1024 * 1024


def _content_type(path: Path) -> str:
    ext = path.suffix.lower()
    explicit = {
        ".md": "text/markdown", ".markdown": "text/markdown",
        ".py": "text/x-python", ".js": "text/javascript",
        ".ts": "text/typescript", ".jsx": "text/javascript",
        ".tsx": "text/typescript", ".sh": "application/x-sh",
        ".json": "application/json", ".yaml": "application/yaml",
        ".yml": "application/yaml", ".csv": "text/csv",
        ".html": "text/html", ".xml": "application/xml",
        ".svg": "image/svg+xml", ".pdf": "application/pdf",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }
    if ext in explicit:
        return explicit[ext]
    guess, _ = mimetypes.guess_type(str(path))
    return guess or "application/octet-stream"


def _project_id_from_dir(workspace_dir: Path, projects_root: Path | None) -> str:
    if projects_root and workspace_dir.is_relative_to(projects_root):
        rel = workspace_dir.relative_to(projects_root)
        parts = rel.parts
        return parts[0] if parts else "default"
    return "default"


def _migrate_one(store: ArtifactStore, file_path: Path, base: Path, project_id: str,
                 actions: list[dict], dry_run: bool) -> None:
    rel = file_path.relative_to(base).as_posix()
    if any(part in SKIP_DIRS for part in file_path.parts):
        return
    size = file_path.stat().st_size
    ct = _content_type(file_path)
    is_text = is_text_type(ct) or file_path.suffix.lower() in TEXT_EXTS

    if is_text:
        if size > MAX_TEXT_BYTES:
            actions.append({"path": rel, "action": "skip", "reason": "exceeds 5MB text limit"})
            return
        try:
            content = file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            actions.append({"path": rel, "action": "skip", "reason": f"read error: {e}"})
            return
        if dry_run:
            actions.append({"path": rel, "action": "would_create_text", "size": size, "content_type": ct})
            return
        existing = store.get_by_path(project_id, rel)
        if existing:
            actions.append({"path": rel, "action": "skip_existing", "id": existing["id"]})
            return
        a = store.create(
            project_id=project_id, path=rel, content=content,
            content_type=ct, source={"kind": "migrated"}, edited_by="migration",
        )
        actions.append({"path": rel, "action": "created", "id": a["id"], "kind": "text"})
        return

    if file_path.suffix.lower() in BINARY_EXTS_PREVIEWABLE:
        if size > MAX_BLOB_BYTES:
            actions.append({"path": rel, "action": "skip", "reason": "exceeds 100MB binary limit"})
            return
        if dry_run:
            actions.append({"path": rel, "action": "would_create_binary", "size": size, "content_type": ct})
            return
        existing = store.get_by_path(project_id, rel)
        if existing:
            actions.append({"path": rel, "action": "skip_existing", "id": existing["id"]})
            return
        blob = file_path.read_bytes()
        a = store.create(
            project_id=project_id, path=rel, content=blob,
            content_type=ct, source={"kind": "migrated"}, edited_by="migration",
        )
        actions.append({"path": rel, "action": "created", "id": a["id"], "kind": "binary"})
        return

    # Anything else stays on disk under data/scratch
    actions.append({"path": rel, "action": "left_on_disk_as_scratch"})


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    s = get_settings()
    store = ArtifactStore() if not args.dry_run else ArtifactStore()
    actions: list[dict] = []

    bases: list[tuple[Path, str]] = []
    if s.workspace_dir.exists():
        bases.append((s.workspace_dir, "default"))
    if s.projects_dir.exists():
        for entry in s.projects_dir.iterdir():
            ws = entry / "workspace"
            if ws.exists() and ws.is_dir():
                bases.append((ws, entry.name))

    for base, project_id in bases:
        logger.info("== %s (project=%s) ==", base, project_id)
        for f in base.rglob("*"):
            if f.is_file():
                _migrate_one(store, f, base, project_id, actions, args.dry_run)

    log_dir = s.db_dir / "migration_log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"files_to_artifacts_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    log_path.write_text(json.dumps({
        "dry_run": args.dry_run,
        "completed_at": datetime.now(timezone.utc).isoformat(),
        "actions": actions,
    }, indent=2))

    summary: dict[str, int] = {}
    for a in actions:
        summary[a["action"]] = summary.get(a["action"], 0) + 1
    logger.info("Migration log: %s", log_path)
    for k, v in summary.items():
        logger.info("  %s: %d", k, v)


if __name__ == "__main__":
    main()
