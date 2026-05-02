"""TaskRunStore — bookkeeping for autonomous task executions.

Records every fired execution of an APScheduler-scheduled autonomous task,
so the Settings -> Tasks dashboard and per-project chat Tasks tab can show
status, duration, results, and which project a run is for.

The scheduler itself stays APScheduler. This is just a side-log.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> str:
    s = get_settings()
    s.db_dir.mkdir(parents=True, exist_ok=True)
    return str(s.db_dir / "task_runs.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    sql_path = Path(__file__).resolve().parent.parent / "data" / "migrations" / "002_phase_g.sql"
    if sql_path.exists():
        conn.executescript(sql_path.read_text())
    else:
        # Inline fallback for the table this module owns
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS task_runs (
                id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_name TEXT NOT NULL,
                project_id TEXT NOT NULL DEFAULT 'default', description TEXT,
                status TEXT NOT NULL, started_at TEXT, completed_at TEXT,
                duration_ms INTEGER, result TEXT, error TEXT, session_id TEXT,
                artifact_id TEXT
            );
        """)
    return conn


def start_run(
    *,
    task_id: str,
    task_name: str,
    project_id: str = "default",
    description: str | None = None,
) -> str:
    """Insert a new run row in 'running' state. Returns run_id."""
    run_id = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            """INSERT INTO task_runs (id, task_id, task_name, project_id,
               description, status, started_at)
               VALUES (?,?,?,?,?,?,?)""",
            (run_id, task_id, task_name, project_id, description or "",
             "running", _now()),
        )
    return run_id


def complete_run(
    run_id: str,
    *,
    result: dict[str, Any] | None = None,
    session_id: str | None = None,
    artifact_id: str | None = None,
) -> None:
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            "SELECT started_at FROM task_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not row:
            return
        try:
            started = datetime.fromisoformat(row["started_at"])
            now_dt = datetime.fromisoformat(now)
            ms = int((now_dt - started).total_seconds() * 1000)
        except Exception:
            ms = None
        conn.execute(
            """UPDATE task_runs SET status='completed', completed_at=?,
               duration_ms=?, result=?, session_id=?, artifact_id=?
               WHERE id = ?""",
            (now, ms, json.dumps(result or {}), session_id, artifact_id, run_id),
        )


def fail_run(run_id: str, *, error: str, session_id: str | None = None) -> None:
    now = _now()
    with _connect() as conn:
        row = conn.execute(
            "SELECT started_at FROM task_runs WHERE id = ?", (run_id,)
        ).fetchone()
        if not row:
            return
        try:
            started = datetime.fromisoformat(row["started_at"])
            ms = int((datetime.fromisoformat(now) - started).total_seconds() * 1000)
        except Exception:
            ms = None
        conn.execute(
            """UPDATE task_runs SET status='failed', completed_at=?, duration_ms=?,
               error=?, session_id=? WHERE id = ?""",
            (now, ms, error[:2000], session_id, run_id),
        )


def cancel_run(run_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute(
            "UPDATE task_runs SET status='cancelled', completed_at=? "
            "WHERE id = ? AND status IN ('running','queued')",
            (_now(), run_id),
        )
        return cur.rowcount > 0


def list_runs(
    *,
    project_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    args: list[Any] = []
    if project_id and project_id != "all":
        clauses.append("project_id = ?"); args.append(project_id)
    if status:
        clauses.append("status = ?"); args.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM task_runs {where} "
            f"ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (*args, limit, offset),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        if d.get("result"):
            try: d["result"] = json.loads(d["result"])
            except Exception: pass
        out.append(d)
    return out


def get_run(run_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute("SELECT * FROM task_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    if d.get("result"):
        try: d["result"] = json.loads(d["result"])
        except Exception: pass
    return d


def delete_run(run_id: str) -> bool:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM task_runs WHERE id = ?", (run_id,))
        return cur.rowcount > 0
