"""Release data for "latest version" searches (endoflife.date).

Graded against ground truth fetched at run time, the agent searched every time
but stated the exact current version only about half the time: search snippets
and blog posts are months old, and a release-notes page for one version (even a
beta - "PostgreSQL 19.0 release notes", release date 2026-??-??) reads like a
release. endoflife.date publishes maintained release tables for ~480 products
(only released cycles, newest first, with the latest patch of each), so when a
web_search query asks for a version and names one of them, web_search appends
that table to the results. One small cached request; failures add nothing.

The product name in the query is sent to endoflife.date. SEARCH_RELEASE_FACTS=false
turns this off.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import date

logger = logging.getLogger(__name__)

API = "https://endoflife.date/api"
_VERSION_INTENT_RE = re.compile(
    r"\b(latest|newest|current|stable|lts|release[sd]?|version|versions|out yet|update[sd]?|eol|end of life|supported)\b", re.I)
# common names that don't contain the slug's words
_ALIASES = {"golang": "go", "node": "nodejs", "postgres": "postgresql", "k8s": "kubernetes",
            "linux kernel": "linux", "kernel": "linux", "docker": "docker-engine", "py": "python"}
_PRODUCTS_TTL, _CYCLES_TTL = 24 * 3600, 3600
_cache: dict[str, tuple[float, object]] = {}


async def _get_json(path: str, ttl: float):
    hit = _cache.get(path)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    from utils.http import pooled_client
    async with pooled_client(timeout=6.0) as client:
        resp = await asyncio.wait_for(client.get(f"{API}/{path}"), timeout=6.0)
        resp.raise_for_status()
        data = resp.json()
    _cache[path] = (time.time(), data)
    return data


# Words that may precede a product name in a version question. Any other word
# right before a slug means the slug is only part of a longer name: "Uptime
# Kuma" is not Kong's "kuma" (whose 2.14.5 the agent then reported).
_LEADING_OK = set("""what whats what's is are the of a an for latest newest current stable lts release released releases
    version versions out yet update updated eol end life supported now today new and or vs versus compare to in on with
    about check find search which when did does has have get""".split())


def match_products(query: str, products: list[str], limit: int = 2) -> list[str]:
    """Products named in the query: every word of the slug appears in order
    ("home assistant" -> home-assistant; "node.js" -> nodejs), preceded by the
    start of the query or a non-name word. Longest first."""
    q = query.lower().replace("node.js", "nodejs").replace(".net", "dotnet")
    toks = re.findall(r"[a-z0-9]+", q)
    prods = set(products)

    def named_at(parts: list[str]) -> bool:
        n = len(parts)
        for i in range(len(toks) - n + 1):
            if toks[i:i + n] == parts and (i == 0 or toks[i - 1] in _LEADING_OK or toks[i - 1].isdigit()):
                return True
        return False

    found = []
    for name, slug in _ALIASES.items():
        if slug in prods and named_at(name.split()):
            found.append(slug)
    for slug in products:
        parts = slug.split("-")
        if named_at(parts) or (len(parts) > 1 and named_at(["".join(parts)])):
            found.append(slug)
    found = sorted(set(found), key=lambda s: (-len(s.split("-")), -len(s)))
    keep = []
    for s in found:
        if not any(s != k and s in k.split("-") for k in keep):
            keep.append(s)
    return keep[:limit]


def _is_date_past(v) -> bool:
    if v is True:
        return True
    if isinstance(v, str):
        try:
            return date.fromisoformat(v) <= date.today()
        except ValueError:
            return False
    return False


def format_cycles(slug: str, cycles: list[dict], max_cycles: int = 4) -> str:
    if not cycles:
        return ""
    today = date.today().isoformat()
    newest = cycles[0]
    lines = [f"Release data from endoflife.date/{slug} (fetched {today}; released versions only, newest first):",
             f"  newest release: {newest.get('latest')} (cycle {newest.get('cycle')}, released {newest.get('latestReleaseDate') or newest.get('releaseDate') or '?'})"]
    lts = next((c for c in cycles if _is_date_past(c.get("lts"))), None)
    if lts and lts is not newest:
        lines.append(f"  newest LTS: {lts.get('latest')} (cycle {lts.get('cycle')}, released {lts.get('latestReleaseDate') or '?'})")
    elif lts is newest:
        lines[1] += " - this is an LTS cycle"
    for c in cycles[1:max_cycles]:
        eol = c.get("eol")
        status = "end of life" if _is_date_past(eol) else (f"supported until {eol}" if isinstance(eol, str) else "supported")
        lines.append(f"  cycle {c.get('cycle')}: latest {c.get('latest')} ({status})")
    return "\n".join(lines)


async def release_facts(query: str) -> str:
    """'' unless the query asks about versions of a product endoflife.date tracks."""
    from config import get_settings
    if not get_settings().search_release_facts or not _VERSION_INTENT_RE.search(query or ""):
        return ""
    try:
        products = await _get_json("all.json", _PRODUCTS_TTL)
        blocks = []
        for slug in match_products(query, products):
            block = format_cycles(slug, await _get_json(f"{slug}.json", _CYCLES_TTL))
            if block:
                blocks.append(block)
        return "\n\n".join(blocks)
    except Exception as e:
        logger.info("release facts skipped for %r: %s", query, e)
        return ""


# ── GitHub releases (products endoflife.date doesn't track) ─────────────────
#
# Measured on homelab software tracked only on GitHub: without release data the
# agent reported a pre-release (Ollama "0.35.1" = v0.35.1-rc0), a blog post's
# version a week stale (Jellyfin 12.0, 12.1 was out) and a version that does
# not exist (Uptime Kuma "2.14.5"). GitHub's release list says which tags are
# stable. Unauthenticated API: 60 requests/hour, 10 searches/minute per IP -
# cached, and skipped on any error.

GITHUB_API = "https://api.github.com"
_GH_URL_RE = re.compile(r"github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)")
_STOP = re.compile(r"\b(what|whats|what's|is|the|of|a|an|for|latest|newest|current|stable|lts|release[sd]?|version|versions|"
                   r"out|yet|update[sd]?|eol|end|life|supported|now|today|new|\d{4})\b", re.I)


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _product_phrase(query: str) -> str:
    return re.sub(r"\s+", " ", _STOP.sub(" ", re.sub(r"[^\w\s.-]", " ", query))).strip()


def repo_candidates(query: str, search_results: str) -> list[str]:
    """owner/repo links in the search results whose repo (or owner) name is named in the query."""
    q = _norm(query)
    out = []
    for owner, repo in _GH_URL_RE.findall(search_results or ""):
        repo = repo.removesuffix(".git")
        if owner.lower() in ("orgs", "topics", "search", "sponsors", "features", "marketplace"):
            continue
        names = [n for n in (_norm(repo), _norm(owner)) if len(n) >= 4]
        if any(n in q for n in names) and f"{owner}/{repo}" not in out:
            out.append(f"{owner}/{repo}")
    return out


async def _gh_json(path: str, ttl: float):
    hit = _cache.get("gh:" + path)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    from utils.http import pooled_client
    async with pooled_client(timeout=6.0) as client:
        resp = await asyncio.wait_for(client.get(f"{GITHUB_API}/{path}", headers={"Accept": "application/vnd.github+json"}), timeout=6.0)
        resp.raise_for_status()
        data = resp.json()
    _cache["gh:" + path] = (time.time(), data)
    return data


def format_github(repo: str, releases: list[dict]) -> str:
    rel = [r for r in releases if not r.get("draft")]
    stable = next((r for r in rel if not r.get("prerelease")), None)
    if not stable:
        return ""
    day = lambda r: (r.get("published_at") or "")[:10] or "?"
    lines = [f"Release data from GitHub ({repo} releases, fetched {date.today().isoformat()}):",
             f"  newest stable release: {stable.get('tag_name')} (published {day(stable)})"]
    newer_pre = [r for r in rel if r.get("prerelease") and (r.get("published_at") or "") > (stable.get("published_at") or "")]
    if newer_pre:
        lines.append("  newer pre-releases (NOT stable): " + ", ".join(f"{r.get('tag_name')} ({day(r)})" for r in newer_pre[:3]))
    older = [r.get("tag_name") for r in rel if not r.get("prerelease") and r is not stable][:3]
    if older:
        lines.append("  previous stable releases: " + ", ".join(older))
    return "\n".join(lines)


async def github_release_facts(query: str, search_results: str) -> str:
    """'' unless the query asks for a version and names a GitHub project."""
    from config import get_settings
    if not get_settings().search_release_facts or not _VERSION_INTENT_RE.search(query or ""):
        return ""
    try:
        repos = repo_candidates(query, search_results)
        if not repos:
            phrase = _product_phrase(query)
            if not phrase:
                return ""
            from urllib.parse import quote
            found = await _gh_json(f"search/repositories?q={quote(phrase + ' in:name')}&sort=stars&per_page=3", _PRODUCTS_TTL)
            q = _norm(query)
            repos = [f"{r['owner']['login']}/{r['name']}" for r in (found.get("items") or [])
                     if r.get("stargazers_count", 0) >= 500 and (_norm(r["name"]) in q or _norm(r["owner"]["login"]) in q)
                     and len(_norm(r["name"])) >= 4][:1]
        for repo in repos[:1]:
            block = format_github(repo, await _gh_json(f"repos/{repo}/releases?per_page=15", _CYCLES_TTL))
            if block:
                return block
    except Exception as e:
        logger.info("GitHub release facts skipped for %r: %s", query, e)
    return ""
