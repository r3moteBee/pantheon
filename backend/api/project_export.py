"""Project exporter — package all or selected project data as a portable .zip archive.

Supports selective export of:
  - metadata: Project config and settings
  - memory: Episodic (conversations, messages, notes, task logs), semantic, graph
  - files: Workspace files, personality files, archival notes
  - tasks: Scheduled task definitions

The archive includes a manifest with version, format, and checksum info so the
importer can validate integrity and compatibility.
"""
from __future__ import annotations

import io
import json
import logging
import hashlib
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

EXPORT_FORMAT_VERSION = "1.0"
EXPORT_MAGIC = "pantheon-project-export"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ── Data collectors ──────────────────────────────────────────────────────────

def _collect_metadata(project_id: str) -> dict[str, Any]:
    """Collect project metadata from projects.json."""
    meta_path = settings.db_dir / "projects.json"
    if not meta_path.exists():
        return {}
    projects = json.loads(meta_path.read_text())
    return projects.get(project_id, {})


def _resolve_episodic_db_path() -> str:
    """Resolve the actual episodic DB path used at runtime.

    EpisodicMemory and GraphMemory use their own default paths which may
    differ from settings.episodic_db_path. We try the settings path first,
    then fall back to the class default to match runtime behaviour.
    """
    import sqlite3
    from pathlib import Path as _P

    candidates = [
        str(settings.episodic_db_path),       # /app/data/db/episodic.db
        "data/episodic.db",                    # EpisodicMemory class default (relative)
        str(settings.data_dir / "episodic.db"),  # alternate flat layout
    ]
    for path in candidates:
        p = _P(path)
        if p.exists():
            try:
                conn = sqlite3.connect(path)
                conn.execute("SELECT count(*) FROM messages")
                conn.close()
                logger.debug("Episodic DB resolved to: %s", path)
                return path
            except Exception:
                continue
    # Fall back to settings path even if it doesn't exist yet
    return str(settings.episodic_db_path)


def _resolve_graph_db_path() -> str:
    """Resolve the actual graph DB path used at runtime."""
    import sqlite3
    from pathlib import Path as _P

    candidates = [
        str(settings.graph_db_path),           # /app/data/db/graph.db
        "data/graph.db",                       # GraphMemory class default (relative)
        str(settings.data_dir / "graph.db"),   # alternate flat layout
    ]
    for path in candidates:
        p = _P(path)
        if p.exists():
            try:
                conn = sqlite3.connect(path)
                conn.execute("SELECT count(*) FROM graph_nodes")
                conn.close()
                logger.debug("Graph DB resolved to: %s", path)
                return path
            except Exception:
                continue
    return str(settings.graph_db_path)


def _collect_episodic(project_id: str) -> dict[str, Any]:
    """Export episodic memory rows (conversations, messages, task_logs, notes)."""
    import sqlite3
    db_path = _resolve_episodic_db_path()
    result = {"conversations": [], "messages": [], "task_logs": [], "memory_notes": []}

    logger.info("Exporting episodic memory from: %s (project=%s)", db_path, project_id)
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        for row in conn.execute(
            "SELECT * FROM conversations WHERE project_id = ?", (project_id,)
        ).fetchall():
            result["conversations"].append(dict(row))

        for row in conn.execute(
            "SELECT * FROM messages WHERE project_id = ?", (project_id,)
        ).fetchall():
            result["messages"].append(dict(row))

        for row in conn.execute(
            "SELECT * FROM task_logs WHERE project_id = ?", (project_id,)
        ).fetchall():
            result["task_logs"].append(dict(row))

        for row in conn.execute(
            "SELECT * FROM memory_notes WHERE project_id = ?", (project_id,)
        ).fetchall():
            result["memory_notes"].append(dict(row))

        conn.close()
        logger.info(
            "Episodic export: %d convos, %d msgs, %d logs, %d notes",
            len(result["conversations"]), len(result["messages"]),
            len(result["task_logs"]), len(result["memory_notes"]),
        )
    except Exception as e:
        logger.warning("Failed to collect episodic data from %s: %s", db_path, e)

    return result


def _collect_graph(project_id: str) -> dict[str, Any]:
    """Export graph memory nodes and edges."""
    import sqlite3
    db_path = _resolve_graph_db_path()
    result = {"nodes": [], "edges": []}

    logger.info("Exporting graph memory from: %s (project=%s)", db_path, project_id)
    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        for row in conn.execute(
            "SELECT * FROM graph_nodes WHERE project_id = ?", (project_id,)
        ).fetchall():
            result["nodes"].append(dict(row))

        for row in conn.execute(
            "SELECT * FROM graph_edges WHERE project_id = ?", (project_id,)
        ).fetchall():
            result["edges"].append(dict(row))

        conn.close()
        logger.info(
            "Graph export: %d nodes, %d edges",
            len(result["nodes"]), len(result["edges"]),
        )
    except Exception as e:
        logger.warning("Failed to collect graph data from %s: %s", db_path, e)

    return result


