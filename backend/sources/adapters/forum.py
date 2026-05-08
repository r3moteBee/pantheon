"""Forum source adapters.

Two genres for now:
  - forum/reddit       Reddit thread (post + top comments) via the
                       public ``.json`` endpoint
  - forum/hackernews   HN thread (item + recursive children) via the
                       Algolia Hacker News API

Both render a single-document markdown body shaped as:

    # <thread title>
    **OP — <author> — <points>** · <YYYY-MM-DD>

    <selftext or link>

    ---

    ## Top comments

    **<author> — <points>**
    > <body>

so the LLM extractor sees one coherent discussion rather than a tree.

Identifier formats accepted:
  - forum/reddit
      * full thread URL: https://www.reddit.com/r/<sub>/comments/<id>/<slug>/
      * shorthand: <id>  (e.g. ``abc123``) — caller must also pass
        ``extras['subreddit']`` so we can build the JSON URL

      Reddit's bot detection routinely 403s anonymous server traffic
      regardless of User-Agent. Three failure modes and the escape
      hatch:
        - Most ingests will work when run from a residential IP.
        - If www / old / api.reddit.com all 403, pass
          ``extras['raw_payload']`` containing the JSON the caller
          fetched themselves (open the .json URL in a logged-in
          browser and paste). Same shape as the public endpoint.
        - Phase C will add proper OAuth via reddit app credentials.

  - forum/hackernews
      * https://news.ycombinator.com/item?id=<id>
      * just the numeric id

Both use ``llm_default`` extraction. Forum content is structurally a
discussion but the default prompt captures topics/claims well; we
can add a forum-specialized extractor later if recall is poor.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlparse

from sources.base import (
    FetchedContent,
    IngestRequest,
    SourceAdapter,
)
from sources.registry import register_adapter
from sources.util import slugify

logger = logging.getLogger(__name__)


_USER_AGENT = "Pantheon/1.0 (research-harness)"
# Reddit aggressively 403s anything that looks like a bot. Use a
# browser-shaped UA when hitting reddit.com — the .json endpoint
# itself is public, the gating is purely UA-based.
_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
_HTTP_TIMEOUT = 30


async def _http_get_json(url: str, *, user_agent: str | None = None) -> Any:
    import httpx
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        r = await client.get(
            url,
            headers={
                "User-Agent": user_agent or _USER_AGENT,
                "Accept": "application/json",
            },
        )
        r.raise_for_status()
        return r.json()


# ── Reddit ────────────────────────────────────────────────────────

_REDDIT_URL_RE = re.compile(
    r"^https?://(?:www\.|old\.|np\.)?reddit\.com/r/(?P<sub>[^/]+)/comments/(?P<id>[a-z0-9]+)",
    re.IGNORECASE,
)


def _parse_reddit_identifier(identifier: str, extras: dict) -> tuple[str, str, str]:
    """Return (subreddit, post_id, primary_json_url) from any accepted shape.

    The returned URL is the primary (www.reddit.com) endpoint; callers
    should fall back to the old.reddit.com mirror if that 403s.
    """
    m = _REDDIT_URL_RE.match(identifier or "")
    if m:
        sub = m.group("sub")
        pid = m.group("id")
    else:
        pid = (identifier or "").strip()
        sub = (extras or {}).get("subreddit") or ""
        if not pid:
            raise RuntimeError("forum/reddit: empty identifier")
        if not sub:
            raise RuntimeError(
                f"forum/reddit: cannot resolve {identifier!r} without "
                "extras['subreddit']"
            )
    json_url = f"https://www.reddit.com/r/{sub}/comments/{pid}/.json?raw_json=1&limit=50"
    return sub, pid, json_url


def _reddit_json_urls(sub: str, pid: str) -> list[str]:
    """All endpoint variants we'll try, in order."""
    q = "raw_json=1&limit=50"
    return [
        f"https://www.reddit.com/r/{sub}/comments/{pid}/.json?{q}",
        f"https://old.reddit.com/r/{sub}/comments/{pid}/.json?{q}",
        f"https://api.reddit.com/r/{sub}/comments/{pid}?{q}",
    ]


async def _fetch_reddit_payload(sub: str, pid: str) -> tuple[Any, str]:
    """Try each Reddit endpoint with a browser UA. Returns (payload, url_used).

    Reddit's gating is mostly UA-based. The www endpoint blocks bot UAs
    most aggressively; old.reddit and api.reddit are typically more
    permissive but not always available. We try in order and surface the
    last error if all three fail.
    """
    last_err: str = ""
    for url in _reddit_json_urls(sub, pid):
        try:
            payload = await _http_get_json(url, user_agent=_BROWSER_UA)
            return payload, url
        except Exception as e:
            last_err = f"{url}: {type(e).__name__}: {e}"
            continue
    raise RuntimeError(
        f"forum/reddit: all endpoints blocked or failed for r/{sub}/{pid} "
        f"(last error: {last_err}). Reddit may be rate-limiting; "
        f"consider re-running later or supplying extras['transcript_url'] "
        f"with a mirror."
    )


