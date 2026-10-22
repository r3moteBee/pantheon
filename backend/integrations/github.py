"""GitHub integration — single-user PAT flow.

Trimmed from tuatha's multitenant client. Token is passed in directly per call;
the API surface stores tokens via the secrets vault and looks them up here.
"""
from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import quote, unquote, urlsplit

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


class GitHubInvalidName(GitHubError, ValueError):
    """An owner/repo/branch/path that would not stay inside /repos/{owner}/{repo}/."""


# ── Name / path validation ──
# Every value the model (or a request body) supplies goes into a URL path
# under /repos/{owner}/{repo}/. httpx normalises dot segments, so an
# unencoded "../../other" would leave that prefix (2026-10-22 review).
# All URL building goes through repo_path(); _request() refuses any path
# that still carries a dot segment.

_OWNER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_CTRL_RE = re.compile(r"[\x00-\x1f\x7f]")


def validate_owner(owner: str) -> str:
    """GitHub user/org names: letters, digits and '-', 1-39 chars."""
    if not isinstance(owner, str) or not _OWNER_RE.match(owner):
        raise GitHubInvalidName(f"Invalid GitHub owner name: {owner!r}")
    return owner


def validate_repo_name(repo: str) -> str:
    """GitHub repo names: letters, digits, '.', '_', '-'; never '.' or '..'."""
    if not isinstance(repo, str) or not _REPO_RE.match(repo) or repo in (".", ".."):
        raise GitHubInvalidName(f"Invalid GitHub repository name: {repo!r}")
    return repo


def validate_ref_name(name: str, what: str = "branch") -> str:
    """git check-ref-format rules for a branch/tag/ref name (plus no '%',
    so nothing decodes into a dot segment server-side)."""
    bad = (
        not isinstance(name, str) or not name or len(name) > 255
        or name.startswith(("/", "-", ".")) or name.endswith(("/", ".", ".lock"))
        or ".." in name or "//" in name or "@{" in name or name == "@"
        or _CTRL_RE.search(name) is not None
        or any(c in name for c in " ~^:?*[\\%")
        or any(seg.startswith(".") or seg.endswith(".lock") for seg in name.split("/"))
    )
    if bad:
        raise GitHubInvalidName(f"Invalid {what} name: {name!r}")
    return name


def quote_repo_file_path(path: str) -> str:
    """A file/directory path inside a repo, URL-quoted per segment.
    '' (repo root) is allowed; '.'/'..' segments, backslashes and control
    characters are not."""
    if path is None:
        path = ""
    if not isinstance(path, str):
        raise GitHubInvalidName(f"Invalid repo path: {path!r}")
    if "\\" in path or _CTRL_RE.search(path):
        raise GitHubInvalidName(f"Invalid repo path: {path!r}")
    p = path.strip("/")
    if not p:
        return ""
    for seg in p.split("/"):
        if seg == "" or seg in (".", "..") or unquote(seg) in (".", ".."):
            raise GitHubInvalidName(f"Invalid repo path: {path!r}")
    return quote(p, safe="/")


def repo_path(owner: str, repo: str, *parts: str) -> str:
    """'/repos/{owner}/{repo}/...' from validated owner/repo and already
    validated (and quoted) parts — the one place repo URLs are built."""
    base = f"/repos/{validate_owner(owner)}/{validate_repo_name(repo)}"
    tail = "/".join(p for p in parts if p)
    return f"{base}/{tail}" if tail else base


def _ref_part(name: str, what: str = "branch") -> str:
    return quote(validate_ref_name(name, what), safe="/")


