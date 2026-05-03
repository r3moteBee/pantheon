"""GitHub integration — single-user PAT flow.

Trimmed from tuatha's multitenant client. Token is passed in directly per call;
the API surface stores tokens via the secrets vault and looks them up here.
"""
from __future__ import annotations

import base64
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

GITHUB_API = "https://api.github.com"

DEFAULT_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
    "User-Agent": "Pantheon/1.0",
}

EXPIRATION_HEADER = "github-authentication-token-expiration"


class GitHubError(Exception):
    pass


class GitHubAuthError(GitHubError):
    pass


class GitHubForbidden(GitHubError):
    pass


class GitHubNotFound(GitHubError):
    pass


@dataclass
class GitHubUserInfo:
    login: str
    account_id: str
    display_name: str
    email: str | None
    token_expires_at: datetime | None
    scopes: list[str]


def _short(s: str, n: int = 200) -> str:
    s = (s or "").strip()
    return s[:n] + "…" if len(s) > n else s


def _parse_expiration(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_scopes(headers: httpx.Headers) -> list[str]:
    scopes = headers.get("x-oauth-scopes")
    if scopes:
        return [s.strip() for s in scopes.split(",") if s.strip()]
    if EXPIRATION_HEADER in headers:
        return ["fine-grained"]
    return []


# ── Verification (one-shot, no client object) ──

async def verify_pat(token: str) -> GitHubUserInfo:
    """Validate a PAT and return user info."""
    headers = {**DEFAULT_HEADERS, "Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient(timeout=httpx.Timeout(10.0)) as client:
        resp = await client.get(f"{GITHUB_API}/user", headers=headers)

    if resp.status_code == 401:
        raise GitHubAuthError("GitHub rejected the token (401). Check it's current.")
    if resp.status_code == 403:
        raise GitHubForbidden("Token authenticated but /user is forbidden.")
    if resp.status_code >= 400:
        raise GitHubError(f"GitHub {resp.status_code}: {_short(resp.text)}")

    user = resp.json()
    return GitHubUserInfo(
        login=user["login"],
        account_id=str(user.get("id") or user["login"]),
        display_name=(user.get("name") or user["login"]).strip(),
        email=user.get("email"),
        token_expires_at=_parse_expiration(resp.headers.get(EXPIRATION_HEADER)),
        scopes=_parse_scopes(resp.headers),
    )


# ── Authenticated client (single-user) ──


class GitHubClient:
    """
    Per-call authenticated GitHub client.

    `token` is the PAT (either classic or fine-grained). Caller resolves
    the token from the secrets vault and constructs this on demand.
    """

    def __init__(self, token: str) -> None:
        if not token:
            raise GitHubAuthError("Empty token")
        self._token = token

    @property
    def headers(self) -> dict[str, str]:
        return {**DEFAULT_HEADERS, "Authorization": f"Bearer {self._token}"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: Any | None = None,
    ) -> httpx.Response:
        url = path if path.startswith("http") else f"{GITHUB_API}{path}"
        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0)) as client:
            resp = await client.request(method, url, headers=self.headers, params=params, json=json)
        if resp.status_code == 401:
            raise GitHubAuthError("GitHub rejected the token (401). Re-enter the PAT.")
        if resp.status_code == 403:
            raise GitHubForbidden(_short(resp.text) or "Forbidden — PAT lacks required permissions")
        if resp.status_code == 404:
            raise GitHubNotFound(_short(resp.text) or "Not found")
        if resp.status_code >= 400:
            raise GitHubError(f"GitHub {resp.status_code}: {_short(resp.text)}")
        return resp

    async def _paginate(self, path: str, *, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        next_url: str | None = path
        next_params = dict(params or {})
        next_params.setdefault("per_page", 100)
        while next_url:
            resp = await self._request("GET", next_url, params=next_params)
            data = resp.json()
            if isinstance(data, list):
                items.extend(data)
            link = resp.headers.get("link", "")
            next_url = None
            next_params = {}  # absolute next URL has params baked in
            for part in link.split(","):
                if 'rel="next"' in part:
                    start = part.find("<")
                    end = part.find(">")
                    if start != -1 and end != -1:
                        next_url = part[start + 1:end]
                    break
        return items

    # ── User & repos ──

    async def get_user(self) -> dict[str, Any]:
        return (await self._request("GET", "/user")).json()

    async def list_user_repos(
        self,
        *,
        affiliation: str = "owner,collaborator,organization_member",
        sort: str = "updated",
    ) -> list[dict[str, Any]]:
        return await self._paginate("/user/repos", params={"affiliation": affiliation, "sort": sort})

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        return (await self._request("GET", f"/repos/{owner}/{repo}")).json()

    # ── File reads ──

    async def list_directory(
        self, owner: str, repo: str, path: str = "", ref: str | None = None
    ) -> list[dict[str, Any]]:
        params = {"ref": ref} if ref else None
        resp = await self._request("GET", f"/repos/{owner}/{repo}/contents/{path}", params=params)
        data = resp.json()
        return data if isinstance(data, list) else [data]

    async def read_file(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> dict[str, Any]:
        """Returns dict with 'content' (utf-8 text), 'sha', 'size', 'encoding'."""
        params = {"ref": ref} if ref else None
        resp = await self._request("GET", f"/repos/{owner}/{repo}/contents/{path}", params=params)
        data = resp.json()
        if isinstance(data, list):
            raise GitHubError(f"{path} is a directory, not a file")
        encoded = data.get("content") or ""
        try:
            content = base64.b64decode(encoded).decode("utf-8")
        except Exception:
            content = ""  # binary; caller should fall back to download_url
        return {
            "content": content,
            "sha": data.get("sha"),
            "size": data.get("size"),
            "encoding": data.get("encoding"),
            "download_url": data.get("download_url"),
        }

    # ── Branch / commit / PR ──

    async def get_default_branch(self, owner: str, repo: str) -> str:
        info = await self.get_repo(owner, repo)
        return info.get("default_branch") or "main"

    async def list_branches(self, owner: str, repo: str) -> list[dict[str, Any]]:
        """List branches for a repo. Returns [{name, commit_sha}, ...]."""
        rows = await self._paginate(f"/repos/{owner}/{repo}/branches")
        out = []
        for r in rows:
            out.append({
                "name": r.get("name"),
                "commit_sha": (r.get("commit") or {}).get("sha"),
                "protected": r.get("protected", False),
            })
        return out

    async def get_ref(self, owner: str, repo: str, ref: str) -> dict[str, Any]:
        """ref is 'heads/<branch>' or 'tags/<tag>'."""
        resp = await self._request("GET", f"/repos/{owner}/{repo}/git/ref/{ref}")
        return resp.json()

    async def create_branch(
        self, owner: str, repo: str, *, new_branch: str, base_branch: str | None = None
    ) -> dict[str, Any]:
        base = base_branch or await self.get_default_branch(owner, repo)
        base_ref = await self.get_ref(owner, repo, f"heads/{base}")
        sha = base_ref["object"]["sha"]
        body = {"ref": f"refs/heads/{new_branch}", "sha": sha}
        resp = await self._request("POST", f"/repos/{owner}/{repo}/git/refs", json=body)
        return resp.json()

    async def write_files(
        self,
        owner: str,
        repo: str,
        *,
        branch: str,
        files: list[dict[str, str]],  # [{"path": "x.py", "content": "..."}]
        message: str,
    ) -> dict[str, Any]:
        """Atomic multi-file commit via the Git Trees API."""
        # 1. Get current commit + tree on the branch
        head = await self.get_ref(owner, repo, f"heads/{branch}")
        head_sha = head["object"]["sha"]
        commit_resp = await self._request("GET", f"/repos/{owner}/{repo}/git/commits/{head_sha}")
        commit = commit_resp.json()
        base_tree_sha = commit["tree"]["sha"]

        # 2. Build new tree with file blobs
        tree_entries = []
        for f in files:
            blob_body = {"content": f["content"], "encoding": "utf-8"}
            blob = (await self._request(
                "POST", f"/repos/{owner}/{repo}/git/blobs", json=blob_body
            )).json()
            tree_entries.append({
                "path": f["path"],
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            })
        new_tree = (await self._request(
            "POST", f"/repos/{owner}/{repo}/git/trees",
            json={"base_tree": base_tree_sha, "tree": tree_entries},
        )).json()

        # 3. Create the commit
        new_commit = (await self._request(
            "POST", f"/repos/{owner}/{repo}/git/commits",
            json={"message": message, "tree": new_tree["sha"], "parents": [head_sha]},
        )).json()

        # 4. Move the branch to the new commit
        await self._request(
            "PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
            json={"sha": new_commit["sha"], "force": False},
        )
        return {"commit_sha": new_commit["sha"], "branch": branch, "files": [f["path"] for f in files]}

    async def create_pr(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        head: str,        # 'feature-branch'
        base: str,        # 'main'
        body: str = "",
        draft: bool = False,
    ) -> dict[str, Any]:
        body_json = {"title": title, "head": head, "base": base, "body": body, "draft": draft}
        resp = await self._request("POST", f"/repos/{owner}/{repo}/pulls", json=body_json)
        return resp.json()

    async def merge_pr(
        self, owner: str, repo: str, pr_number: int, *, merge_method: str = "squash"
    ) -> dict[str, Any]:
        resp = await self._request(
            "PUT", f"/repos/{owner}/{repo}/pulls/{pr_number}/merge",
            json={"merge_method": merge_method},
        )
        return resp.json()
