"""Web search provider chain with rate limiting, quota tracking, and caching.

Reads provider config from the vault and walks them in order. Each provider
falls through to the next on:
  - exception (network/HTTP/JSON error)
  - empty results
  - rate-limit / quota exhaustion (skip without calling)

Usage stats and thresholds are persisted to data/db/search_usage.json and
exposed via the settings API for display in the integrations page.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from config import get_settings

logger = logging.getLogger(__name__)
_settings = get_settings()

# ── Provider definitions ─────────────────────────────────────────────────────
# Default chain. Users override via vault key `search_providers` (JSON list).
# Each entry: {name, type, url, api_key_vault_key, daily_limit, monthly_limit, rps}
# type ∈ {"brave", "searxng", "ddg", "generic"}

DEFAULT_PROVIDERS: list[dict[str, Any]] = [
    {
        "name": "brave",
        "type": "brave",
        "url": "https://api.search.brave.com/res/v1/web/search",
        "api_key_vault_key": "brave_api_key",
        "daily_limit": 0,         # 0 = unlimited
        "monthly_limit": 2000,    # free tier
        "rps": 1,                 # free tier: 1 req/sec
        "enabled": True,
    },
    {
        "name": "searxng",
        "type": "searxng",
        "url": "http://localhost:8888",
        "api_key_vault_key": "",
        "daily_limit": 0,
        "monthly_limit": 0,
        "rps": 0,                 # 0 = no limit
        "enabled": True,
    },
    {
        "name": "ddg",
        "type": "ddg",
        "url": "",
        "api_key_vault_key": "",
        "daily_limit": 0,
        "monthly_limit": 0,
        "rps": 0,
        "enabled": True,
    },
]

_VAULT_PROVIDERS_KEY = "search_providers"
_USAGE_FILE = "search_usage.json"
_CACHE_TTL_SECONDS = 600  # 10 minutes


# ─────────────────────────────────────────────────────────────────────────────


class SearchProviderManager:
    """Persistent provider chain with quota tracking + result cache."""

    def __init__(self) -> None:
        self._usage_path = _settings.data_dir / "db" / _USAGE_FILE
        self._usage: dict[str, Any] = self._load_usage()
        self._cache: dict[str, tuple[float, str, str]] = {}  # query -> (ts, provider_name, result)
        self._last_call_ts: dict[str, float] = {}  # provider_name -> last call timestamp
        self._lock = asyncio.Lock()

    # ── Persistence ──────────────────────────────────────────────────────

    def _load_usage(self) -> dict[str, Any]:
        if self._usage_path.exists():
            try:
                return json.loads(self._usage_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"providers": {}}

    def _save_usage(self) -> None:
        self._usage_path.parent.mkdir(parents=True, exist_ok=True)
        self._usage_path.write_text(json.dumps(self._usage, indent=2), encoding="utf-8")

    # ── Provider config ──────────────────────────────────────────────────

    def get_providers(self) -> list[dict[str, Any]]:
        """Return the configured provider chain. Falls back to defaults."""
        try:
            from secrets.vault import get_vault
            raw = get_vault().get_secret(_VAULT_PROVIDERS_KEY)
            if raw:
                providers = json.loads(raw)
                if isinstance(providers, list) and providers:
                    return providers
        except Exception as e:
            logger.debug("Could not load search provider config from vault: %s", e)
        return DEFAULT_PROVIDERS.copy()

    def set_providers(self, providers: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Persist a new provider chain to the vault."""
        from secrets.vault import get_vault
        get_vault().set_secret(_VAULT_PROVIDERS_KEY, json.dumps(providers))
        return self.get_providers()

    # ── Quota tracking ───────────────────────────────────────────────────

    def _today_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _month_key(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m")

    def _provider_usage(self, name: str) -> dict[str, Any]:
        return self._usage.setdefault("providers", {}).setdefault(
            name, {"daily": {}, "monthly": {}, "history": [], "errors": 0, "skipped": 0}
        )

    def get_usage(self) -> dict[str, Any]:
        """Return per-provider usage stats merged with current limits."""
        today = self._today_key()
        month = self._month_key()
        out = []
        for prov in self.get_providers():
            name = prov["name"]
            u = self._provider_usage(name)
            daily_used = u.get("daily", {}).get(today, 0)
            monthly_used = u.get("monthly", {}).get(month, 0)
            daily_limit = int(prov.get("daily_limit", 0) or 0)
            monthly_limit = int(prov.get("monthly_limit", 0) or 0)
            out.append({
                "name": name,
                "type": prov.get("type", "generic"),
                "url": prov.get("url", ""),
                "enabled": prov.get("enabled", True),
                "rps": prov.get("rps", 0),
                "daily_used": daily_used,
                "daily_limit": daily_limit,
                "daily_remaining": max(0, daily_limit - daily_used) if daily_limit > 0 else -1,
                "monthly_used": monthly_used,
                "monthly_limit": monthly_limit,
                "monthly_remaining": max(0, monthly_limit - monthly_used) if monthly_limit > 0 else -1,
                "errors": u.get("errors", 0),
                "skipped": u.get("skipped", 0),
                "api_key_set": bool(self._get_api_key(prov)),
                "last_used": u.get("history", [{}])[-1].get("timestamp") if u.get("history") else None,
                "remote": u.get("remote"),
            })
        return {"providers": out, "date": today, "month": month}

    def _record_call(self, name: str, ok: bool, results_count: int, remote_stats: dict[str, Any] | None = None) -> None:
        today = self._today_key()
        month = self._month_key()
        u = self._provider_usage(name)
        if ok:
            u["daily"][today] = u["daily"].get(today, 0) + 1
            u["monthly"][month] = u["monthly"].get(month, 0) + 1
        else:
            u["errors"] = u.get("errors", 0) + 1
        if remote_stats:
            u["remote"] = {**remote_stats, "captured_at": datetime.now(timezone.utc).isoformat()}
        u.setdefault("history", []).append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "ok": ok,
            "results": results_count,
        })
        if len(u["history"]) > 500:
            u["history"] = u["history"][-500:]
        self._cleanup_old()
        self._save_usage()

    def _record_skip(self, name: str, reason: str) -> None:
        u = self._provider_usage(name)
        u["skipped"] = u.get("skipped", 0) + 1
        u["last_skip_reason"] = reason
        self._save_usage()

    def _cleanup_old(self) -> None:
        cutoff_day = (datetime.now(timezone.utc) - timedelta(days=90)).strftime("%Y-%m-%d")
        cutoff_month = (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m")
        for u in self._usage.get("providers", {}).values():
            u["daily"] = {k: v for k, v in u.get("daily", {}).items() if k >= cutoff_day}
            u["monthly"] = {k: v for k, v in u.get("monthly", {}).items() if k >= cutoff_month}

    def reset_provider_usage(self, name: str, period: str = "daily") -> None:
        u = self._provider_usage(name)
        if period == "daily":
            u.get("daily", {}).pop(self._today_key(), None)
        elif period == "monthly":
            u.get("monthly", {}).pop(self._month_key(), None)
        elif period == "all":
            u["daily"] = {}
            u["monthly"] = {}
            u["errors"] = 0
            u["skipped"] = 0
        self._save_usage()

    # ── Limit checking ───────────────────────────────────────────────────

    def _quota_exhausted(self, prov: dict[str, Any]) -> str | None:
        name = prov["name"]
        u = self._provider_usage(name)
        today, month = self._today_key(), self._month_key()
        d_lim = int(prov.get("daily_limit", 0) or 0)
        m_lim = int(prov.get("monthly_limit", 0) or 0)
        if d_lim > 0 and u.get("daily", {}).get(today, 0) >= d_lim:
            return f"daily quota {d_lim} reached"
        if m_lim > 0 and u.get("monthly", {}).get(month, 0) >= m_lim:
            return f"monthly quota {m_lim} reached"
        return None

    async def _enforce_rps(self, prov: dict[str, Any]) -> None:
        rps = float(prov.get("rps") or 0)
        if rps <= 0:
            return
        min_interval = 1.0 / rps
        name = prov["name"]
        last = self._last_call_ts.get(name, 0)
        elapsed = time.monotonic() - last
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self._last_call_ts[name] = time.monotonic()

    # ── API key resolution ───────────────────────────────────────────────

    def _get_api_key(self, prov: dict[str, Any]) -> str:
        key_name = prov.get("api_key_vault_key", "")
        if not key_name:
            return ""
        try:
            from secrets.vault import get_vault
            return get_vault().get_secret(key_name) or ""
        except Exception:
            return ""

    # ── Public search entry point ────────────────────────────────────────

    async def search(self, query: str) -> str:
        """Walk the provider chain and return the first non-empty result set."""
        norm = re.sub(r"\s+", " ", query.strip().lower())

        # Cache check
        cached = self._cache.get(norm)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
            logger.debug("search cache hit (%s)", cached[1])
            return f"[cached via {cached[1]}]\n{cached[2]}"

        attempts: list[str] = []
        for prov in self.get_providers():
            if not prov.get("enabled", True):
                continue
            name = prov["name"]
            quota_msg = self._quota_exhausted(prov)
            if quota_msg:
                self._record_skip(name, quota_msg)
                attempts.append(f"{name}: skipped ({quota_msg})")
                continue

            try:
                async with self._lock:
                    await self._enforce_rps(prov)
                result_text, count, remote_stats = await self._call_provider(prov, query)
            except Exception as e:
                logger.warning("search provider %s failed: %s", name, e)
                self._record_call(name, ok=False, results_count=0)
                attempts.append(f"{name}: error ({type(e).__name__})")
                continue

            self._record_call(name, ok=True, results_count=count, remote_stats=remote_stats)
            if count == 0:
                attempts.append(f"{name}: 0 results")
                continue

            tag = f"[searched via {name}"
            if attempts:
                tag += " — fallthrough: " + "; ".join(attempts)
            tag += "]\n"
            full = tag + result_text
            self._cache[norm] = (time.time(), name, result_text)
            return full

        return "No search results — all providers exhausted: " + "; ".join(attempts)

    # ── Provider implementations ─────────────────────────────────────────

    async def _call_provider(self, prov: dict[str, Any], query: str) -> tuple[str, int, dict[str, Any] | None]:
        ptype = (prov.get("type") or "generic").lower()
        if ptype == "brave":
            return await self._brave(prov, query)
        if ptype == "searxng":
            text, n = await self._searxng(prov, query)
            return text, n, None
        if ptype == "ddg":
            text, n = await self._ddg(query)
            return text, n, None
        text, n = await self._generic(prov, query)
        return text, n, None

    @staticmethod
    def _parse_brave_window(header_value: str) -> list[int]:
        """Brave returns comma-separated values for per-second and per-month windows.
        Example: 'X-RateLimit-Limit: 1, 2000'  ->  [1, 2000]
        """
        if not header_value:
            return []
        out = []
        for part in header_value.split(","):
            part = part.strip()
            try:
                out.append(int(part))
            except ValueError:
                pass
        return out

    async def _brave(self, prov: dict[str, Any], query: str) -> tuple[str, int, dict[str, Any] | None]:
        api_key = self._get_api_key(prov)
        if not api_key:
            raise RuntimeError("brave api key not set")
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
            "User-Agent": "Pantheon/1.0",
        }
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(prov["url"], params={"q": query, "count": 6}, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        # Capture Brave rate-limit headers as remote stats.
        # Brave returns two windows: per-second and per-month.
        limits    = self._parse_brave_window(resp.headers.get("X-RateLimit-Limit", ""))
        remaining = self._parse_brave_window(resp.headers.get("X-RateLimit-Remaining", ""))
        reset     = self._parse_brave_window(resp.headers.get("X-RateLimit-Reset", ""))
        remote_stats: dict[str, Any] | None = None
        if limits or remaining:
            remote_stats = {
                "second_limit":     limits[0]    if len(limits)    > 0 else None,
                "second_remaining": remaining[0] if len(remaining) > 0 else None,
                "second_reset":     reset[0]     if len(reset)     > 0 else None,
                "month_limit":      limits[1]    if len(limits)    > 1 else None,
                "month_remaining":  remaining[1] if len(remaining) > 1 else None,
                "month_reset":      reset[1]     if len(reset)     > 1 else None,
            }
            # Derived: monthly used = limit - remaining
            if remote_stats["month_limit"] is not None and remote_stats["month_remaining"] is not None:
                remote_stats["month_used"] = remote_stats["month_limit"] - remote_stats["month_remaining"]

        results = (data.get("web") or {}).get("results", [])
        return self._format_results(results, "description"), len(results), remote_stats

    async def _searxng(self, prov: dict[str, Any], query: str) -> tuple[str, int]:
        url = prov["url"].rstrip("/")
        if not url.endswith("/search"):
            url += "/search"
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(url, params={"q": query, "format": "json"},
                                    headers={"User-Agent": "Pantheon/1.0", "Accept": "application/json"})
            resp.raise_for_status()
            data = resp.json()
        results = data.get("results", [])
        return self._format_results(results, "content"), len(results)

    async def _ddg(self, query: str) -> tuple[str, int]:
        url = "https://html.duckduckgo.com/html/"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; Pantheon/1.0)"}
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, data={"q": query, "b": ""}, headers=headers)
            resp.raise_for_status()
        results_re = re.findall(r'<a class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>', resp.text, re.S)
        snippets_re = re.findall(r'<a class="result__snippet"[^>]*>(.*?)</a>', resp.text, re.S)
        clean_tag = re.compile(r'<[^>]+>')
        items = []
        for i, (link, title) in enumerate(results_re[:6]):
            items.append({
                "title": clean_tag.sub('', title).strip(),
                "url": link,
                "snippet": clean_tag.sub('', snippets_re[i]).strip() if i < len(snippets_re) else "",
            })
        return self._format_results(items, "snippet"), len(items)

    async def _generic(self, prov: dict[str, Any], query: str) -> tuple[str, int]:
        api_key = self._get_api_key(prov)
        headers = {"User-Agent": "Pantheon/1.0", "Accept": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
            resp = await client.get(prov["url"], params={"q": query, "format": "json"}, headers=headers)
            resp.raise_for_status()
            data = resp.json()
        if isinstance(data, list):
            raw = data
        elif isinstance(data, dict):
            raw = data.get("results") or (data.get("web") or {}).get("results", [])
        else:
            raw = []
        return self._format_results(raw, "snippet"), len(raw)

    def _format_results(self, raw: list[dict[str, Any]], snippet_field: str) -> str:
        lines = []
        for i, item in enumerate(raw[:6]):
            title = (item.get("title") or "").strip()
            url = (item.get("url") or item.get("href") or "").strip()
            snippet = (
                item.get(snippet_field)
                or item.get("description")
                or item.get("content")
                or item.get("snippet")
                or ""
            ).strip()
            if title and url:
                lines.append(f"{i+1}. {title}\n   {url}\n   {snippet}")
        return "\n\n".join(lines)


# ── Singleton ───────────────────────────────────────────────────────────────

_manager: SearchProviderManager | None = None


def get_search_manager() -> SearchProviderManager:
    global _manager
    if _manager is None:
        _manager = SearchProviderManager()
    return _manager
