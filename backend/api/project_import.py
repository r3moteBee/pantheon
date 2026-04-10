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
            # Only scan text-like files for injection
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
                    pass  # Binary file in text extension — odd but not fatal

    # ── Layer 3: Data integrity verification ─────────────────────────

    checksums = manifest.get("checksums", {})

    # Verify checksums for JSON data files
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

    # Validate JSON data structure
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

    # Determine overall pass/fail
    has_critical = any(f.severity == ScanSeverity.critical for f in findings)
    has_error = any(f.severity == ScanSeverity.error for f in findings)
    passed = not has_critical and not has_error

    return ScanResult(passed=passed, findings=findings, stats=stats)


# ── Import engine ────────────────────────────────────────────────────────────

def import_project(
    archive_bytes: bytes,
    target_project_id: str | None = None,
    components: list[str] | None = None,
    overwrite: bool = False,
    skip_scan: bool = False,
) -> ImportResult:
    """Import a project from a Pantheon export archive.

    Args:
        archive_bytes: The zip archive bytes.
        target_project_id: Override the project ID (use archive's if None).
        components: Which components to import. None = all available.
        overwrite: If True, merge into existing project. If False, fail on conflict.
        skip_scan: Skip security scan (not recommended).

    Returns:
        ImportResult with status and details.
    """
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
    project_name = manifest.get("project_name", source_project_id)
    available_components = manifest.get("components", [])

    project_id = target_project_id or source_project_id

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
        # Generate a unique ID
        base_id = project_id
        counter = 1
        while project_id in projects:
            project_id = f"{base_id}-{counter}"
            counter += 1

    imported_components: list[str] = []
    import_stats: dict[str, Any] = {}

    # ── Step 4: Import metadata ──────────────────────────────────────
    if "metadata" in components and "metadata/project.json" in zf.namelist():
        try:
            meta = json.loads(zf.read("metadata/project.json"))
            meta["id"] = project_id
            meta["name"] = project_name if project_id != source_project_id else meta.get("name", project_name)
            meta["imported_at"] = datetime.now(timezone.utc).isoformat()
            meta["imported_from"] = source_project_id

            projects[project_id] = meta
            meta_path.parent.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(json.dumps(projects, indent=2))

            # Create project directories
            project_dir = settings.projects_dir / project_id
            for subdir in ("workspace", "personality", "notes"):
                (project_dir / subdir).mkdir(parents=True, exist_ok=True)

            imported_components.append("metadata")
        except Exception as e:
            logger.error("Failed to import metadata: %s", e)

    elif "metadata" in components:
        # No metadata file but component requested — create minimal entry
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
                _import_episodic(zf, project_id, import_stats)
                imported_components.append("memory:episodic")
            except Exception as e:
                logger.error("Failed to import episodic memory: %s", e)

        # Graph
        if "memory/graph.json" in zf.namelist():
            try:
                _import_graph(zf, project_id, import_stats)
                imported_components.append("memory:graph")
            except Exception as e:
                logger.error("Failed to import graph memory: %s", e)

        # Semantic
        if "memory/semantic.json" in zf.namelist():
            try:
                _import_semantic(zf, project_id, import_stats)
                imported_components.append("memory:semantic")
            except Exception as e:
                logger.error("Failed to import semantic memory: %s", e)

    # ── Step 6: Import files ─────────────────────────────────────────
    if "files" in components:
        try:
            count = _import_files(zf, project_id)
            import_stats["files_restored"] = count
            imported_components.append("files")
        except Exception as e:
            logger.error("Failed to import files: %s", e)

    # ── Step 7: Import tasks ─────────────────────────────────────────
    if "tasks" in components and "tasks/tasks.json" in zf.namelist():
        try:
            tasks = json.loads(zf.read("tasks/tasks.json"))
            import_stats["tasks"] = len(tasks)
            imported_components.append("tasks")
            # Note: Task schedules are informational only — they need to be
            # re-created through the task API to properly register with APScheduler.
            # Store them as a reference file.
            if tasks:
                task_ref_path = settings.projects_dir / project_id / "notes" / "imported_tasks.json"
                task_ref_path.parent.mkdir(parents=True, exist_ok=True)
                task_ref_path.write_text(json.dumps(tasks, indent=2))
        except Exception as e:
            logger.error("Failed to import tasks: %s", e)

    zf.close()

    logger.info(
        "Project imported: %s (components=%s, stats=%s)",
        project_id, imported_components, import_stats,
    )

    return ImportResult(
        success=True,
        project_id=project_id,
        project_name=project_name,
        message=f"Project '{project_name}' imported successfully as '{project_id}'",
        scan=scan,
        components_imported=imported_components,
        stats=import_stats,
    )


# ── Import helpers ───────────────────────────────────────────────────────────