def _collect_semantic(project_id: str) -> list[dict[str, Any]]:
    """Export semantic memory documents from ChromaDB.

    Uses multiple fetch strategies to work around ChromaDB version
    differences (offset support, HTTP vs persistent client quirks).
    Embeddings are skipped to keep exports small and fast.
    """
    items = []
    errors: list[str] = []

    try:
        from memory.semantic import SemanticMemory
        sem = SemanticMemory(project_id=project_id)
        collection = sem._get_collection()
        total = collection.count()
        logger.info(
            "Semantic export: collection=%s, count=%d, project=%s",
            sem.collection_name, total, project_id,
        )
        if total == 0:
            _collect_semantic._last_errors = []  # type: ignore[attr-defined]
            return items

        # Strategy 1: Single .get() for everything (works for most cases)
        try:
            logger.info("Semantic export: trying single get (limit=%d)...", total)
            results = collection.get(
                include=["documents", "metadatas"],
                limit=total,
            )
            if results and results.get("ids"):
                for i, doc_id in enumerate(results["ids"]):
                    entry: dict[str, Any] = {"id": doc_id}
                    if results.get("documents") and i < len(results["documents"]):
                        entry["document"] = results["documents"][i]
                    if results.get("metadatas") and i < len(results["metadatas"]):
                        entry["metadata"] = results["metadatas"][i]
                    items.append(entry)
                logger.info("Semantic export: strategy 1 got %d items", len(items))
        except Exception as e1:
            logger.warning("Semantic export: single get failed: %s", e1)
            errors.append(f"strategy1: {e1}")

            # Strategy 2: Get all IDs first, then fetch by ID in batches
            try:
                logger.info("Semantic export: trying ID-based batch fetch...")
                id_results = collection.get(include=[])
                all_ids = id_results.get("ids", []) if id_results else []
                logger.info("Semantic export: got %d IDs", len(all_ids))

                batch_size = 50
                for batch_start in range(0, len(all_ids), batch_size):
                    batch_ids = all_ids[batch_start : batch_start + batch_size]
                    try:
                        batch_results = collection.get(
                            ids=batch_ids,
                            include=["documents", "metadatas"],
                        )
                        if batch_results and batch_results.get("ids"):
                            for i, doc_id in enumerate(batch_results["ids"]):
                                entry = {"id": doc_id}
                                if batch_results.get("documents") and i < len(batch_results["documents"]):
                                    entry["document"] = batch_results["documents"][i]
                                if batch_results.get("metadatas") and i < len(batch_results["metadatas"]):
                                    entry["metadata"] = batch_results["metadatas"][i]
                                items.append(entry)
                    except Exception as eb:
                        msg = f"Batch {batch_start} failed: {eb}"
                        logger.error("Semantic export: %s", msg)
                        errors.append(msg)

                logger.info("Semantic export: strategy 2 got %d items", len(items))
            except Exception as e2:
                logger.error("Semantic export: ID-based fetch also failed: %s", e2, exc_info=True)
                errors.append(f"strategy2: {e2}")

        logger.info("Semantic export: %d documents collected (%d errors)", len(items), len(errors))
    except Exception as e:
        logger.error("Failed to collect semantic data: %s", e, exc_info=True)
        errors.append(str(e))

    _collect_semantic._last_errors = errors  # type: ignore[attr-defined]
    return items


def _collect_tasks(project_id: str) -> list[dict[str, Any]]:
    """Export scheduled task definitions for this project."""
    tasks = []
    try:
        from tasks.scheduler import list_jobs
        all_jobs = list_jobs()
        tasks = [j for j in all_jobs if j.get("project_id") == project_id]
    except Exception as e:
        logger.warning("Failed to collect tasks: %s", e)
    return tasks


def _add_directory_to_zip(
    zf: zipfile.ZipFile,
    src_dir: Path,
    arc_prefix: str,
) -> int:
    """Recursively add a directory to the zip. Returns file count."""
    count = 0
    if not src_dir.is_dir():
        return 0
    for fp in sorted(src_dir.rglob("*")):
        if fp.is_file():
            # Skip system files
            if fp.name in (".DS_Store", "Thumbs.db", "__pycache__"):
                continue
            arcname = f"{arc_prefix}/{fp.relative_to(src_dir)}"
            zf.write(fp, arcname)
            count += 1
    return count


