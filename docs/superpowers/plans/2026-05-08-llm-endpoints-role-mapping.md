# LLM Endpoints + Role Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Pantheon's flat per-role LLM settings (one base_url+api_key+model triplet per role, ~5x duplicated) with named endpoints + role-to-endpoint mapping, matching the cleaner pattern used in tuatha.

**Architecture:** New backend module `llm_config/` owns two JSON blobs in the existing vault: `llm_saved_endpoints` (registry of endpoints with name, base_url, api_type, api_key reference) and `llm_role_mapping` (role → endpoint+model). A new `/api/llm/...` router exposes CRUD on endpoints + read/write on role mapping + a generic probe-models call. `ModelProvider` stays; existing role-getter functions in `models/provider.py` are rewritten to read from the new mapping (with a one-shot migration from legacy flat keys on first read). Frontend's monolithic `LLMSection` is replaced by two new components: `EndpointList` (cards + add form) and `RoleMapping` (one row per role with cascading dropdowns).

**Tech Stack:** Python 3.12 + FastAPI + Pydantic v2 + existing `secrets/vault.py` (Fernet/SQLite). React + Vite frontend + Tailwind. Tests via pytest.

**Roles in scope** (5, matches existing Pantheon getters): `chat`, `prefill`, `vision`, `embed`, `rerank`.

**API types in scope** (4): `openai` (covers OpenAI, OpenAI-compatible like LM Studio / OpenRouter / Together / vLLM), `anthropic`, `ollama`, `custom`.

---

## File Structure

**Backend (new):**
- `backend/llm_config/__init__.py` — package marker
- `backend/llm_config/models.py` — Pydantic models (`SavedEndpoint`, `RoleAssignment`, request/response shapes)
- `backend/llm_config/store.py` — vault read/write for `llm_saved_endpoints` + `llm_role_mapping`; secret-key naming convention
- `backend/llm_config/probe.py` — generic probe-models for any (base_url, api_type, api_key)
- `backend/llm_config/migration.py` — one-shot migrator from legacy flat vault keys
- `backend/api/llm_endpoints.py` — FastAPI router mounting at `/api/llm/...`

**Backend (modified):**
- `backend/main.py:31` — add import for new router
- `backend/main.py:230` — register new router
- `backend/models/provider.py:259-378` — rewrite the 5 role-getter functions to consult the new store
- `backend/api/settings.py` — leave as-is for legacy compatibility; the new flow does not modify or remove it (Phase 2 cleanup)

**Backend tests:**
- `backend/tests/integration/test_llm_endpoints.py` — store, migration, router

**Frontend (new):**
- `frontend/src/components/settings/EndpointList.jsx` — card list + add-endpoint-form host
- `frontend/src/components/settings/EndpointCard.jsx` — single saved endpoint
- `frontend/src/components/settings/AddEndpointForm.jsx` — name + api_type + base_url + api_key form
- `frontend/src/components/settings/RoleMapping.jsx` — five role rows + Save button
- `frontend/src/components/settings/RoleMappingRow.jsx` — endpoint dropdown + model dropdown/input

**Frontend (modified):**
- `frontend/src/components/Settings.jsx:8-414` — replace the existing `LLMSection` with `<EndpointList />` and `<RoleMapping />`
- `frontend/src/api/client.js` — add 6 new client functions
- `frontend/package.json` — bump version

**Total LOC budget:** backend ~700, frontend ~700, tests ~400. Single PR.

---

## Vault Key Layout

Two JSON blobs, plus per-endpoint API keys keyed by name:

| Vault key                                | Type     | Contents                                                   |
| ---------------------------------------- | -------- | ---------------------------------------------------------- |
| `llm_saved_endpoints`                    | JSON     | `[{"name": str, "base_url": str, "api_type": str}]`        |
| `llm_role_mapping`                       | JSON     | `{"chat": {"endpoint": str, "model": str}, ...}`           |
| `llm_endpoint_key__<name>`               | secret   | API key for endpoint `<name>` (one row per endpoint)       |
| `llm_config_migrated_v1`                 | flag     | `"true"` once migration ran; absence triggers it           |

Names are `slugify`'d on save: lowercase alphanumeric + dashes, max 40 chars. Empty/duplicate name = 400 error.

---

## Migration Behavior (one-shot)

On first read of `llm_saved_endpoints` (or first `GET /api/llm/endpoints`), if `llm_config_migrated_v1` is unset:
1. For each role with non-empty legacy config (`llm_*`, `prefill_*`, `vision_*`, `embedding_*`, `reranker_*`), synthesize a saved endpoint:
   - `name`: `primary` for chat; `prefill`, `vision`, `embed`, `rerank` for the others
   - `base_url`: from legacy key (e.g., `vault.get_secret("vision_base_url")` falling back to `settings_config.vision_base_url`)
   - `api_type`: heuristic — `ollama` if URL contains `:11434`, `anthropic` if URL contains `anthropic.com`, otherwise `openai`
   - API key copied to `llm_endpoint_key__<name>`
2. Roles whose legacy config is empty inherit from `primary`. So if only the chat config is set, all five roles point to `primary` with the configured model.
3. Build `llm_role_mapping` from legacy `*_model` keys (e.g., `chat → {endpoint: "primary", model: vault.get_secret("llm_model")}`).
4. Set `llm_config_migrated_v1 = "true"`.
5. Idempotent — re-running with the flag set is a no-op.

Legacy keys are NOT deleted. The legacy `/api/settings` endpoints continue to work unchanged for backward compat, reading the same vault. Phase 2 (separate plan) cleans them up after a deprecation window.

---

## Provider Resolution

`get_provider()`, `get_prefill_provider()`, etc. each map to a role:
- `get_provider()` → role `chat`
- `get_prefill_provider()` → role `prefill`
- `get_vision_provider()` → role `vision`
- `get_embedding_provider()` → role `embed`
- `get_reranker_provider()` → role `rerank`

New behavior: each getter calls `llm_config.store.resolve_role(role)` which returns a `(base_url, api_key, model, api_type)` tuple, then constructs `ModelProvider(base_url, api_key, model)`. If the role isn't mapped (vision / reranker can be empty), returns `None` (matching current behavior — see `provider.py:325` and `:354`).

The `_provider_instance` caching at `provider.py:242` becomes a per-role dict to avoid re-resolving on every call. `reset_provider()` clears the dict.

---

## Task Decomposition

12 tasks. Each is one focused change with TDD + commit. Backend first (tasks 1-7), then provider rewrite (8), then frontend (9-11), then end-to-end (12).

---

### Task 1: Create the llm_config package + Pydantic models

**Files:**
- Create: `backend/llm_config/__init__.py`
- Create: `backend/llm_config/models.py`
- Test: `backend/tests/integration/test_llm_endpoints.py`

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/integration/test_llm_endpoints.py
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_llm_endpoints.py -v`
Expected: 4 failures with `ModuleNotFoundError: No module named 'llm_config'`

- [ ] **Step 3: Create the package marker**

Create `backend/llm_config/__init__.py` with content:
```python
"""LLM endpoints registry + role-to-endpoint mapping.

Replaces Pantheon's flat per-role config (llm_*, prefill_*, ...) with
a saved-endpoints registry + a role mapping that points at endpoints
by name. See docs/superpowers/plans/2026-05-08-llm-endpoints-role-mapping.md.
"""
```

- [ ] **Step 4: Implement the Pydantic models**

Create `backend/llm_config/models.py`:
```python
"""Pydantic models for saved endpoints + role assignments."""
from __future__ import annotations
import re
from typing import Literal
from pydantic import BaseModel, Field, field_validator