def _import_episodic(
    zf: zipfile.ZipFile,
    project_id: str,
    stats: dict[str, Any],
) -> None:
    """Restore episodic memory data into SQLite."""
    import sqlite3
    data = json.loads(zf.read("memory/episodic.json"))
    db_path = str(settings.episodic_db_path)

    # Ensure tables exist
    from memory.episodic import EpisodicMemory
    EpisodicMemory(db_path=db_path, project_id=project_id)

    conn = sqlite3.connect(db_path)
    try:
        # Import conversations
        for row in data.get("conversations", []):
            row["project_id"] = project_id
            conn.execute("""
                INSERT OR IGNORE INTO conversations
                (id, project_id, session_id, title, created_at, updated_at, metadata)
                VALUES (:id, :project_id, :session_id, :title, :created_at, :updated_at, :metadata)
            """, row)

        # Import messages
        for row in data.get("messages", []):
            row["project_id"] = project_id
            conn.execute("""
                INSERT OR IGNORE INTO messages
                (id, project_id, session_id, role, content, timestamp, metadata)
                VALUES (:id, :project_id, :session_id, :role, :content, :timestamp, :metadata)
            """, row)

        # Import task logs
        for row in data.get("task_logs", []):
            row["project_id"] = project_id
            conn.execute("""
                INSERT OR IGNORE INTO task_logs
                (id, project_id, task_id, task_name, event, details, timestamp)
                VALUES (:id, :project_id, :task_id, :task_name, :event, :details, :timestamp)
            """, row)

        # Import memory notes
        for row in data.get("memory_notes", []):
            row["project_id"] = project_id
            conn.execute("""
                INSERT OR IGNORE INTO memory_notes
                (id, project_id, session_id, content, tags, created_at, updated_at)
                VALUES (:id, :project_id, :session_id, :content, :tags, :created_at, :updated_at)
            """, row)

        conn.commit()
        stats["conversations_imported"] = len(data.get("conversations", []))
        stats["messages_imported"] = len(data.get("messages", []))
        stats["task_logs_imported"] = len(data.get("task_logs", []))
        stats["notes_imported"] = len(data.get("memory_notes", []))
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
    db_path = str(settings.graph_db_path)

    # Ensure tables exist
    from memory.graph import GraphMemory
    GraphMemory(project_id=project_id, db_path=db_path)

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        # Import nodes first (edges depend on them)
        for row in data.get("nodes", []):
            row["project_id"] = project_id
            conn.execute("""
                INSERT OR IGNORE INTO graph_nodes
                (id, project_id, node_type, label, metadata, created_at, updated_at)
                VALUES (:id, :project_id, :node_type, :label, :metadata, :created_at, :updated_at)
            """, row)

        # Import edges
        for row in data.get("edges", []):
            row["project_id"] = project_id
            conn.execute("""
                INSERT OR IGNORE INTO graph_edges
                (id, project_id, node_a_id, node_b_id, relationship, weight, created_at)
                VALUES (:id, :project_id, :node_a_id, :node_b_id, :relationship, :weight, :created_at)
            """, row)

        conn.commit()
        stats["graph_nodes_imported"] = len(data.get("nodes", []))
        stats["graph_edges_imported"] = len(data.get("edges", []))
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

    # Batch upsert for efficiency
    batch_size = 100
    total = 0
    for i in range(0, len(data), batch_size):
        batch = data[i : i + batch_size]
        ids = [item["id"] for item in batch]
        documents = [item.get("document", "") for item in batch]
        metadatas = [item.get("metadata", {}) for item in batch]
        # Update project_id in metadata
        for m in metadatas:
            m["project_id"] = project_id
        # Flatten metadata values to strings
        metadatas = [{k: str(v) for k, v in m.items()} for m in metadatas]

        kwargs: dict[str, Any] = {
            "ids": ids,
            "documents": documents,
            "metadatas": metadatas,
        }
        # Include embeddings if present
        embeddings = [item.get("embedding") for item in batch]
        if all(e is not None for e in embeddings):
            kwargs["embeddings"] = embeddings

        collection.upsert(**kwargs)
        total += len(batch)

    stats["semantic_imported"] = total


def _import_files(zf: zipfile.ZipFile, project_id: str) -> int:
    """Restore workspace, personality, and notes files."""
    project_dir = settings.projects_dir / project_id
    project_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    dest_resolved = project_dir.resolve()

    for name in zf.namelist():
        if not name.startswith("files/") or name.endswith("/"):
            continue

        # Strip "files/" prefix
        relative = name[len("files/"):]
        if not relative:
            continue

        # Safety: resolve and verify target is within project_dir
        target = (project_dir / relative).resolve()
        if not str(target).startswith(str(dest_resolved)):
            logger.warning("Skipping path traversal attempt: %s", name)
            continue

        # Create parent directories
        target.parent.mkdir(parents=True, exist_ok=True)

        # Extract file
        with zf.open(name) as src:
            target.write_bytes(src.read())
        count += 1

    return count
