"""Everything a project owns: counted (delete preview) and removed (project delete).

Deleting a project used to remove only data/projects/<id>/ and its projects.json
entry; its conversations, memory notes, graph, artifacts, file index, jobs,
schedules, settings and both vector stores stayed behind (found 2026-10-02
after a test harness had created and "deleted" 133 projects: 438 conversations
and ~280 MB of Chroma dirs were still there). Every per-project store is listed
here once - add new ones to STORES/_purge_* and to the export (api/project_export.py).
"""
from __future__ import annotations

import json
import logging
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)

# (db file under db_dir, table, label shown in the UI). Child tables before parents.
SQL_STORES: list[tuple[str, str, str]] = [
    ("episodic.db", "messages", "chat messages"),
    ("episodic.db", "conversations", "conversations"),
    ("episodic.db", "task_logs", "task log entries"),
    ("episodic.db", "memory_notes", "memory notes"),
    ("graph.db", "graph_edges", "graph links"),
    ("graph.db", "graph_nodes", "graph entities"),
    ("file_index.db", "indexed_files", "indexed files"),
    ("merge_proposals.db", "topic_merge_proposals", "topic merge proposals"),
    ("phase_g.db", "task_runs", "task runs"),
    ("phase_g.db", "project_mcp_enablement", "MCP settings"),
    ("phase_g.db", "project_settings", "project settings"),
    ("phase_g.db", "project_repo_bindings", "repo bindings"),
    ("sources.db", "project_repo_bindings", "repo bindings"),
]


class ProjectBusy(RuntimeError):
    """The project has work running; it can't be deleted until that stops."""


def _db(name: str) -> Path:
    s = get_settings()
    if name == "episodic.db":
        return Path(s.episodic_db_path)
    if name == "graph.db":
        return Path(s.graph_db_path)
    return s.db_dir / name


def _connect(path: Path) -> sqlite3.Connection | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _sql_count(db: str, table: str, project_id: str) -> int:
    conn = _connect(_db(db))
    if conn is None:
        return 0
    try:
        if not _has_table(conn, table):
            return 0
        return conn.execute(f"SELECT COUNT(*) FROM {table} WHERE project_id = ?", (project_id,)).fetchone()[0]
    finally:
        conn.close()


def _sql_delete(db: str, table: str, project_id: str) -> int:
    conn = _connect(_db(db))
    if conn is None:
        return 0
    try:
        if not _has_table(conn, table):
            return 0
        with conn:
            return conn.execute(f"DELETE FROM {table} WHERE project_id = ?", (project_id,)).rowcount
    finally:
        conn.close()


# ── Vector stores (Chroma) ───────────────────────────────────────────────────

def _collections(project_id: str) -> list[tuple[str, str, str]]:
    """(local dir, collection name, label) for the project's two Chroma collections."""
    from memory.episodic import episodic_collection_name
    from memory.semantic import _sanitize_collection_name
    return [
        (project_id, _sanitize_collection_name(f"proj-{project_id}"), "semantic memories and search chunks"),
        (f"episodic-{project_id}", episodic_collection_name(project_id), "message search vectors"),
    ]


def _chroma_client(local_dir: str, create: bool):
    s = get_settings()
    import chromadb
    host = (s.chroma_host or "").strip()
    if host:
        return chromadb.HttpClient(host=host, port=s.chroma_port), None
    path = Path(s.data_dir) / "chroma" / local_dir
    if not path.exists() and not create:
        return None, path
    return chromadb.PersistentClient(path=str(path)), path