# Allowed values — kept here as the source of truth so the API and
# frontend can read the same enums.
API_TYPES = ("openai", "anthropic", "ollama", "custom")
ROLES = ("chat", "prefill", "vision", "embed", "rerank")

ApiType = Literal["openai", "anthropic", "ollama", "custom"]
Role = Literal["chat", "prefill", "vision", "embed", "rerank"]


def _slugify_name(s: str) -> str:
    """Lowercase, alphanumeric + dashes, max 40 chars. Mirrors the
    convention used elsewhere (sources/util.py) but kept local to
    avoid the cross-package dependency."""
    out = re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")
    return out[:40]


class SavedEndpoint(BaseModel):
    """One configured upstream endpoint. Stored as an entry in the
    `llm_saved_endpoints` JSON array in the vault."""
    name: str = Field(..., min_length=1, max_length=40)
    base_url: str = Field(..., min_length=1)
    api_type: ApiType

    @field_validator("name", mode="before")
    @classmethod
    def _slugify(cls, v: str) -> str:
        slug = _slugify_name(v or "")
        if not slug:
            raise ValueError("name must contain at least one alphanumeric character")
        return slug

    @field_validator("base_url")
    @classmethod
    def _strip_trailing_slash(cls, v: str) -> str:
        return (v or "").rstrip("/")


class RoleAssignment(BaseModel):
    """A single role → endpoint + model binding."""
    role: Role
    endpoint: str  # endpoint name; empty string means "unassigned"
    model: str  # may be empty when role is unassigned


class EndpointWithKey(SavedEndpoint):
    """Used for create/update — carries the API key in the request body."""
    api_key: str | None = None


class EndpointPublic(SavedEndpoint):
    """What we return from GET endpoints — never includes the api_key."""
    api_key_set: bool


class RoleMappingPayload(BaseModel):
    """PUT /api/llm/roles body — full role map at once."""
    roles: list[RoleAssignment]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_llm_endpoints.py -v`
Expected: 4 passes

- [ ] **Step 6: Commit**

```bash
cd ~/pantheon
git add backend/llm_config/__init__.py backend/llm_config/models.py backend/tests/integration/test_llm_endpoints.py
git commit -m "llm_config: scaffold package with Pydantic models for saved endpoints + roles"
```

---

### Task 2: Vault-backed store

**Files:**
- Create: `backend/llm_config/store.py`
- Modify: `backend/tests/integration/test_llm_endpoints.py` (append tests)

- [ ] **Step 1: Append failing tests**

Append to `backend/tests/integration/test_llm_endpoints.py`:
```python
def test_store_round_trip_endpoints(monkeypatch, tmp_path):
    """Saved endpoints round-trip through the vault."""
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("VAULT_MASTER_KEY", "test-key")
    # Drop any cached singleton.
    from secrets import vault as _v
    _v._vault = None
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
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("VAULT_MASTER_KEY", "test-key")
    from secrets import vault as _v
    _v._vault = None
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
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("VAULT_MASTER_KEY", "test-key")
    from secrets import vault as _v
    _v._vault = None
    from llm_config.store import set_role_mapping
    from llm_config.models import RoleAssignment

    with pytest.raises(ValueError, match="unknown endpoint"):
        set_role_mapping([RoleAssignment(role="chat", endpoint="missing", model="x")])


