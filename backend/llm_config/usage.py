"""Per-call LLM routing log (data/db/llm_calls.db).

One row per attempt — a call that falls back writes one failed row per
skipped model plus the final one. This is the evidence base for tuning
routes (and for a later per-turn router): latency, error rate and token
use per task class and model.
"""
from __future__ import annotations

import logging
import sqlite3
import statistics
import time
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30

# Outcome of a routed chat turn — what phase-3 tuning learns from.
_DECISION_OUTCOME_COLUMNS = (
    ("decision_id", "TEXT"),
    ("latency_ms", "INTEGER"),
    ("iterations", "INTEGER"),
    ("tool_calls", "INTEGER"),
    ("tool_errors", "INTEGER"),
    ("truncated", "INTEGER"),
    ("stream_error", "INTEGER"),
    ("corrected", "INTEGER DEFAULT 0"),
    ("rating", "INTEGER"),
)
_last_prune = 0.0
_ready_paths: set[str] = set()


def _connect():
    from db_utils import apply_sqlite_pragmas, ClosingConnection
    settings = get_settings()
    settings.db_dir.mkdir(parents=True, exist_ok=True)
    path = str(settings.db_dir / "llm_calls.db")
    conn = sqlite3.connect(path)
    apply_sqlite_pragmas(conn)
    if path not in _ready_paths:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS llm_calls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                task_class TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                model TEXT NOT NULL,
                operation TEXT NOT NULL,
                attempt INTEGER NOT NULL,
                ok INTEGER NOT NULL,
                status TEXT,
                error TEXT,
                latency_ms INTEGER,
                prompt_tokens INTEGER,
                completion_tokens INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_llm_calls_ts ON llm_calls(ts);
            CREATE TABLE IF NOT EXISTS route_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                session_id TEXT,
                task_class TEXT NOT NULL,
                rule TEXT NOT NULL,
                reason TEXT,
                endpoint TEXT,
                model TEXT,
                served_model TEXT,
                message_chars INTEGER,
                est_tokens INTEGER
            );
            CREATE INDEX IF NOT EXISTS idx_route_decisions_ts ON route_decisions(ts);
        """)
        # Phase 3 outcome columns (added to existing DBs in place).
        have = {r[1] for r in conn.execute("PRAGMA table_info(route_decisions)")}
        for col, typ in _DECISION_OUTCOME_COLUMNS:
            if col not in have:
                conn.execute(f"ALTER TABLE route_decisions ADD COLUMN {col} {typ}")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_route_decisions_did ON route_decisions(decision_id)")
        conn.commit()
        _ready_paths.add(path)
    return ClosingConnection(conn)  # type: ignore


def record(
    *, task_class: str, endpoint: str, model: str, operation: str, attempt: int,
    ok: bool, latency_ms: int, status: str | None = None, error: str | None = None,
    prompt_tokens: int | None = None, completion_tokens: int | None = None,
) -> None:
    """Best-effort — logging must never break an LLM call."""
    global _last_prune
    try:
        now = time.time()
        with _connect() as conn:
            conn.execute(
                """INSERT INTO llm_calls (ts, task_class, endpoint, model, operation,
                       attempt, ok, status, error, latency_ms, prompt_tokens, completion_tokens)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (now, task_class, endpoint, model, operation, attempt, int(ok),
                 status, (error or "")[:300] or None, latency_ms, prompt_tokens, completion_tokens),
            )
            if now - _last_prune > 3600:
                conn.execute("DELETE FROM llm_calls WHERE ts < ?", (now - RETENTION_DAYS * 86400,))
                conn.execute("DELETE FROM route_decisions WHERE ts < ?", (now - RETENTION_DAYS * 86400,))
                _last_prune = now
            conn.commit()
    except Exception:
        logger.debug("llm usage record failed", exc_info=True)


