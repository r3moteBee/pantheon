"""Smoke test for Phase B source adapters: forum/*, podcast/*, github/*.

These tests do NOT hit the network. They verify:
  1. Importing the adapters package registers all expected source_types.
  2. Identifier parsers accept the documented shapes and reject
     malformed input.
  3. render_artifact_path produces sane paths for each adapter.

Run: pytest backend/tests/integration/test_phase_b_adapters.py -v
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

# Keep settings.ensure_dirs() out of /app.
os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)

import pytest


def _registry():
    """Import the adapters package (triggering registration) and
    return the registry list_adapters() output."""
    # noqa: F401 — importing for side effects
    from sources import adapters  # noqa: F401
    from sources.registry import list_adapters
    return {a["source_type"]: a for a in list_adapters()}


def test_phase_b_adapters_registered():
    reg = _registry()
    expected = {
        "forum/reddit",
        "forum/hackernews",
        "podcast/episode",
        "github/release",
        "github/changelog",
    }
    missing = expected - set(reg)
    assert not missing, f"Phase B adapters missing from registry: {missing}"


def test_bucket_aliases_resolve():
    from sources import adapters  # noqa: F401
    from sources.registry import resolve_by_bucket

    assert "forum/reddit" in resolve_by_bucket("forum")
    assert "forum/reddit" in resolve_by_bucket("reddit")
    assert "forum/hackernews" in resolve_by_bucket("hn")
    assert "podcast/episode" in resolve_by_bucket("podcast")
    assert "github/release" in resolve_by_bucket("github")


# ── forum/reddit identifier parsing ───────────────────────────────

def test_reddit_identifier_url_form():
    from sources.adapters.forum import _parse_reddit_identifier
    sub, pid, json_url = _parse_reddit_identifier(
        "https://www.reddit.com/r/MachineLearning/comments/abc123/some-title/", {},
    )
    assert sub == "MachineLearning"
    assert pid == "abc123"
    assert "/r/MachineLearning/comments/abc123/.json" in json_url


def test_reddit_identifier_shorthand_requires_subreddit():
    from sources.adapters.forum import _parse_reddit_identifier
    with pytest.raises(RuntimeError, match="subreddit"):
        _parse_reddit_identifier("abc123", {})


def test_reddit_identifier_shorthand_with_subreddit():
    from sources.adapters.forum import _parse_reddit_identifier
    sub, pid, _ = _parse_reddit_identifier("abc123", {"subreddit": "rust"})
    assert (sub, pid) == ("rust", "abc123")


def test_reddit_raw_payload_escape_hatch():
    """When Reddit blocks the server, callers can pass the JSON
    directly via extras['raw_payload'] and the adapter must skip
    network fetch entirely."""
    import asyncio
    from dataclasses import asdict
    from sources.adapters.forum import RedditThread
    from sources.base import IngestRequest

    fake_post_listing = {
        "data": {
            "children": [{
                "kind": "t3",
                "data": {
                    "title": "test thread",
                    "author": "alice",
                    "score": 42,
                    "selftext": "body of post",
                    "url": "https://www.reddit.com/r/test/comments/abc/test_thread/",
                    "permalink": "/r/test/comments/abc/test_thread/",
                    "created_utc": 1_700_000_000,
                    "num_comments": 1,
                },
            }],
        },
    }
    fake_comments_listing = {
        "data": {
            "children": [{
                "kind": "t1",
                "data": {"author": "bob", "score": 5, "body": "nice"},
            }],
        },
    }
    payload = [fake_post_listing, fake_comments_listing]
    req = IngestRequest(
        source_type="forum/reddit",
        identifier="https://www.reddit.com/r/test/comments/abc/test_thread/",
        project_id="default",
        extras={"raw_payload": payload},
    )
    fetched = asyncio.run(RedditThread().fetch(req))
    assert "test thread" in fetched.title
    assert "body of post" in fetched.text
    assert "bob" in fetched.text  # comment author included
    assert fetched.extra_meta["endpoint_used"].startswith("caller_supplied:")


# ── forum/hackernews identifier parsing ───────────────────────────

def test_hn_identifier_url():
    from sources.adapters.forum import _parse_hn_identifier
    assert _parse_hn_identifier("https://news.ycombinator.com/item?id=12345") == "12345"


def test_hn_identifier_numeric():
    from sources.adapters.forum import _parse_hn_identifier
    assert _parse_hn_identifier("12345") == "12345"


def test_hn_identifier_invalid():
    from sources.adapters.forum import _parse_hn_identifier
    with pytest.raises(RuntimeError):
        _parse_hn_identifier("not a number")


# ── github/release identifier parsing ─────────────────────────────

def test_release_url_with_tag():
    from sources.adapters.github import _parse_release_identifier
    o, r, t = _parse_release_identifier(
        "https://github.com/anthropics/claude-code/releases/tag/v1.4.2",
    )
    assert (o, r, t) == ("anthropics", "claude-code", "v1.4.2")


def test_release_url_latest():
    from sources.adapters.github import _parse_release_identifier
    o, r, t = _parse_release_identifier(
        "https://github.com/anthropics/claude-code/releases/latest",
    )
    assert (o, r, t) == ("anthropics", "claude-code", "latest")


def test_release_repo_tag_shorthand():
    from sources.adapters.github import _parse_release_identifier
    assert _parse_release_identifier("foo/bar:v0.1.0") == ("foo", "bar", "v0.1.0")
    assert _parse_release_identifier("foo/bar") == ("foo", "bar", "latest")
    assert _parse_release_identifier("foo/bar.git:v0.1.0") == ("foo", "bar", "v0.1.0")


def test_release_invalid():
    from sources.adapters.github import _parse_release_identifier
    with pytest.raises(RuntimeError):
        _parse_release_identifier("not a github thing")


# ── github/changelog identifier parsing ───────────────────────────

def test_changelog_blob_url():
    from sources.adapters.github import _parse_changelog_identifier
    o, r, b, p = _parse_changelog_identifier(
        "https://github.com/foo/bar/blob/main/CHANGELOG.md",
    )
    assert (o, r, b, p) == ("foo", "bar", "main", "CHANGELOG.md")


def test_changelog_raw_url():
    from sources.adapters.github import _parse_changelog_identifier
    o, r, b, p = _parse_changelog_identifier(
        "https://raw.githubusercontent.com/foo/bar/main/docs/CHANGELOG.md",
    )
    assert (o, r, b, p) == ("foo", "bar", "main", "docs/CHANGELOG.md")


def test_changelog_repo_root():
    from sources.adapters.github import _parse_changelog_identifier
    o, r, b, p = _parse_changelog_identifier("https://github.com/foo/bar")
    assert (o, r, b, p) == ("foo", "bar", None, None)


def test_changelog_shorthand_with_branch_and_path():
    from sources.adapters.github import _parse_changelog_identifier
    o, r, b, p = _parse_changelog_identifier("foo/bar:dev:HISTORY.md")
    assert (o, r, b, p) == ("foo", "bar", "dev", "HISTORY.md")


# ── render_artifact_path sanity ───────────────────────────────────

@dataclass
class _StubReq:
    identifier: str = ""
    project_id: str = "default"
    extras: dict = None
    source_type: str = ""


@dataclass
class _StubFetched:
    text: str = "x"
    title: str = ""
    author_or_publisher: str = ""
    url: str = ""
    published_at: str | None = None
    extra_meta: dict = None


def test_reddit_render_path():
    from sources.adapters.forum import RedditThread
    a = RedditThread()
    f = _StubFetched(
        title="Some Cool Post",
        author_or_publisher="alice",
        published_at="2026-04-01",
        extra_meta={"subreddit": "MachineLearning", "post_id": "abc123"},
    )
    p = a.render_artifact_path(_StubReq(identifier="abc123"), f)
    assert p == "forums/reddit/machinelearning/2026-04-01/some-cool-post.md"


def test_hn_render_path():
    from sources.adapters.forum import HackerNewsThread
    a = HackerNewsThread()
    f = _StubFetched(
        title="Show HN: My Thing",
        author_or_publisher="bob",
        published_at="2026-04-01",
        extra_meta={"item_id": "12345"},
    )
    p = a.render_artifact_path(_StubReq(identifier="12345"), f)
    assert p == "forums/hackernews/2026-04-01/show-hn-my-thing-12345.md"


def test_github_release_render_path():
    from sources.adapters.github import GitHubRelease
    a = GitHubRelease()
    f = _StubFetched(
        title="Release v1.4.2",
        author_or_publisher="anthropics/claude-code",
        published_at="2026-04-01",
        extra_meta={"owner": "anthropics", "repo": "claude-code", "tag": "v1.4.2"},
    )
    p = a.render_artifact_path(_StubReq(identifier="anthropics/claude-code:v1.4.2"), f)
    assert p == "github/releases/anthropics-claude-code/v1-4-2.md"


def test_github_changelog_render_path():
    from sources.adapters.github import GitHubChangelog
    a = GitHubChangelog()
    f = _StubFetched(
        title="foo/bar — CHANGELOG.md",
        author_or_publisher="foo/bar",
        published_at="2026-04-01",
        extra_meta={"owner": "foo", "repo": "bar", "branch": "main", "path": "CHANGELOG.md"},
    )
    p = a.render_artifact_path(_StubReq(identifier="foo/bar"), f)
    assert p == "github/changelogs/foo-bar/main-changelog-md.md"
