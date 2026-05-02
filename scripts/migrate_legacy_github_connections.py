"""One-shot migration — promote legacy per-project github_connections rows
to account-level connections + project_repo_bindings rows.

Pre-G.1 connections were created with project_id set; the new model
stores PATs as account-level (project_id=NULL) and pins repos to
projects via project_repo_bindings. The runtime get_default_connection()
already falls back to legacy rows, but converting them keeps things
clean and unblocks features that assume the new shape.

Idempotent: re-running skips connections already converted (project_id IS NULL).

Run: python scripts/migrate_legacy_github_connections.py [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from config import get_settings  # noqa: E402

logger = logging.getLogger("migrate_legacy_github_connections")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    s = get_settings()
    db_path = s.db_dir / "sources.db"
    if not db_path.exists():
        logger.info("No sources.db found at %s — nothing to migrate.", db_path)
        return

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # Schema check — both tables must exist (would have been created by
    # api/connections._connect on first hit, but be safe).
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS github_connections (
            id TEXT PRIMARY KEY, project_id TEXT,
            owner TEXT NOT NULL, repo TEXT NOT NULL, full_name TEXT NOT NULL,
            default_branch TEXT NOT NULL DEFAULT 'main',
            account_login TEXT, display_name TEXT,
            created_at TEXT NOT NULL, last_used_at TEXT,
            status TEXT NOT NULL DEFAULT 'active', error_message TEXT
        );
        CREATE TABLE IF NOT EXISTS project_repo_bindings (
            project_id TEXT PRIMARY KEY, connection_id TEXT NOT NULL,
            owner TEXT NOT NULL, repo TEXT NOT NULL,
            default_branch TEXT NOT NULL DEFAULT 'main',
            bound_at TEXT NOT NULL
        );
    """)

    rows = conn.execute(
        "SELECT * FROM github_connections WHERE project_id IS NOT NULL"
    ).fetchall()

    if not rows:
        logger.info("No legacy per-project connections found. Nothing to do.")
        return

    actions: list[dict] = []
    now = datetime.now(timezone.utc).isoformat()

    for r in rows:
        d = dict(r)
        cid = d["id"]
        proj = d["project_id"]
        # Skip if a binding already exists for this project (manual rebind happened)
        existing = conn.execute(
            "SELECT 1 FROM project_repo_bindings WHERE project_id = ?", (proj,)
        ).fetchone()
        if existing:
            actions.append({"connection_id": cid, "project_id": proj,
                            "action": "skip_already_bound"})
            continue
        if not d.get("owner") or not d.get("repo"):
            actions.append({"connection_id": cid, "project_id": proj,
                            "action": "skip_no_repo"})
            continue

        if args.dry_run:
            actions.append({"connection_id": cid, "project_id": proj,
                            "action": "would_promote",
                            "repo": d["full_name"]})
            continue

        # Promote: clear project_id on the connection + create binding
        conn.execute(
            "UPDATE github_connections SET project_id = NULL WHERE id = ?", (cid,)
        )
        conn.execute(
            """INSERT OR REPLACE INTO project_repo_bindings
               (project_id, connection_id, owner, repo, default_branch, bound_at)
               VALUES (?,?,?,?,?,?)""",
            (proj, cid, d["owner"], d["repo"], d.get("default_branch") or "main", now),
        )
        actions.append({"connection_id": cid, "project_id": proj,
                        "action": "promoted", "repo": d["full_name"]})

    if not args.dry_run:
        conn.commit()

    log_dir = s.db_dir / "migration_log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"legacy_github_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.json"
    log_path.write_text(json.dumps(
        {"dry_run": args.dry_run, "completed_at": now, "actions": actions},
        indent=2,
    ))

    summary: dict[str, int] = {}
    for a in actions:
        summary[a["action"]] = summary.get(a["action"], 0) + 1
    logger.info("Migration log: %s", log_path)
    for k, v in summary.items():
        logger.info("  %s: %d", k, v)


if __name__ == "__main__":
    main()
