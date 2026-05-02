"""ArtifactStore — SQLite-backed durable content store with version history.

Text content lives in the `content` TEXT column (≤ 5 MB).
Binary content lives at data/blobs/<sha-prefix>/<sha> (content-addressed).
Versions are an append-only log; the artifacts row tracks current_version_id.
"""
from __future__ import annotations

import difflib
import hashlib
import io
import json
import logging
import sqlite3
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from config import get_settings

logger = logging.getLogger(__name__)

TEXT_TYPES_PREFIX = (
    "text/", "application/json", "application/yaml", "application/xml",
    "application/x-yaml", "image/svg+xml", "chat-export",
)

MAX_TEXT_BYTES = 5 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def is_text_type(content_type: str) -> bool:
    return any(content_type.startswith(p) for p in TEXT_TYPES_PREFIX)


class ArtifactStore:
    """Single-user artifact store. Initialised lazily."""

    def __init__(self, db_path: str | None = None, blobs_dir: Path | None = None):
        s = get_settings()
        s.db_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path or str(s.db_dir / "artifacts.db")
        self.blobs_dir = (blobs_dir or (s.data_dir / "blobs"))
        self.blobs_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        sql_path = Path(__file__).resolve().parent.parent / "data" / "migrations" / "001_artifacts.sql"
        sql = sql_path.read_text() if sql_path.exists() else _INLINE_SCHEMA
        with self._connect() as conn:
            conn.executescript(sql)

    # ── Storage helpers ─────────────────────────────────────────

    def _blob_dest(self, sha: str) -> Path:
        prefix = sha[:2]
        d = self.blobs_dir / prefix
        d.mkdir(parents=True, exist_ok=True)
        return d / sha

    def _store_blob(self, blob: bytes) -> tuple[str, str]:
        """Returns (sha256, relative_path_under_blobs_dir)."""
        sha = _sha256(blob)
        dest = self._blob_dest(sha)
        if not dest.exists():
            dest.write_bytes(blob)
        rel = dest.relative_to(self.blobs_dir).as_posix()
        return sha, rel

    def _load_blob(self, rel: str) -> bytes:
        return (self.blobs_dir / rel).read_bytes()

    # ── Hydration ───────────────────────────────────────────────

    def _hydrate_artifact(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        d["tags"] = json.loads(d.get("tags") or "[]")
        d["source"] = json.loads(d.get("source") or "{}")
        d["pinned"] = bool(d.get("pinned"))
        return d

    def _hydrate_version(self, row: sqlite3.Row) -> dict[str, Any]:
        return dict(row)

    # ── CRUD ────────────────────────────────────────────────────

    def create(
        self,
        *,
        project_id: str = "default",
        path: str,
        content: str | bytes,
        content_type: str,
        title: str | None = None,
        tags: list[str] | None = None,
        source: dict[str, Any] | None = None,
        edited_by: str = "user",
    ) -> dict[str, Any]:
        artifact_id = str(uuid.uuid4())
        version_id = str(uuid.uuid4())
        is_text = is_text_type(content_type)
        if is_text:
            if isinstance(content, bytes):
                content_text = content.decode("utf-8", errors="replace")
            else:
                content_text = content
            blob = content_text.encode("utf-8")
            if len(blob) > MAX_TEXT_BYTES:
                raise ValueError(f"text artifact exceeds {MAX_TEXT_BYTES} bytes")
            sha = _sha256(blob)
            text_value: str | None = content_text
            blob_path: str | None = None
            size = len(blob)
        else:
            if isinstance(content, str):
                blob = content.encode("utf-8")
            else:
                blob = content
            sha, blob_path = self._store_blob(blob)
            text_value = None
            size = len(blob)

        now = _now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO artifact_versions
                   (id, artifact_id, version_number, content, blob_path,
                    size_bytes, sha256, edit_summary, edited_by, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (version_id, artifact_id, 1, text_value, blob_path, size, sha,
                 "Initial version", edited_by, now),
            )
            conn.execute(
                """INSERT INTO artifacts
                   (id, project_id, path, title, content_type, content,
                    blob_path, size_bytes, sha256, tags, source, pinned,
                    current_version_id, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (artifact_id, project_id, path, title or _path_to_title(path),
                 content_type, text_value, blob_path, size, sha,
                 json.dumps(tags or []), json.dumps(source or {}), 0,
                 version_id, now, now),
            )
        logger.info("artifact created id=%s path=%s type=%s", artifact_id, path, content_type)
        return self.get(artifact_id)

    def update(
        self,
        artifact_id: str,
        *,
        content: str | bytes | None = None,
        title: str | None = None,
        tags: list[str] | None = None,
        edit_summary: str | None = None,
        edited_by: str = "user",
    ) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM artifacts WHERE id = ? AND deleted_at IS NULL",
                               (artifact_id,)).fetchone()
            if not row:
                raise KeyError(artifact_id)
            current = dict(row)
            now = _now()

            if content is not None:
                is_text = is_text_type(current["content_type"])
                if is_text:
                    text_value = (content if isinstance(content, str)
                                  else content.decode("utf-8", errors="replace"))
                    blob = text_value.encode("utf-8")
                    if len(blob) > MAX_TEXT_BYTES:
                        raise ValueError(f"text artifact exceeds {MAX_TEXT_BYTES} bytes")
                    sha = _sha256(blob)
                    blob_path: str | None = None
                    size = len(blob)
                else:
                    blob = content if isinstance(content, bytes) else content.encode("utf-8")
                    sha, blob_path = self._store_blob(blob)
                    text_value = None
                    size = len(blob)

                next_n = (conn.execute(
                    "SELECT MAX(version_number) FROM artifact_versions WHERE artifact_id = ?",
                    (artifact_id,)).fetchone()[0] or 0) + 1
                version_id = str(uuid.uuid4())
                conn.execute(
                    """INSERT INTO artifact_versions
                       (id, artifact_id, version_number, content, blob_path,
                        size_bytes, sha256, edit_summary, edited_by, created_at)
                       VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (version_id, artifact_id, next_n, text_value, blob_path,
                     size, sha, edit_summary or "Updated", edited_by, now),
                )
                conn.execute(
                    """UPDATE artifacts SET content = ?, blob_path = ?, size_bytes = ?,
                       sha256 = ?, current_version_id = ?, updated_at = ? WHERE id = ?""",
                    (text_value, blob_path, size, sha, version_id, now, artifact_id),
                )

            sets = []
            args: list[Any] = []
            if title is not None:
                sets.append("title = ?"); args.append(title)
            if tags is not None:
                sets.append("tags = ?"); args.append(json.dumps(tags))
            if sets:
                sets.append("updated_at = ?"); args.append(now)
                args.append(artifact_id)
                conn.execute(f"UPDATE artifacts SET {', '.join(sets)} WHERE id = ?", args)

        return self.get(artifact_id)

    def get(self, artifact_id: str, *, include_deleted: bool = False) -> dict[str, Any] | None:
        with self._connect() as conn:
            q = "SELECT * FROM artifacts WHERE id = ?"
            if not include_deleted:
                q += " AND deleted_at IS NULL"
            row = conn.execute(q, (artifact_id,)).fetchone()
        return self._hydrate_artifact(row) if row else None

    def get_by_path(self, project_id: str, path: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifacts WHERE project_id = ? AND path = ? AND deleted_at IS NULL",
                (project_id, path),
            ).fetchone()
        return self._hydrate_artifact(row) if row else None

    def list(
        self,
        *,
        project_id: str = "default",
        tag: str | None = None,
        content_type: str | None = None,
        path_prefix: str | None = None,
        pinned_only: bool = False,
        search: str | None = None,
        sort: str = "modified_desc",
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        order = {
            "modified_desc": "updated_at DESC",
            "modified_asc":  "updated_at ASC",
            "created_desc":  "created_at DESC",
            "title_asc":     "title COLLATE NOCASE ASC",
            "size_desc":     "size_bytes DESC",
        }.get(sort, "updated_at DESC")

        clauses = ["project_id = ?", "deleted_at IS NULL"]
        args: list[Any] = [project_id]
        if content_type:
            clauses.append("content_type = ?"); args.append(content_type)
        if path_prefix:
            clauses.append("path LIKE ?"); args.append(path_prefix + "%")
        if pinned_only:
            clauses.append("pinned = 1")
        if search:
            clauses.append("(title LIKE ? OR content LIKE ? OR tags LIKE ?)")
            wildcard = f"%{search}%"
            args.extend([wildcard, wildcard, wildcard])
        # tag filter is a JSON contains; do it post-query for portability
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM artifacts WHERE {' AND '.join(clauses)} "
                f"ORDER BY pinned DESC, {order} LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        results = [self._hydrate_artifact(r) for r in rows]
        if tag:
            results = [r for r in results if tag in (r.get("tags") or [])]
        return results

    def list_versions(self, artifact_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM artifact_versions WHERE artifact_id = ? "
                "ORDER BY version_number DESC",
                (artifact_id,),
            ).fetchall()
        return [self._hydrate_version(r) for r in rows]

    def get_version(self, artifact_id: str, version_number: int) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM artifact_versions WHERE artifact_id = ? AND version_number = ?",
                (artifact_id, version_number),
            ).fetchone()
        return self._hydrate_version(row) if row else None

    def restore_version(self, artifact_id: str, version_number: int, *, edited_by: str = "user") -> dict[str, Any]:
        v = self.get_version(artifact_id, version_number)
        if not v:
            raise KeyError(f"version {version_number}")
        if v["content"] is not None:
            return self.update(artifact_id, content=v["content"],
                               edit_summary=f"Restore v{version_number}", edited_by=edited_by)
        if v["blob_path"]:
            blob = self._load_blob(v["blob_path"])
            return self.update(artifact_id, content=blob,
                               edit_summary=f"Restore v{version_number}", edited_by=edited_by)
        raise ValueError("version has no content")

    def diff(self, artifact_id: str, a: int, b: int) -> str | None:
        va, vb = self.get_version(artifact_id, a), self.get_version(artifact_id, b)
        if not va or not vb:
            return None
        if va.get("content") is None or vb.get("content") is None:
            return None  # binary diff unsupported
        diff = difflib.unified_diff(
            (va["content"] or "").splitlines(keepends=True),
            (vb["content"] or "").splitlines(keepends=True),
            fromfile=f"v{a}", tofile=f"v{b}",
        )
        return "".join(diff)

    def rename(self, artifact_id: str, new_path: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("UPDATE artifacts SET path = ?, updated_at = ? WHERE id = ?",
                         (new_path, _now(), artifact_id))
        return self.get(artifact_id)

    def pin(self, artifact_id: str, pinned: bool) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("UPDATE artifacts SET pinned = ?, updated_at = ? WHERE id = ?",
                         (1 if pinned else 0, _now(), artifact_id))
        return self.get(artifact_id)

    def soft_delete(self, artifact_id: str) -> None:
        with self._connect() as conn:
            conn.execute("UPDATE artifacts SET deleted_at = ?, updated_at = ? WHERE id = ?",
                         (_now(), _now(), artifact_id))

    def restore(self, artifact_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            conn.execute("UPDATE artifacts SET deleted_at = NULL, updated_at = ? WHERE id = ?",
                         (_now(), artifact_id))
        return self.get(artifact_id)

    # ── Bulk ────────────────────────────────────────────────────

    def bulk_add_tags(self, ids: Iterable[str], tags: list[str]) -> int:
        n = 0
        for aid in ids:
            cur = self.get(aid)
            if not cur:
                continue
            new_tags = sorted(set((cur.get("tags") or []) + tags))
            self.update(aid, tags=new_tags, edit_summary="bulk add tags")
            n += 1
        return n

    def bulk_remove_tags(self, ids: Iterable[str], tags: list[str]) -> int:
        n = 0
        bad = set(tags)
        for aid in ids:
            cur = self.get(aid)
            if not cur:
                continue
            new_tags = [t for t in (cur.get("tags") or []) if t not in bad]
            self.update(aid, tags=new_tags, edit_summary="bulk remove tags")
            n += 1
        return n

    def bulk_delete(self, ids: Iterable[str]) -> int:
        n = 0
        for aid in ids:
            self.soft_delete(aid)
            n += 1
        return n

    def bulk_export(self, ids: Iterable[str]) -> bytes:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for aid in ids:
                a = self.get(aid)
                if not a:
                    continue
                if a.get("content") is not None:
                    zf.writestr(a["path"], a["content"])
                elif a.get("blob_path"):
                    blob = self._load_blob(a["blob_path"])
                    zf.writestr(a["path"], blob)
        return buf.getvalue()

    # ── Folder / tag aggregation ────────────────────────────────

    def list_all_projects(
        self,
        *,
        tag: str | None = None,
        content_type: str | None = None,
        path_prefix: str | None = None,
        pinned_only: bool = False,
        search: str | None = None,
        sort: str = "modified_desc",
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Cross-project listing for the 'All projects' UI toggle."""
        order = {
            "modified_desc": "updated_at DESC",
            "modified_asc":  "updated_at ASC",
            "created_desc":  "created_at DESC",
            "title_asc":     "title COLLATE NOCASE ASC",
            "size_desc":     "size_bytes DESC",
        }.get(sort, "updated_at DESC")
        clauses = ["deleted_at IS NULL"]
        args: list[Any] = []
        if content_type:
            clauses.append("content_type = ?"); args.append(content_type)
        if path_prefix:
            clauses.append("path LIKE ?"); args.append(path_prefix + "%")
        if pinned_only:
            clauses.append("pinned = 1")
        if search:
            clauses.append("(title LIKE ? OR content LIKE ? OR tags LIKE ?)")
            wildcard = f"%{search}%"
            args.extend([wildcard, wildcard, wildcard])
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM artifacts WHERE {' AND '.join(clauses)} "
                f"ORDER BY pinned DESC, {order} LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        results = [self._hydrate_artifact(r) for r in rows]
        if tag:
            results = [r for r in results if tag in (r.get("tags") or [])]
        return results

    def folder_tree_all(self) -> list[str]:
        """Folder tree across all projects (path stays as stored)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT path FROM artifacts WHERE deleted_at IS NULL"
            ).fetchall()
        folders: set[str] = set()
        for r in rows:
            parts = (r["path"] or "").split("/")[:-1]
            for i in range(1, len(parts) + 1):
                folders.add("/".join(parts[:i]))
        return sorted(p for p in folders if p)

    def folder_tree(self, project_id: str = "default") -> list[str]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT path FROM artifacts WHERE project_id = ? AND deleted_at IS NULL",
                (project_id,),
            ).fetchall()
        folders: set[str] = set()
        for r in rows:
            parts = (r["path"] or "").split("/")[:-1]
            for i in range(1, len(parts) + 1):
                folders.add("/".join(parts[:i]))
        return sorted(p for p in folders if p)

    def tag_counts(self, project_id: str = "default") -> dict[str, int]:
        counts: dict[str, int] = {}
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT tags FROM artifacts WHERE project_id = ? AND deleted_at IS NULL",
                (project_id,),
            ).fetchall()
        for r in rows:
            for t in json.loads(r["tags"] or "[]"):
                counts[t] = counts.get(t, 0) + 1
        return counts


def _path_to_title(path: str) -> str:
    leaf = path.rsplit("/", 1)[-1]
    return leaf.rsplit(".", 1)[0].replace("-", " ").replace("_", " ").strip() or leaf


# Inline copy of the migration in case the file isn't shipped yet.
_INLINE_SCHEMA = """\
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY, project_id TEXT NOT NULL DEFAULT 'default',
    path TEXT NOT NULL, title TEXT, content_type TEXT NOT NULL,
    content TEXT, blob_path TEXT, size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '[]',
    source TEXT NOT NULL DEFAULT '{}', pinned INTEGER NOT NULL DEFAULT 0,
    current_version_id TEXT NOT NULL, created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL, deleted_at TEXT
);
CREATE TABLE IF NOT EXISTS artifact_versions (
    id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL,
    version_number INTEGER NOT NULL, content TEXT, blob_path TEXT,
    size_bytes INTEGER NOT NULL, sha256 TEXT NOT NULL,
    edit_summary TEXT, edited_by TEXT, created_at TEXT NOT NULL,
    UNIQUE (artifact_id, version_number)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_artifacts_project_path
    ON artifacts(project_id, path) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_artifacts_project_created
    ON artifacts(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_pinned
    ON artifacts(project_id, pinned) WHERE deleted_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_artifact_versions_artifact
    ON artifact_versions(artifact_id, version_number DESC);
"""


_INSTANCE: ArtifactStore | None = None

def get_store() -> ArtifactStore:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = ArtifactStore()
    return _INSTANCE

def project_slug(project_id: str) -> str:
    """Return a filesystem-safe slug for a project's display name.

    Used by save flows to prefix artifact paths so artifacts cluster by
    project in the folder tree (e.g. 'my-project/chats/2026-05-01-foo.md').
    Falls back to the project_id if the project record cannot be loaded.
    """
    import json
    import re
    from pathlib import Path
    try:
        from config import get_settings
        meta = Path(get_settings().db_dir) / "projects.json"
        if meta.exists():
            data = json.loads(meta.read_text() or "{}")
            row = data.get(project_id) if isinstance(data, dict) else None
            name = (row or {}).get("name") if isinstance(row, dict) else None
            if name:
                slug = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
                if slug:
                    return slug
    except Exception:
        pass
    if project_id and project_id != "default":
        return re.sub(r"[^a-zA-Z0-9]+", "-", project_id).strip("-").lower() or "default"
    return "default"
