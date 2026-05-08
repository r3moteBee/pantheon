"""Tests for the LLM endpoints + role mapping subsystem."""
from __future__ import annotations
import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)

import pytest

pytest_plugins = ["pytest_asyncio"]


def test_saved_endpoint_validates_api_type():
    from llm_config.models import SavedEndpoint
    e = SavedEndpoint(name="primary", base_url="https://api.openai.com/v1", api_type="openai")
    assert e.name == "primary"
    with pytest.raises(ValueError):
        SavedEndpoint(name="x", base_url="https://x", api_type="bogus")


def test_saved_endpoint_slugifies_name():
    from llm_config.models import SavedEndpoint
    e = SavedEndpoint(name="My Local Ollama!", base_url="http://localhost:11434", api_type="ollama")
    assert e.name == "my-local-ollama"


def test_saved_endpoint_rejects_empty_name():
    from llm_config.models import SavedEndpoint
    with pytest.raises(ValueError):
        SavedEndpoint(name="", base_url="https://x", api_type="openai")


def test_role_assignment_validates_role_name():
    from llm_config.models import RoleAssignment
    r = RoleAssignment(role="chat", endpoint="primary", model="gpt-4o")
    assert r.role == "chat"
    with pytest.raises(ValueError):
        RoleAssignment(role="bogus", endpoint="primary", model="x")


# ── Vault-backed store tests ──────────────────────────────────────
#
# Note: Pantheon's Settings.vault_db_path is a derived property from
# DATA_DIR — there is no VAULT_DB_PATH env var. To isolate each test's
# vault DB, we construct a SecretsVault directly against tmp_path and
# pin it as the module-level singleton via monkeypatch so the original
# is auto-restored at teardown. Without monkeypatch the singleton
# would leak across modules and point at a vanished tmp_path DB.
def _reset_vault(monkeypatch, tmp_path):
    """Replace the vault singleton with one rooted at tmp_path. monkeypatch
    auto-restores the original at test teardown so subsequent tests in
    other modules aren't poisoned."""
    from secrets import vault as _v
    fresh = _v.SecretsVault(
        db_path=str(tmp_path / "vault.db"),
        master_key="test-key",
    )
    monkeypatch.setattr(_v, "_vault_instance", fresh)
    monkeypatch.setattr(_v, "_cache", {})


def test_store_round_trip_endpoints(monkeypatch, tmp_path):
    """Saved endpoints round-trip through the vault."""
    _reset_vault(monkeypatch, tmp_path)
    from llm_config.store import (
        list_endpoints, save_endpoint, delete_endpoint,
    )
    from llm_config.models import EndpointWithKey

    assert list_endpoints() == []
    save_endpoint(EndpointWithKey(
        name="primary", base_url="https://api.openai.com/v1",
        api_type="openai", api_key="sk-test",
    ))
    eps = list_endpoints()
    assert len(eps) == 1
    assert eps[0].name == "primary"
    assert eps[0].api_key_set is True

    delete_endpoint("primary")
    assert list_endpoints() == []


def test_store_role_mapping_round_trip(monkeypatch, tmp_path):
    _reset_vault(monkeypatch, tmp_path)
    from llm_config.store import (
        save_endpoint, set_role_mapping, get_role_mapping,
    )
    from llm_config.models import EndpointWithKey, RoleAssignment

    save_endpoint(EndpointWithKey(
        name="primary", base_url="https://api.openai.com/v1",
        api_type="openai", api_key="sk-test",
    ))
    set_role_mapping([
        RoleAssignment(role="chat", endpoint="primary", model="gpt-4o"),
        RoleAssignment(role="embed", endpoint="primary", model="text-embedding-3-small"),
    ])
    rm = get_role_mapping()
    assert rm["chat"]["endpoint"] == "primary"
    assert rm["chat"]["model"] == "gpt-4o"
    assert rm["embed"]["model"] == "text-embedding-3-small"


def test_store_set_role_rejects_unknown_endpoint(monkeypatch, tmp_path):
    _reset_vault(monkeypatch, tmp_path)
    from llm_config.store import set_role_mapping
    from llm_config.models import RoleAssignment

    with pytest.raises(ValueError, match="unknown endpoint"):
        set_role_mapping([RoleAssignment(role="chat", endpoint="missing", model="x")])


