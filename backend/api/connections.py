"""Account-level connections + per-project bindings.

Refactored from api/sources.py for Phase G:
  * GitHub PATs are stored as account-level connections (project_id is now
    optional; legacy per-project rows still work and are migrated lazily).
  * A separate project_repo_bindings table pins one repo per project.
"""
from __future__ import annotations

import json
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


# ── Storage ──────────────────────────────────────────────────────────────────

def _db_path() -> str:
    s = get_settings()
    s.db_dir.mkdir(parents=True, exist_ok=True)
    return str(s.db_dir / "sources.db")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    # github_connections (existing) + project_repo_bindings (new in Phase G)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS github_connections (
            id TEXT PRIMARY KEY,
            project_id TEXT,
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
    conn.execute("""
        CREATE TABLE IF NOT EXISTS project_repo_bindings (
            project_id TEXT PRIMARY KEY,
            connection_id TEXT NOT NULL,
            owner TEXT NOT NULL,
            repo TEXT NOT NULL,
            default_branch TEXT NOT NULL DEFAULT 'main',
            bound_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_gh_account ON github_connections(account_login)")
    conn.commit()
    return conn


def _vault_key(connection_id: str) -> str:
    return f"github_pat::{connection_id}"


# ── Models ───────────────────────────────────────────────────────────────────

class CreateGitHubConnectionRequest(BaseModel):
    """Account-level connection. project_id is optional and only used to
    grandfather legacy per-project connections; new connections are global."""
    token: str
    repo: str | None = None      # 'owner/repo' — kept for tests/UI flow
    project_id: str | None = None
    default_branch: str | None = None


class BindRepoRequest(BaseModel):
    connection_id: str
    owner: str
    repo: str
    default_branch: str | None = None


# ── Connection endpoints ─────────────────────────────────────────────────────

@router.post("/connections/github")
async def create_github_connection(req: CreateGitHubConnectionRequest) -> dict[str, Any]:
    """Add a GitHub account-level PAT. Verifies the token and stores it
    encrypted in the vault. The picked repo (if provided) becomes the
    default for any project bindings made from this connection."""
    try:
        user = await verify_pat(req.token)
    except GitHubAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except (GitHubForbidden, GitHubError) as e:
        raise HTTPException(status_code=400, detail=str(e))

    owner = repo_name = ""
    default_branch = req.default_branch or "main"
    if req.repo:
        if "/" not in req.repo:
            raise HTTPException(status_code=400, detail="repo must be 'owner/repo'")
        owner, _, repo_name = req.repo.partition("/")
        client = GitHubClient(req.token)
        try:
            repo_info = await client.get_repo(owner, repo_name)
            default_branch = req.default_branch or repo_info.get("default_branch") or "main"
        except GitHubNotFound:
            raise HTTPException(status_code=404, detail=f"Repo {req.repo} not visible to token")
        except (GitHubForbidden, GitHubError) as e:
            raise HTTPException(status_code=400, detail=str(e))

    connection_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    SecretsVault().set_secret(_vault_key(connection_id), req.token)

    with _connect() as conn:
        conn.execute(
            """INSERT INTO github_connections
               (id, project_id, owner, repo, full_name, default_branch,
                account_login, display_name, created_at, status)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                connection_id, req.project_id, owner, repo_name,
                f"{owner}/{repo_name}" if owner else "",
                default_branch, user.login, user.display_name, now, "active",
            ),
        )

    return _row_to_dict({
        "id": connection_id, "project_id": req.project_id, "owner": owner,
        "repo": repo_name, "full_name": f"{owner}/{repo_name}" if owner else "",
        "default_branch": default_branch,
        "account_login": user.login, "display_name": user.display_name,
        "created_at": now, "last_used_at": None, "status": "active",
        "error_message": None,
    })


@router.get("/connections/github")
async def list_github_connections(
    project_id: str | None = Query(default=None),
) -> dict[str, Any]:
    with _connect() as conn:
        if project_id and project_id not in ("all", ""):
            rows = conn.execute(
                "SELECT * FROM github_connections WHERE project_id IS NULL OR project_id = ? "
                "ORDER BY created_at DESC",
                (project_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM github_connections ORDER BY created_at DESC"
            ).fetchall()
    return {"connections": [_row_to_dict(dict(r)) for r in rows]}


@router.delete("/connections/github/{connection_id}")
async def delete_github_connection(connection_id: str) -> dict[str, str]:
    try:
        SecretsVault().delete_secret(_vault_key(connection_id))
    except Exception:
        pass
    with _connect() as conn:
        cur = conn.execute("DELETE FROM github_connections WHERE id = ?", (connection_id,))
        # Clear any project bindings that referenced it
        conn.execute("DELETE FROM project_repo_bindings WHERE connection_id = ?", (connection_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="connection not found")
    return {"status": "deleted", "id": connection_id}


@router.get("/connections/github/repos")
async def list_repos(token: str = Query(..., description="Temporary PAT for picking a repo")) -> dict[str, Any]:
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
        {"full_name": r.get("full_name"), "default_branch": r.get("default_branch"),
         "private": r.get("private"), "description": r.get("description")}
        for r in repos
    ]
    return {"repos": summary, "count": len(summary)}


@router.get("/connections/github/{connection_id}/repos")
async def list_connection_repos(connection_id: str) -> dict[str, Any]:
    """List repos visible to a stored connection's PAT (no token in URL)."""
    if not get_connection(connection_id):
        raise HTTPException(status_code=404, detail="connection not found")
    token = get_token(connection_id)
    if not token:
        raise HTTPException(status_code=400, detail="connection has no stored token")
    client = GitHubClient(token)
    try:
        repos = await client.list_user_repos()
    except GitHubAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {
        "repos": [
            {"full_name": r.get("full_name"), "default_branch": r.get("default_branch"),
             "private": r.get("private"), "description": r.get("description")}
            for r in repos
        ],
    }


@router.get("/connections/github/{connection_id}/branches")
async def list_connection_branches(
    connection_id: str,
    owner: str = Query(...),
    repo: str = Query(...),
) -> dict[str, Any]:
    """List branches in a repo accessible to the stored connection."""
    if not get_connection(connection_id):
        raise HTTPException(status_code=404, detail="connection not found")
    token = get_token(connection_id)
    if not token:
        raise HTTPException(status_code=400, detail="connection has no stored token")
    client = GitHubClient(token)
    try:
        branches = await client.list_branches(owner, repo)
    except GitHubNotFound:
        raise HTTPException(status_code=404, detail=f"{owner}/{repo} not found")
    except GitHubAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except GitHubError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"branches": branches}