def test_store_resolve_role_returns_full_tuple(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("VAULT_MASTER_KEY", "test-key")
    from secrets import vault as _v
    _v._vault = None
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
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("VAULT_MASTER_KEY", "test-key")
    from secrets import vault as _v
    _v._vault = None
    from llm_config.store import resolve_role
    assert resolve_role("vision") is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_llm_endpoints.py -v -k "store"`
Expected: 5 failures with `ModuleNotFoundError: No module named 'llm_config.store'`

- [ ] **Step 3: Implement the store**

Create `backend/llm_config/store.py`:
```python
"""Vault-backed persistence for saved endpoints + role mapping.

Layout in the vault:
  - llm_saved_endpoints       JSON array of {name, base_url, api_type}
  - llm_role_mapping          JSON object: role -> {endpoint, model}
  - llm_endpoint_key__<name>  one secret per endpoint, the API key
  - llm_config_migrated_v1    flag set by migration.py, read here only
                              to decide whether resolve_role should
                              trigger migration on first call
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass

from secrets.vault import get_vault
from llm_config.models import (
    EndpointPublic, EndpointWithKey, RoleAssignment, ROLES,
)

logger = logging.getLogger(__name__)

_ENDPOINTS_KEY = "llm_saved_endpoints"
_ROLE_MAPPING_KEY = "llm_role_mapping"


def _key_secret_name(endpoint_name: str) -> str:
    return f"llm_endpoint_key__{endpoint_name}"


@dataclass
class ResolvedRole:
    """What ModelProvider needs to construct itself for a role."""
    base_url: str
    api_key: str
    model: str
    api_type: str
    endpoint_name: str


# ── Endpoint CRUD ─────────────────────────────────────────────────

def list_endpoints() -> list[EndpointPublic]:
    vault = get_vault()
    raw = vault.get_secret(_ENDPOINTS_KEY) or "[]"
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("llm_saved_endpoints corrupt, returning empty list")
        return []
    out: list[EndpointPublic] = []
    for it in items:
        try:
            name = it.get("name", "")
            key_set = bool(vault.get_secret(_key_secret_name(name)))
            out.append(EndpointPublic(
                name=name,
                base_url=it.get("base_url", ""),
                api_type=it.get("api_type", "openai"),
                api_key_set=key_set,
            ))
        except Exception as e:
            logger.warning("skipping malformed endpoint %r: %s", it, e)
    return out


def save_endpoint(payload: EndpointWithKey) -> EndpointPublic:
    """Create or update an endpoint by name. If api_key is None on
    update, the existing key is preserved; passing empty string clears it."""
    vault = get_vault()
    raw = vault.get_secret(_ENDPOINTS_KEY) or "[]"
    items = json.loads(raw) if raw else []
    # Remove existing entry with same name (update).
    items = [i for i in items if i.get("name") != payload.name]
    items.append({
        "name": payload.name,
        "base_url": payload.base_url,
        "api_type": payload.api_type,
    })
    vault.set_secret(_ENDPOINTS_KEY, json.dumps(items))
    if payload.api_key is not None:
        vault.set_secret(_key_secret_name(payload.name), payload.api_key)
    return EndpointPublic(
        name=payload.name,
        base_url=payload.base_url,
        api_type=payload.api_type,
        api_key_set=bool(payload.api_key) or bool(vault.get_secret(_key_secret_name(payload.name))),
    )


def delete_endpoint(name: str) -> None:
    """Delete an endpoint and its API key. Roles that reference it
    are unbound (endpoint set to "")."""
    vault = get_vault()
    raw = vault.get_secret(_ENDPOINTS_KEY) or "[]"
    items = [i for i in json.loads(raw or "[]") if i.get("name") != name]
    vault.set_secret(_ENDPOINTS_KEY, json.dumps(items))
    try:
        vault.delete_secret(_key_secret_name(name))
    except Exception:
        pass
    # Unbind any roles pointing at this endpoint.
    rm = get_role_mapping()
    changed = False
    for role, binding in list(rm.items()):
        if binding.get("endpoint") == name:
            rm[role] = {"endpoint": "", "model": ""}
            changed = True
    if changed:
        vault.set_secret(_ROLE_MAPPING_KEY, json.dumps(rm))


def get_endpoint(name: str) -> EndpointPublic | None:
    for e in list_endpoints():
        if e.name == name:
            return e
    return None


def get_endpoint_api_key(name: str) -> str | None:
    return get_vault().get_secret(_key_secret_name(name))


# ── Role mapping ──────────────────────────────────────────────────

def get_role_mapping() -> dict[str, dict[str, str]]:
    raw = get_vault().get_secret(_ROLE_MAPPING_KEY) or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def set_role_mapping(roles: list[RoleAssignment]) -> None:
    """Replace the entire role mapping. Validates that every endpoint
    referenced exists. Roles not in `roles` are left untouched."""
    existing_names = {e.name for e in list_endpoints()}
    for r in roles:
        if r.endpoint and r.endpoint not in existing_names:
            raise ValueError(f"unknown endpoint {r.endpoint!r} for role {r.role!r}")
    rm = get_role_mapping()
    for r in roles:
        rm[r.role] = {"endpoint": r.endpoint, "model": r.model}
    get_vault().set_secret(_ROLE_MAPPING_KEY, json.dumps(rm))


def resolve_role(role: str) -> ResolvedRole | None:
    """Return ResolvedRole for the given role, or None if unmapped.

    Triggers one-shot migration from legacy flat keys on first call
    if the migration flag isn't set."""
    if role not in ROLES:
        return None
    # Lazy migration: if not migrated, do it now. Imported locally
    # to keep store.py free of the heuristic logic.
    vault = get_vault()
    if not vault.get_secret("llm_config_migrated_v1"):
        from llm_config.migration import migrate_from_legacy
        migrate_from_legacy()
    rm = get_role_mapping()
    binding = rm.get(role) or {}
    endpoint_name = binding.get("endpoint") or ""
    model = binding.get("model") or ""
    if not endpoint_name or not model:
        return None
    ep = get_endpoint(endpoint_name)
    if ep is None:
        return None
    return ResolvedRole(
        base_url=ep.base_url,
        api_key=get_endpoint_api_key(endpoint_name) or "",
        model=model,
        api_type=ep.api_type,
        endpoint_name=endpoint_name,
    )
```

- [ ] **Step 4: Add a migration stub**

Create `backend/llm_config/migration.py` with a no-op (real implementation comes in Task 3):
```python
"""One-shot migration from legacy flat vault keys → saved endpoints + role mapping."""
from __future__ import annotations
import logging
from secrets.vault import get_vault

logger = logging.getLogger(__name__)


def migrate_from_legacy() -> None:
    """Stub — real implementation in Task 3. Sets the flag so
    resolve_role doesn't loop."""
    get_vault().set_secret("llm_config_migrated_v1", "true")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_llm_endpoints.py -v`
Expected: all (9) tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/llm_config/store.py backend/llm_config/migration.py backend/tests/integration/test_llm_endpoints.py
git commit -m "llm_config: vault-backed store for endpoints + role mapping"
```

---

### Task 3: Migration from legacy flat config

**Files:**
- Modify: `backend/llm_config/migration.py`
- Modify: `backend/tests/integration/test_llm_endpoints.py` (append tests)

- [ ] **Step 1: Append failing tests**

Append:
```python
def test_migration_creates_primary_from_legacy(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("VAULT_MASTER_KEY", "test-key")
    from secrets import vault as _v
    _v._vault = None

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
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("VAULT_MASTER_KEY", "test-key")
    from secrets import vault as _v
    _v._vault = None
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
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("VAULT_MASTER_KEY", "test-key")
    from secrets import vault as _v
    _v._vault = None
    from llm_config.migration import migrate_from_legacy
    from llm_config.store import list_endpoints, get_role_mapping
    migrate_from_legacy()
    assert list_endpoints() == []
    assert get_role_mapping() == {}
    # Flag still set so we don't re-run.
    assert _v.get_vault().get_secret("llm_config_migrated_v1") == "true"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_llm_endpoints.py -v -k "migration"`
Expected: 3 failures — migration is currently a no-op.

- [ ] **Step 3: Implement migration**

Replace `backend/llm_config/migration.py` content with:
```python
"""One-shot migration from legacy flat vault keys → saved endpoints + role mapping.

Reads the per-role legacy keys (llm_*, prefill_*, vision_*, embedding_*,
reranker_*) and synthesizes saved endpoints + a role mapping. Idempotent
via the llm_config_migrated_v1 flag.
"""
from __future__ import annotations
import json
import logging

from secrets.vault import get_vault
from config import get_settings
from llm_config.store import _ENDPOINTS_KEY, _ROLE_MAPPING_KEY, _key_secret_name

logger = logging.getLogger(__name__)

_FLAG = "llm_config_migrated_v1"


def _guess_api_type(base_url: str) -> str:
    """Heuristic — used only for migration. Users can edit afterwards."""
    u = (base_url or "").lower()
    if ":11434" in u or "/ollama" in u or "ollama" in u:
        return "ollama"
    if "anthropic.com" in u:
        return "anthropic"
    return "openai"


def _read_legacy(role_prefix: str) -> dict[str, str]:
    """Pull (base_url, api_key, model) for a legacy role from vault → settings fallback.

    role_prefix is one of: 'llm', 'prefill', 'vision', 'embedding', 'reranker'.
    Returns {"base_url", "api_key", "model"} with empty strings for missing.
    """
    vault = get_vault()
    settings = get_settings()
    if role_prefix == "llm":
        base = vault.get_secret("llm_base_url") or settings.llm_base_url or ""
        key = vault.get_secret("llm_api_key") or settings.llm_api_key or ""
        model = vault.get_secret("llm_model") or settings.llm_model or ""
    elif role_prefix == "prefill":
        base = vault.get_secret("prefill_base_url") or settings.prefill_base_url or ""
        key = vault.get_secret("prefill_api_key") or settings.prefill_api_key or ""
        model = vault.get_secret("llm_prefill_model") or settings.llm_prefill_model or ""
    elif role_prefix == "vision":
        base = vault.get_secret("vision_base_url") or settings.vision_base_url or ""
        key = vault.get_secret("vision_api_key") or settings.vision_api_key or ""
        model = vault.get_secret("llm_vision_model") or settings.llm_vision_model or ""
    elif role_prefix == "embedding":
        base = vault.get_secret("embedding_base_url") or settings.embedding_base_url or ""
        key = vault.get_secret("embedding_api_key") or settings.embedding_api_key or ""
        model = vault.get_secret("embedding_model") or settings.embedding_model or ""
    elif role_prefix == "reranker":
        base = vault.get_secret("reranker_base_url") or settings.reranker_base_url or ""
        key = vault.get_secret("reranker_api_key") or settings.reranker_api_key or ""
        model = vault.get_secret("reranker_model") or settings.reranker_model or ""
    else:
        return {"base_url": "", "api_key": "", "model": ""}
    return {"base_url": base or "", "api_key": key or "", "model": model or ""}


# Maps the new role names to the legacy role_prefix and an endpoint
# name to use when synthesizing.
_ROLE_TO_LEGACY = [
    # (new_role, legacy_prefix, synthesized_endpoint_name)
    ("chat", "llm", "primary"),
    ("prefill", "prefill", "prefill"),
    ("vision", "vision", "vision"),
    ("embed", "embedding", "embed"),
    ("rerank", "reranker", "rerank"),
]


def migrate_from_legacy() -> None:
    """Idempotent. Sets llm_config_migrated_v1 = 'true' on completion."""
    vault = get_vault()
    if vault.get_secret(_FLAG) == "true":
        return

    endpoints: list[dict[str, str]] = []
    role_mapping: dict[str, dict[str, str]] = {}
    primary_legacy = _read_legacy("llm")
    primary_present = bool(primary_legacy["base_url"])

    if primary_present:
        endpoints.append({
            "name": "primary",
            "base_url": primary_legacy["base_url"].rstrip("/"),
            "api_type": _guess_api_type(primary_legacy["base_url"]),
        })
        if primary_legacy["api_key"]:
            vault.set_secret(_key_secret_name("primary"), primary_legacy["api_key"])

    for new_role, prefix, ep_name in _ROLE_TO_LEGACY:
        legacy = _read_legacy(prefix)
        if prefix == "llm":
            # Already handled as 'primary'.
            if primary_present:
                role_mapping[new_role] = {"endpoint": "primary", "model": legacy["model"]}
            continue
        if legacy["base_url"]:
            # Distinct endpoint configured for this role.
            endpoints.append({
                "name": ep_name,
                "base_url": legacy["base_url"].rstrip("/"),
                "api_type": _guess_api_type(legacy["base_url"]),
            })
            if legacy["api_key"]:
                vault.set_secret(_key_secret_name(ep_name), legacy["api_key"])
            role_mapping[new_role] = {"endpoint": ep_name, "model": legacy["model"]}
        elif primary_present:
            # Inherit from primary; use the role-specific model if
            # one was set, otherwise leave the model blank so callers
            # can detect the absence.
            role_mapping[new_role] = {
                "endpoint": "primary",
                "model": legacy["model"] or "",
            }
        else:
            # No legacy config at all for this role.
            role_mapping[new_role] = {"endpoint": "", "model": ""}

    if endpoints:
        vault.set_secret(_ENDPOINTS_KEY, json.dumps(endpoints))
    if role_mapping:
        vault.set_secret(_ROLE_MAPPING_KEY, json.dumps(role_mapping))

    vault.set_secret(_FLAG, "true")
    logger.info(
        "llm_config: migrated legacy settings — %d endpoints, %d role bindings",
        len(endpoints), len(role_mapping),
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_llm_endpoints.py -v`
Expected: all (12) tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_config/migration.py backend/tests/integration/test_llm_endpoints.py
git commit -m "llm_config: migrate legacy flat per-role config on first read"
```

---

### Task 4: Generic probe-models

**Files:**
- Create: `backend/llm_config/probe.py`
- Modify: `backend/tests/integration/test_llm_endpoints.py` (append)

- [ ] **Step 1: Append failing tests**

```python
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
```

Also add to the top of the file (just under the `import pytest` line):
```python
pytest_plugins = ["pytest_asyncio"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_llm_endpoints.py -v -k "probe"`
Expected: 4 failures with `ModuleNotFoundError: No module named 'llm_config.probe'` (or pytest_asyncio not installed — install via `~/pantheon/.venv/bin/pip install pytest-asyncio` if needed).

- [ ] **Step 3: Implement probe**

Create `backend/llm_config/probe.py`:
```python
"""Generic probe-models for any (base_url, api_type, api_key) tuple.

Different API types expose model lists differently:
  - openai (and OpenAI-compatible): GET /v1/models -> {"data": [{"id": ...}]}
  - ollama: GET /api/tags -> {"models": [{"name": ...}]}
  - anthropic: no public list endpoint; we return a curated static list
  - custom: try /v1/models first, fall back to /models, then to anthropic's static set
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    ok: bool
    models: list[str] = field(default_factory=list)
    error: str = ""
    base_url: str = ""
    api_type: str = ""


# Curated default for Anthropic; users can type any model id manually.
_ANTHROPIC_STATIC = [
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]


async def _async_get(url: str, *, headers: dict, timeout: int = 15):
    """Indirection so tests can monkeypatch network access."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.get(url, headers=headers)


def _bearer(api_key: str) -> dict[str, str]:
    if not api_key or api_key.lower() in ("", "none"):
        return {}
    return {"Authorization": f"Bearer {api_key}"}


async def _probe_openai(base_url: str, api_key: str) -> ProbeResult:
    url = base_url.rstrip("/") + "/models"
    try:
        r = await _async_get(url, headers=_bearer(api_key))
        r.raise_for_status()
        data = r.json() or {}
        models = sorted({(m or {}).get("id", "") for m in data.get("data", []) if (m or {}).get("id")})
        return ProbeResult(ok=True, models=list(models), base_url=base_url, api_type="openai")
    except Exception as e:
        return ProbeResult(ok=False, error=str(e), base_url=base_url, api_type="openai")


async def _probe_ollama(base_url: str) -> ProbeResult:
    # Ollama's /api/tags is sibling to /v1; trim a trailing /v1 if present.
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    url = root + "/api/tags"
    try:
        r = await _async_get(url, headers={})
        r.raise_for_status()
        data = r.json() or {}
        models = sorted({(m or {}).get("name", "") for m in data.get("models", []) if (m or {}).get("name")})
        return ProbeResult(ok=True, models=list(models), base_url=base_url, api_type="ollama")
    except Exception as e:
        return ProbeResult(ok=False, error=str(e), base_url=base_url, api_type="ollama")


async def probe_models(*, base_url: str, api_type: str, api_key: str) -> ProbeResult:
    """Discover available models for an endpoint.

    api_type semantics:
      - 'openai': GET /v1/models (works for OpenAI, LM Studio, vLLM, OpenRouter, etc.)
      - 'ollama': GET /api/tags
      - 'anthropic': static curated list (no public listing endpoint)
      - 'custom': try /v1/models, fall back to ollama-style /api/tags
    """
    if api_type == "anthropic":
        return ProbeResult(ok=True, models=list(_ANTHROPIC_STATIC), base_url=base_url, api_type="anthropic")
    if api_type == "ollama":
        return await _probe_ollama(base_url)
    if api_type == "openai":
        return await _probe_openai(base_url, api_key)
    # custom
    r1 = await _probe_openai(base_url, api_key)
    if r1.ok:
        return r1
    r2 = await _probe_ollama(base_url)
    if r2.ok:
        return r2
    return ProbeResult(ok=False, error=r1.error or r2.error, base_url=base_url, api_type="custom")
```

- [ ] **Step 4: Install pytest-asyncio if missing, run tests**

Run: `cd backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_llm_endpoints.py -v`
If you get `unknown marker 'asyncio'` errors: `~/pantheon/.venv/bin/pip install pytest-asyncio`
Expected: all (16) tests pass.

- [ ] **Step 5: Commit**

```bash
git add backend/llm_config/probe.py backend/tests/integration/test_llm_endpoints.py
git commit -m "llm_config: probe_models for OpenAI / Ollama / Anthropic / custom"
```

---

### Task 5: API router

**Files:**
- Create: `backend/api/llm_endpoints.py`
- Modify: `backend/main.py:31` (add import) and `backend/main.py:230` (add include_router)
- Modify: `backend/tests/integration/test_llm_endpoints.py` (append)

- [ ] **Step 1: Append failing tests**

```python
def _fastapi_client(monkeypatch, tmp_path):
    """Spin up a TestClient with isolated vault state."""
    from fastapi.testclient import TestClient
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("VAULT_MASTER_KEY", "test-key")
    from secrets import vault as _v
    _v._vault = None
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_llm_endpoints.py -v -k "router"`
Expected: 7 failures — `/api/llm/...` returns 404.

- [ ] **Step 3: Implement the router**

Create `backend/api/llm_endpoints.py`:
```python
"""LLM endpoints + role mapping API.

Routes (mounted under /api/llm by main.py):
  GET    /endpoints              list saved endpoints
  POST   /endpoints              create or update
  DELETE /endpoints/{name}       delete (also unbinds any roles using it)
  GET    /roles                  read role mapping
  PUT    /roles                  replace role mapping (full set in body)
  POST   /probe                  probe models for an endpoint
                                 (either by saved name, or ad-hoc tuple)
"""
from __future__ import annotations
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from llm_config import probe as _probe
from llm_config.models import EndpointWithKey, ROLES, RoleMappingPayload
from llm_config.store import (
    delete_endpoint, get_endpoint_api_key, get_role_mapping,
    list_endpoints, save_endpoint, set_role_mapping,
)
from models.provider import reset_provider

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/llm/endpoints")
async def get_endpoints() -> dict[str, Any]:
    eps = list_endpoints()
    return {"endpoints": [e.model_dump() for e in eps]}


@router.post("/llm/endpoints")
async def create_or_update_endpoint(payload: EndpointWithKey) -> dict[str, Any]:
    try:
        ep = save_endpoint(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    reset_provider()
    return ep.model_dump()


@router.delete("/llm/endpoints/{name}")
async def remove_endpoint(name: str) -> dict[str, str]:
    delete_endpoint(name)
    reset_provider()
    return {"status": "deleted", "name": name}


@router.get("/llm/roles")
async def get_roles() -> dict[str, Any]:
    rm = get_role_mapping()
    # Always return one entry per role so the UI can render the full table.
    full = {}
    for role in ROLES:
        full[role] = rm.get(role) or {"endpoint": "", "model": ""}
    return {"roles": full}


@router.put("/llm/roles")
async def update_roles(payload: RoleMappingPayload) -> dict[str, Any]:
    try:
        set_role_mapping(payload.roles)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    reset_provider()
    return await get_roles()


class ProbeRequest(BaseModel):
    """One of:
      - endpoint_name: probe a saved endpoint (uses its stored key)
      - {base_url, api_type, api_key}: ad-hoc probe (no save needed)
    """
    endpoint_name: str | None = None
    base_url: str | None = None
    api_type: str | None = None
    api_key: str | None = None


@router.post("/llm/probe")
async def probe_endpoint(req: ProbeRequest) -> dict[str, Any]:
    if req.endpoint_name:
        eps = {e.name: e for e in list_endpoints()}
        ep = eps.get(req.endpoint_name)
        if ep is None:
            raise HTTPException(status_code=404, detail=f"unknown endpoint {req.endpoint_name!r}")
        api_key = get_endpoint_api_key(req.endpoint_name) or ""
        result = await _probe.probe_models(
            base_url=ep.base_url, api_type=ep.api_type, api_key=api_key,
        )
    else:
        if not (req.base_url and req.api_type):
            raise HTTPException(status_code=400, detail="base_url and api_type required for ad-hoc probe")
        result = await _probe.probe_models(
            base_url=req.base_url, api_type=req.api_type, api_key=req.api_key or "",
        )
    return {
        "ok": result.ok, "models": result.models, "error": result.error,
        "base_url": result.base_url, "api_type": result.api_type,
    }
```

- [ ] **Step 4: Wire the router into main.py**

In `backend/main.py`, find the import block around line 31 and add (alphabetical with the other api imports):

```python
from api.llm_endpoints import router as llm_endpoints_router
```

Find the `app.include_router(...)` block around line 230 and add:

```python
app.include_router(llm_endpoints_router, prefix="/api", tags=["llm"])
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_llm_endpoints.py -v`
Expected: all (23) tests pass.

- [ ] **Step 6: Commit**

```bash
git add backend/api/llm_endpoints.py backend/main.py backend/tests/integration/test_llm_endpoints.py
git commit -m "llm_config: /api/llm/{endpoints,roles,probe} router"
```

---

### Task 6: Provider getters consult new store

**Files:**
- Modify: `backend/models/provider.py:242-378` — rewrite the 5 role getters + cache + reset
- Modify: `backend/tests/integration/test_llm_endpoints.py` (append)

- [ ] **Step 1: Append failing tests**

```python
def test_get_provider_uses_new_store(monkeypatch, tmp_path):
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("VAULT_MASTER_KEY", "test-key")
    from secrets import vault as _v
    _v._vault = None
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
    monkeypatch.setenv("VAULT_DB_PATH", str(tmp_path / "vault.db"))
    monkeypatch.setenv("VAULT_MASTER_KEY", "test-key")
    from secrets import vault as _v
    _v._vault = None
    _v.get_vault().set_secret("llm_config_migrated_v1", "true")
    from models import provider
    provider.reset_provider()
    assert provider.get_vision_provider() is None
```

- [ ] **Step 2: Run tests**

Run: `cd backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_llm_endpoints.py::test_get_provider_uses_new_store tests/integration/test_llm_endpoints.py::test_get_vision_provider_returns_none_when_unbound -v`
Expected: failures — old provider getters still read flat config keys.

- [ ] **Step 3: Read the current provider.py role getters**

Read `backend/models/provider.py` lines 242-385 to confirm the exact signatures.

- [ ] **Step 4: Rewrite the getters**

Replace `backend/models/provider.py:242-385` (the whole tail starting at `_provider_instance: ModelProvider | None = None`) with:

```python
# Per-role provider cache. Cleared by reset_provider().
_role_cache: dict[str, ModelProvider] = {}


def _build_for_role(role: str) -> ModelProvider | None:
    """Resolve role → endpoint+model from the llm_config store and
    instantiate a ModelProvider. Returns None when the role is
    unmapped (acceptable for vision and reranker)."""
    from llm_config.store import resolve_role
    rr = resolve_role(role)
    if rr is None:
        return None
    return ModelProvider(base_url=rr.base_url, api_key=rr.api_key, model=rr.model)


def get_provider() -> ModelProvider:
    """Primary chat provider. Falls back to a no-op-ish ModelProvider
    constructed from settings if the role isn't mapped — same shape
    as before to keep existing call sites working when the user hasn't
    finished configuring."""
    if "chat" not in _role_cache:
        built = _build_for_role("chat")
        _role_cache["chat"] = built or ModelProvider()
    return _role_cache["chat"]


def get_embedding_provider() -> ModelProvider:
    """Embedding provider. Falls back to ModelProvider() with settings."""
    if "embed" not in _role_cache:
        built = _build_for_role("embed")
        _role_cache["embed"] = built or ModelProvider()
    return _role_cache["embed"]


def get_prefill_provider() -> ModelProvider:
    """Prefill / fallback provider. Falls back to the chat provider
    (same as the legacy behavior when prefill_* keys were empty)."""
    if "prefill" not in _role_cache:
        built = _build_for_role("prefill")
        _role_cache["prefill"] = built or get_provider()
    return _role_cache["prefill"]


def get_vision_provider() -> ModelProvider | None:
    """Optional vision provider. None when unmapped."""
    if "vision" not in _role_cache:
        _role_cache["vision"] = _build_for_role("vision")  # may be None
    return _role_cache["vision"]


def get_reranker_provider() -> ModelProvider | None:
    """Optional reranker provider. None when unmapped."""
    if "rerank" not in _role_cache:
        _role_cache["rerank"] = _build_for_role("rerank")  # may be None
    return _role_cache["rerank"]


def reset_provider() -> None:
    """Clear all cached per-role providers. Called whenever settings
    or role mapping change so the next get_*_provider() rebuilds."""
    _role_cache.clear()
```

Important: the `ModelProvider()` no-argument constructor at line 17-30 already falls back to `settings.llm_*` config — we keep using that as the safety net so a misconfigured Pantheon still boots.

- [ ] **Step 5: Run the targeted tests**

Run: `cd backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_llm_endpoints.py -v`
Expected: all (25) tests pass.

- [ ] **Step 6: Run the full integration suite to catch regressions**

Run: `cd backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ -v`
Expected: all tests pass (the 48 prior + 25 new = 73). Investigate any regressions before committing.

- [ ] **Step 7: Commit**

```bash
git add backend/models/provider.py backend/tests/integration/test_llm_endpoints.py
git commit -m "models/provider: resolve role getters via llm_config store"
```

---

### Task 7: Frontend API client functions

**Files:**
- Modify: `frontend/src/api/client.js` — add 6 new functions

- [ ] **Step 1: Read the bottom of client.js**

Read `frontend/src/api/client.js` last ~50 lines to see the export style and how existing endpoint functions are written.

- [ ] **Step 2: Add the new client functions**

Append to `frontend/src/api/client.js` (just before the final closing of any default-export block, or alongside other named exports — match the file's existing style):

```javascript
// ── LLM endpoints + role mapping ─────────────────────────────────

export const listLlmEndpoints = () =>
  api.get('/llm/endpoints').then((r) => r.data.endpoints);

export const saveLlmEndpoint = (payload) =>
  api.post('/llm/endpoints', payload).then((r) => r.data);

export const deleteLlmEndpoint = (name) =>
  api.delete(`/llm/endpoints/${encodeURIComponent(name)}`).then((r) => r.data);

export const getLlmRoles = () =>
  api.get('/llm/roles').then((r) => r.data.roles);

export const setLlmRoles = (roles) =>
  api.put('/llm/roles', { roles }).then((r) => r.data.roles);

export const probeLlmEndpoint = (payload) =>
  api.post('/llm/probe', payload).then((r) => r.data);
```

If `client.js` uses a default-export object pattern (check the file), add the same six functions as keys in that object instead.

- [ ] **Step 3: Smoke-check there are no syntax errors**

Run: `cd ~/pantheon/frontend && npx eslint src/api/client.js 2>&1 | tail -5`
Expected: no errors.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/api/client.js
git commit -m "frontend/api: add llm endpoints + roles + probe client functions"
```

---

### Task 8: EndpointCard component

**Files:**
- Create: `frontend/src/components/settings/EndpointCard.jsx`

- [ ] **Step 1: Create the component**

```jsx
// frontend/src/components/settings/EndpointCard.jsx
import { useState } from 'react';
import { deleteLlmEndpoint, probeLlmEndpoint } from '../../api/client';

const API_TYPE_LABELS = {
  openai: 'OpenAI / OpenAI-compatible',
  anthropic: 'Anthropic',
  ollama: 'Ollama',
  custom: 'Custom',
};

export default function EndpointCard({ endpoint, onChange }) {
  const [expanded, setExpanded] = useState(false);
  const [probeBusy, setProbeBusy] = useState(false);
  const [probeResult, setProbeResult] = useState(null);

  const handleProbe = async () => {
    setProbeBusy(true);
    setProbeResult(null);
    try {
      const r = await probeLlmEndpoint({ endpoint_name: endpoint.name });
      setProbeResult(r);
    } catch (e) {
      setProbeResult({ ok: false, error: String(e?.message || e), models: [] });
    } finally {
      setProbeBusy(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(`Delete endpoint "${endpoint.name}"? Roles using it will be unbound.`)) {
      return;
    }
    await deleteLlmEndpoint(endpoint.name);
    onChange?.();
  };

  return (
    <div className="border border-slate-700 rounded-md p-3 bg-slate-900/50">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="text-slate-300 hover:text-white"
            aria-expanded={expanded}
          >
            {expanded ? '▾' : '▸'}
          </button>
          <span className="font-mono font-semibold text-slate-100">{endpoint.name}</span>
          <span className="text-xs px-2 py-0.5 rounded bg-slate-700 text-slate-300">
            {API_TYPE_LABELS[endpoint.api_type] || endpoint.api_type}
          </span>
          {!endpoint.api_key_set && (
            <span className="text-xs text-amber-400">no API key</span>
          )}
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={handleProbe}
            disabled={probeBusy}
            className="text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-50"
          >
            {probeBusy ? 'Probing…' : 'Probe'}
          </button>
          <button
            type="button"
            onClick={handleDelete}
            className="text-xs px-2 py-1 rounded bg-red-900/50 hover:bg-red-800 text-red-200"
          >
            Delete
          </button>
        </div>
      </div>

      {expanded && (
        <div className="mt-3 text-sm text-slate-300 space-y-1 pl-6">
          <div>
            <span className="text-slate-500">URL:</span>{' '}
            <code className="text-xs">{endpoint.base_url}</code>
          </div>
          {probeResult && (
            <div className="mt-2">
              {probeResult.ok ? (
                <div className="text-emerald-300 text-xs">
                  Found {probeResult.models.length} models
                  {probeResult.models.length > 0 && (
                    <ul className="mt-1 max-h-40 overflow-auto text-slate-400">
                      {probeResult.models.map((m) => (
                        <li key={m}>
                          <code className="text-xs">{m}</code>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : (
                <div className="text-red-300 text-xs">Probe failed: {probeResult.error}</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Commit**

```bash
mkdir -p frontend/src/components/settings
git add frontend/src/components/settings/EndpointCard.jsx
git commit -m "frontend/settings: EndpointCard component"
```

---

### Task 9: AddEndpointForm component

**Files:**
- Create: `frontend/src/components/settings/AddEndpointForm.jsx`

- [ ] **Step 1: Create the component**

```jsx
// frontend/src/components/settings/AddEndpointForm.jsx
import { useState } from 'react';
import { saveLlmEndpoint, probeLlmEndpoint } from '../../api/client';

const API_TYPES = [
  { value: 'openai', label: 'OpenAI / OpenAI-compatible', placeholder: 'https://api.openai.com/v1' },
  { value: 'anthropic', label: 'Anthropic', placeholder: 'https://api.anthropic.com' },
  { value: 'ollama', label: 'Ollama', placeholder: 'http://localhost:11434/v1' },
  { value: 'custom', label: 'Custom', placeholder: 'https://your.endpoint/v1' },
];

export default function AddEndpointForm({ onSaved }) {
  const [name, setName] = useState('');
  const [apiType, setApiType] = useState('openai');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [showKey, setShowKey] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [testResult, setTestResult] = useState(null);

  const apiTypeMeta = API_TYPES.find((t) => t.value === apiType);

  const handleTest = async () => {
    setError('');
    setTestResult(null);
    setBusy(true);
    try {
      const r = await probeLlmEndpoint({
        base_url: baseUrl, api_type: apiType, api_key: apiKey,
      });
      setTestResult(r);
    } catch (e) {
      setError(String(e?.response?.data?.detail || e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  const handleSave = async (e) => {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      await saveLlmEndpoint({
        name, base_url: baseUrl, api_type: apiType, api_key: apiKey || null,
      });
      setName('');
      setBaseUrl('');
      setApiKey('');
      setTestResult(null);
      onSaved?.();
    } catch (e) {
      setError(String(e?.response?.data?.detail || e?.message || e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form onSubmit={handleSave} className="border border-slate-700 rounded-md p-3 bg-slate-900/30 space-y-3">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-slate-400 mb-1">Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. local-ollama"
            required
            className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-sm"
          />
        </div>
        <div>
          <label className="block text-xs text-slate-400 mb-1">API type</label>
          <select
            value={apiType}
            onChange={(e) => setApiType(e.target.value)}
            className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-sm"
          >
            {API_TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label className="block text-xs text-slate-400 mb-1">Base URL</label>
        <input
          type="url"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder={apiTypeMeta?.placeholder || ''}
          required
          className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-sm"
        />
      </div>
      <div>
        <label className="block text-xs text-slate-400 mb-1">API key</label>
        <div className="flex gap-2">
          <input
            type={showKey ? 'text' : 'password'}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder="(leave blank for Ollama / open endpoints)"
            className="flex-1 bg-slate-800 border border-slate-600 rounded px-2 py-1 text-sm"
          />
          <button
            type="button"
            onClick={() => setShowKey((v) => !v)}
            className="text-xs px-2 py-1 rounded bg-slate-700"
          >
            {showKey ? 'Hide' : 'Show'}
          </button>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={handleTest}
          disabled={busy || !baseUrl}
          className="text-xs px-3 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-50"
        >
          {busy ? 'Testing…' : 'Test'}
        </button>
        <button
          type="submit"
          disabled={busy || !name || !baseUrl}
          className="text-xs px-3 py-1 rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50"
        >
          Save endpoint
        </button>
        {testResult && (
          <span className={`text-xs ${testResult.ok ? 'text-emerald-300' : 'text-red-300'}`}>
            {testResult.ok
              ? `OK — ${testResult.models.length} models`
              : `Failed: ${testResult.error}`}
          </span>
        )}
        {error && <span className="text-xs text-red-300">{error}</span>}
      </div>
    </form>
  );
}
```

- [ ] **Step 2: Commit**

```bash
git add frontend/src/components/settings/AddEndpointForm.jsx
git commit -m "frontend/settings: AddEndpointForm with Test + Save"
```

---

### Task 10: EndpointList + RoleMapping containers

**Files:**
- Create: `frontend/src/components/settings/EndpointList.jsx`
- Create: `frontend/src/components/settings/RoleMapping.jsx`
- Create: `frontend/src/components/settings/RoleMappingRow.jsx`

- [ ] **Step 1: EndpointList**

Create `frontend/src/components/settings/EndpointList.jsx`:
```jsx
import { useEffect, useState } from 'react';
import { listLlmEndpoints } from '../../api/client';
import EndpointCard from './EndpointCard';
import AddEndpointForm from './AddEndpointForm';

export default function EndpointList({ onChange }) {
  const [endpoints, setEndpoints] = useState([]);
  const [loading, setLoading] = useState(true);

  const refresh = async () => {
    setLoading(true);
    try {
      setEndpoints(await listLlmEndpoints());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
  }, []);

  const handleChange = () => {
    refresh();
    onChange?.();
  };

  return (
    <section className="space-y-3">
      <header className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-200">Endpoints</h3>
        <span className="text-xs text-slate-500">
          {loading ? '…' : `${endpoints.length} configured`}
        </span>
      </header>
      <div className="space-y-2">
        {endpoints.map((e) => (
          <EndpointCard key={e.name} endpoint={e} onChange={handleChange} />
        ))}
        {!loading && endpoints.length === 0 && (
          <div className="text-xs text-slate-500 italic">
            No endpoints yet. Add one below.
          </div>
        )}
      </div>
      <AddEndpointForm onSaved={handleChange} />
    </section>
  );
}
```

- [ ] **Step 2: RoleMappingRow**

Create `frontend/src/components/settings/RoleMappingRow.jsx`:
```jsx
import { useEffect, useState } from 'react';
import { probeLlmEndpoint } from '../../api/client';

export default function RoleMappingRow({
  role, label, description, endpoints, value, onChange,
}) {
  const [models, setModels] = useState([]);
  const [probing, setProbing] = useState(false);
  const [probeError, setProbeError] = useState('');

  const selectedEndpoint = value?.endpoint || '';
  const selectedModel = value?.model || '';

  useEffect(() => {
    setModels([]);
    setProbeError('');
  }, [selectedEndpoint]);

  const fetchModels = async () => {
    if (!selectedEndpoint) return;
    setProbing(true);
    setProbeError('');
    try {
      const r = await probeLlmEndpoint({ endpoint_name: selectedEndpoint });
      if (r.ok) {
        setModels(r.models);
      } else {
        setProbeError(r.error || 'probe failed');
      }
    } finally {
      setProbing(false);
    }
  };

  return (
    <div className="grid grid-cols-12 gap-2 items-center py-2 border-b border-slate-800">
      <div className="col-span-3">
        <div className="text-sm text-slate-200">{label}</div>
        <div className="text-xs text-slate-500">{description}</div>
      </div>
      <div className="col-span-4">
        <select
          value={selectedEndpoint}
          onChange={(e) => onChange({ endpoint: e.target.value, model: '' })}
          className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1 text-sm"
        >
          <option value="">— unassigned —</option>
          {endpoints.map((ep) => (
            <option key={ep.name} value={ep.name}>{ep.name}</option>
          ))}
        </select>
      </div>
      <div className="col-span-5 flex gap-2">
        {models.length > 0 ? (
          <select
            value={selectedModel}
            onChange={(e) => onChange({ endpoint: selectedEndpoint, model: e.target.value })}
            className="flex-1 bg-slate-800 border border-slate-600 rounded px-2 py-1 text-sm"
          >
            <option value="">— pick a model —</option>
            {models.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        ) : (
          <input
            type="text"
            value={selectedModel}
            onChange={(e) => onChange({ endpoint: selectedEndpoint, model: e.target.value })}
            placeholder="model id"
            disabled={!selectedEndpoint}
            className="flex-1 bg-slate-800 border border-slate-600 rounded px-2 py-1 text-sm disabled:opacity-50"
          />
        )}
        <button
          type="button"
          onClick={fetchModels}
          disabled={!selectedEndpoint || probing}
          className="text-xs px-2 py-1 rounded bg-slate-700 hover:bg-slate-600 disabled:opacity-50"
        >
          {probing ? '…' : 'Fetch'}
        </button>
      </div>
      {probeError && (
        <div className="col-span-12 text-xs text-red-300 pl-3">{probeError}</div>
      )}
    </div>
  );
}
```

- [ ] **Step 3: RoleMapping**

Create `frontend/src/components/settings/RoleMapping.jsx`:
```jsx
import { useEffect, useState } from 'react';
import {
  getLlmRoles, setLlmRoles, listLlmEndpoints,
} from '../../api/client';
import RoleMappingRow from './RoleMappingRow';

const ROLES = [
  { id: 'chat', label: 'Chat', description: 'Main agent loop' },
  { id: 'prefill', label: 'Prefill / fallback', description: 'Curation, summarization, secondary calls' },
  { id: 'vision', label: 'Vision', description: 'Image-aware completions (optional)' },
  { id: 'embed', label: 'Embeddings', description: 'Semantic memory + topic embeddings' },
  { id: 'rerank', label: 'Reranker', description: 'Optional re-ranker for retrieval' },
];

export default function RoleMapping({ refreshKey }) {
  const [endpoints, setEndpoints] = useState([]);
  const [roles, setRolesState] = useState({});
  const [saving, setSaving] = useState(false);
  const [saveStatus, setSaveStatus] = useState('');

  const refresh = async () => {
    const [eps, rm] = await Promise.all([listLlmEndpoints(), getLlmRoles()]);
    setEndpoints(eps);
    setRolesState(rm);
  };

  useEffect(() => {
    refresh();
  }, [refreshKey]);

  const handleRowChange = (roleId, value) => {
    setRolesState((prev) => ({ ...prev, [roleId]: value }));
  };

  const handleSave = async () => {
    setSaving(true);
    setSaveStatus('');
    try {
      const payload = ROLES.map(({ id }) => ({
        role: id,
        endpoint: roles[id]?.endpoint || '',
        model: roles[id]?.model || '',
      }));
      await setLlmRoles(payload);
      setSaveStatus('Saved');
    } catch (e) {
      setSaveStatus(`Error: ${String(e?.response?.data?.detail || e.message)}`);
    } finally {
      setSaving(false);
    }
  };

  return (
    <section className="space-y-2">
      <header className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-200">Role mapping</h3>
        <span className="text-xs text-slate-500">{endpoints.length} endpoints available</span>
      </header>
      <div className="border border-slate-700 rounded-md bg-slate-900/40 px-3">
        {ROLES.map(({ id, label, description }) => (
          <RoleMappingRow
            key={id}
            role={id}
            label={label}
            description={description}
            endpoints={endpoints}
            value={roles[id]}
            onChange={(v) => handleRowChange(id, v)}
          />
        ))}
      </div>
      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={handleSave}
          disabled={saving}
          className="text-xs px-3 py-1 rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save role mapping'}
        </button>
        {saveStatus && (
          <span className={`text-xs ${saveStatus.startsWith('Error') ? 'text-red-300' : 'text-emerald-300'}`}>
            {saveStatus}
          </span>
        )}
      </div>
    </section>
  );
}
```

- [ ] **Step 4: ESLint check**

Run: `cd ~/pantheon/frontend && npx eslint src/components/settings/ 2>&1 | tail -10`
Expected: no errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/components/settings/
git commit -m "frontend/settings: EndpointList + RoleMapping containers"
```

---

### Task 11: Wire new components into Settings.jsx

**Files:**
- Modify: `frontend/src/components/Settings.jsx:8-414` — replace the old `LLMSection` body

- [ ] **Step 1: Read the existing Settings.jsx structure**

Open `frontend/src/components/Settings.jsx` and look at lines 1-50 to see imports + how `LLMSection` is used by the parent component.

- [ ] **Step 2: Replace the LLMSection body**

Find the function `function LLMSection(...)` at `frontend/src/components/Settings.jsx:8` and replace its entire body (until the matching closing brace, around line 414) with:

```jsx
function LLMSection() {
  const [refreshKey, setRefreshKey] = useState(0);
  return (
    <div className="space-y-6">
      <EndpointList onChange={() => setRefreshKey((k) => k + 1)} />
      <RoleMapping refreshKey={refreshKey} />
    </div>
  );
}
```

Also at the top of `Settings.jsx`, add the imports (group alphabetically with the existing ones):

```jsx
import EndpointList from './settings/EndpointList';
import RoleMapping from './settings/RoleMapping';
```

If the existing file imported `useState` already, leave that alone. Otherwise add `import { useState } from 'react';` if it's not present.

- [ ] **Step 3: Drop the now-unused helpers**

Inside Settings.jsx, scan for any locally-defined helpers used only by the old `LLMSection` body (e.g., `function ProviderRow(...)`, `function ModelDataList(...)`). Delete them — they're dead now.

If a helper is used by other sections (search the file), leave it.

- [ ] **Step 4: Build the frontend**

Run: `cd ~/pantheon/frontend && VITE_API_URL="" npm run build 2>&1 | tail -20`
Expected: clean build, "built in Xs" line. Investigate any errors before continuing.

- [ ] **Step 5: Bump version**

Edit `frontend/package.json` and bump the `"version"` field. Use today's date with the next H suffix; e.g. if current is `2026.05.07.H4`, set `2026.05.08.H1`.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/components/Settings.jsx frontend/package.json
git commit -m "frontend/Settings: replace LLMSection with EndpointList + RoleMapping"
```

---

### Task 12: End-to-end smoke test

**Files:**
- (No new files — manual + scripted verification)

- [ ] **Step 1: Restart the backend**

The user runs deploy commands themselves. Tell the user to run:

```bash
cd ~/pantheon
./stop.sh && pkill -f "uvicorn main:app" 2>/dev/null
find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
./start.sh && sleep 3 && curl -s http://localhost:8000/api/health
```

Expected: health reports the new version string set in Task 11 Step 5.

- [ ] **Step 2: Verify migration ran**

Ask the user to confirm: open the Settings page, look for two panels (Endpoints + Role Mapping). The endpoints should be auto-populated from any prior LLM config the user had — typically a single "primary" endpoint with the existing api_type/base_url.

If migration didn't fire (panels empty but legacy config exists):
```bash
curl -s http://localhost:8000/api/llm/endpoints
curl -s http://localhost:8000/api/llm/roles
```
The first call to either should trigger migration via `resolve_role()`'s lazy path. If those return empty and the user is *certain* they have legacy config, add a debug log in `migration.py` and investigate.

- [ ] **Step 3: Manual UI walkthrough**

Walk through these scenarios in the browser:
1. The migrated `primary` endpoint shows in the list with the correct base_url + api_type badge.
2. Click "Probe" on the card — model list expands.
3. Add a second endpoint (e.g. point at Ollama at `http://localhost:11434/v1`), Test, Save.
4. In Role Mapping, switch the `embed` role to the new Ollama endpoint, click Fetch, pick a model, click Save role mapping.
5. Reload the page — values persist.
6. Send a chat message — confirm it still works (uses the migrated `chat` role).
7. Trigger an embedding (e.g. ingest a small artifact) — confirm `embed` role uses the newly-assigned Ollama endpoint (check logs for the base_url).

- [ ] **Step 4: Run full backend test suite**

Run: `cd backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ -v 2>&1 | tail -10`
Expected: all tests pass.

- [ ] **Step 5: Commit nothing (no code changes); branch ready to merge**

If everything in Steps 2-4 worked, the feature is shippable. Push the branch:

```bash
git push -u origin feature/llm-endpoints-role-mapping
```

Then merge to main following the same fast-forward + branch-cleanup pattern used for Phase B and the CFR adapter.

---

## Self-Review

**Spec coverage check** (against the original requirements):
- Backend data model (saved_endpoints + role_mapping) → Tasks 1-2 ✓
- Backend API surface (6 routes: list/post/delete endpoints, get/put roles, probe) → Task 5 ✓
- Migration from existing flat settings → Task 3 ✓
- Frontend UI rewrite (split LLMSection into EndpointList + RoleMapping) → Tasks 8-11 ✓
- ModelProvider role lookup → Task 6 ✓
- Smoke test end-to-end → Task 12 ✓

**Placeholder scan:** No "TBD", no "fill in details", no "similar to Task N". Every code block contains the actual code.

**Type consistency:**
- `SavedEndpoint`, `EndpointWithKey`, `EndpointPublic`, `RoleAssignment`, `RoleMappingPayload` — all defined in Task 1, used consistently in Tasks 2-5.
- `ResolvedRole` defined in Task 2, consumed in Task 6.
- API route paths (`/api/llm/endpoints`, `/api/llm/roles`, `/api/llm/probe`) consistent across Tasks 5 and 7.
- Client function names (`listLlmEndpoints`, `saveLlmEndpoint`, etc.) consistent in Tasks 7-10.
- 5 roles (chat / prefill / vision / embed / rerank) consistent across migration mapping (Task 3), provider getters (Task 6), API roles dict (Task 5), and frontend ROLES array (Task 10).

**Risk notes:**
- Task 6's per-role cache change in `provider.py` is a behavioral change for code that previously held `ModelProvider` references across `reset_provider()` calls. Search for any caller that does that — none expected, but worth a grep before merging Task 6.
- Migration heuristic for `api_type` (Task 3) might mis-detect (e.g., a custom OpenAI-compatible endpoint at `:11434` would be tagged "ollama"). User can edit the endpoint after migration to fix.
- Frontend ESLint rules and Tailwind classes are assumed to match Pantheon conventions; the styling may need to be adjusted to match the existing Settings.jsx visual language (dark theme, slate palette). Visual polish is a Phase 2 follow-up.
