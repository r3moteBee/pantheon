"""SearXNG is the default search provider; keyed providers are used only once configured.

Before: the default chain started with Brave, which has no key on a fresh install, so
every search raised "brave api key not set", logged a warning, recorded a failed call
and told the agent about the fallthrough before SearXNG answered.
"""
from __future__ import annotations

import logging

from agent.search_providers import DEFAULT_PROVIDERS, SearchProviderManager


def P(name, type_=None, key_name=""):
    return {"name": name, "type": type_ or name, "url": "http://x", "api_key_vault_key": key_name,
            "daily_limit": 0, "monthly_limit": 0, "rps": 0, "enabled": True}


BRAVE = P("brave", key_name="brave_api_key")
SEARXNG = P("searxng")
DDG = P("ddg")


def _manager(monkeypatch, providers, keys=None, counts=None):
    """Manager with the network replaced. Like the real _brave/_tavily/..., a keyed
    provider called without a key raises 'api key not set'."""
    keys, counts = keys or {}, counts or {}
    m = SearchProviderManager()
    calls, recorded = [], []
    monkeypatch.setattr(m, "get_providers", lambda: [dict(p) for p in providers])
    monkeypatch.setattr(m, "_get_api_key", lambda prov: keys.get(prov["name"], ""))
    monkeypatch.setattr(m, "_quota_exhausted", lambda prov: None)

    async def _no_rps(prov):
        return None

    async def _call(prov, query):
        calls.append(prov["name"])
        if prov["type"] in ("brave", "tavily", "google", "bing") and not keys.get(prov["name"]):
            raise RuntimeError(f"{prov['type']} api key not set")
        return f"results from {prov['name']}", counts.get(prov["name"], 3), None

    monkeypatch.setattr(m, "_enforce_rps", _no_rps)
    monkeypatch.setattr(m, "_call_provider", _call)
    monkeypatch.setattr(m, "_record_call",
                        lambda name, ok, results_count, remote_stats=None: recorded.append((name, ok)))
    return m, calls, recorded


def test_searxng_is_first_in_the_default_chain():
    assert DEFAULT_PROVIDERS[0]["type"] == "searxng"
    assert any(p["type"] == "brave" for p in DEFAULT_PROVIDERS)   # still there, for when a key is added


def test_keyed_types_cover_every_provider_that_refuses_to_run_without_a_key():
    from agent.search_providers import KEY_REQUIRED_TYPES
    assert {"brave", "tavily", "google", "bing"} <= KEY_REQUIRED_TYPES
    assert "searxng" not in KEY_REQUIRED_TYPES and "ddg" not in KEY_REQUIRED_TYPES


async def test_brave_without_a_key_is_skipped_silently(monkeypatch, caplog):
    # Old saved chains still list brave first - it must be skipped, not "failed".
    m, calls, recorded = _manager(monkeypatch, [BRAVE, SEARXNG, DDG])
    with caplog.at_level(logging.DEBUG, logger="agent.search_providers"):
        out = await m.search("self-hosted search")
    assert calls == ["searxng"]
    assert out.startswith("[searched via searxng]")                # no fallthrough note for the agent
    assert ("brave", False) not in recorded                        # not counted as a failed call
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_brave_is_used_once_a_key_is_configured(monkeypatch):
    m, calls, _ = _manager(monkeypatch, [BRAVE, SEARXNG], keys={"brave": "k"})
    out = await m.search("q")
    assert calls == ["brave"]
    assert out.startswith("[searched via brave]")


async def test_keyless_provider_is_skipped_on_fallthrough_too(monkeypatch):
    m, calls, _ = _manager(monkeypatch, [SEARXNG, BRAVE, DDG], counts={"searxng": 0})
    out = await m.search("q")
    assert calls == ["searxng", "ddg"]
    assert "searxng: 0 results" in out and "brave" not in out


async def test_generic_provider_with_optional_key_is_still_tried(monkeypatch):
    generic = P("myapi", type_="generic", key_name="search_key__myapi")
    m, calls, _ = _manager(monkeypatch, [generic, DDG])
    await m.search("q")
    assert calls == ["myapi"]
