"""Tavily API credit tracking and threshold management.

Tracks credit usage per day and month, enforces configurable thresholds,
and provides fallback signals when limits are reached.

Credit costs (from Tavily pricing):
  tavily-search:  basic = 1 credit/query,  advanced = 2 credits/query
  tavily-extract: basic = 1 credit/5 URLs, advanced = 2 credits/5 URLs
  tavily-map:     basic = 1 credit/10 URLs, with instructions = 1 credit/5 URLs
  tavily-crawl:   map pricing + extract pricing (combined)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# ── Credit cost table ────────────────────────────────────────────────────────
# Maps (tool_name, search_depth/mode) → credits consumed per call.
# When depth is unknown, we assume "basic" (cheaper) to avoid over-counting.

CREDIT_COSTS: dict[str, dict[str, float]] = {
    "tavily-search": {
        "basic": 1.0,
        "advanced": 2.0,
    },
    "tavily-extract": {
        "basic": 1.0,    # per 5 URLs
        "advanced": 2.0,  # per 5 URLs
    },
    "tavily-map": {
        "basic": 1.0,          # per 10 URLs
        "instructions": 1.0,   # per 5 URLs (with instructions)
    },
    "tavily-crawl": {
        # Crawl = map + extract pricing combined
        "basic": 2.0,
        "advanced": 3.0,
    },
}

# Default thresholds (0 = unlimited)
DEFAULT_DAILY_LIMIT = 0
DEFAULT_MONTHLY_LIMIT = 0

# Vault keys
_VAULT_DAILY_LIMIT = "tavily_daily_limit"
_VAULT_MONTHLY_LIMIT = "tavily_monthly_limit"
_USAGE_FILE = "tavily_usage.json"


class TavilyCreditTracker:
    """Tracks Tavily API credit usage with daily and monthly thresholds."""

    def __init__(self) -> None:
        self._usage_path = settings.data_dir / "db" / _USAGE_FILE
        self._usage: dict[str, Any] = self._load_usage()

    # ── Persistence ──────────────────────────────────────────────────────

    def _load_usage(self) -> dict[str, Any]:
        """Load usage data from disk."""
        if self._usage_path.exists():
            try:
                return json.loads(self._usage_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"daily": {}, "monthly": {}, "history": []}

    def _save_usage(self) -> None:
        """Persist usage data to disk."""
        self._usage_path.parent.mkdir(parents=True, exist_ok=True)
        self._usage_path.write_text(
            json.dumps(self._usage, indent=2),
            encoding="utf-8",
        )

    # ── Thresholds ───────────────────────────────────────────────────────

    def get_thresholds(self) -> dict[str, int]:
        """Get current daily and monthly credit thresholds."""
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            daily = int(vault.get_secret(_VAULT_DAILY_LIMIT) or DEFAULT_DAILY_LIMIT)
            monthly = int(vault.get_secret(_VAULT_MONTHLY_LIMIT) or DEFAULT_MONTHLY_LIMIT)
        except Exception:
            daily = DEFAULT_DAILY_LIMIT
            monthly = DEFAULT_MONTHLY_LIMIT
        return {"daily_limit": daily, "monthly_limit": monthly}

    def set_thresholds(self, daily_limit: int | None = None, monthly_limit: int | None = None) -> dict[str, int]:
        """Set daily and/or monthly credit thresholds."""
        from secrets.vault import get_vault
        vault = get_vault()
        if daily_limit is not None:
            vault.set_secret(_VAULT_DAILY_LIMIT, str(daily_limit))
        if monthly_limit is not None:
            vault.set_secret(_VAULT_MONTHLY_LIMIT, str(monthly_limit))
        return self.get_thresholds()

    # ── Usage tracking ───────────────────────────────────────────────────

    def _today_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _month_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def get_usage(self) -> dict[str, Any]:
        """Get current usage stats."""
        today = self._today_key()
        month = self._month_key()
        thresholds = self.get_thresholds()

        daily_used = self._usage.get("daily", {}).get(today, 0.0)
        monthly_used = self._usage.get("monthly", {}).get(month, 0.0)

        return {
            "daily_used": daily_used,
            "daily_limit": thresholds["daily_limit"],
            "daily_remaining": max(0, thresholds["daily_limit"] - daily_used) if thresholds["daily_limit"] > 0 else -1,
            "monthly_used": monthly_used,
            "monthly_limit": thresholds["monthly_limit"],
            "monthly_remaining": max(0, thresholds["monthly_limit"] - monthly_used) if thresholds["monthly_limit"] > 0 else -1,
            "date": today,
            "month": month,
        }

    def record_usage(self, tool_name: str, arguments: dict[str, Any] | None = None) -> float:
        """Record credit usage for a Tavily tool call. Returns credits consumed."""
        credits = self._calculate_credits(tool_name, arguments or {})

        today = self._today_key()
        month = self._month_key()

        # Update daily
        if "daily" not in self._usage:
            self._usage["daily"] = {}
        self._usage["daily"][today] = self._usage["daily"].get(today, 0.0) + credits

        # Update monthly
        if "monthly" not in self._usage:
            self._usage["monthly"] = {}
        self._usage["monthly"][month] = self._usage["monthly"].get(month, 0.0) + credits

        # Append to history (keep last 1000 entries)
        if "history" not in self._usage:
            self._usage["history"] = []
        self._usage["history"].append({
            "tool": tool_name,
            "credits": credits,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        if len(self._usage["history"]) > 1000:
            self._usage["history"] = self._usage["history"][-1000:]

        # Clean up old daily entries (keep 90 days)
        self._cleanup_old_entries()

        self._save_usage()
        logger.info(
            "Tavily credit used: %.1f for %s (daily: %.1f, monthly: %.1f)",
            credits, tool_name,
            self._usage["daily"].get(today, 0),
            self._usage["monthly"].get(month, 0),
        )
        return credits

    def _calculate_credits(self, tool_name: str, arguments: dict[str, Any]) -> float:
        """Calculate credit cost for a specific tool call."""
        # Normalize tool name (strip mcp_ prefix variations)
        clean_name = tool_name
        for prefix in ("mcp_tavily_", "tavily_", "tavily-"):
            if clean_name.startswith(prefix):
                clean_name = clean_name[len(prefix):]
                break
        # Re-add the tavily- prefix for lookup
        if not clean_name.startswith("tavily-"):
            clean_name = f"tavily-{clean_name}"

        cost_table = CREDIT_COSTS.get(clean_name, {})
        if not cost_table:
            # Unknown tool — assume 1 credit
            return 1.0

        # Determine the depth/mode from arguments
        depth = arguments.get("search_depth", arguments.get("depth", "basic"))
        if isinstance(depth, str):
            depth = depth.lower()
        else:
            depth = "basic"

        # For extract/map, check if instructions are present
        if clean_name == "tavily-map" and arguments.get("instructions"):
            depth = "instructions"

        return cost_table.get(depth, list(cost_table.values())[0])

    def check_threshold(self) -> dict[str, Any]:
        """Check if any threshold is exceeded.

        Returns:
            {"exceeded": bool, "reason": str, "usage": {...}}
        """
        usage = self.get_usage()

        if usage["daily_limit"] > 0 and usage["daily_used"] >= usage["daily_limit"]:
            return {
                "exceeded": True,
                "reason": f"Daily Tavily credit limit reached ({usage['daily_used']:.0f}/{usage['daily_limit']} credits)",
                "usage": usage,
            }

        if usage["monthly_limit"] > 0 and usage["monthly_used"] >= usage["monthly_limit"]:
            return {
                "exceeded": True,
                "reason": f"Monthly Tavily credit limit reached ({usage['monthly_used']:.0f}/{usage['monthly_limit']} credits)",
                "usage": usage,
            }

        return {"exceeded": False, "reason": "", "usage": usage}

    def _cleanup_old_entries(self) -> None:
        """Remove daily entries older than 90 days and monthly entries older than 12 months."""
        now = datetime.now(timezone.utc)

        # Clean daily
        if "daily" in self._usage:
            cutoff = (now.replace(day=1) if now.day > 1 else now).strftime("%Y-%m-%d")
            # Keep entries from last 90 days
            from datetime import timedelta
            cutoff_date = (now - timedelta(days=90)).strftime("%Y-%m-%d")
            self._usage["daily"] = {
                k: v for k, v in self._usage["daily"].items()
                if k >= cutoff_date
            }

        # Clean monthly (keep 12 months)
        if "monthly" in self._usage:
            from datetime import timedelta
            cutoff_month = (now - timedelta(days=365)).strftime("%Y-%m")
            self._usage["monthly"] = {
                k: v for k, v in self._usage["monthly"].items()
                if k >= cutoff_month
            }

    def reset_daily(self) -> None:
        """Manually reset today's usage."""
        today = self._today_key()
        if "daily" in self._usage:
            self._usage["daily"].pop(today, None)
        self._save_usage()

    def reset_monthly(self) -> None:
        """Manually reset this month's usage."""
        month = self._month_key()
        if "monthly" in self._usage:
            self._usage["monthly"].pop(month, None)
        self._save_usage()


# ── Singleton ────────────────────────────────────────────────────────────────

_tracker: TavilyCreditTracker | None = None


def get_tavily_tracker() -> TavilyCreditTracker:
    global _tracker
    if _tracker is None:
        _tracker = TavilyCreditTracker()
    return _tracker
