"""Shared utilities for source adapters."""
from __future__ import annotations

import re


def slugify(s: str, max_len: int = 60) -> str:
    """Lowercase, replace non-alphanumeric runs with '-', trim, cap length.

    Used for both the title slug and the channel/author segment in
    artifact paths so the path is deterministic regardless of which
    adapter constructed it.
    """
    if not s:
        return ""
    out = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    if len(out) > max_len:
        # Trim on a hyphen boundary if possible.
        cut = out[:max_len].rsplit("-", 1)[0]
        out = cut or out[:max_len]
    return out



def parse_relative_date(value: str | None, *, today: "date | None" = None) -> str | None:
    """Convert a relative-date string ("4 months ago", "2 weeks ago",
    "yesterday", etc.) to an ISO YYYY-MM-DD string anchored at
    ``today`` (defaults to UTC today).

    Already-ISO inputs pass through unchanged. Returns None for
    inputs we can\'t parse so callers can fall back to other
    sources.

    Approximations:
      - 1 month  ≈ 30 days
      - 1 year   ≈ 365 days
      - 1 week   = 7 days
      - "yesterday" = today - 1 day
      - "X hours ago" / "X minutes ago" → today (assume same UTC day)

    Why approximate? The YouTube MCP only returns coarse relative
    times in search results — there's no information to recover an
    exact date. Approximating is good enough for time-bin grouping
    and chronological sort, which is all our analytics need.
    """
    if not value or not isinstance(value, str):
        return None
    s = value.strip().lower()
    if not s:
        return None

    # Already ISO?
    import re as _re
    if _re.match(r"^\d{4}-\d{2}-\d{2}", s):
        return s[:10]

    from datetime import date, timedelta, timezone, datetime
    base = today or datetime.now(timezone.utc).date()

    if s in ("today", "just now"):
        return base.isoformat()
    if s in ("yesterday",):
        return (base - timedelta(days=1)).isoformat()

    m = _re.match(
        r"^(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago",
        s,
    )
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    days = {
        "second": 0, "minute": 0, "hour": 0,  # same UTC day
        "day": n,
        "week": n * 7,
        "month": n * 30,
        "year": n * 365,
    }.get(unit, None)
    if days is None:
        return None
    return (base - timedelta(days=days)).isoformat()