def test_store_resolve_role_returns_full_tuple(monkeypatch, tmp_path):
    _reset_vault(monkeypatch, tmp_path)
    from llm_config.store import (
        save_endpoint, set_role_mapping, resolve_role,
    )
    from llm_config.models import EndpointWithKey, RoleAssignment

    save_endpoint(EndpointWithKey(
        name="primary", base_url="https://api.openai.com/v1",
        api_type="openai", api_key="sk-test",
    ))
    set_role_mapping([RoleAssignment(role="chat", endpoint="primary", model="gpt-4o")])
    r = resolve_role("chat")
    assert r is not None
    assert r.base_url == "https://api.openai.com/v1"
    assert r.api_key == "sk-test"
    assert r.model == "gpt-4o"
    assert r.api_type == "openai"


def test_store_resolve_role_returns_none_when_unmapped(monkeypatch, tmp_path):
    _reset_vault(monkeypatch, tmp_path)
    from llm_config.store import resolve_role
    assert resolve_role("vision") is None


def test_migration_creates_primary_from_legacy(monkeypatch, tmp_path):
    _reset_vault(monkeypatch, tmp_path)
    from secrets import vault as _v
    vault = _v.get_vault()
    vault.set_secret("llm_base_url", "https://api.openai.com/v1")
    vault.set_secret("llm_api_key", "sk-legacy")
    vault.set_secret("llm_model", "gpt-4o")
    vault.set_secret("embedding_base_url", "http://localhost:11434/v1")
    vault.set_secret("embedding_api_key", "ollama")
    vault.set_secret("embedding_model", "nomic-embed-text")

    from llm_config.migration import migrate_from_legacy
    migrate_from_legacy()

    from llm_config.store import list_endpoints, get_role_mapping, resolve_role
    eps = {e.name: e for e in list_endpoints()}
    assert "primary" in eps
    assert "embed" in eps
    assert eps["primary"].api_type == "openai"
    assert eps["embed"].api_type == "ollama"  # detected from :11434

    rm = get_role_mapping()
    assert rm["chat"]["endpoint"] == "primary"
    assert rm["chat"]["model"] == "gpt-4o"
    assert rm["embed"]["endpoint"] == "embed"

    # Roles without legacy config inherit from primary.
    assert rm["prefill"]["endpoint"] == "primary"
    assert rm["vision"]["endpoint"] == "primary"

    # Resolved role returns the API key from the new layout.
    r = resolve_role("chat")
    assert r is not None
    assert r.api_key == "sk-legacy"


def test_migration_is_idempotent(monkeypatch, tmp_path):
    _reset_vault(monkeypatch, tmp_path)
    from secrets import vault as _v
    vault = _v.get_vault()
    vault.set_secret("llm_base_url", "https://api.openai.com/v1")
    vault.set_secret("llm_api_key", "sk-legacy")
    vault.set_secret("llm_model", "gpt-4o")
    from llm_config.migration import migrate_from_legacy
    from llm_config.store import list_endpoints

    migrate_from_legacy()
    first = len(list_endpoints())
    migrate_from_legacy()
    assert len(list_endpoints()) == first


def test_migration_no_legacy_creates_nothing(monkeypatch, tmp_path):
    _reset_vault(monkeypatch, tmp_path)
    from secrets import vault as _v
    from llm_config.migration import migrate_from_legacy
    from llm_config.store import list_endpoints, get_role_mapping
    migrate_from_legacy()
    assert list_endpoints() == []
    assert get_role_mapping() == {}
    # Flag still set so we don't re-run.
    assert _v.get_vault().get_secret("llm_config_migrated_v1") == "true"


@pytest.mark.asyncio
async def test_probe_openai_extracts_model_ids(monkeypatch):
    """probe() shape-tests the OpenAI /v1/models response."""
    from llm_config import probe

    async def _fake_get(url, *, headers, timeout):
        class _R:
            status_code = 200
            def json(self):
                return {"data": [{"id": "gpt-4o"}, {"id": "gpt-4o-mini"}]}
            def raise_for_status(self):
                pass
        return _R()

    monkeypatch.setattr(probe, "_async_get", _fake_get)
    result = await probe.probe_models(
        base_url="https://api.openai.com/v1",
        api_type="openai",
        api_key="sk-test",
    )
    assert result.ok is True
    assert "gpt-4o" in result.models
    assert "gpt-4o-mini" in result.models