def _vector_count(project_id: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for local_dir, name, label in _collections(project_id):
        try:
            client, _ = _chroma_client(local_dir, create=False)
            if client is not None:
                out[label] = client.get_collection(name).count()
        except Exception:
            pass
    return out


def _forget_local_client(path: Path) -> None:
    """Drop chromadb's process-wide cached System for ``path`` so a later client
    on the same path starts fresh instead of using the deleted files."""
    try:
        from chromadb.api.client import SharedSystemClient
        systems = getattr(SharedSystemClient, "_identifier_to_system", {})
        system = systems.pop(str(path), None)
        if system is not None:
            system.stop()
    except Exception as e:  # private API - best effort
        logger.debug("chroma cache reset skipped: %s", e)


def _vector_delete(project_id: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for local_dir, name, label in _collections(project_id):
        try:
            client, path = _chroma_client(local_dir, create=False)
            if client is None:
                continue
            try:
                out[label] = client.get_collection(name).count()
                client.delete_collection(name)
            except Exception:
                pass   # no such collection
            # The local dir may also hold another project's collection: a project named
            # "episodic-foo" keeps its semantic store in the same dir as foo's episodic one.
            if path is not None and not client.list_collections():
                _forget_local_client(path)
                shutil.rmtree(path, ignore_errors=True)
        except Exception as e:
            logger.warning("Project delete: vector store %s not removed: %s", name, e)
    return out


# ── Artifacts ────────────────────────────────────────────────────────────────

def _artifact_conn() -> sqlite3.Connection | None:
    return _connect(get_settings().db_dir / "artifacts.db")


def _artifact_count(project_id: str) -> dict[str, int]:
    conn = _artifact_conn()
    if conn is None or not _has_table(conn, "artifacts"):
        return {}
    try:
        live = conn.execute("SELECT COUNT(*) FROM artifacts WHERE project_id=? AND deleted_at IS NULL",
                            (project_id,)).fetchone()[0]
        trashed = conn.execute("SELECT COUNT(*) FROM artifacts WHERE project_id=? AND deleted_at IS NOT NULL",
                               (project_id,)).fetchone()[0]
        versions = conn.execute("SELECT COUNT(*) FROM artifact_versions WHERE artifact_id IN "
                                "(SELECT id FROM artifacts WHERE project_id=?)", (project_id,)).fetchone()[0]
        return {"artifacts": live, "deleted artifacts (still stored)": trashed, "artifact versions": versions}
    finally:
        conn.close()


def _artifact_delete(project_id: str) -> dict[str, int]:
    """Rows, versions, previews, and blob files no other artifact uses (blobs are
    content-addressed and shared across projects)."""
    s = get_settings()
    conn = _artifact_conn()
    if conn is None or not _has_table(conn, "artifacts"):
        return {}
    try:
        ids = [r[0] for r in conn.execute("SELECT id FROM artifacts WHERE project_id=?", (project_id,))]
        if not ids:
            return {}
        marks = ",".join("?" * len(ids))
        blobs = {r[0] for r in conn.execute(
            f"SELECT blob_path FROM artifacts WHERE id IN ({marks}) AND blob_path IS NOT NULL "
            f"UNION SELECT blob_path FROM artifact_versions WHERE artifact_id IN ({marks}) AND blob_path IS NOT NULL",
            ids + ids)}
        with conn:
            versions = conn.execute(f"DELETE FROM artifact_versions WHERE artifact_id IN ({marks})", ids).rowcount
            arts = conn.execute(f"DELETE FROM artifacts WHERE id IN ({marks})", ids).rowcount
        removed_blobs = 0
        blobs_dir = Path(s.data_dir) / "blobs"
        for rel in blobs:
            used = conn.execute("SELECT 1 FROM artifacts WHERE blob_path=? UNION SELECT 1 FROM artifact_versions "
                                "WHERE blob_path=? LIMIT 1", (rel, rel)).fetchone()
            target = (blobs_dir / rel).resolve()
            if not used and target.is_file() and blobs_dir.resolve() in target.parents:
                target.unlink()
                removed_blobs += 1
    finally:
        conn.close()
    for aid in ids:
        try:
            from artifacts import embedder, preview
            task = embedder._pending.pop(aid, None)   # a debounced embed would recreate vectors
            if task is not None:
                task.cancel()
            preview.invalidate_for_artifact(aid)
        except Exception:
            pass
    return {"artifacts": arts, "artifact versions": versions, "artifact files": removed_blobs}


# ── Jobs and schedules ───────────────────────────────────────────────────────

def _jobs(project_id: str) -> list[dict[str, Any]]:
    conn = _connect(get_settings().db_dir / "jobs.db")
    if conn is None or not _has_table(conn, "jobs"):
        return []
    try:
        return [dict(r) for r in conn.execute("SELECT id, status FROM jobs WHERE project_id=?", (project_id,))]
    finally:
        conn.close()


def _schedules(project_id: str) -> list[dict[str, Any]]:
    try:
        from tasks.scheduler import list_jobs
        return [j for j in list_jobs() if j.get("project_id") == project_id]
    except Exception:
        return []


# ── Files, vault, skill state ────────────────────────────────────────────────

def _project_dir(project_id: str) -> Path:
    return get_settings().projects_dir / project_id


def _file_count(project_id: str) -> int:
    d = _project_dir(project_id)
    return sum(1 for p in d.rglob("*") if p.is_file()) if d.is_dir() else 0


def _vault_cleanup(project_id: str) -> int:
    n = 0
    try:
        from secrets.vault import get_vault
        vault = get_vault()
        if vault.delete_secret(f"skill_discovery_{project_id}"):
            n += 1
        from messaging.channel_store import get_channel_store
        store = get_channel_store()
        mappings = store._load_mappings()
        keep = {k: v for k, v in mappings.items() if (v or {}).get("project_id") != project_id}
        if len(keep) != len(mappings):
            store._save_mappings(keep)
            n += len(mappings) - len(keep)
        if vault.get_secret("messaging_default_project") == project_id:
            vault.delete_secret("messaging_default_project")
            n += 1
    except Exception as e:
        logger.warning("Project delete: vault cleanup incomplete: %s", e)
    return n


def _skill_state_cleanup(project_id: str) -> None:
    try:
        from skills.registry import get_skill_registry
        reg = get_skill_registry()
        changed = False
        for skill in reg._skills.values():
            if project_id in (skill.disabled_projects or []):
                skill.disabled_projects = [p for p in skill.disabled_projects if p != project_id]
                changed = True
        if changed:
            reg._save_skill_state()
    except Exception as e:
        logger.warning("Project delete: skill state cleanup skipped: %s", e)


# ── Public API ───────────────────────────────────────────────────────────────

def inventory(project_id: str) -> dict[str, int]:
    """What deleting ``project_id`` would remove: label -> count (zeros left out)."""
    counts: dict[str, int] = {}

    def add(label: str, n: int) -> None:
        if n:
            counts[label] = counts.get(label, 0) + n
    for db, table, label in SQL_STORES:
        add(label, _sql_count(db, table, project_id))
    for label, n in _artifact_count(project_id).items():
        add(label, n)
    for label, n in _vector_count(project_id).items():
        add(label, n)
    add("files (workspace, notes, personality)", _file_count(project_id))
    jobs = _jobs(project_id)
    add("background jobs", len(jobs))
    add("scheduled tasks", len(_schedules(project_id)))
    return counts


def running_jobs(project_id: str) -> list[str]:
    return [j["id"] for j in _jobs(project_id) if j["status"] == "running"]


def purge(project_id: str) -> dict[str, int]:
    """Remove everything ``project_id`` owns except its projects.json entry (the
    caller removes that last). Raises ProjectBusy while a job is running."""
    busy = running_jobs(project_id)
    if busy:
        raise ProjectBusy(f"{len(busy)} job(s) still running in this project - cancel them first")
    removed: dict[str, int] = {}

    def add(label: str, n: int) -> None:
        if n:
            removed[label] = removed.get(label, 0) + n

    # stop new work first
    for job in _schedules(project_id):
        try:
            from tasks.scheduler import cancel_job
            if cancel_job(job["id"]):
                add("scheduled tasks", 1)
        except Exception as e:
            logger.warning("Project delete: schedule %s not cancelled: %s", job.get("id"), e)
    try:
        from jobs.store import get_store as get_jobs
        js = get_jobs()
        for job in _jobs(project_id):
            if job["status"] == "queued":
                js.cancel(job["id"])
    except Exception as e:
        logger.warning("Project delete: queued jobs not cancelled: %s", e)
    add("background jobs", _sql_delete("jobs.db", "jobs", project_id))

    for db, table, label in SQL_STORES:
        add(label, _sql_delete(db, table, project_id))
    for label, n in _artifact_delete(project_id).items():
        add(label, n)
    for label, n in _vector_delete(project_id).items():
        add(label, n)

    d = _project_dir(project_id)
    if d.is_dir():
        add("files (workspace, notes, personality)", _file_count(project_id))
        shutil.rmtree(d)
    add("settings entries", _vault_cleanup(project_id))
    _skill_state_cleanup(project_id)
    logger.info("Project %s purged: %s", project_id, removed)
    return removed
