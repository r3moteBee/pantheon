"""GitHub source adapters.

Two genres:
  - github/release     a single tagged release on a repo. Fetched via
                       the GitHub REST API
                       (``GET /repos/{owner}/{repo}/releases/tags/{tag}``
                       or ``/releases/latest``). Body is the release
                       notes markdown.
  - github/changelog   a CHANGELOG-shaped file in a repo (default
                       ``CHANGELOG.md`` on the default branch). Fetched
                       as raw text. Useful when projects don't tag
                       releases but maintain a single accumulating
                       file.

Authentication. If the project (or the default project binding) has
an active GitHub PAT in the connections vault, the adapter uses it
for higher rate limits. Without a token, it works on public repos
under the unauthenticated rate budget (60 req/hr/IP) — ample for
research workloads. Both modes are correct; auth just gives headroom.

Identifier formats accepted:
  - github/release
      * https://github.com/<owner>/<repo>/releases/tag/<tag>
      * https://github.com/<owner>/<repo>/releases/latest
      * <owner>/<repo>:<tag>           (e.g. ``anthropics/claude-code:v1.4.2``)
      * <owner>/<repo>:latest
      * <owner>/<repo>                 (resolves to ``latest``)
  - github/changelog
      * https://github.com/<owner>/<repo>            (assumes default branch + ``CHANGELOG.md``)
      * https://github.com/<owner>/<repo>/blob/<branch>/<path>
      * https://raw.githubusercontent.com/<owner>/<repo>/<branch>/<path>
      * <owner>/<repo>                 (default branch + ``CHANGELOG.md``)
      * <owner>/<repo>:<branch>:<path>
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sources.base import (
    FetchedContent,
    IngestRequest,
    SourceAdapter,
)
from sources.registry import register_adapter
from sources.util import slugify

logger = logging.getLogger(__name__)

_GITHUB_API = "https://api.github.com"
_USER_AGENT = "Pantheon/1.0 (research-harness)"
_HTTP_TIMEOUT = 30


def _resolve_token(project_id: str) -> str | None:
    """Best-effort token lookup. Returns None if no connection is
    bound — adapters then fall through to unauthenticated calls."""
    try:
        from api.connections import get_default_connection, get_token
    except Exception as e:
        logger.debug("github adapter: connections module unavailable (%s)", e)
        return None
    try:
        conn = get_default_connection(project_id)
        if not conn:
            return None
        return get_token(conn["id"])
    except Exception as e:
        logger.debug("github adapter: token lookup failed for %s: %s", project_id, e)
        return None


def _gh_headers(token: str | None) -> dict[str, str]:
    h = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _USER_AGENT,
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


async def _http_get(url: str, *, headers: dict | None = None) -> "httpx.Response":
    import httpx
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        r = await client.get(url, headers=headers or {})
        return r


# ── github/release ────────────────────────────────────────────────

_RELEASE_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/releases/(?:tag/(?P<tag>[^/?#]+)|(?P<latest>latest))",
    re.IGNORECASE,
)
_REPO_TAG_RE = re.compile(
    r"^(?P<owner>[^/]+)/(?P<repo>[^/:]+)(?::(?P<tag>.+))?$",
)


def _parse_release_identifier(identifier: str) -> tuple[str, str, str]:
    """Return (owner, repo, tag_or_latest). ``tag_or_latest`` is the
    literal string 'latest' to mean "use /releases/latest", otherwise
    a tag like 'v1.4.2'."""
    m = _RELEASE_URL_RE.match(identifier or "")
    if m:
        owner = m.group("owner")
        repo = m.group("repo").removesuffix(".git")
        tag = m.group("tag") or "latest"
        return owner, repo, tag
    m = _REPO_TAG_RE.match((identifier or "").strip())
    if not m:
        raise RuntimeError(
            f"github/release: cannot parse identifier {identifier!r}; "
            f"expected URL or '<owner>/<repo>[:<tag>]'"
        )
    owner = m.group("owner")
    repo = m.group("repo").removesuffix(".git")
    tag = m.group("tag") or "latest"
    return owner, repo, tag


class GitHubRelease(SourceAdapter):
    source_type = "github/release"
    display_name = "GitHub — tagged release"
    bucket_aliases = ("github", "release")
    requires_mcp = ()
    artifact_path_template = (
        "github/releases/{owner_repo}/{tag_slug}.md"
    )
    extractor_strategy = "llm_changelog"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        owner, repo, tag = _parse_release_identifier(req.identifier)
        if tag == "latest":
            api_url = f"{_GITHUB_API}/repos/{owner}/{repo}/releases/latest"
        else:
            api_url = f"{_GITHUB_API}/repos/{owner}/{repo}/releases/tags/{tag}"

        token = _resolve_token(req.project_id)
        r = await _http_get(api_url, headers=_gh_headers(token))
        if r.status_code == 404:
            raise RuntimeError(
                f"github/release: 404 for {owner}/{repo}@{tag} "
                f"(repo private, tag missing, or no releases?)"
            )
        if r.status_code == 403 and "rate limit" in (r.text or "").lower():
            raise RuntimeError(
                "github/release: rate-limited (consider connecting a "
                "GitHub PAT in Connections to raise the budget)"
            )
        r.raise_for_status()
        rel = r.json() or {}

        body_md = (rel.get("body") or "").strip()
        if not body_md:
            body_md = "_(release has no body / notes)_"
        # Add a small header so the artifact reads cleanly without
        # depending purely on frontmatter for context.
        title = rel.get("name") or rel.get("tag_name") or f"{owner}/{repo}@{tag}"
        head = [
            f"# {title}",
            f"**{owner}/{repo}** · tag `{rel.get('tag_name', tag)}`",
        ]
        if rel.get("published_at"):
            head.append(f"Published: {rel['published_at'][:10]}")
        head.append("")
        body = "\n".join(head) + "\n" + body_md + "\n"

        published_at = (rel.get("published_at") or "")[:10] or None
        return FetchedContent(
            text=body,
            title=title,
            author_or_publisher=f"{owner}/{repo}",
            url=rel.get("html_url") or f"https://github.com/{owner}/{repo}/releases/tag/{rel.get('tag_name', tag)}",
            published_at=published_at,
            extra_meta={
                "owner": owner,
                "repo": repo,
                "tag": rel.get("tag_name") or tag,
                "release_id": rel.get("id"),
                "draft": rel.get("draft"),
                "prerelease": rel.get("prerelease"),
                "author_login": ((rel.get("author") or {}).get("login")) or "",
                "retrieved_at": (req.extras or {}).get("retrieved_at"),
                "fetch_method": "github_api",
                "auth": "pat" if token else "anonymous",
            },
        )

    def render_artifact_path(self, req, fetched):
        owner = fetched.extra_meta.get("owner", "")
        repo = fetched.extra_meta.get("repo", "")
        tag = fetched.extra_meta.get("tag", "")
        return self.artifact_path_template.format(
            owner_repo=slugify(f"{owner}-{repo}") or "unknown",
            tag_slug=slugify(tag) or "latest",
            slug=slugify(fetched.title) or "release",
            published_at=fetched.published_at or "unknown-date",
            author_or_publisher=slugify(fetched.author_or_publisher) or "unknown",
            identifier=slugify(req.identifier),
            source_type=self.source_type.replace("/", "-"),
        )


# ── github/changelog ──────────────────────────────────────────────

_BLOB_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/blob/(?P<branch>[^/]+)/(?P<path>.+)$",
    re.IGNORECASE,
)
_RAW_URL_RE = re.compile(
    r"^https?://raw\.githubusercontent\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/(?P<branch>[^/]+)/(?P<path>.+)$",
    re.IGNORECASE,
)
_REPO_ROOT_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/?$",
    re.IGNORECASE,
)
_REPO_BRANCH_PATH_RE = re.compile(
    r"^(?P<owner>[^/]+)/(?P<repo>[^/:]+)(?::(?P<branch>[^:]+)(?::(?P<path>.+))?)?$",
)


def _parse_changelog_identifier(identifier: str) -> tuple[str, str, str | None, str | None]:
    """Return (owner, repo, branch_or_None, path_or_None). When
    branch / path are None the adapter resolves them at fetch time
    (default branch + ``CHANGELOG.md``)."""
    s = (identifier or "").strip()
    m = _RAW_URL_RE.match(s)
    if m:
        return (
            m.group("owner"), m.group("repo").removesuffix(".git"),
            m.group("branch"), m.group("path"),
        )
    m = _BLOB_URL_RE.match(s)
    if m:
        return (
            m.group("owner"), m.group("repo").removesuffix(".git"),
            m.group("branch"), m.group("path"),
        )
    m = _REPO_ROOT_URL_RE.match(s)
    if m:
        return m.group("owner"), m.group("repo").removesuffix(".git"), None, None
    m = _REPO_BRANCH_PATH_RE.match(s)
    if m:
        return (
            m.group("owner"), m.group("repo").removesuffix(".git"),
            m.group("branch"), m.group("path"),
        )
    raise RuntimeError(
        f"github/changelog: cannot parse identifier {s!r}; expected URL "
        f"or '<owner>/<repo>[:<branch>[:<path>]]'"
    )


async def _resolve_default_branch(owner: str, repo: str, token: str | None) -> str:
    r = await _http_get(
        f"{_GITHUB_API}/repos/{owner}/{repo}",
        headers=_gh_headers(token),
    )
    if r.status_code == 404:
        raise RuntimeError(f"github/changelog: 404 for {owner}/{repo}")
    r.raise_for_status()
    info = r.json() or {}
    return info.get("default_branch") or "main"


_CHANGELOG_CANDIDATES = (
    "CHANGELOG.md", "CHANGELOG", "CHANGES.md", "CHANGES", "HISTORY.md",
    "docs/CHANGELOG.md", "docs/changelog.md", "release-notes.md",
    "RELEASE_NOTES.md",
    # Common monorepo layout — try the package named after the repo
    # (e.g. vitejs/vite keeps its changelog at packages/vite/CHANGELOG.md).
    "packages/{repo}/CHANGELOG.md",
    "packages/core/CHANGELOG.md",
)


async def _find_changelog_path(owner: str, repo: str, branch: str, token: str | None) -> str:
    """Probe the standard candidate paths until one returns 200.

    Uses GET with a Range header rather than HEAD because
    raw.githubusercontent.com\'s HEAD handling is inconsistent (often
    returns 405 or 403 even for files that exist). Range bytes=0-127
    keeps the response tiny.
    """
    import httpx
    tried: list[str] = []
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        for cand_template in _CHANGELOG_CANDIDATES:
            cand = cand_template.format(repo=repo)
            tried.append(cand)
            url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{cand}"
            try:
                r = await client.get(
                    url,
                    headers={
                        "User-Agent": _USER_AGENT,
                        "Range": "bytes=0-127",
                    },
                )
                # 200 (full body, server ignored Range) or 206 (partial)
                # both mean the file exists and is fetchable.
                if r.status_code in (200, 206) and r.content:
                    return cand
            except Exception:
                continue
    raise RuntimeError(
        f"github/changelog: no CHANGELOG-like file in {owner}/{repo}@{branch} "
        f"(probed: {', '.join(tried)})"
    )


class GitHubChangelog(SourceAdapter):
    source_type = "github/changelog"
    display_name = "GitHub — repo CHANGELOG file"
    bucket_aliases = ("github", "changelog")
    requires_mcp = ()
    artifact_path_template = (
        "github/changelogs/{owner_repo}/{branch}-{path_slug}.md"
    )
    extractor_strategy = "llm_changelog"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        owner, repo, branch, path = _parse_changelog_identifier(req.identifier)
        token = _resolve_token(req.project_id)

        if not branch:
            branch = await _resolve_default_branch(owner, repo, token)
        if not path:
            path = await _find_changelog_path(owner, repo, branch, token)

        raw_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{path}"
        r = await _http_get(raw_url, headers={"User-Agent": _USER_AGENT})
        if r.status_code == 404:
            raise RuntimeError(
                f"github/changelog: 404 for {owner}/{repo}@{branch}:{path}"
            )
        r.raise_for_status()
        text = r.text or ""
        if len(text.strip()) < 50:
            raise RuntimeError(
                f"github/changelog: file at {raw_url} is < 50 chars; "
                f"likely empty or wrong path"
            )

        # Pull the most recent commit date for the file as published_at,
        # so chronological grouping has something useful to work with.
        published_at: str | None = None
        try:
            commits_url = (
                f"{_GITHUB_API}/repos/{owner}/{repo}/commits"
                f"?path={path}&sha={branch}&per_page=1"
            )
            cr = await _http_get(commits_url, headers=_gh_headers(token))
            if cr.status_code == 200:
                items = cr.json() or []
                if items and isinstance(items, list):
                    iso = ((items[0].get("commit") or {})
                           .get("author", {}).get("date") or "")
                    if iso:
                        published_at = iso[:10]
        except Exception as e:
            logger.debug("github/changelog: latest-commit lookup failed: %s", e)

        title = f"{owner}/{repo} — {path}"
        return FetchedContent(
            text=text,
            title=title,
            author_or_publisher=f"{owner}/{repo}",
            url=f"https://github.com/{owner}/{repo}/blob/{branch}/{path}",
            published_at=published_at,
            extra_meta={
                "owner": owner,
                "repo": repo,
                "branch": branch,
                "path": path,
                "retrieved_at": (req.extras or {}).get("retrieved_at"),
                "fetch_method": "github_raw",
                "auth": "pat" if token else "anonymous",
            },
        )

    def render_artifact_path(self, req, fetched):
        owner = fetched.extra_meta.get("owner", "")
        repo = fetched.extra_meta.get("repo", "")
        branch = fetched.extra_meta.get("branch", "")
        path = fetched.extra_meta.get("path", "")
        return self.artifact_path_template.format(
            owner_repo=slugify(f"{owner}-{repo}") or "unknown",
            branch=slugify(branch) or "main",
            path_slug=slugify(path) or "changelog",
            slug=slugify(fetched.title) or "changelog",
            published_at=fetched.published_at or "unknown-date",
            author_or_publisher=slugify(fetched.author_or_publisher) or "unknown",
            identifier=slugify(req.identifier),
            source_type=self.source_type.replace("/", "-"),
        )


register_adapter(GitHubRelease())
register_adapter(GitHubChangelog())