def _has_dot_segment(path: str) -> bool:
    for seg in urlsplit(path).path.split("/"):
        s = seg
        for _ in range(3):  # %252e%252e -> %2e%2e -> ..
            s = unquote(s)
        if s in (".", "..") or "\\" in s:
            return True
    return False


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

    def __init__(self, token: str, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        if not token:
            raise GitHubAuthError("Empty token")
        self._token = token
        self._transport = transport  # tests pass httpx.MockTransport

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
        if path.startswith("http"):
            # Only pagination 'next' links: never send the token elsewhere.
            if not path.startswith(GITHUB_API + "/"):
                raise GitHubInvalidName(f"Refusing non-GitHub API URL: {path[:100]!r}")
            url = path
        else:
            url = f"{GITHUB_API}{path}"
        if _has_dot_segment(url):
            raise GitHubInvalidName(f"Refusing GitHub API path with a dot segment: {path[:200]!r}")
        kw: dict[str, Any] = {"timeout": httpx.Timeout(20.0)}
        if self._transport is not None:
            kw["transport"] = self._transport
        async with httpx.AsyncClient(**kw) as client:
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

    async def create_repo(self, name: str, *, private: bool = True, description: str = "") -> dict[str, Any]:
        """New repo under the token's user, with a first commit (README) so
        branches can be made from it right away."""
        resp = await self._request("POST", "/user/repos", json={
            "name": name, "private": private, "description": description, "auto_init": True})
        return resp.json()

    async def get_repo(self, owner: str, repo: str) -> dict[str, Any]:
        return (await self._request("GET", repo_path(owner, repo))).json()

    # ── File reads ──

    async def list_directory(
        self, owner: str, repo: str, path: str = "", ref: str | None = None
    ) -> list[dict[str, Any]]:
        params = {"ref": ref} if ref else None
        resp = await self._request("GET", repo_path(owner, repo, "contents", quote_repo_file_path(path)),
                                   params=params)
        data = resp.json()
        return data if isinstance(data, list) else [data]

    async def read_file(
        self, owner: str, repo: str, path: str, ref: str | None = None
    ) -> dict[str, Any]:
        """Returns dict with 'content' (utf-8 text), 'sha', 'size', 'encoding'."""
        qpath = quote_repo_file_path(path)
        if not qpath:
            raise GitHubInvalidName("read_file needs a file path")
        params = {"ref": ref} if ref else None
        resp = await self._request("GET", repo_path(owner, repo, "contents", qpath), params=params)
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
        rows = await self._paginate(repo_path(owner, repo, "branches"))
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
        kind, _, name = (ref or "").partition("/")
        if kind not in ("heads", "tags"):
            raise GitHubInvalidName(f"Invalid ref (want heads/<branch> or tags/<tag>): {ref!r}")
        resp = await self._request("GET", repo_path(owner, repo, "git", "ref", kind, _ref_part(name)))
        return resp.json()

    async def create_branch(
        self, owner: str, repo: str, *, new_branch: str, base_branch: str | None = None
    ) -> dict[str, Any]:
        validate_ref_name(new_branch)
        base = base_branch or await self.get_default_branch(owner, repo)
        base_ref = await self.get_ref(owner, repo, f"heads/{base}")
        sha = base_ref["object"]["sha"]
        body = {"ref": f"refs/heads/{new_branch}", "sha": sha}
        resp = await self._request("POST", repo_path(owner, repo, "git", "refs"), json=body)
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
        validate_ref_name(branch)
        for f in files:
            if not quote_repo_file_path(f.get("path") or ""):
                raise GitHubInvalidName("write_files: empty file path")
        # 1. Get current commit + tree on the branch
        head = await self.get_ref(owner, repo, f"heads/{branch}")
        head_sha = head["object"]["sha"]
        commit_resp = await self._request("GET", repo_path(owner, repo, "git", "commits", quote(str(head_sha), safe="")))
        commit = commit_resp.json()
        base_tree_sha = commit["tree"]["sha"]

        # 2. Build new tree with file blobs
        tree_entries = []
        for f in files:
            blob_body = {"content": f["content"], "encoding": "utf-8"}
            blob = (await self._request(
                "POST", repo_path(owner, repo, "git", "blobs"), json=blob_body
            )).json()
            tree_entries.append({
                "path": f["path"],
                "mode": "100644",
                "type": "blob",
                "sha": blob["sha"],
            })
        new_tree = (await self._request(
            "POST", repo_path(owner, repo, "git", "trees"),
            json={"base_tree": base_tree_sha, "tree": tree_entries},
        )).json()

        # 3. Create the commit
        new_commit = (await self._request(
            "POST", repo_path(owner, repo, "git", "commits"),
            json={"message": message, "tree": new_tree["sha"], "parents": [head_sha]},
        )).json()

        # 4. Move the branch to the new commit
        await self._request(
            "PATCH", repo_path(owner, repo, "git", "refs", "heads", _ref_part(branch)),
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
        validate_ref_name(base, "base branch")
        if ":" in head:  # 'owner:branch' for cross-fork PRs
            validate_owner(head.split(":", 1)[0])
            validate_ref_name(head.split(":", 1)[1], "head branch")
        else:
            validate_ref_name(head, "head branch")
        body_json = {"title": title, "head": head, "base": base, "body": body, "draft": draft}
        resp = await self._request("POST", repo_path(owner, repo, "pulls"), json=body_json)
        return resp.json()

    async def merge_pr(
        self, owner: str, repo: str, pr_number: int, *, merge_method: str = "squash"
    ) -> dict[str, Any]:
        resp = await self._request(
            "PUT", repo_path(owner, repo, "pulls", str(int(pr_number)), "merge"),
            json={"merge_method": merge_method},
        )
        return resp.json()

    async def list_pulls(
        self, owner: str, repo: str, *, state: str = "open",
        head: str | None = None, base: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"state": state, "per_page": 100}
        if head:
            params["head"] = head
        if base:
            params["base"] = base
        rows = await self._paginate(repo_path(owner, repo, "pulls"), params=params)
        out = []
        for r in rows:
            out.append({
                "number": r.get("number"),
                "title": r.get("title"),
                "state": r.get("state"),
                "head": (r.get("head") or {}).get("ref"),
                "base": (r.get("base") or {}).get("ref"),
                "html_url": r.get("html_url"),
                "draft": r.get("draft", False),
            })
        return out

    async def delete_branch(self, owner: str, repo: str, branch: str) -> None:
        """Delete a branch ref. 422 from GitHub if it's the default branch."""
        await self._request(
            "DELETE", repo_path(owner, repo, "git", "refs", "heads", _ref_part(branch)),
        )
