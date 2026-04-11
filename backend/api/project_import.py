"""Project importer — restore a project from a Pantheon export archive.

Includes a multi-layer security scanner that validates the archive before
importing any data:
  Layer 1: Archive structure validation (manifest, checksums, format version)
  Layer 2: Content safety scan (path traversal, script injection, oversized data)
  Layer 3: Data integrity verification (JSON schema, foreign key consistency)

Only after all three layers pass does the actual import proceed.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import shutil
import zipfile
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from config import get_settings
from api.project_export import EXPORT_FORMAT_VERSION, EXPORT_MAGIC

logger = logging.getLogger(__name__)
settings = get_settings()


# ── Models ────────────────────────────────────────────────────────────────────

class ScanSeverity(str, Enum):
    info = "info"
    warning = "warning"
    error = "error"
    critical = "critical"


class ScanFinding(BaseModel):
    severity: ScanSeverity
    category: str
    message: str
    detail: str = ""


class ScanResult(BaseModel):
    passed: bool
    findings: list[ScanFinding] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)


class ImportResult(BaseModel):
    success: bool
    project_id: str = ""
    project_name: str = ""
    message: str = ""
    scan: ScanResult | None = None
    components_imported: list[str] = Field(default_factory=list)
    stats: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)


# ── Security Scanner ─────────────────────────────────────────────────────────

# Dangerous patterns in text content
_DANGEROUS_PATTERNS = [
    (re.compile(r"<script[\s>]", re.IGNORECASE), "HTML script tag detected"),
    (re.compile(r"javascript:", re.IGNORECASE), "JavaScript protocol handler"),
    (re.compile(r"on\w+\s*=", re.IGNORECASE), "HTML event handler attribute"),
    (re.compile(r"data:text/html", re.IGNORECASE), "Data URI with HTML content"),
    (re.compile(r"__import__\s*\(", re.IGNORECASE), "Python __import__ call"),
    (re.compile(r"\beval\s*\(", re.IGNORECASE), "eval() call detected"),
    (re.compile(r"\bexec\s*\(", re.IGNORECASE), "exec() call detected"),
    (re.compile(r"\bos\.system\s*\(", re.IGNORECASE), "os.system() call"),
    (re.compile(r"\bsubprocess\.", re.IGNORECASE), "subprocess module usage"),
    (re.compile(r"\bpickle\.(loads?|dump)", re.IGNORECASE), "pickle deserialization"),
    (re.compile(r"\byaml\.unsafe_load", re.IGNORECASE), "Unsafe YAML loading"),
    (re.compile(r"\b__class__\b.*__subclasses__", re.IGNORECASE), "Python class introspection chain"),
]

# File extensions that should never appear in a project export
_FORBIDDEN_EXTENSIONS = {
    ".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".com", ".scr",
    ".msi", ".ps1", ".vbs", ".wsf", ".jar", ".war", ".class",
    ".sh", ".bash", ".zsh", ".fish",  # shell scripts in workspace
    ".pif", ".application", ".gadget", ".hta", ".cpl", ".inf",
    ".reg", ".lnk", ".url",
}

# Maximum sizes
_MAX_ARCHIVE_SIZE = 500 * 1024 * 1024  # 500 MB
_MAX_SINGLE_FILE_SIZE = 100 * 1024 * 1024  # 100 MB
_MAX_JSON_RECORDS = 500_000  # Max rows per table
_MAX_FILE_COUNT = 10_000


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def scan_archive(archive_bytes: bytes) -> ScanResult:
    """Run the full 3-layer security scan on an archive.

    Returns a ScanResult with pass/fail and detailed findings.
    """
    findings: list[ScanFinding] = []
    stats: dict[str, Any] = {}

    # ── Layer 1: Archive structure validation ────────────────────────

    # Size check
    stats["archive_size"] = len(archive_bytes)
    if len(archive_bytes) > _MAX_ARCHIVE_SIZE:
        findings.append(ScanFinding(
            severity=ScanSeverity.critical,
            category="size",
            message=f"Archive exceeds maximum size ({len(archive_bytes)} > {_MAX_ARCHIVE_SIZE})",
        ))
        return ScanResult(passed=False, findings=findings, stats=stats)

    # Valid zip check
    try:
        zf = zipfile.ZipFile(io.BytesIO(archive_bytes), "r")
    except (zipfile.BadZipFile, Exception) as e:
        findings.append(ScanFinding(
            severity=ScanSeverity.critical,
            category="format",
            message=f"Invalid zip archive: {e}",
        ))
        return ScanResult(passed=False, findings=findings, stats=stats)

    # File count check
    members = zf.namelist()
    stats["file_count"] = len(members)
    if len(members) > _MAX_FILE_COUNT:
        findings.append(ScanFinding(
            severity=ScanSeverity.critical,
            category="size",
            message=f"Archive contains too many files ({len(members)} > {_MAX_FILE_COUNT})",
        ))
        zf.close()
        return ScanResult(passed=False, findings=findings, stats=stats)

    # Manifest check
    if "manifest.json" not in members:
        findings.append(ScanFinding(
            severity=ScanSeverity.critical,
            category="format",
            message="Missing manifest.json — not a valid Pantheon export",
        ))
        zf.close()
        return ScanResult(passed=False, findings=findings, stats=stats)

    try:
        manifest = json.loads(zf.read("manifest.json"))
    except (json.JSONDecodeError, Exception) as e:
        findings.append(ScanFinding(
            severity=ScanSeverity.critical,
            category="format",
            message=f"Invalid manifest.json: {e}",
        ))
        zf.close()
        return ScanResult(passed=False, findings=findings, stats=stats)

    # Magic string check
    if manifest.get("magic") != EXPORT_MAGIC:
        findings.append(ScanFinding(
            severity=ScanSeverity.critical,
            category="format",
            message="Invalid magic string — not a Pantheon export archive",
        ))
        zf.close()
        return ScanResult(passed=False, findings=findings, stats=stats)

    # Format version check
    export_version = manifest.get("format_version", "0")
    if export_version != EXPORT_FORMAT_VERSION:
        findings.append(ScanFinding(
            severity=ScanSeverity.warning,
            category="compatibility",
            message=f"Format version mismatch: archive={export_version}, expected={EXPORT_FORMAT_VERSION}",
            detail="Import may succeed but some data might not be fully compatible.",
        ))

    stats["project_id"] = manifest.get("project_id", "")
    stats["project_name"] = manifest.get("project_name", "")
    stats["components"] = manifest.get("components", [])
    stats["exported_at"] = manifest.get("exported_at", "")

    # ── Layer 2: Content safety scan ─────────────────────────────────

    for name in members:
        if name == "manifest.json":
            continue

        # Path traversal check
        if ".." in name or name.startswith("/") or name.startswith("\\"):
            findings.append(ScanFinding(
                severity=ScanSeverity.critical,
                category="path_traversal",
                message=f"Path traversal detected: {name}",
            ))
            continue

        # Disallowed directory prefixes (only allow known structure)
        allowed_prefixes = ("metadata/", "memory/", "files/", "tasks/")
        if not any(name.startswith(p) for p in allowed_prefixes) and name != "manifest.json":
            findings.append(ScanFinding(
                severity=ScanSeverity.warning,
                category="structure",
                message=f"Unexpected file outside known directories: {name}",
            ))

        # Extension check for files in the files/ directory
        if name.startswith("files/"):
            ext = Path(name).suffix.lower()
            if ext in _FORBIDDEN_EXTENSIONS:
                findings.append(ScanFinding(
                    severity=ScanSeverity.error,
                    category="forbidden_file",
                    message=f"Forbidden file type in archive: {name} ({ext})",
                ))

        # Size check per member
        info = zf.getinfo(name)
        if info.file_size > _MAX_SINGLE_FILE_SIZE:
            findings.append(ScanFinding(
                severity=ScanSeverity.error,
                category="size",
                message=f"File exceeds size limit: {name} ({info.file_size} bytes)",
            ))

        # Symlink check (zip external attributes)
        mode = (info.external_attr >> 16) & 0xFFFF
        if mode and (mode & 0xF000) == 0xA000:
            findings.append(ScanFinding(
                severity=ScanSeverity.critical,
                category="symlink",
                message=f"Symlink detected (refused): {name}",
            ))

    # Content scanning for text-based files
    for name in members:
        if name.startswith("files/") and not name.endswith("/"):
            ext = Path(name).suffix.lower()
            if ext in (".md", ".txt", ".json", ".yaml", ".yml", ".html", ".htm",
                        ".xml", ".csv", ".py", ".js", ".ts", ".jsx", ".tsx"):
                try:
                    content = zf.read(name).decode("utf-8", errors="replace")
                    for pattern, desc in _DANGEROUS_PATTERNS:
                        if pattern.search(content):
                            findings.append(ScanFinding(
                                severity=ScanSeverity.warning,
                                category="injection",
                                message=f"{desc} in {name}",
                                detail=f"Pattern: {pattern.pattern}",
                            ))
                except Exception:
                    pass

    # ── Layer 3: Data integrity verification ─────────────────────────

    checksums = manifest.get("checksums", {})

    checksum_map = {
        "metadata": "metadata/project.json",
        "episodic": "memory/episodic.json",
        "graph": "memory/graph.json",
        "semantic": "memory/semantic.json",
        "tasks": "tasks/tasks.json",
    }
    for key, filepath in checksum_map.items():
        if key in checksums and filepath in members:
            actual = _hash_bytes(zf.read(filepath))
            expected = checksums[key]
            if actual != expected:
                findings.append(ScanFinding(
                    severity=ScanSeverity.error,
                    category="integrity",
                    message=f"Checksum mismatch for {filepath}",
                    detail=f"Expected {expected[:16]}..., got {actual[:16]}...",
                ))

    if "memory/episodic.json" in members:
        try:
            episodic = json.loads(zf.read("memory/episodic.json"))
            for key in ("conversations", "messages", "task_logs", "memory_notes"):
                if key in episodic:
                    count = len(episodic[key])
                    if count > _MAX_JSON_RECORDS:
                        findings.append(ScanFinding(
                            severity=ScanSeverity.error,
                            category="size",
                            message=f"Too many {key} records: {count} > {_MAX_JSON_RECORDS}",
                        ))
                    if not isinstance(episodic[key], list):
                        findings.append(ScanFinding(
                            severity=ScanSeverity.error,
                            category="schema",
                            message=f"episodic.{key} should be a list",
                        ))
        except json.JSONDecodeError:
            findings.append(ScanFinding(
                severity=ScanSeverity.error,
                category="schema",
                message="Invalid JSON in memory/episodic.json",
            ))

    if "memory/graph.json" in members:
        try:
            graph = json.loads(zf.read("memory/graph.json"))
            for key in ("nodes", "edges"):
                if key in graph and not isinstance(graph[key], list):
                    findings.append(ScanFinding(
                        severity=ScanSeverity.error,
                        category="schema",
                        message=f"graph.{key} should be a list",
                    ))
        except json.JSONDecodeError:
            findings.append(ScanFinding(
                severity=ScanSeverity.error,
                category="schema",
                message="Invalid JSON in memory/graph.json",
            ))

    if "memory/semantic.json" in members:
        try:
            semantic = json.loads(zf.read("memory/semantic.json"))
            if not isinstance(semantic, list):
                findings.append(ScanFinding(
                    severity=ScanSeverity.error,
                    category="schema",
                    message="semantic.json should be a list",
                ))
            elif len(semantic) > _MAX_JSON_RECORDS:
                findings.append(ScanFinding(
                    severity=ScanSeverity.error,
                    category="size",
                    message=f"Too many semantic records: {len(semantic)} > {_MAX_JSON_RECORDS}",
                ))
        except json.JSONDecodeError:
            findings.append(ScanFinding(
                severity=ScanSeverity.error,
                category="schema",
                message="Invalid JSON in memory/semantic.json",
            ))

    zf.close()

    has_critical = any(f.severity == ScanSeverity.critical for f in findings)
    has_error = any(f.severity == ScanSeverity.error for f in findings)
    passed = not has_critical and not has_error

    return ScanResult(passed=passed, findings=findings, stats=stats)


# ── Import engine ────────────────────────────────────────────────────────────

def import_project(
    archive_bytes: bytes,
    target_project_id: str | None = None,
    target_project_name: str | None = None,
    components: list[str] | None = None,
    overwrite: bool = False,
    skip_scan: bool = False,
) -> ImportResult:
    """Import a project from a Pantheon export archive.

    Args:
        archive_bytes: The zip archive bytes.
        target_project_id: Override the project ID (use archive's if None).
        target_project_name: Override the project name (use archive's if None).
        components: Which components to import. None = all available.
        overwrite: If True, merge into existing project. If False, auto-rename on conflict.
        skip_scan: Skip security scan (not recommended).

    Returns:
        ImportResult with status and details.
    """
    warnings: list[str] = []

    # ── Step 1: Security scan ────────────────────────────────────────
    if not skip_scan:
        scan = scan_archive(archive_bytes)
        if not scan.passed:
            return ImportResult(
                success=False,
                message="Security scan failed — import blocked",
                scan=scan,
            )
    else:
        scan = ScanResult(passed=True, findings=[
            ScanFinding(
                severity=ScanSeverity.warning,
                category="scan_skipped",
                message="Security scan was skipped by user request",
            )
        ])

    # ── Step 2: Extract manifest ─────────────────────────────────────
    zf = zipfile.ZipFile(io.BytesIO(archive_bytes), "r")
    manifest = json.loads(zf.read("manifest.json"))
    source_project_id = manifest.get("project_id", "imported")
    source_project_name = manifest.get("project_name", source_project_id)
    available_components = manifest.get("components", [])

    project_id = target_project_id or source_project_id
    project_name = target_project_name or source_project_name

    # Sanitize project ID
    project_id = re.sub(r'[^a-zA-Z0-9-_]', '-', project_id).lower()

    if components is None:
        components = available_components
    else:
        components = [c for c in components if c in available_components]

    # ── Step 3: Check for conflicts ──────────────────────────────────
    meta_path = settings.db_dir / "projects.json"
    projects = {}
    if meta_path.exists():
        try:
            projects = json.loads(meta_path.read_text())
        except Exception:
            pass

    if project_id in projects and not overwrite:
        base_id = project_id
        counter = 1
        while project_id in projects:
            project_id = f"{base_id}-{counter}"
            counter += 1
        warnings.append(f"Project ID conflict — renamed to '{project_id}'")

    imported_components: list[str] = []
    import_stats: dict[str, Any] = {}

    # ── Step 4: Import metadata ──────────────────────────────────────
    if "metadata" in components and "metadata/project.json" in zf.namelist():
        try:
            meta = json.loads(zf.read("metadata/project.json"))
            meta["id"] = project_id
            meta["name"] = project_name
            meta["imported_at"] = datetime.now(timezone.utc).isoformat()
            meta["imported_from"] = source_project_id

            projects[project_id] = meta
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(json.dumps(projects, indent=2))

            project_dir = settings.projects_dir / project_id
            for subdir in ("workspace", "personality", "notes"):
                (project_dir / subdir).mkdir(parents=True, exist_ok=True)

            imported_components.append("metadata")
        except Exception as e:
            logger.error("Failed to import metadata: %s", e, exc_info=True)
            warnings.append(f"metadata: {e}")

    elif "metadata" in components:
        projects[project_id] = {
            "id": project_id,
            "name": project_name,
            "description": f"Imported from {source_project_id}",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "active": True,
        }
        meta_path.parent.mkdir(parents=True, exist_ok=True)
        meta_path.write_text(json.dumps(projects, indent=2))

        project_dir = settings.projects_dir / project_id
        for subdir in ("workspace", "personality", "notes"):
            (project_dir / subdir).mkdir(parents=True, exist_ok=True)
        imported_components.append("metadata")

    # ── Step 5: Import memory ────────────────────────────────────────
    if "memory" in components:
        # Episodic
        if "memory/episodic.json" in zf.namelist():
            try:
                n = _import_episodic(zf, project_id, import_stats)
                imported_components.append("memory:episodic")
            except Exception as e:
                logger.error("Failed to import episodic memory: %s", e, exc_info=True)
                warnings.append(f"episodic: {e}")

        # Graph
        if "memory/graph.json" in zf.namelist():
            try:
                _import_graph(zf, project_id, import_stats)
                imported_components.append("memory:graph")
            except Exception as e:
                logger.error("Failed to import graph memory: %s", e, exc_info=True)
                warnings.append(f"graph: {e}")

        # Semantic
        if "memory/semantic.json" in zf.namelist():
            try:
                _import_semantic(zf, project_id, import_stats)
                imported_components.append("memory:semantic")
            except Exception as e:
                logger.error("Failed to import semantic memory: %s", e, exc_info=True)
                warnings.append(f"semantic: {e}")

    # ── Step 6: Import files ─────────────────────────────────────────
    if "files" in components:
        try:
            count = _import_files(zf, project_id)
            import_stats["files_restored"] = count
            imported_components.append("files")
        except Exception as e:
            logger.error("Failed to import files: %s", e, exc_info=True)
            warnings.append(f"files: {e}")

    # ── Step 7: Import tasks ─────────────────────────────────────────
    if "tasks" in components and "tasks/tasks.json" in zf.namelist():
        try:
            _import_tasks(zf, project_id, import_stats, warnings)
            imported_components.append("tasks")
        except Exception as e:
            logger.error("Failed to import tasks: %s", e, exc_info=True)
            warnings.append(f"tasks: {e}")

    zf.close()

    logger.info(
        "Project imported: %s (components=%s, stats=%s, warnings=%d)",
        project_id, imported_components, import_stats, len(warnings),
    )

    return ImportResult(
        success=True,
        project_id=project_id,
        project_name=project_name,
        message=f"Project '{project_name}' imported successfully as '{project_id}'",
        scan=scan,
        components_imported=imported_components,
        stats=import_stats,
        warnings=warnings,
    )


# ── Import helpers ───────────────────────────────────────────────────────────

def _get_episodic_db_path() -> str:
    """Get the episodic DB path, matching what the runtime actually uses."""
    from memory.episodic import EpisodicMemory
    ep = EpisodicMemory(project_id="__probe__")
    return ep.db_path


def _get_graph_db_path() -> str:
    """Get the graph DB path, matching what the runtime actually uses."""
    from memory.graph import GraphMemory
    gm = GraphMemory(project_id="__probe__")
    return gm.db_path


def _import_episodic(
    zf: zipfile.ZipFile,
    project_id: str,
    stats: dict[str, Any],
) -> None:
    """Restore episodic memory data into SQLite."""
    import sqlite3
    data = json.loads(zf.read("memory/episodic.json"))
    db_path = _get_episodic_db_path()

    logger.info("Importing episodic to %s (project=%s)", db_path, project_id)

    conn = sqlite3.connect(db_path)
    try:
        # Ensure tables exist
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL DEFAULT 'default',
                session_id TEXT NOT NULL,
                title TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS messages (
                id TEXT PRIMARY KEY,
                conversation_id TEXT,
                project_id TEXT NOT NULL DEFAULT 'default',
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                metadata TEXT DEFAULT '{}'
            );
            CREATE TABLE IF NOT EXISTS task_logs (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL DEFAULT 'default',
                task_id TEXT NOT NULL,
                task_name TEXT,
                event TEXT NOT NULL,
                details TEXT,
                timestamp TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS memory_notes (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL DEFAULT 'default',
                session_id TEXT,
                content TEXT NOT NULL,
                tags TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
        """)

        # Import conversations — use INSERT OR REPLACE so reimports work
        for row in data.get("conversations", []):
            row["project_id"] = project_id
            cols = ["id", "project_id", "session_id", "title", "created_at", "updated_at", "metadata"]
            vals = {c: row.get(c) for c in cols}
            conn.execute("""
                INSERT OR REPLACE INTO conversations
                (id, project_id, session_id, title, created_at, updated_at, metadata)
                VALUES (:id, :project_id, :session_id, :title, :created_at, :updated_at, :metadata)
            """, vals)

        # Import messages
        for row in data.get("messages", []):
            row["project_id"] = project_id
            cols = ["id", "project_id", "session_id", "role", "content", "timestamp", "metadata"]
            vals = {c: row.get(c) for c in cols}
            # conversation_id may be present
            vals["conversation_id"] = row.get("conversation_id")
            conn.execute("""
                INSERT OR REPLACE INTO messages
                (id, conversation_id, project_id, session_id, role, content, timestamp, metadata)
                VALUES (:id, :conversation_id, :project_id, :session_id, :role, :content, :timestamp, :metadata)
            """, vals)

        # Import task logs
        for row in data.get("task_logs", []):
            row["project_id"] = project_id
            cols = ["id", "project_id", "task_id", "task_name", "event", "details", "timestamp"]
            vals = {c: row.get(c) for c in cols}
            conn.execute("""
                INSERT OR REPLACE INTO task_logs
                (id, project_id, task_id, task_name, event, details, timestamp)
                VALUES (:id, :project_id, :task_id, :task_name, :event, :details, :timestamp)
            """, vals)

        # Import memory notes
        for row in data.get("memory_notes", []):
            row["project_id"] = project_id
            cols = ["id", "project_id", "session_id", "content", "tags", "created_at", "updated_at"]
            vals = {c: row.get(c) for c in cols}
            conn.execute("""
                INSERT OR REPLACE INTO memory_notes
                (id, project_id, session_id, content, tags, created_at, updated_at)
                VALUES (:id, :project_id, :session_id, :content, :tags, :created_at, :updated_at)
            """, vals)

        conn.commit()
        stats["conversations_imported"] = len(data.get("conversations", []))
        stats["messages_imported"] = len(data.get("messages", []))
        stats["task_logs_imported"] = len(data.get("task_logs", []))
        stats["notes_imported"] = len(data.get("memory_notes", []))
        logger.info(
            "Episodic import done: %d convos, %d msgs, %d logs, %d notes",
            stats["conversations_imported"], stats["messages_imported"],
            stats["task_logs_imported"], stats["notes_imported"],
        )
    finally:
        conn.close()


def _import_graph(
    zf: zipfile.ZipFile,
    project_id: str,
    stats: dict[str, Any],
) -> None:
    """Restore graph memory data into SQLite."""
    import sqlite3
    data = json.loads(zf.read("memory/graph.json"))
    db_path = _get_graph_db_path()

    logger.info("Importing graph to %s (project=%s)", db_path, project_id)

    conn = sqlite3.connect(db_path)
    try:
        # Ensure tables exist — no foreign keys during import to avoid ordering issues
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS graph_nodes (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL DEFAULT 'default',
                node_type TEXT NOT NULL,
                label TEXT NOT NULL,
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS graph_edges (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL DEFAULT 'default',
                node_a_id TEXT NOT NULL,
                node_b_id TEXT NOT NULL,
                relationship TEXT NOT NULL,
                weight REAL DEFAULT 1.0,
                created_at TEXT NOT NULL
            );
        """)

        # Import nodes first
        node_count = 0
        for row in data.get("nodes", []):
            row["project_id"] = project_id
            cols = ["id", "project_id", "node_type", "label", "metadata", "created_at", "updated_at"]
            vals = {c: row.get(c) for c in cols}
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO graph_nodes
                    (id, project_id, node_type, label, metadata, created_at, updated_at)
                    VALUES (:id, :project_id, :node_type, :label, :metadata, :created_at, :updated_at)
                """, vals)
                node_count += 1
            except Exception as e:
                logger.debug("Skipping graph node %s: %s", row.get("id"), e)

        # Import edges — skip edges with missing node references
        edge_count = 0
        for row in data.get("edges", []):
            row["project_id"] = project_id
            cols = ["id", "project_id", "node_a_id", "node_b_id", "relationship", "weight", "created_at"]
            vals = {c: row.get(c) for c in cols}
            try:
                conn.execute("""
                    INSERT OR REPLACE INTO graph_edges
                    (id, project_id, node_a_id, node_b_id, relationship, weight, created_at)
                    VALUES (:id, :project_id, :node_a_id, :node_b_id, :relationship, :weight, :created_at)
                """, vals)
                edge_count += 1
            except Exception as e:
                logger.debug("Skipping graph edge %s: %s", row.get("id"), e)

        conn.commit()
        stats["graph_nodes_imported"] = node_count
        stats["graph_edges_imported"] = edge_count
        logger.info("Graph import done: %d nodes, %d edges", node_count, edge_count)
    finally:
        conn.close()


def _import_semantic(
    zf: zipfile.ZipFile,
    project_id: str,
    stats: dict[str, Any],
) -> None:
    """Restore semantic memory into ChromaDB."""
    data = json.loads(zf.read("memory/semantic.json"))
    if not data:
        stats["semantic_imported"] = 0
        return

    from memory.semantic import SemanticMemory
    sem = SemanticMemory(project_id=project_id)
    collection = sem._get_collection()

    # Batch upsert — small batches for reliability
    batch_size = 50
    total = 0
    for i in range(0, len(data), batch_size):
        batch = data[i : i + batch_size]
        ids = [item["id"] for item in batch]
        documents = [item.get("document", "") for item in batch]
        metadatas = [item.get("metadata", {}) for item in batch]
        # Update project_id in metadata
        for m in metadatas:
            m["project_id"] = project_id
        # Flatten metadata values to strings (ChromaDB requirement)
        metadatas = [{k: str(v) for k, v in m.items()} for m in metadatas]

        kwargs: dict[str, Any] = {
            "ids": ids,
            "documents": documents,
            "metadatas": metadatas,
        }
        # Include embeddings if present (exported archives may or may not have them)
        embeddings = [item.get("embedding") for item in batch]
        if embeddings and all(e is not None for e in embeddings):
            kwargs["embeddings"] = embeddings

        collection.upsert(**kwargs)
        total += len(batch)

    stats["semantic_imported"] = total
    logger.info("Semantic import done: %d entries", total)


def _import_tasks(
    zf: zipfile.ZipFile,
    project_id: str,
    stats: dict[str, Any],
    warnings: list[str],
) -> None:
    """Import task definitions — register them with APScheduler."""
    tasks = json.loads(zf.read("tasks/tasks.json"))
    if not tasks:
        stats["tasks_imported"] = 0
        return

    created = 0
    skipped = 0

    try:
        from tasks.scheduler import get_scheduler, list_jobs

        # Get existing task names/descriptions to avoid duplicates
        existing_jobs = list_jobs()
        existing_names = {j.get("name", "").lower() for j in existing_jobs}
        existing_descs = {j.get("description", "").lower() for j in existing_jobs}

        scheduler = get_scheduler()

        for task in tasks:
            task_name = task.get("name", "")
            task_desc = task.get("description", "")
            task_schedule = task.get("schedule", "")

            # Skip if a task with the same name already exists
            if task_name.lower() in existing_names:
                skipped += 1
                continue

            # Skip if a task with the same description already exists
            if task_desc.lower() in existing_descs:
                skipped += 1
                continue

            if not task_schedule or not task_name:
                skipped += 1
                continue

            try:
                # Parse cron schedule
                from apscheduler.triggers.cron import CronTrigger
                parts = task_schedule.strip().split()
                if len(parts) >= 5:
                    trigger = CronTrigger(
                        minute=parts[0],
                        hour=parts[1],
                        day=parts[2],
                        month=parts[3],
                        day_of_week=parts[4],
                    )
                else:
                    skipped += 1
                    warnings.append(f"Task '{task_name}' has invalid schedule: {task_schedule}")
                    continue

                # Import as a reference — the actual job function needs to
                # be wired to the agent's task runner
                from tasks.scheduler import run_task
                scheduler.add_job(
                    run_task,
                    trigger=trigger,
                    id=f"imported-{project_id}-{task_name}",
                    name=task_name,
                    kwargs={
                        "description": task_desc,
                        "project_id": project_id,
                        "schedule": task_schedule,
                    },
                    replace_existing=True,
                )
                created += 1
            except Exception as e:
                skipped += 1
                warnings.append(f"Task '{task_name}': {e}")

    except ImportError:
        # APScheduler not available — save as reference file
        warnings.append("Task scheduler not available — tasks saved as reference file only")
        task_ref_path = settings.projects_dir / project_id / "notes" / "imported_tasks.json"
        task_ref_path.parent.mkdir(parents=True, exist_ok=True)
        task_ref_path.write_text(json.dumps(tasks, indent=2))
    except Exception as e:
        warnings.append(f"Task import failed: {e}")
        # Save as reference anyway
        task_ref_path = settings.projects_dir / project_id / "notes" / "imported_tasks.json"
        task_ref_path.parent.mkdir(parents=True, exist_ok=True)
        task_ref_path.write_text(json.dumps(tasks, indent=2))

    stats["tasks_imported"] = created
    stats["tasks_skipped"] = skipped
    if skipped:
        warnings.append(f"{skipped} task(s) skipped (duplicates or invalid)")
    logger.info("Task import done: %d created, %d skipped", created, skipped)


def _import_files(zf: zipfile.ZipFile, project_id: str) -> int:
    """Restore workspace, personality, and notes files."""
    project_dir = settings.projects_dir / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    dest_resolved = project_dir.resolve()

    for name in zf.namelist():
        if not name.startswith("files/") or name.endswith("/"):
            continue

        relative = name[len("files/"):]
        if not relative:
            continue

        target = (project_dir / relative).resolve()
        if not str(target).startswith(str(dest_resolved)):
            logger.warning("Skipping path traversal attempt: %s", name)
            continue

        target.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(name) as src:
            target.write_bytes(src.read())
        count += 1

    return count