# ── Project repo binding ─────────────────────────────────────────────────────

@router.get("/projects/{project_id}/repo")
async def get_project_repo(project_id: str) -> dict[str, Any]:
    binding = get_project_binding(project_id)
    if not binding:
        return {"project_id": project_id, "binding": None}
    return {"project_id": project_id, "binding": binding}


@router.post("/projects/{project_id}/repo")
async def bind_project_repo(project_id: str, req: BindRepoRequest) -> dict[str, Any]:
    # Verify the connection exists
    with _connect() as conn:
        conn_row = conn.execute(
            "SELECT * FROM github_connections WHERE id = ?", (req.connection_id,)
        ).fetchone()
    if not conn_row:
        raise HTTPException(status_code=404, detail="connection not found")
    default_branch = req.default_branch or "main"
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute(
            """INSERT INTO project_repo_bindings
               (project_id, connection_id, owner, repo, default_branch, bound_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(project_id) DO UPDATE SET
                   connection_id=excluded.connection_id, owner=excluded.owner,
                   repo=excluded.repo, default_branch=excluded.default_branch,
                   bound_at=excluded.bound_at""",
            (project_id, req.connection_id, req.owner, req.repo, default_branch, now),
        )
    return get_project_binding(project_id)


@router.delete("/projects/{project_id}/repo")
async def unbind_project_repo(project_id: str) -> dict[str, str]:
    with _connect() as conn:
        cur = conn.execute("DELETE FROM project_repo_bindings WHERE project_id = ?", (project_id,))
    return {"status": "unbound" if cur.rowcount > 0 else "no_binding", "project_id": project_id}


# ── Helpers used by agent tools and other modules ────────────────────────────

def get_connection(connection_id: str) -> dict[str, Any] | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM github_connections WHERE id = ?", (connection_id,)
        ).fetchone()
    return dict(row) if row else None


def get_project_binding(project_id: str) -> dict[str, Any] | None:
    """Return the active binding for a project, or None."""
    with _connect() as conn:
        row = conn.execute(
            "SELECT b.*, c.account_login, c.display_name "
            "FROM project_repo_bindings b "
            "LEFT JOIN github_connections c ON c.id = b.connection_id "
            "WHERE b.project_id = ?",
            (project_id,),
        ).fetchone()
    return dict(row) if row else None


def get_default_connection(project_id: str = "default") -> dict[str, Any] | None:
    """Resolve which connection a project should use.
    Prefers the explicit binding; falls back to legacy per-project connection."""
    binding = get_project_binding(project_id)
    if binding:
        return get_connection(binding["connection_id"])
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM github_connections WHERE project_id = ? AND status='active' "
            "ORDER BY created_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
    return dict(row) if row else None


def get_token(connection_id: str) -> str | None:
    return SecretsVault().get_secret(_vault_key(connection_id))


def has_active_connection(project_id: str = "default") -> bool:
    return get_default_connection(project_id) is not None


def get_project_repo_for_tools(project_id: str) -> dict[str, Any] | None:
    """For agent tools: return {connection_id, owner, repo, default_branch}
    or None if the project has no repo. Prefers explicit binding; falls
    back to legacy per-project connection."""
    binding = get_project_binding(project_id)
    if binding:
        return {
            "connection_id": binding["connection_id"],
            "owner": binding["owner"],
            "repo": binding["repo"],
            "default_branch": binding["default_branch"],
        }
    legacy = get_default_connection(project_id)
    if legacy and legacy.get("owner") and legacy.get("repo"):
        return {
            "connection_id": legacy["id"],
            "owner": legacy["owner"],
            "repo": legacy["repo"],
            "default_branch": legacy.get("default_branch") or "main",
        }
    return None


def mark_used(connection_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE github_connections SET last_used_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), connection_id),
        )


def mark_error(connection_id: str, error: str) -> None:
    with _connect() as conn:
        conn.execute(
            "UPDATE github_connections SET status='error', error_message=? WHERE id = ?",
            (error[:500], connection_id),
        )


def _row_to_dict(d: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": d.get("id"), "project_id": d.get("project_id"),
        "owner": d.get("owner"), "repo": d.get("repo"),
        "full_name": d.get("full_name"),
        "default_branch": d.get("default_branch", "main"),
        "account_login": d.get("account_login"),
        "display_name": d.get("display_name"),
        "created_at": d.get("created_at"),
        "last_used_at": d.get("last_used_at"),
        "status": d.get("status", "active"),
        "error_message": d.get("error_message"),
    }
