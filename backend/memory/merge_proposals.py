"""Storage for proposed graph node merges.

A merge proposal is created automatically when the cross-artifact
similarity pipeline finds two topic nodes whose embeddings are
close enough that linking with SEMANTICALLY_SIMILAR_TO would feel
redundant — the labels probably refer to the same entity (e.g.
"Dell" and "Dell Technologies"). Rather than auto-merging (which
loses information irreversibly), proposals sit in 'pending' state
until a human approves; agent tools list / approve / reject them.

Schema is small and self-contained: a single SQLite table at
``data/db/merge_proposals.db``.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from config import get_settings

logger = logging.getLogger(__name__)


_DDL = """\
CREATE TABLE IF NOT EXISTS topic_merge_proposals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    proposed_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|merged|stale

    node_a_label TEXT NOT NULL,
    node_a_type TEXT NOT NULL,
    node_b_label TEXT NOT NULL,
    node_b_type TEXT NOT NULL,

    similarity_score REAL NOT NULL,
    reason TEXT,
    proposal_metadata TEXT,                  -- JSON, free-form

    canonical_label TEXT,
    approved_at TEXT,
    approved_by TEXT,
    merged_at TEXT,

    UNIQUE (project_id, node_a_label, node_a_type, node_b_label, node_b_type)
);
CREATE INDEX IF NOT EXISTS idx_tmp_status ON topic_merge_proposals(project_id, status);
CREATE INDEX IF NOT EXISTS idx_tmp_score ON topic_merge_proposals(project_id, similarity_score DESC);
"""


def _db_path() -> Path:
    s = get_settings()
    p = s.db_dir / "merge_proposals.db"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_db_path()))
    conn.row_factory = sqlite3.Row
    conn.executescript(_DDL)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_pair(
    a_label: str, a_type: str, b_label: str, b_type: str,
) -> tuple[str, str, str, str]:
    """Sort the pair so (A,B) and (B,A) collapse to one row."""
    if (a_label.lower(), a_type.lower()) <= (b_label.lower(), b_type.lower()):
        return a_label, a_type, b_label, b_type
    return b_label, b_type, a_label, a_type


def propose(
    *,
    project_id: str,
    label_a: str,
    type_a: str,
    label_b: str,
    type_b: str,
    similarity_score: float,
    reason: str = "high-similarity",
    proposal_metadata: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    """Insert a proposal. Idempotent on (project_id, normalized pair):
    re-proposing the same pair updates the score if the new score is
    higher and the proposal is still pending. Returns (id, created)
    where created=False means the row already existed.

    Pairs that have status='approved'/'merged'/'rejected' are NOT
    overwritten — those are decisions the user has already made.
    """
    a_lbl, a_t, b_lbl, b_t = _normalize_pair(label_a, type_a, label_b, type_b)
    if a_lbl.lower() == b_lbl.lower() and a_t.lower() == b_t.lower():
        return ("", False)  # don't propose self-merges

    with _connect() as conn:
        existing = conn.execute(
            """SELECT id, status, similarity_score
               FROM topic_merge_proposals
               WHERE project_id=? AND node_a_label=? AND node_a_type=?
                 AND node_b_label=? AND node_b_type=?""",
            (project_id, a_lbl, a_t, b_lbl, b_t),
        ).fetchone()
        if existing:
            if existing["status"] != "pending":
                # Already decided — don't reopen.
                return (existing["id"], False)
            if similarity_score > (existing["similarity_score"] or 0):
                conn.execute(
                    """UPDATE topic_merge_proposals
                       SET similarity_score=?, reason=?, proposed_at=?
                       WHERE id=?""",
                    (similarity_score, reason, _now(), existing["id"]),
                )
                conn.commit()
            return (existing["id"], False)
        pid = str(uuid.uuid4())
        conn.execute(
            """INSERT INTO topic_merge_proposals
                 (id, project_id, proposed_at, status,
                  node_a_label, node_a_type, node_b_label, node_b_type,
                  similarity_score, reason, proposal_metadata)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, project_id, _now(), "pending",
             a_lbl, a_t, b_lbl, b_t,
             similarity_score, reason,
             json.dumps(proposal_metadata or {})),
        )
        conn.commit()
    logger.info("merge proposal queued: %s/%s vs %s/%s (%.3f)",
                a_lbl, a_t, b_lbl, b_t, similarity_score)
    return (pid, True)


def list_proposals(
    *,
    project_id: str,
    status: str | None = "pending",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List proposals, newest-by-score first. status=None returns all."""
    q = """SELECT * FROM topic_merge_proposals
           WHERE project_id=?"""
    args: list[Any] = [project_id]
    if status:
        q += " AND status=?"
        args.append(status)
    q += " ORDER BY similarity_score DESC LIMIT ?"
    args.append(limit)
    with _connect() as conn:
        rows = conn.execute(q, args).fetchall()
    return [_hydrate(r) for r in rows]


def get_proposal(proposal_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM topic_merge_proposals WHERE id=?",
            (proposal_id,),
        ).fetchone()
    return _hydrate(row) if row else None


def set_status(
    proposal_id: str,
    status: str,
    *,
    canonical_label: str | None = None,
    approved_by: str | None = None,
    merged_at: str | None = None,
) -> bool:
    if status not in {"pending", "approved", "rejected", "merged", "stale"}:
        raise ValueError(f"invalid status: {status}")
    with _connect() as conn:
        cursor = conn.execute(
            """UPDATE topic_merge_proposals
               SET status=?,
                   canonical_label=COALESCE(?, canonical_label),
                   approved_at=COALESCE(?, approved_at),
                   approved_by=COALESCE(?, approved_by),
                   merged_at=COALESCE(?, merged_at)
               WHERE id=?""",
            (status, canonical_label,
             _now() if status in {"approved", "merged"} else None,
             approved_by,
             merged_at if status == "merged" else None,
             proposal_id),
        )
        conn.commit()
    return cursor.rowcount > 0


def _hydrate(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    if d.get("proposal_metadata"):
        try:
            d["proposal_metadata"] = json.loads(d["proposal_metadata"])
        except Exception:
            pass
    return d