@pytest.mark.asyncio
async def test_probe_ollama_uses_tags_endpoint(monkeypatch):
    from llm_config import probe

    captured = {}
    async def _fake_get(url, *, headers, timeout):
        captured["url"] = url
        class _R:
            status_code = 200
            def json(self):
                return {"models": [{"name": "llama3.2:3b"}, {"name": "qwen2.5:14b"}]}
            def raise_for_status(self):
                pass
        return _R()

    monkeypatch.setattr(probe, "_async_get", _fake_get)
    result = await probe.probe_models(
        base_url="http://localhost:11434/v1",
        api_type="ollama",
        api_key="",
    )
    assert "/api/tags" in captured["url"]  # ollama uses its native endpoint
    assert "llama3.2:3b" in result.models


@pytest.mark.asyncio
async def test_probe_anthropic_returns_static_list():
    from llm_config import probe
    result = await probe.probe_models(
        base_url="https://api.anthropic.com",
        api_type="anthropic",
        api_key="sk-ant",
    )
    assert result.ok is True
    assert any("opus" in m for m in result.models)
    assert any("sonnet" in m for m in result.models)


@pytest.mark.asyncio
async def test_probe_handles_failure(monkeypatch):
    from llm_config import probe

    async def _fake_get(url, *, headers, timeout):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(probe, "_async_get", _fake_get)
    result = await probe.probe_models(
        base_url="http://localhost:99999",
        api_type="openai",
        api_key="",
    )
    assert result.ok is False
    assert "connection refused" in result.error


def _fastapi_client(monkeypatch, tmp_path):
    """Spin up a TestClient with isolated vault state."""
    from fastapi.testclient import TestClient
    _reset_vault(monkeypatch, tmp_path)
    from main import app
    return TestClient(app)


def test_router_get_endpoints_empty(monkeypatch, tmp_path):
    c = _fastapi_client(monkeypatch, tmp_path)
    r = c.get("/api/llm/endpoints")
    assert r.status_code == 200
    assert r.json() == {"endpoints": []}