def _format_reddit_body(post: dict, comments: list[dict]) -> str:
    title = post.get("title") or "(no title)"
    author = post.get("author") or "[deleted]"
    score = post.get("score")
    created = _epoch_to_iso(post.get("created_utc"))
    selftext = (post.get("selftext") or "").strip()
    url = post.get("url") or ""
    link_flair = post.get("link_flair_text") or ""

    head = [f"# {title}"]
    meta = f"**OP — u/{author} — {score} points**"
    if created:
        meta += f" · {created}"
    if link_flair:
        meta += f" · _{link_flair}_"
    head.append(meta)
    head.append("")
    if selftext:
        head.append(selftext)
    elif url and not url.startswith("https://www.reddit.com"):
        head.append(f"Link: {url}")
    head.append("")
    head.append("---")
    head.append("")
    head.append("## Top comments")
    head.append("")

    parts = ["\n".join(head)]
    for c in comments:
        cauthor = c.get("author") or "[deleted]"
        cscore = c.get("score")
        cbody = (c.get("body") or "").strip()
        if not cbody or cbody in ("[deleted]", "[removed]"):
            continue
        line = f"**u/{cauthor} — {cscore} points**\n"
        # Quote the comment body so the OP body and replies are
        # visually distinct in the rendered markdown.
        line += "\n".join(f"> {ln}" if ln else ">" for ln in cbody.splitlines())
        parts.append(line)
    return "\n\n".join(parts).strip() + "\n"


def _flatten_reddit_comments(listing: dict, *, max_comments: int = 50) -> list[dict]:
    out: list[dict] = []
    children = ((listing or {}).get("data") or {}).get("children") or []
    for ch in children:
        if ch.get("kind") != "t1":
            continue
        d = ch.get("data") or {}
        out.append(d)
        # recurse into top-level replies (one level deep is enough)
        rep = d.get("replies")
        if isinstance(rep, dict):
            sub_children = ((rep.get("data") or {}).get("children") or [])
            for sc in sub_children:
                if sc.get("kind") == "t1":
                    out.append(sc.get("data") or {})
        if len(out) >= max_comments:
            break
    return out[:max_comments]


def _epoch_to_iso(ts: float | int | None) -> str | None:
    if not ts:
        return None
    try:
        from datetime import datetime, timezone
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).date().isoformat()
    except Exception:
        return None


class RedditThread(SourceAdapter):
    source_type = "forum/reddit"
    display_name = "Forum — Reddit thread"
    bucket_aliases = ("forum", "reddit")
    requires_mcp = ()
    artifact_path_template = (
        "forums/reddit/{subreddit}/{published_at}/{slug}.md"
    )
    extractor_strategy = "llm_default"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        sub, pid, _ = _parse_reddit_identifier(req.identifier, req.extras or {})
        # Escape hatch: when Reddit blocks the server\'s IP/UA combo,
        # the caller can fetch the .json from a logged-in browser and
        # pass it through extras["raw_payload"]. Same shape as the
        # public endpoint returns.
        raw_payload = (req.extras or {}).get("raw_payload")
        if raw_payload is not None:
            import json as _json
            payload = _json.loads(raw_payload) if isinstance(raw_payload, str) else raw_payload
            json_url = f"caller_supplied:r/{sub}/{pid}"
        else:
            payload, json_url = await _fetch_reddit_payload(sub, pid)
        # api.reddit.com returns a single Listing (not a 2-element list),
        # so unwrap the comments shape into [post, comments] for shared
        # downstream handling.
        if isinstance(payload, dict) and payload.get("kind") == "Listing":
            # api.reddit.com nesting: post -> dict, no comments side-channel.
            post_listing = payload
            comments_listing: dict = {}
            payload = [post_listing, comments_listing]
        if not isinstance(payload, list) or len(payload) < 1:
            raise RuntimeError(f"forum/reddit: unexpected payload shape from {json_url!r}")
        post_listing = payload[0]
        post_children = ((post_listing or {}).get("data") or {}).get("children") or []
        if not post_children:
            raise RuntimeError(f"forum/reddit: no post in payload for {pid!r}")
        post = post_children[0].get("data") or {}
        comments_listing = payload[1] if len(payload) > 1 else None
        comments = _flatten_reddit_comments(comments_listing or {})

        body = _format_reddit_body(post, comments)
        return FetchedContent(
            text=body,
            title=post.get("title") or pid,
            author_or_publisher=post.get("author") or "",
            url=f"https://www.reddit.com{post.get('permalink', '')}".rstrip("/"),
            published_at=_epoch_to_iso(post.get("created_utc")),
            extra_meta={
                "subreddit": sub,
                "post_id": pid,
                "score": post.get("score"),
                "num_comments": post.get("num_comments"),
                "comments_captured": len(comments),
                "retrieved_at": (req.extras or {}).get("retrieved_at"),
                "fetch_method": "reddit_json",
                "endpoint_used": json_url,
            },
        )

    def render_artifact_path(self, req, fetched):
        published = fetched.published_at or "unknown-date"
        return self.artifact_path_template.format(
            subreddit=slugify(fetched.extra_meta.get("subreddit", "") or "unknown"),
            slug=slugify(fetched.title) or fetched.extra_meta.get("post_id", "post"),
            published_at=published,
            author_or_publisher=slugify(fetched.author_or_publisher) or "unknown",
            identifier=slugify(req.identifier),
            source_type=self.source_type.replace("/", "-"),
        )


