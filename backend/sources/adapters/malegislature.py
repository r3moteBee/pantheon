"""Massachusetts Legislature source adapters.

Mechanism: the malegislature.gov public REST API at ``/api``. No auth.
All endpoints return JSON; bodies are either plain prose (sections,
bills) or HTML (session-law ChapterText) which we run through
``markdownify``.

Genres:
  - malegis/general-law-section   one MGL section, e.g. "M.G.L. c. 23A § 1"
  - malegis/general-law-chapter   whole MGL chapter, e.g. "Chapter 23A"
  - malegis/session-law           one act/resolve, e.g. "2024 Chapter 1"
  - malegis/bill                  one bill/docket, e.g. "H4038" (current court)
  - malegis/hearing               one hearing event, by integer EventId
  - malegis/roll-call             one floor roll call
  - malegis/committee-vote        one committee vote on a document

See ``docs/superpowers/specs/2026-05-08-massachusetts-legislature-adapter-design.md``
for the full design.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Optional

from sources.base import (
    FetchedContent,
    IngestRequest,
    SourceAdapter,
)
from sources.registry import register_adapter
from sources.util import html_to_markdown

logger = logging.getLogger(__name__)

_API_BASE = "https://malegislature.gov/api"
_SITE_BASE = "https://malegislature.gov"
_USER_AGENT = "Pantheon/1.0 (research-harness)"
_HTTP_TIMEOUT = 60
_DEFAULT_COURT_FLOOR = 193  # hard-coded fallback when API + cache both fail

# Process-lifetime cache for the current GeneralCourt number. The MA
# General Court increments roughly every two years; once we've resolved
# it from the API once, we don't need to ask again for the lifetime of
# the FastAPI process.
_CURRENT_COURT_CACHE: dict[str, int] = {}


async def _http_get_json(url: str) -> Any:
    """GET a URL and return parsed JSON. Raises on non-2xx.

    httpx is imported lazily so the module loads cleanly in test
    contexts where networking deps may be stubbed.
    """
    import httpx
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        r = await client.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        r.raise_for_status()
        return r.json()


async def _current_court() -> int:
    """Return the current GeneralCourt number, with three-level fallback.

    1. Cached value from a prior call (process lifetime).
    2. Live query of /GeneralCourts/Documents — the highest court
       number visible there is the current one.
    3. Hard-coded floor (_DEFAULT_COURT_FLOOR) with a warning logged.
    """
    if "court" in _CURRENT_COURT_CACHE:
        return _CURRENT_COURT_CACHE["court"]
    try:
        payload = await _http_get_json(f"{_API_BASE}/GeneralCourts/Documents")
        nums = [int(c["GeneralCourtNumber"]) for c in payload or []
                if isinstance(c, dict) and c.get("GeneralCourtNumber")]
        if nums:
            n = max(nums)
            _CURRENT_COURT_CACHE["court"] = n
            return n
    except Exception as e:
        logger.warning("malegis: current-court lookup failed (%s); using fallback", e)
    # Fallback to whatever we last cached (if anything), else the floor.
    n = _CURRENT_COURT_CACHE.get("court", _DEFAULT_COURT_FLOOR)
    _CURRENT_COURT_CACHE.setdefault("court", n)
    return n