def test_router_create_and_list_endpoint(monkeypatch, tmp_path):
    c = _fastapi_client(monkeypatch, tmp_path)
    r = c.post("/api/llm/endpoints", json={
        "name": "primary",
        "base_url": "https://api.openai.com/v1",
        "api_type": "openai",
        "api_key": "sk-test",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "primary"
    assert body["api_key_set"] is True
    assert "api_key" not in body  # never echoed back

    r2 = c.get("/api/llm/endpoints")
    assert len(r2.json()["endpoints"]) == 1


def test_router_delete_endpoint_unbinds_roles(monkeypatch, tmp_path):
    c = _fastapi_client(monkeypatch, tmp_path)
    c.post("/api/llm/endpoints", json={
        "name": "primary", "base_url": "https://x", "api_type": "openai", "api_key": "k",
    })
    c.put("/api/llm/roles", json={"roles": [
        {"role": "chat", "endpoint": "primary", "model": "gpt-4o"},
    ]})
    c.delete("/api/llm/endpoints/primary")
    r = c.get("/api/llm/roles")
    assert r.json()["roles"]["chat"]["endpoint"] == ""


def test_router_role_mapping_round_trip(monkeypatch, tmp_path):
    c = _fastapi_client(monkeypatch, tmp_path)
    c.post("/api/llm/endpoints", json={
        "name": "primary", "base_url": "https://x", "api_type": "openai", "api_key": "k",
    })
    r = c.put("/api/llm/roles", json={"roles": [
        {"role": "chat", "endpoint": "primary", "model": "gpt-4o"},
        {"role": "embed", "endpoint": "primary", "model": "text-embedding-3-small"},
    ]})
    assert r.status_code == 200
    g = c.get("/api/llm/roles").json()
    assert g["roles"]["chat"]["model"] == "gpt-4o"
    assert g["roles"]["embed"]["endpoint"] == "primary"


def test_router_role_rejects_unknown_endpoint(monkeypatch, tmp_path):
    c = _fastapi_client(monkeypatch, tmp_path)
    r = c.put("/api/llm/roles", json={"roles": [
        {"role": "chat", "endpoint": "missing", "model": "gpt-4o"},
    ]})
    assert r.status_code == 400
    assert "missing" in r.json()["detail"]


def test_router_probe_models_with_monkeypatched_probe(monkeypatch, tmp_path):
    c = _fastapi_client(monkeypatch, tmp_path)
    from llm_config import probe
    async def _fake(*, base_url, api_type, api_key):
        return probe.ProbeResult(ok=True, models=["m1", "m2"], base_url=base_url, api_type=api_type)
    monkeypatch.setattr(probe, "probe_models", _fake)
    r = c.post("/api/llm/probe", json={
        "base_url": "https://x", "api_type": "openai", "api_key": "k",
    })
    assert r.status_code == 200
    assert r.json()["models"] == ["m1", "m2"]


def test_router_probe_models_with_saved_endpoint_uses_stored_key(monkeypatch, tmp_path):
    c = _fastapi_client(monkeypatch, tmp_path)
    c.post("/api/llm/endpoints", json={
        "name": "primary", "base_url": "https://x", "api_type": "openai", "api_key": "stored-key",
    })
    captured = {}
    from llm_config import probe
    async def _fake(*, base_url, api_type, api_key):
        captured["api_key"] = api_key
        return probe.ProbeResult(ok=True, models=[], base_url=base_url, api_type=api_type)
    monkeypatch.setattr(probe, "probe_models", _fake)
    c.post("/api/llm/probe", json={"endpoint_name": "primary"})
    assert captured["api_key"] == "stored-key"


def test_get_provider_uses_new_store(monkeypatch, tmp_path):
    _reset_vault(monkeypatch, tmp_path)
    from secrets import vault as _v
    vault = _v.get_vault()
    # Pre-set the migrated flag so we skip the legacy heuristic.
    vault.set_secret("llm_config_migrated_v1", "true")
    from llm_config.store import save_endpoint, set_role_mapping
    from llm_config.models import EndpointWithKey, RoleAssignment
    save_endpoint(EndpointWithKey(
        name="primary", base_url="https://api.openai.com/v1",
        api_type="openai", api_key="sk-new",
    ))
    set_role_mapping([RoleAssignment(role="chat", endpoint="primary", model="gpt-4o")])

    # Force the provider to re-resolve.
    from models import provider
    provider.reset_provider()
    p = provider.get_provider()
    assert p.base_url == "https://api.openai.com/v1"
    assert p.api_key == "sk-new"
    assert p.model == "gpt-4o"


def test_get_vision_provider_returns_none_when_unbound(monkeypatch, tmp_path):
    _reset_vault(monkeypatch, tmp_path)
    from secrets import vault as _v
    _v.get_vault().set_secret("llm_config_migrated_v1", "true")
    from models import provider
    provider.reset_provider()
    assert provider.get_vision_provider() is None


def test_embed_role_uses_assigned_model_not_legacy_default(monkeypatch, tmp_path):
    """When the embed role is mapped to a new endpoint+model, the
    resulting ModelProvider's embedding_model field reflects the
    assigned model — not whatever the legacy settings default is."""
    _reset_vault(monkeypatch, tmp_path)
    from secrets import vault as _v
    _v.get_vault().set_secret("llm_config_migrated_v1", "true")
    from llm_config.store import save_endpoint, set_role_mapping
    from llm_config.models import EndpointWithKey, RoleAssignment

    save_endpoint(EndpointWithKey(
        name="ollama-local",
        base_url="http://localhost:11434/v1",
        api_type="ollama",
        api_key="",
    ))
    set_role_mapping([RoleAssignment(
        role="embed", endpoint="ollama-local", model="nomic-embed-text",
    )])

    from models import provider
    provider.reset_provider()
    p = provider.get_embedding_provider()
    assert p.embedding_model == "nomic-embed-text"
    assert p.base_url == "http://localhost:11434/v1"


def test_list_endpoints_triggers_migration(monkeypatch, tmp_path):
    """Opening the Settings page (which calls list_endpoints first)
    should surface the user's legacy vault config. Regression test
    for the migration-trigger asymmetry that would otherwise show
    empty endpoints on first load."""
    _reset_vault(monkeypatch, tmp_path)
    from secrets import vault as _v
    vault = _v.get_vault()
    vault.set_secret("llm_base_url", "https://api.openai.com/v1")
    vault.set_secret("llm_api_key", "sk-legacy")
    vault.set_secret("llm_model", "gpt-4o")

    from llm_config.store import list_endpoints, get_role_mapping
    eps = list_endpoints()
    assert len(eps) == 1
    assert eps[0].name == "primary"

    rm = get_role_mapping()
    assert rm["chat"]["endpoint"] == "primary"
    assert rm["chat"]["model"] == "gpt-4o"
