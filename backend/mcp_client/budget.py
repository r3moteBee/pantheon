"""Per-connection call budgets for MCP servers.

Every MCP tool call is metered: ``record()`` stores its cost in
``data/db/mcp_usage.db``. A connection may set a daily and/or monthly
limit in its config (``cfg["budget"] = {"daily": N, "monthly": N}``,
0 = unlimited); ``check()`` says whether the next call fits.

Cost is 1 per call unless a rule says otherwise:
  - ``cfg["budget"]["costs"]``: ``{"<tool>": n}`` or
    ``{"<tool>": {"default": n, "by_arg": {"<arg>": {"<value>": n}}}}``
  - a built-in preset (``PRESETS``) for metered services, picked from the
    connection's name/URL (Tavily credits today). The preset also sets the
    unit shown in the UI ("credits" vs "calls").

Tool names are matched exactly, then with the service prefix stripped
("tavily-search", "tavily_search" → "search").
"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Presets ──────────────────────────────────────────────────────────────────

PRESETS: dict[str, dict[str, Any]] = {
    # https://docs.tavily.com/documentation/api-credits
    "tavily": {
        "unit": "credits",
        "costs": {
            "search": {"default": 1, "by_arg": {"search_depth": {"advanced": 2}}},
            "extract": {"default": 1, "by_arg": {"extract_depth": {"advanced": 2},
                                                 "search_depth": {"advanced": 2}}},
            "map": {"default": 1},
            "crawl": {"default": 2, "by_arg": {"extract_depth": {"advanced": 3},
                                               "search_depth": {"advanced": 3}}},
        },
        "note": "Search 1–2 credits · Extract 1–2 per 5 URLs · Map 1 per 10 URLs · Crawl map+extract.",
    },
}

WARN_FRACTION = 0.8


def preset_for(cfg: dict[str, Any]) -> str | None:
    explicit = (cfg.get("budget") or {}).get("preset")
    if explicit:
        return explicit if explicit in PRESETS else None
    hay = f"{cfg.get('name', '')} {cfg.get('url', '')}".lower()
    return next((p for p in PRESETS if p in hay), None)


def limits(cfg: dict[str, Any]) -> dict[str, int]:
    b = cfg.get("budget") or {}
    return {"daily": int(b.get("daily") or 0), "monthly": int(b.get("monthly") or 0)}


def unit(cfg: dict[str, Any]) -> str:
    p = preset_for(cfg)
    return PRESETS[p]["unit"] if p else "calls"


def _rule_cost(rule: Any, arguments: dict[str, Any]) -> float:
    if isinstance(rule, (int, float)):
        return float(rule)
    if not isinstance(rule, dict):
        return 1.0
    for arg, table in (rule.get("by_arg") or {}).items():
        val = arguments.get(arg)
        if isinstance(val, str) and val.lower() in table:
            return float(table[val.lower()])
    return float(rule.get("default", 1))


def cost(cfg: dict[str, Any], tool: str, arguments: dict[str, Any] | None = None) -> float:
    arguments = arguments or {}
    rules: dict[str, Any] = {}
    p = preset_for(cfg)
    if p:
        rules.update(PRESETS[p]["costs"])
    rules.update((cfg.get("budget") or {}).get("costs") or {})
    candidates = [tool]
    if p:
        candidates.append(re.sub(rf"^{re.escape(p)}[-_]", "", tool))
    for c in candidates:
        if c in rules:
            return _rule_cost(rules[c], arguments)
    return 1.0


# ── Usage store ──────────────────────────────────────────────────────────────

_ready: set[str] = set()


def _db_path() -> str:
    from config import get_settings
    s = get_settings()
    s.db_dir.mkdir(parents=True, exist_ok=True)
    return str(s.db_dir / "mcp_usage.db")


def _connect() -> sqlite3.Connection:
    from db_utils import apply_sqlite_pragmas
    path = _db_path()
    conn = sqlite3.connect(path)
    apply_sqlite_pragmas(conn)
    if path not in _ready:
        conn.execute("""CREATE TABLE IF NOT EXISTS usage (
            connection TEXT NOT NULL, tool TEXT NOT NULL,
            cost REAL NOT NULL, ts TEXT NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS usage_conn_ts ON usage(connection, ts)")
        conn.commit()
        _ready.add(path)
    return conn


def _period_starts(now: datetime | None = None) -> dict[str, str]:
    now = now or datetime.now(timezone.utc)
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return {"daily": day.isoformat(), "monthly": day.replace(day=1).isoformat()}


def record(connection: str, tool: str, amount: float, *, ts: datetime | None = None) -> None:
    ts = ts or datetime.now(timezone.utc)
    with _connect() as conn:
        conn.execute("INSERT INTO usage (connection, tool, cost, ts) VALUES (?, ?, ?, ?)",
                     (connection, tool, amount, ts.isoformat()))
        # Keep ~13 months so the monthly view always has last year's month.
        conn.execute("DELETE FROM usage WHERE ts < ?", ((ts - timedelta(days=400)).isoformat(),))


def used(connection: str) -> dict[str, float]:
    starts = _period_starts()
    with _connect() as conn:
        out = {}
        for period, start in starts.items():
            row = conn.execute("SELECT COALESCE(SUM(cost), 0) FROM usage WHERE connection = ? AND ts >= ?",
                               (connection, start)).fetchone()
            out[period] = float(row[0])
        by_tool = conn.execute(
            "SELECT tool, SUM(cost), COUNT(*) FROM usage WHERE connection = ? AND ts >= ? "
            "GROUP BY tool ORDER BY SUM(cost) DESC", (connection, starts["monthly"])).fetchall()
    out["by_tool"] = [{"tool": t, "used": float(c), "calls": n} for t, c, n in by_tool]
    return out


def reset(connection: str, period: str) -> None:
    """Zero the current day's or month's usage for a connection."""
    start = _period_starts()[period]
    with _connect() as conn:
        conn.execute("DELETE FROM usage WHERE connection = ? AND ts >= ?", (connection, start))


def forget(connection: str) -> None:
    with _connect() as conn:
        conn.execute("DELETE FROM usage WHERE connection = ?", (connection,))


def rename(old: str, new: str) -> None:
    with _connect() as conn:
        conn.execute("UPDATE usage SET connection = ? WHERE connection = ?", (new, old))


# ── Checks ───────────────────────────────────────────────────────────────────

def status(cfg: dict[str, Any]) -> dict[str, Any]:
    """Usage, limits and remaining for the current day and month."""
    name = cfg["name"]
    lim = limits(cfg)
    u = used(name)
    out: dict[str, Any] = {"connection": name, "unit": unit(cfg), "preset": preset_for(cfg),
                           "by_tool": u["by_tool"]}
    for period in ("daily", "monthly"):
        limit = lim[period]
        out[period] = {
            "used": u[period],
            "limit": limit,
            "remaining": max(0.0, limit - u[period]) if limit else None,
        }
    p = preset_for(cfg)
    if p and PRESETS[p].get("note"):
        out["note"] = PRESETS[p]["note"]
    return out


def check(cfg: dict[str, Any], next_cost: float = 1.0) -> str | None:
    """None if a call costing ``next_cost`` fits the budget, else why not."""
    s = status(cfg)
    for period, label in (("daily", "Daily"), ("monthly", "Monthly")):
        p = s[period]
        if p["limit"] and p["used"] + next_cost > p["limit"]:
            return (f"{label} budget for '{cfg['name']}' reached "
                    f"({p['used']:.0f}/{p['limit']} {s['unit']})")
    return None


def warning(cfg: dict[str, Any]) -> str | None:
    """A note once usage passes WARN_FRACTION of a limit."""
    s = status(cfg)
    for period in ("daily", "monthly"):
        p = s[period]
        if p["limit"] and WARN_FRACTION * p["limit"] <= p["used"] < p["limit"]:
            return (f"[Note: '{cfg['name']}' {period} budget {p['used']:.0f}/{p['limit']} "
                    f"{s['unit']} used, {p['remaining']:.0f} left]")
    return None


# ── One-shot migration from the Tavily-only tracker ──────────────────────────

def migrate_tavily(configs: list[dict[str, Any]]) -> bool:
    """Move vault ``tavily_daily_limit``/``tavily_monthly_limit`` onto the
    Tavily connection's budget and carry this month's usage from
    ``tavily_usage.json`` into the usage store. Returns True if configs
    changed (caller saves them). Runs once (vault flag)."""
    from secrets.vault import get_vault
    vault = get_vault()
    if vault.get_secret("mcp_budget_migrated_v1"):
        return False
    changed = False
    tav = next((c for c in configs if preset_for(c) == "tavily"), None)
    daily = vault.get_secret("tavily_daily_limit")
    monthly = vault.get_secret("tavily_monthly_limit")
    if tav is not None and (daily or monthly):
        b = dict(tav.get("budget") or {})
        if daily and not b.get("daily"):
            b["daily"] = int(float(daily))
        if monthly and not b.get("monthly"):
            b["monthly"] = int(float(monthly))
        tav["budget"] = b
        changed = True
    from config import get_settings
    old = Path(get_settings().db_dir) / "tavily_usage.json"
    if tav is not None and old.exists():
        try:
            data = json.loads(old.read_text(encoding="utf-8"))
            now = datetime.now(timezone.utc)
            month_used = float((data.get("monthly") or {}).get(now.strftime("%Y-%m"), 0) or 0)
            day_used = float((data.get("daily") or {}).get(now.strftime("%Y-%m-%d"), 0) or 0)
            if day_used:
                record(tav["name"], "migrated", day_used, ts=now)
            if month_used - day_used > 0:
                start = datetime.fromisoformat(_period_starts(now)["monthly"])
                record(tav["name"], "migrated", month_used - day_used, ts=start)
            old.rename(old.with_suffix(".json.migrated"))
        except Exception:
            logger.warning("Could not carry over tavily_usage.json", exc_info=True)
    for key in ("tavily_daily_limit", "tavily_monthly_limit"):
        if vault.get_secret(key) is not None:
            vault.delete_secret(key)
    vault.set_secret("mcp_budget_migrated_v1", "1")
    return changed
