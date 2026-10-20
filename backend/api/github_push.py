"""Push artifacts to GitHub (integrations/github_push.py) and remember what
was pushed where, so the Artifacts page can show what changed since."""
from __future__ import annotations

import hashlib
import logging
import posixpath
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from api.connections import (
    _connect, get_connection, get_project_binding, get_token, mark_error, mark_used,
)
from integrations.github import GitHubAuthError, GitHubClient, GitHubError, GitHubForbidden, GitHubNotFound
from integrations.github_push import PushError, push_files, sensitive_files, summary

logger = logging.getLogger(__name__)
router = APIRouter()


def _history_db():
    conn = _connect()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS artifact_pushes (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL,
            artifact_path TEXT NOT NULL,
            repo TEXT NOT NULL,
            branch TEXT NOT NULL,
            repo_path TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            commit_sha TEXT,
            pushed_at TEXT NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_pushes_artifact ON artifact_pushes(project_id, artifact_id, pushed_at)")
    return conn


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def collect(project_id: str, *, path_prefix: str | None = None,
            artifact_ids: list[str] | None = None) -> tuple[dict[str, bytes], dict[str, dict], str]:
    """Files to push, keyed by path relative to their folder, plus each one's
    artifact {id, path} and that folder. A folder (path_prefix) pushes its
    contents; selected artifacts push relative to their common folder."""
    from artifacts.store import get_store
    store = get_store()
    files: dict[str, bytes] = {}
    meta: dict[str, dict] = {}
    if path_prefix:
        prefix = path_prefix.strip().rstrip("/") + "/"
        for a in store.list(project_id=project_id, path_prefix=prefix, limit=10_000):
            if not a["path"].startswith(prefix):
                continue
            full = store.get(a["id"])
            data = _bytes(store, full)
            if data is not None:
                rel = full["path"][len(prefix):]
                files[rel], meta[rel] = data, {"id": full["id"], "path": full["path"]}
        return files, meta, prefix
    rows = [store.get(i) for i in (artifact_ids or [])]
    rows = [r for r in rows if r and r["project_id"] == project_id]
    if not rows:
        return {}, {}, ""
    common = posixpath.commonpath([posixpath.dirname(r["path"]) or "." for r in rows])
    common = "" if common == "." else common
    for r in rows:
        data = _bytes(store, r)
        if data is not None:
            rel = posixpath.relpath(r["path"], common) if common else r["path"]
            files[rel], meta[rel] = data, {"id": r["id"], "path": r["path"]}
    return files, meta, (common + "/" if common else "")


def _bytes(store: Any, a: dict) -> bytes | None:
    if a.get("content") is not None:
        return a["content"].encode("utf-8")
    if a.get("blob_path"):
        return store._load_blob(a["blob_path"])
    return None


def record(project_id: str, meta: dict[str, dict], files: dict[str, bytes], *, repo: str, branch: str,
           dest_dir: str, commit_sha: str | None) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _history_db() as conn:
        for rel, m in meta.items():
            conn.execute(
                "INSERT INTO artifact_pushes VALUES (?,?,?,?,?,?,?,?,?,?)",
                (uuid.uuid4().hex, project_id, m["id"], m["path"], repo, branch,
                 f"{dest_dir}/{rel}" if dest_dir else rel, _sha(files[rel]), commit_sha, now))
        conn.commit()


def push_status(project_id: str, path_prefix: str | None = None) -> list[dict[str, Any]]:
    """Each artifact's last push and whether it changed since."""
    files, meta, _ = collect(project_id, path_prefix=path_prefix) if path_prefix else ({}, {}, "")
    with _history_db() as conn:
        rows = conn.execute("SELECT * FROM artifact_pushes WHERE project_id = ? ORDER BY pushed_at",
                            (project_id,)).fetchall()
    last = {r["artifact_id"]: dict(r) for r in rows}   # the latest push wins
    out = []
    for rel, m in (meta.items() if path_prefix else []):
        p = last.get(m["id"])
        out.append({"artifact_id": m["id"], "path": m["path"], "last_push": p,
                    "changed": p is None or p["content_sha256"] != _sha(files[rel])})
    if not path_prefix:
        out = [{"artifact_id": k, "path": v["artifact_path"], "last_push": v, "changed": None}
               for k, v in last.items()]
    return out


def resolve_repo(project_id: str, connection_id: str | None, owner: str | None,
                 repo: str | None) -> tuple[dict, str, str]:
    """(connection row, owner, repo): the given repo, else the project's binding."""
    if not (owner and repo):
        b = get_project_binding(project_id)
        if not b:
            raise PushError("No repository given and the project has none bound. Pick one, or bind a "
                            "repo to the project.")
        connection_id, owner, repo = connection_id or b["connection_id"], b["owner"], b["repo"]
    if not connection_id:
        b = get_project_binding(project_id)
        connection_id = b["connection_id"] if b else None
    conn = get_connection(connection_id) if connection_id else None
    if not conn:
        raise PushError("GitHub connection not found; add a token in Settings → Connections.")
    return conn, owner, repo


async def run_push(project_id: str, files: dict[str, bytes], meta: dict[str, dict], *, conn: dict,
                   owner: str, repo: str, **kw: Any) -> dict[str, Any]:
    token = get_token(conn["id"])
    if not token:
        raise PushError("That GitHub connection has no stored token; re-add it in Settings → Connections.")
    client = GitHubClient(token)
    try:
        res = await push_files(client, owner, repo, files, **kw)
    except GitHubAuthError as e:
        mark_error(conn["id"], str(e))
        raise PushError(f"GitHub rejected the token: {e}")
    except GitHubForbidden as e:
        raise PushError(f"GitHub refused the push (the token may lack write access to {owner}/{repo}): {e}")
    except GitHubNotFound as e:
        raise PushError(f"GitHub: {owner}/{repo} not found or not visible to this token ({e}).")
    except GitHubError as e:
        raise PushError(f"GitHub error: {e}")
    mark_used(conn["id"])
    if res.changed or res.unchanged:
        record(project_id, meta, files, repo=res.repo, branch=res.branch,
               dest_dir=kw.get("dest_dir") or "", commit_sha=res.commit_sha)
    return {"repo": res.repo, "branch": res.branch, "base_branch": res.base_branch,
            "created_branch": res.created_branch, "commit_sha": res.commit_sha, "commit_url": res.commit_url,
            "uploaded": res.uploaded, "unchanged": res.unchanged, "deleted": res.deleted,
            "pr_number": res.pr_number, "pr_url": res.pr_url, "pr_existing": res.pr_existing,
            "summary": summary(res)}


# ── HTTP ─────────────────────────────────────────────────────────────────────

class PushRequest(BaseModel):
    path_prefix: str | None = None
    artifact_ids: list[str] | None = None
    connection_id: str | None = None
    owner: str | None = None
    repo: str | None = None
    branch: str
    base_branch: str | None = None
    dest_dir: str = ""
    message: str = Field(default="", max_length=5000)
    open_pr: bool = True
    pr_title: str | None = None
    pr_body: str = ""
    mirror: bool = False
    allow_default_branch: bool = False
    confirm_sensitive: bool = False


@router.post("/projects/{project_id}/github/push")
async def push(project_id: str, req: PushRequest) -> dict[str, Any]:
    if not req.path_prefix and not req.artifact_ids:
        raise HTTPException(400, "path_prefix or artifact_ids is required")
    files, meta, folder = collect(project_id, path_prefix=req.path_prefix, artifact_ids=req.artifact_ids)
    if not files:
        raise HTTPException(404, "no artifacts to push")
    flagged = sensitive_files(files)
    if flagged and not req.confirm_sensitive:
        raise HTTPException(409, {"message": "These files look like they contain credentials.",
                                  "sensitive": flagged})
    try:
        conn, owner, repo = resolve_repo(project_id, req.connection_id, req.owner, req.repo)
        name = folder.rstrip("/").rsplit("/", 1)[-1] or "artifacts"
        return await run_push(
            project_id, files, meta, conn=conn, owner=owner, repo=repo, branch=req.branch,
            message=req.message.strip() or f"Update {name} from Pantheon", dest_dir=req.dest_dir,
            base_branch=req.base_branch, mirror=req.mirror, open_pr=req.open_pr,
            pr_title=req.pr_title, pr_body=req.pr_body, allow_default_branch=req.allow_default_branch)
    except PushError as e:
        raise HTTPException(400, str(e))


@router.get("/projects/{project_id}/github/pushes")
async def pushes(project_id: str, path_prefix: str | None = None) -> dict[str, Any]:
    items = push_status(project_id, path_prefix)
    return {"items": items, "changed": sum(1 for i in items if i["changed"]),
            "pushed": sum(1 for i in items if i["last_push"])}


class CreateRepoRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._-]+$")
    private: bool = True
    description: str = ""


@router.post("/connections/github/{connection_id}/create-repo")
async def create_repo(connection_id: str, req: CreateRepoRequest) -> dict[str, Any]:
    conn = get_connection(connection_id)
    token = get_token(connection_id) if conn else None
    if not token:
        raise HTTPException(404, "connection not found")
    try:
        r = await GitHubClient(token).create_repo(req.name, private=req.private, description=req.description)
    except GitHubForbidden as e:
        raise HTTPException(403, f"GitHub refused to create the repository (the token needs the "
                                 f"'repo' scope or Administration write): {e}")
    except GitHubError as e:
        raise HTTPException(400, f"GitHub error: {e}")
    mark_used(connection_id)
    return {"owner": r["owner"]["login"], "repo": r["name"], "full_name": r["full_name"],
            "private": r.get("private"), "default_branch": r.get("default_branch") or "main",
            "html_url": r.get("html_url")}
