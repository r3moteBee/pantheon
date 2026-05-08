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
    vault.delete_secret(_key_secret_name(name))
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
    referenced exists; raises ValueError on the first missing one.

    Any role not present in `roles` is removed from the stored mapping.
    """
    existing_names = {e.name for e in list_endpoints()}
    for r in roles:
        if r.endpoint and r.endpoint not in existing_names:
            raise ValueError(f"unknown endpoint {r.endpoint!r} for role {r.role!r}")
    rm = {r.role: {"endpoint": r.endpoint, "model": r.model} for r in roles}
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
