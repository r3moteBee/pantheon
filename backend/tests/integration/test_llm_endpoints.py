"""Tests for the LLM endpoints + role mapping subsystem."""
from __future__ import annotations
import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)

import pytest


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