# ── Hacker News ───────────────────────────────────────────────────

_HN_URL_RE = re.compile(
    r"news\.ycombinator\.com/item\?id=(?P<id>\d+)", re.IGNORECASE,
)


def _parse_hn_identifier(identifier: str) -> str:
    m = _HN_URL_RE.search(identifier or "")
    if m:
        return m.group("id")
    s = (identifier or "").strip()
    if s.isdigit():
        return s
    raise RuntimeError(f"forum/hackernews: cannot extract item id from {identifier!r}")


def _format_hn_body(item: dict, *, max_comments: int = 60) -> str:
    title = item.get("title") or "(no title)"
    author = item.get("author") or "(unknown)"
    points = item.get("points")
    created = _hn_iso(item.get("created_at"))
    text = item.get("text") or ""
    url = item.get("url") or ""

    head = [f"# {title}"]
    meta = f"**OP — {author}** — {points} points"
    if created:
        meta += f" · {created}"
    head.append(meta)
    head.append("")
    if text:
        # HN stores HTML-escaped text with <p> separators; cheap unwrap.
        from html import unescape
        cleaned = unescape(text).replace("<p>", "\n\n").replace("</p>", "")
        head.append(cleaned.strip())
    elif url:
        head.append(f"Link: {url}")
    head.append("")
    head.append("---")
    head.append("")
    head.append("## Top comments")
    head.append("")

    parts: list[str] = ["\n".join(head)]
    captured = 0
    for child in _walk_hn_children(item):
        if captured >= max_comments:
            break
        cauthor = child.get("author") or "[deleted]"
        ctext = child.get("text") or ""
        if not ctext or child.get("type") != "comment":
            continue
        from html import unescape
        ctext = unescape(ctext).replace("<p>", "\n\n").replace("</p>", "").strip()
        if not ctext:
            continue
        line = f"**{cauthor}**\n" + "\n".join(
            f"> {ln}" if ln else ">" for ln in ctext.splitlines()
        )
        parts.append(line)
        captured += 1
    return "\n\n".join(parts).strip() + "\n"


def _walk_hn_children(item: dict):
    """Pre-order walk through children/grandchildren etc."""
    for child in item.get("children") or []:
        if not isinstance(child, dict):
            continue
        yield child
        yield from _walk_hn_children(child)


def _hn_iso(ts: str | None) -> str | None:
    if not ts:
        return None
    # Algolia returns ISO-8601 like "2025-04-12T13:45:09.000Z"
    return ts[:10] if isinstance(ts, str) and len(ts) >= 10 else None


class HackerNewsThread(SourceAdapter):
    source_type = "forum/hackernews"
    display_name = "Forum — Hacker News thread"
    bucket_aliases = ("forum", "hn", "hackernews")
    requires_mcp = ()
    artifact_path_template = (
        "forums/hackernews/{published_at}/{slug}-{item_id}.md"
    )
    extractor_strategy = "llm_default"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        item_id = _parse_hn_identifier(req.identifier)
        url = f"https://hn.algolia.com/api/v1/items/{item_id}"
        try:
            item = await _http_get_json(url)
        except Exception as e:
            raise RuntimeError(f"forum/hackernews: fetch {url!r} failed: {e}")
        if not isinstance(item, dict) or not item.get("id"):
            raise RuntimeError(f"forum/hackernews: empty item for id={item_id}")

        body = _format_hn_body(item)
        return FetchedContent(
            text=body,
            title=item.get("title") or f"HN item {item_id}",
            author_or_publisher=item.get("author") or "",
            url=f"https://news.ycombinator.com/item?id={item_id}",
            published_at=_hn_iso(item.get("created_at")),
            extra_meta={
                "item_id": str(item_id),
                "points": item.get("points"),
                "num_comments": _count_hn_comments(item),
                "external_url": item.get("url") or "",
                "retrieved_at": (req.extras or {}).get("retrieved_at"),
                "fetch_method": "hn_algolia",
            },
        )

    def render_artifact_path(self, req, fetched):
        published = fetched.published_at or "unknown-date"
        return self.artifact_path_template.format(
            slug=slugify(fetched.title) or "thread",
            item_id=fetched.extra_meta.get("item_id", "0"),
            published_at=published,
            author_or_publisher=slugify(fetched.author_or_publisher) or "unknown",
            identifier=slugify(req.identifier),
            source_type=self.source_type.replace("/", "-"),
        )


def _count_hn_comments(item: dict) -> int:
    n = 0
    for c in _walk_hn_children(item):
        if c.get("type") == "comment":
            n += 1
    return n


register_adapter(RedditThread())
register_adapter(HackerNewsThread())
