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


def match_products(query: str, products: list[str], limit: int = 2) -> list[str]:
    """Products named in the query: every word of the slug appears in order
    ("home assistant" -> home-assistant; "node.js" -> nodejs). Longest first."""
    q = query.lower().replace("node.js", "nodejs").replace(".net", "dotnet")
    toks = re.findall(r"[a-z0-9]+", q)
    joined = " " + " ".join(toks) + " "
    found = []
    for name, slug in _ALIASES.items():
        if f" {name} " in joined and slug in products:
            found.append(slug)
    for slug in products:
        parts = slug.split("-")
        if " " + " ".join(parts) + " " in joined or (len(parts) > 1 and f" {''.join(parts)} " in joined):
            found.append(slug)
    found = sorted(set(found), key=lambda s: (-len(s.split("-")), -len(s)))
    # drop a shorter slug contained in a longer one (linux vs amazon-linux style overlaps)
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