def summary(hours: float = 24.0) -> dict[str, Any]:
    """Per (task_class, endpoint, model) stats over the last ``hours``."""
    since = time.time() - hours * 3600
    with _connect() as conn:
        rows = conn.execute(
            """SELECT task_class, endpoint, model, attempt, ok, status, error,
                      latency_ms, prompt_tokens, completion_tokens
               FROM llm_calls WHERE ts >= ?""",
            (since,),
        ).fetchall()
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for (cls, ep, model, attempt, ok, status, error, lat, pt, ct) in rows:
        g = groups.setdefault((cls, ep, model), {
            "task_class": cls, "endpoint": ep, "model": model,
            "calls": 0, "errors": 0, "served_as_fallback": 0,
            "latencies": [], "prompt_tokens": 0, "completion_tokens": 0,
            "last_error": None,
        })
        g["calls"] += 1
        if ok:
            if attempt > 0:
                g["served_as_fallback"] += 1
            if lat is not None:
                g["latencies"].append(lat)
        else:
            g["errors"] += 1
            g["last_error"] = f"{status or ''} {error or ''}".strip() or None
        g["prompt_tokens"] += pt or 0
        g["completion_tokens"] += ct or 0
    out = []
    for g in groups.values():
        lats = g.pop("latencies")
        g["p50_ms"] = int(statistics.median(lats)) if lats else None
        g["p95_ms"] = int(sorted(lats)[max(0, int(len(lats) * 0.95) - 1)]) if lats else None
        out.append(g)
    out.sort(key=lambda g: (g["task_class"], -g["calls"]))
    return {"hours": hours, "rows": out}


def record_decision(
    *, session_id: str | None, task_class: str, rule: str, reason: str,
    endpoint: str = "", model: str = "", served_model: str | None = None,
    message_chars: int = 0, est_tokens: int = 0, decision_id: str | None = None,
    outcome: dict[str, Any] | None = None,
) -> None:
    """One row per routed chat turn (best-effort). ``outcome`` carries
    latency_ms, iterations, tool_calls, tool_errors, truncated, stream_error."""
    o = outcome or {}
    try:
        with _connect() as conn:
            conn.execute(
                """INSERT INTO route_decisions (ts, session_id, task_class, rule, reason,
                       endpoint, model, served_model, message_chars, est_tokens,
                       decision_id, latency_ms, iterations, tool_calls, tool_errors,
                       truncated, stream_error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (time.time(), session_id, task_class, rule, (reason or "")[:300],
                 endpoint, model, served_model, message_chars, est_tokens,
                 decision_id, o.get("latency_ms"), o.get("iterations"), o.get("tool_calls"),
                 o.get("tool_errors"), int(bool(o.get("truncated"))), int(bool(o.get("stream_error")))),
            )
            conn.commit()
    except Exception:
        logger.debug("route decision record failed", exc_info=True)


def mark_corrected(decision_id: str) -> None:
    """The user's next move said the routed turn went wrong."""
    try:
        with _connect() as conn:
            conn.execute("UPDATE route_decisions SET corrected = 1 WHERE decision_id = ?", (decision_id,))
            conn.commit()
    except Exception:
        logger.debug("mark_corrected failed", exc_info=True)


def set_rating(decision_id: str, rating: int) -> bool:
    """Thumbs up (1) / down (-1) / clear (0) from the chat UI."""
    with _connect() as conn:
        cur = conn.execute("UPDATE route_decisions SET rating = ? WHERE decision_id = ?",
                           (rating or None, decision_id))
        conn.commit()
        return cur.rowcount > 0


def decision_rows(hours: float = 24.0 * 7) -> list[dict[str, Any]]:
    """Raw routed-turn rows (with outcomes) for tuning."""
    since = time.time() - hours * 3600
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM route_decisions WHERE ts >= ? ORDER BY ts", (since,)
        ).fetchall()
    return [dict(r) for r in rows]


def decision_summary(hours: float = 24.0) -> dict[str, Any]:
    """Chat-router decisions grouped by (task_class, rule) over ``hours``."""
    since = time.time() - hours * 3600
    with _connect() as conn:
        rows = conn.execute(
            """SELECT task_class, rule, COUNT(*), MAX(ts),
                      SUM(CASE WHEN served_model IS NOT NULL AND served_model != model THEN 1 ELSE 0 END)
               FROM route_decisions WHERE ts >= ?
               GROUP BY task_class, rule ORDER BY COUNT(*) DESC""",
            (since,),
        ).fetchall()
    return {"hours": hours, "rows": [
        {"task_class": c, "rule": r, "turns": n, "last_ts": ts, "fell_back": fb or 0}
        for c, r, n, ts, fb in rows
    ]}
