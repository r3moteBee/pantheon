"""Connected sources API — currently GitHub.

Single-user model: one PAT-backed connection per project. Tokens stored
encrypted in the secrets vault. Connections metadata kept in a small
SQLite table at data/db/sources.db.
"""
from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config import get_settings
from secrets.vault import SecretsVault
from integrations.github import (
    GitHubAuthError,
    GitHubClient,
    GitHubError,
    GitHubForbidden,
    GitHubNotFound,
    verify_pat,
)

logger = logging.getLogger(__name__)
router = APIRouter()


# ── Storage ──

def _db_path() -> str:
    s = get_settings()
    db_dir = s.db_dir
    db_dir.mkdir(parents=True, exist_ok=True)
    return str(db_dir / "sources.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS github_connections (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL DEFAULT 'default',
            owner TEXT NOT NULL,
            repo TEXT NOT NULL,
            full_name TEXT NOT NULL,
            default_branch TEXT NOT NULL DEFAULT 'main',
            account_login TEXT,
            display_name TEXT,
            created_at TEXT NOT NULL,
            last_used_at TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            error_message TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gh_project ON github_connections(project_id)")
    conn.commit()
    return conn


def _vault_key(connection_id: str) -> str:
    return f"github_pat::{connection_id}"


# ── Models ──

class CreateGitHubConnectionRequest(BaseModel):
    project_id: str = "default"
    token: str
    repo: str  # 'owner/repo'
    default_branch: str | None = None


class GitHubConnectionResponse(BaseModel):
    id: str
    project_id: str
    owner: str
    repo: str
    full_name: str
    default_branch: str
    account_login: str | None = None
    display_name: str | None = None
    created_at: str
    last_used_at: str | None = None
    status: str
    error_message: str | None = None


# ── Endpoints ──

@router.post("/sources/github", response_model=GitHubConnectionResponse)
async def create_github_connection(req: CreateGitHubConnectionRequest) -> dict[str, Any]:
    """Validate the PAT, fetch the repo's default branch, store everything."""
    try:
        user = await verify_pat(req.token)
    except GitHubAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except (GitHubForbidden, GitHubError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    if "/" not in req.repo:
        raise HTTPException(status_code=400, detail="repo must be in 'owner/repo' format")
    owner, _, repo_name = req.repo.partition("/")

    client = GitHubClient(req.token)
    try:
        repo_info = await client.get_repo(owner, repo_name)
    except GitHubNotFound:
        raise HTTPException(status_code=404, detail=f"Repository {req.repo} not found or token cannot access it")
    except (GitHubForbidden, GitHubError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    default_branch = req.default_branch or repo_info.get("default_branch") or "main"
    connection_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    # Persist token in the vault
    vault = SecretsVault()
    vault.set_secret(_vault_key(connection_id), req.token)

    with _connect() as conn:
        conn.execute(
            """INSERT INTO github_connections
               (id, project_id, owner, repo, full_name, default_branch,
                account_login, display_name, created_at, status)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                connection_id, req.project_id, owner, repo_name, req.repo,
                default_branch, user.login, user.display_name, now, "active",
            ),
        )

    return _row_to_dict({
        "id": connection_id, "project_id": req.project_id, "owner": owner,
        "repo": repo_name, "full_name": req.repo, "default_branch": default_branch,
        "account_login": user.login, "display_name": user.display_name,
        "created_at": now, "last_used_at": None, "status": "active",
        "error_message": None,
    })


@router.get("/sources/github")
async def list_github_connections(
    project_id: str = Query(default="default"),
) -> dict[str, Any]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM github_connections WHERE project_id = ? ORDER BY created_at DESC",
            (project_id,),
        ).fetchall()
    return {"connections": [_row_to_dict(dict(r)) for r in rows]}


@router.delete("/sources/github/{connection_id}")
async def delete_github_connection(connection_id: str) -> dict[str, str]:
    vault = SecretsVault()
    try:
        vault.delete_secret(_vault_key(connection_id))
    except Exception:
        logger.debug("vault delete missing key (ok)")
    with _connect() as conn:
        cur = conn.execute("DELETE FROM github_connections WHERE id = ?", (connection_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="connection not found")
    return {"status": "deleted", "id": connection_id}


@router.get("/sources/github/repos")
async def list_repos(token: str = Query(..., description="Temporary PAT for picking a repo")) -> dict[str, Any]:
    """Used by the UI: paste-a-PAT → list visible repos → pick one to associate."""
    try:
        await verify_pat(token)
    except GitHubAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    client = GitHubClient(token)
    try:
        repos = await client.list_user_repos()
    except GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))
    summary = [
        {
            "full_name": r.get("full_name"),
            "default_branch": r.get("default_branch"),
            "private": r.get("private"),
            "description": r.get("description"),
        }
        for r in repos
    ]
    return {"repos": summary, "count": len(summary)}


# ── Internal helpers used by tools.py ──

def get_connection(connection_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM github_connections WHERE id = ?", (connection_id,)
        ).fetchone()
    return dict(row) if row else None


def get_default_connection(project_id: str = "default") -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM github_connections WHERE project_id = ? "
            "AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    return dict(row) if row else None


def get_token(connection_id: str) -> str | None:
    return SecretsVault().get_secret(_vault_key(connection_id))


def has_active_connection(project_id: str = "default") -> bool:
    return get_default_connection(project_id) is not None


def mark_used(connection_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE github_connections SET last_used_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), connection_id),
        )


def mark_error(connection_id: str, error: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE github_connections SET status = 'error', error_message = ? WHERE id = ?",
            (error[:500], connection_id),
        )


def _row_to_dict(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": d.get("id"),
        "project_id": d.get("project_id", "default"),
        "owner": d.get("owner"),
        "repo": d.get("repo"),
        "full_name": d.get("full_name"),
        "default_branch": d.get("default_branch", "main"),
        "account_login": d.get("account_login"),
        "display_name": d.get("display_name"),
        "created_at": d.get("created_at"),
        "last_used_at": d.get("last_used_at"),
        "status": d.get("status", "active"),
        "error_message": d.get("error_message"),
    }