# ── Main export function ─────────────────────────────────────────────────────

def export_project(
    project_id: str,
    components: list[str] | None = None,
) -> bytes:
    """Export a project as a .zip archive.

    Args:
        project_id: The project to export.
        components: List of components to include. If None, exports all.
            Valid values: "metadata", "memory", "files", "tasks"
            "memory" includes episodic, semantic, and graph tiers.

    Returns:
        The zip archive as bytes.

    Raises:
        FileNotFoundError: If the project doesn't exist.
    """
    valid_components = {"metadata", "memory", "files", "tasks"}
    if components is None:
        components = list(valid_components)
    else:
        unknown = set(components) - valid_components
        if unknown:
            raise ValueError(f"Unknown export components: {unknown}")

    # Verify project exists
    meta = _collect_metadata(project_id)
    if not meta and project_id != "default":
        raise FileNotFoundError(f"Project not found: {project_id}")

    buf = io.BytesIO()
    manifest = {
        "magic": EXPORT_MAGIC,
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": _now_iso(),
        "project_id": project_id,
        "project_name": meta.get("name", project_id),
        "components": components,
        "checksums": {},
        "stats": {},
    }

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # ── Metadata ─────────────────────────────────────────────────
        if "metadata" in components:
            meta_json = json.dumps(meta, indent=2, default=str).encode()
            zf.writestr("metadata/project.json", meta_json)
            manifest["checksums"]["metadata"] = _hash_bytes(meta_json)

        # ── Memory ───────────────────────────────────────────────────
        if "memory" in components:
            # Episodic
            episodic = _collect_episodic(project_id)
            episodic_json = json.dumps(episodic, indent=2, default=str).encode()
            zf.writestr("memory/episodic.json", episodic_json)
            manifest["checksums"]["episodic"] = _hash_bytes(episodic_json)
            manifest["stats"]["conversations"] = len(episodic["conversations"])
            manifest["stats"]["messages"] = len(episodic["messages"])
            manifest["stats"]["task_logs"] = len(episodic["task_logs"])
            manifest["stats"]["memory_notes"] = len(episodic["memory_notes"])

            # Graph
            graph = _collect_graph(project_id)
            graph_json = json.dumps(graph, indent=2, default=str).encode()
            zf.writestr("memory/graph.json", graph_json)
            manifest["checksums"]["graph"] = _hash_bytes(graph_json)
            manifest["stats"]["graph_nodes"] = len(graph["nodes"])
            manifest["stats"]["graph_edges"] = len(graph["edges"])

            # Semantic
            semantic = _collect_semantic(project_id)
            semantic_json = json.dumps(semantic, indent=2, default=str).encode()
            zf.writestr("memory/semantic.json", semantic_json)
            manifest["checksums"]["semantic"] = _hash_bytes(semantic_json)
            manifest["stats"]["semantic_memories"] = len(semantic)
            # Surface any errors that occurred during semantic collection
            sem_errors = getattr(_collect_semantic, '_last_errors', [])
            if sem_errors:
                manifest["warnings"] = manifest.get("warnings", []) + [
                    f"semantic: {e}" for e in sem_errors
                ]

        # ── Files ────────────────────────────────────────────────────
        if "files" in components:
            project_dir = settings.projects_dir / project_id
            file_count = 0
            for subdir in ("workspace", "personality", "notes"):
                src = project_dir / subdir
                file_count += _add_directory_to_zip(zf, src, f"files/{subdir}")

            # Include project_summary.md if it exists
            summary_path = project_dir / "project_summary.md"
            if summary_path.is_file():
                zf.write(summary_path, "files/project_summary.md")
                file_count += 1

            manifest["stats"]["files"] = file_count

        # ── Tasks ────────────────────────────────────────────────────
        if "tasks" in components:
            tasks = _collect_tasks(project_id)
            tasks_json = json.dumps(tasks, indent=2, default=str).encode()
            zf.writestr("tasks/tasks.json", tasks_json)
            manifest["checksums"]["tasks"] = _hash_bytes(tasks_json)
            manifest["stats"]["tasks"] = len(tasks)

        # ── Manifest (always included, written last) ─────────────────
        manifest_json = json.dumps(manifest, indent=2).encode()
        zf.writestr("manifest.json", manifest_json)

    logger.info(
        "Project exported: %s (%d bytes, components=%s)",
        project_id, buf.tell(), components,
    )
    return buf.getvalue()
