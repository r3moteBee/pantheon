"""JobStore — CRUD and atomic transitions for the jobs table."""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable

from config import get_settings

logger = logging.getLogger(__name__)


# ── Statuses ────────────────────────────────────────────────────────────────

class JobStatus:
    QUEUED    = "queued"
    RUNNING   = "running"
    COMPLETED = "completed"
    FAILED    = "failed"
    CANCELLED = "cancelled"
    STALLED   = "stalled"

    TERMINAL = {"completed", "failed", "cancelled", "stalled"}
    ACTIVE   = {"queued", "running"}


class JobNotFound(KeyError):
    pass


# ── Helpers ─────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _db_path() -> str:
    s = get_settings()
    s.db_dir.mkdir(parents=True, exist_ok=True)
    return str(s.db_dir / "jobs.db")


_INLINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, job_type TEXT NOT NULL,
    project_id TEXT NOT NULL DEFAULT 'default',
    status TEXT NOT NULL DEFAULT 'queued',
    title TEXT, description TEXT,
    payload TEXT NOT NULL DEFAULT '{}',
    result TEXT, error TEXT, progress TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 1,
    scheduled_for TEXT, timeout_seconds INTEGER,
    parent_job_id TEXT,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
    last_heartbeat_at TEXT,
    schedule_id TEXT, session_id TEXT, artifact_id TEXT, pr_url TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_project_status
    ON jobs(project_id, status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status_due
    ON jobs(status, scheduled_for)
    WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS idx_jobs_type_project
    ON jobs(job_type, project_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_running_heartbeat
    ON jobs(status, last_heartbeat_at)
    WHERE status = 'running';
"""


# ── Store ───────────────────────────────────────────────────────────────────

class JobStore:
    def __init__(self, db_path: str | None = None):
        self.db_path = db_path or _db_path()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        return conn

    def _init_db(self) -> None:
        sql_path = Path(__file__).resolve().parent.parent / "data" / "migrations" / "003_jobs.sql"
        sql = sql_path.read_text() if sql_path.exists() else _INLINE_SCHEMA
        with self._connect() as conn:
            conn.executescript(sql)
            self._migrate_from_task_runs(conn)

    def _migrate_from_task_runs(self, conn: sqlite3.Connection) -> None:
        """One-shot import of existing task_runs rows into jobs.

        Idempotent — only imports rows whose id isn't already in jobs.
        Marks them with job_type='autonomous_task'.
        """
        # Check if task_runs.db exists in the same db_dir
        db_dir = Path(self.db_path).parent
        runs_db = db_dir / "task_runs.db"
        if not runs_db.exists():
            return
        try:
            src = sqlite3.connect(str(runs_db), timeout=5.0)
            src.row_factory = sqlite3.Row
            rows = src.execute(
                "SELECT * FROM task_runs WHERE id NOT IN "
                "(SELECT id FROM (SELECT '' as id WHERE 0))"  # placeholder
            ).fetchall()
            existing = {r["id"] for r in conn.execute("SELECT id FROM jobs").fetchall()}
            n = 0
            for r in rows:
                if r["id"] in existing:
                    continue
                conn.execute(
                    """INSERT INTO jobs
                       (id, job_type, project_id, status, title, description,
                        payload, result, error, started_at, completed_at,
                        session_id, artifact_id, created_at, attempts, max_attempts)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        r["id"], "autonomous_task",
                        r["project_id"] or "default",
                        r["status"] if r["status"] in JobStatus.TERMINAL or r["status"] in JobStatus.ACTIVE else "completed",
                        r["task_name"] or "(task)",
                        r["description"] or "",
                        json.dumps({"task_id": r["task_id"]}),
                        r["result"] or None,
                        r["error"] or None,
                        r["started_at"], r["completed_at"],
                        r["session_id"], r["artifact_id"],
                        r["started_at"] or _now(),
                        1, 1,
                    ),
                )
                n += 1
            if n:
                logger.info("Imported %d task_runs rows into jobs", n)
            src.close()
        except Exception as e:
            logger.warning("Could not migrate task_runs → jobs: %s", e)

    # ── CRUD ───────────────────────────────────────────────────────────────

    def create(
        self,
        *,
        job_type: str,
        project_id: str = "default",
        title: str | None = None,
        description: str | None = None,
        payload: dict[str, Any] | None = None,
        scheduled_for: str | None = None,
        timeout_seconds: int | None = None,
        max_attempts: int = 1,
        parent_job_id: str | None = None,
        schedule_id: str | None = None,
    ) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO jobs
                   (id, job_type, project_id, status, title, description,
                    payload, scheduled_for, timeout_seconds, max_attempts,
                    parent_job_id, schedule_id, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    job_id, job_type, project_id, JobStatus.QUEUED,
                    title or job_type, description or "",
                    json.dumps(payload or {}),
                    scheduled_for, timeout_seconds, max_attempts,
                    parent_job_id, schedule_id, now,
                ),
            )
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row:
            raise JobNotFound(job_id)
        return self._hydrate(row)

    def get_or_none(self, job_id: str) -> dict[str, Any] | None:
        try: return self.get(job_id)
        except JobNotFound: return None

    def rerun(self, job_id: str) -> dict[str, Any]:
        """Create a fresh job that mirrors a previous (completed/failed/
        cancelled/stalled) one. Same payload, title, description,
        project_id, job_type, schedule_id, timeout. Status starts at
        QUEUED so the worker picks it up.

        The original job row is left untouched — this gives you a
        history of attempts rather than overwriting the original.
        Returns the new job dict."""
        old = self.get(job_id)  # raises JobNotFound if missing
        if old.get("status") in {"queued", "running"}:
            raise ValueError(
                f"job {job_id} is currently {old['status']!r}; "
                f"cannot rerun until it completes or fails"
            )
        new_id = str(uuid.uuid4())
        now = _now()
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO jobs
                   (id, job_type, project_id, status, title, description,
                    payload, scheduled_for, timeout_seconds, max_attempts,
                    parent_job_id, schedule_id, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    new_id, old["job_type"], old["project_id"], JobStatus.QUEUED,
                    old.get("title") or old["job_type"],
                    old.get("description") or "",
                    json.dumps(old.get("payload") or {}),
                    None,                       # scheduled_for: run now
                    old.get("timeout_seconds"),
                    old.get("max_attempts") or 1,
                    job_id,                     # parent_job_id: link to original
                    old.get("schedule_id"),
                    now,
                ),
            )
        return self.get(new_id)

    def find_latest_for_schedule(self, schedule_id: str) -> dict[str, Any] | None:
        """Return the most recent job that fired from this schedule, or None."""
        with self._connect() as conn:
            row = conn.execute(
                """SELECT * FROM jobs WHERE schedule_id = ?
                   ORDER BY created_at DESC LIMIT 1""",
                (schedule_id,),
            ).fetchone()
        return self._hydrate(row) if row else None

    def list_for_schedule(self, schedule_id: str, limit: int = 20) -> list[dict[str, Any]]:
        """Return all jobs that fired from this schedule, newest first."""
        with self._connect() as conn:
            rows = conn.execute(
                """SELECT * FROM jobs WHERE schedule_id = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (schedule_id, limit),
            ).fetchall()
        return [self._hydrate(r) for r in rows]

    def list(
        self,
        *,
        project_id: str | None = None,
        job_type: str | None = None,
        statuses: Iterable[str] | None = None,
        started_within: timedelta | None = None,
        include_system: bool = True,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        args: list[Any] = []
        if project_id and project_id not in ("all", ""):
            clauses.append("project_id = ?"); args.append(project_id)
        if job_type:
            clauses.append("job_type = ?"); args.append(job_type)
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            clauses.append(f"status IN ({placeholders})")
            args.extend(list(statuses))
        if started_within:
            cutoff = (datetime.now(timezone.utc) - started_within).isoformat()
            clauses.append("(started_at IS NULL OR started_at >= ?)"); args.append(cutoff)
        if not include_system:
            clauses.append("job_type NOT IN ('extraction', 'file_indexing')")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*args, limit, offset),
            ).fetchall()
        return [self._hydrate(r) for r in rows]

    # ── Atomic transitions ─────────────────────────────────────────────────

    def claim_next(self) -> dict[str, Any] | None:
        """Atomically pick the oldest queued+due row and flip it to running."""
        now = _now()
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                """SELECT * FROM jobs
                   WHERE status = ?
                     AND (scheduled_for IS NULL OR scheduled_for <= ?)
                   ORDER BY created_at ASC
                   LIMIT 1""",
                (JobStatus.QUEUED, now),
            ).fetchone()
            if not row:
                conn.execute("COMMIT")
                return None
            conn.execute(
                """UPDATE jobs
                   SET status = ?, started_at = ?, last_heartbeat_at = ?,
                       attempts = attempts + 1
                   WHERE id = ?""",
                (JobStatus.RUNNING, now, now, row["id"]),
            )
            conn.execute("COMMIT")
        return self.get(row["id"])

    def heartbeat(self, job_id: str, *, progress: str | None = None) -> bool:
        now = _now()
        with self._connect() as conn:
            sets = "last_heartbeat_at = ?"
            args: list[Any] = [now]
            if progress is not None:
                sets += ", progress = ?"
                args.append(progress[:500])
            args.append(job_id)
            cur = conn.execute(
                f"UPDATE jobs SET {sets} WHERE id = ? AND status = 'running'",
                args,
            )
            return cur.rowcount > 0

    def complete(
        self,
        job_id: str,
        *,
        result: dict[str, Any] | None = None,
        session_id: str | None = None,
        artifact_id: str | None = None,
        pr_url: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE jobs
                   SET status = ?, completed_at = ?, result = ?,
                       session_id = COALESCE(?, session_id),
                       artifact_id = COALESCE(?, artifact_id),
                       pr_url = COALESCE(?, pr_url)
                   WHERE id = ?""",
                (
                    JobStatus.COMPLETED, _now(),
                    json.dumps(result or {}),
                    session_id, artifact_id, pr_url, job_id,
                ),
            )

    def fail(self, job_id: str, *, error: str, session_id: str | None = None) -> None:
        with self._connect() as conn:
            conn.execute(
                """UPDATE jobs SET status = ?, completed_at = ?, error = ?,
                                   session_id = COALESCE(?, session_id)
                   WHERE id = ?""",
                (JobStatus.FAILED, _now(), error[:2000], session_id, job_id),
            )
        _record_failure_to_memory(self, job_id, error)

    def cancel(self, job_id: str) -> bool:
        """User-requested cancel. If queued, terminate immediately. If
        running, set the cooperative flag and let the handler stop on the
        next ctx.cancel_requested() poll."""
        with self._connect() as conn:
            row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return False
            if row["status"] == JobStatus.QUEUED:
                conn.execute(
                    "UPDATE jobs SET status=?, completed_at=? WHERE id=?",
                    (JobStatus.CANCELLED, _now(), job_id),
                )
                return True
            if row["status"] == JobStatus.RUNNING:
                conn.execute(
                    "UPDATE jobs SET cancel_requested = 1 WHERE id = ?", (job_id,)
                )
                return True
            return False

    def is_cancel_requested(self, job_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT cancel_requested FROM jobs WHERE id = ?", (job_id,)
            ).fetchone()
        return bool(row and row["cancel_requested"])

    def stall_running(self, *, idle_seconds: int) -> int:
        """Watchdog: mark running jobs whose last_heartbeat_at is older
        than idle_seconds as stalled. Returns count marked."""
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=idle_seconds)).isoformat()
        msg = (
            f"No heartbeat in over {idle_seconds // 60} minute(s) — "
            "process likely died. Re-queue with the retry button."
        )
        # First grab the ids about to be marked so we can write episodic
        # notes after the UPDATE.
        with self._connect() as conn:
            ids_to_stall = [r["id"] for r in conn.execute(
                """SELECT id FROM jobs WHERE status = 'running'
                   AND (last_heartbeat_at IS NULL OR last_heartbeat_at < ?)""",
                (cutoff,),
            ).fetchall()]
            cur = conn.execute(
                """UPDATE jobs
                   SET status = ?, completed_at = ?, error = ?
                   WHERE status = 'running'
                     AND (last_heartbeat_at IS NULL OR last_heartbeat_at < ?)""",
                (JobStatus.STALLED, _now(), msg, cutoff),
            )
        for jid in ids_to_stall:
            _record_failure_to_memory(self, jid, msg)
        return cur.rowcount

    def retry(self, job_id: str) -> dict[str, Any]:
        """Re-queue a failed/stalled job with the same payload."""
        old = self.get(job_id)
        if old["status"] not in {JobStatus.FAILED, JobStatus.STALLED, JobStatus.CANCELLED}:
            raise ValueError(f"job is {old['status']}, cannot retry")
        return self.create(
            job_type=old["job_type"],
            project_id=old["project_id"],
            title=old.get("title") or "",
            description=old.get("description") or "",
            payload=old.get("payload") or {},
            timeout_seconds=old.get("timeout_seconds"),
            max_attempts=old.get("max_attempts") or 1,
            parent_job_id=old["id"],
            schedule_id=old.get("schedule_id"),
        )

    def delete(self, job_id: str) -> bool:
        """Delete a record (does not affect a running job — it'll just
        produce an orphaned row at completion which we accept)."""
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
            return cur.rowcount > 0

    # ── Hydration ──────────────────────────────────────────────────────────

    def _hydrate(self, row: sqlite3.Row) -> dict[str, Any]:
        d = dict(row)
        for k in ("payload", "result"):
            if d.get(k):
                try: d[k] = json.loads(d[k])
                except Exception: pass
            else:
                d[k] = {}
        d["cancel_requested"] = bool(d.get("cancel_requested"))
        return d


_INSTANCE: JobStore | None = None

def get_store() -> JobStore:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = JobStore()
    return _INSTANCE
