"""Push a folder of artifacts to a GitHub repo as one commit.

The files come from the artifact store, not from the model: the agent (or
the Artifacts page) names a folder and the server sends it. Uses the Git
Data API - blobs, one tree, one commit, then the branch ref - so a push is
atomic and needs no checkout. Files whose git blob hash already matches the
branch are not uploaded again, so a re-push sends only what changed.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from integrations.github import GitHubClient, GitHubError, GitHubNotFound

logger = logging.getLogger(__name__)

MAX_FILES = 1000
MAX_TOTAL_BYTES = 50 * 1024 * 1024
MAX_FILE_BYTES = 50 * 1024 * 1024      # the API refuses blobs over 100 MB

# Paths and contents that usually mean a credential. The Artifacts page asks
# before pushing them; the agent is refused outright.
_SENSITIVE_NAME = re.compile(
    r"(^|/)(\.env(\..*)?|.*\.(pem|key|p12|pfx|keystore|jks)|id_(rsa|dsa|ecdsa|ed25519)(\.pub)?|"
    r"credentials(\.\w+)?|secrets?\.(json|ya?ml|env|toml)|\.netrc|\.npmrc|\.pypirc)$", re.I)
_SENSITIVE_TEXT = re.compile(
    r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----|\bAKIA[0-9A-Z]{16}\b|"
    r"\bgh[pousr]_[A-Za-z0-9]{36,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b|\bsk-[A-Za-z0-9_-]{32,}\b|"
    r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")


class PushError(Exception):
    """The push was not made (bad input, limits, refused branch, GitHub error)."""


@dataclass
class PushResult:
    repo: str
    branch: str
    base_branch: str
    commit_sha: str | None
    commit_url: str | None
    uploaded: list[str] = field(default_factory=list)    # new or changed
    unchanged: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    created_branch: bool = False
    pr_number: int | None = None
    pr_url: str | None = None
    pr_existing: bool = False

    @property
    def changed(self) -> bool:
        return self.commit_sha is not None


def git_blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def sensitive_files(files: dict[str, bytes]) -> list[str]:
    out = []
    for path, data in files.items():
        if _SENSITIVE_NAME.search(path):
            out.append(path)
            continue
        try:
            text = data[:200_000].decode("utf-8")
        except UnicodeDecodeError:
            continue
        if _SENSITIVE_TEXT.search(text):
            out.append(path)
    return sorted(out)


def clean_repo_path(p: str | None) -> str:
    """A repo-relative folder: no leading/trailing slash, no '..' or empty parts."""
    p = (p or "").strip().strip("/")
    if p and any(part in ("", ".", "..") for part in p.split("/")):
        raise PushError(f"{p!r} is not a plain folder path inside the repo")
    return p


def check_limits(files: dict[str, bytes]) -> None:
    if not files:
        raise PushError("Nothing to push: no files.")
    if len(files) > MAX_FILES:
        raise PushError(f"{len(files)} files; a push takes at most {MAX_FILES}.")
    big = [p for p, d in files.items() if len(d) > MAX_FILE_BYTES]
    if big:
        raise PushError(f"Files over {MAX_FILE_BYTES // 1048576} MB: {', '.join(big[:5])}")
    total = sum(len(d) for d in files.values())
    if total > MAX_TOTAL_BYTES:
        raise PushError(f"{total / 1048576:.1f} MB in total; a push takes at most "
                        f"{MAX_TOTAL_BYTES // 1048576} MB.")


async def push_files(client: GitHubClient, owner: str, repo: str, files: dict[str, bytes], *,
                     branch: str, message: str, dest_dir: str = "", base_branch: str | None = None,
                     mirror: bool = False, open_pr: bool = True, pr_title: str | None = None,
                     pr_body: str = "", allow_default_branch: bool = False) -> PushResult:
    """Commit files (path relative to dest_dir -> bytes) to branch.

    The branch is created from base_branch (default: the repo's default
    branch) when missing. Pushing to the default branch itself needs
    allow_default_branch. mirror deletes files under dest_dir that are not in
    files. open_pr opens (or finds) a PR from branch into base_branch."""
    check_limits(files)
    dest = clean_repo_path(dest_dir)
    branch = (branch or "").strip()
    if not branch or branch.startswith("-") or ".." in branch or " " in branch:
        raise PushError(f"Invalid branch name {branch!r}.")
    if mirror and not dest:
        raise PushError("Mirroring needs a folder inside the repo (dest_dir): it would delete "
                        "every other file in the repo.")
    info = await client.get_repo(owner, repo)
    default = info.get("default_branch") or "main"
    base = (base_branch or default).strip()
    if branch == default and not allow_default_branch:
        raise PushError(f"Refusing to push straight to the default branch {default!r}; push to a new "
                        "branch and open a pull request.")
    full = f"{owner}/{repo}"
    result = PushResult(repo=full, branch=branch, base_branch=base, commit_sha=None, commit_url=None)

    try:
        head_sha = (await client.get_ref(owner, repo, f"heads/{branch}"))["object"]["sha"]
    except GitHubNotFound:
        try:
            base_sha = (await client.get_ref(owner, repo, f"heads/{base}"))["object"]["sha"]
        except GitHubNotFound:
            raise PushError(f"{full} has no branch {base!r} to start {branch!r} from "
                            "(an empty repository needs a first commit, e.g. a README).")
        await client._request("POST", f"/repos/{owner}/{repo}/git/refs",
                              json={"ref": f"refs/heads/{branch}", "sha": base_sha})
        head_sha, result.created_branch = base_sha, True
    except GitHubError as e:
        if "empty" in str(e).lower():
            raise PushError(f"{full} is empty; give it a first commit (e.g. a README) and push again.")
        raise

    commit = (await client._request("GET", f"/repos/{owner}/{repo}/git/commits/{head_sha}")).json()
    base_tree = commit["tree"]["sha"]
    tree = (await client._request("GET", f"/repos/{owner}/{repo}/git/trees/{base_tree}",
                                  params={"recursive": "1"})).json()
    existing = {e["path"]: e["sha"] for e in tree.get("tree", []) if e.get("type") == "blob"}
    if mirror and tree.get("truncated"):
        raise PushError(f"{full} is too large to list in one call, so files to delete can't be "
                        "worked out; push without mirroring.")

    entries: list[dict[str, Any]] = []
    wanted = set()
    for rel in sorted(files):
        data = files[rel]
        path = f"{dest}/{rel}" if dest else rel
        wanted.add(path)
        if existing.get(path) == git_blob_sha(data):
            result.unchanged.append(path)
            continue
        try:
            blob_body = {"content": data.decode("utf-8"), "encoding": "utf-8"}
        except UnicodeDecodeError:
            blob_body = {"content": base64.b64encode(data).decode(), "encoding": "base64"}
        blob = (await client._request("POST", f"/repos/{owner}/{repo}/git/blobs", json=blob_body)).json()
        entries.append({"path": path, "mode": "100644", "type": "blob", "sha": blob["sha"]})
        result.uploaded.append(path)
    if mirror:
        for path in sorted(existing):
            if path.startswith(dest + "/") and path not in wanted:
                entries.append({"path": path, "mode": "100644", "type": "blob", "sha": None})
                result.deleted.append(path)

    if entries:
        new_tree = (await client._request("POST", f"/repos/{owner}/{repo}/git/trees",
                                          json={"base_tree": base_tree, "tree": entries})).json()
        new_commit = (await client._request("POST", f"/repos/{owner}/{repo}/git/commits", json={
            "message": message, "tree": new_tree["sha"], "parents": [head_sha]})).json()
        await client._request("PATCH", f"/repos/{owner}/{repo}/git/refs/heads/{branch}",
                              json={"sha": new_commit["sha"], "force": False})
        result.commit_sha = new_commit["sha"]
        result.commit_url = f"https://github.com/{full}/commit/{new_commit['sha']}"

    if open_pr and branch != base:
        pulls = await client.list_pulls(owner, repo, state="open", head=f"{owner}:{branch}", base=base)
        if pulls:
            result.pr_number, result.pr_url, result.pr_existing = pulls[0]["number"], pulls[0]["html_url"], True
        elif result.changed or not result.created_branch:
            try:
                pr = await client.create_pr(owner, repo, title=pr_title or message.splitlines()[0],
                                            head=branch, base=base, body=pr_body)
                result.pr_number, result.pr_url = pr.get("number"), pr.get("html_url")
            except GitHubError as e:
                if "No commits between" not in str(e):
                    raise
    return result


def summary(r: PushResult) -> str:
    """One paragraph for the agent / API callers."""
    if not r.changed:
        head = f"Nothing to commit: all {len(r.unchanged)} file(s) already match {r.repo}@{r.branch}."
    else:
        head = (f"Pushed to {r.repo}@{r.branch}"
                + (f" (new branch from {r.base_branch})" if r.created_branch else "")
                + f": {len(r.uploaded)} new or changed, {len(r.unchanged)} unchanged"
                + (f", {len(r.deleted)} deleted" if r.deleted else "")
                + f". Commit: {r.commit_url}")
    if r.pr_url:
        head += f"\nPull request #{r.pr_number}{' (already open)' if r.pr_existing else ''}: {r.pr_url}"
    return head
