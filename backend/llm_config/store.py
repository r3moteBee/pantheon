"""Vault-backed persistence for saved endpoints + role mapping.

Layout in the vault:
  - llm_saved_endpoints       JSON array of {name, base_url, api_type}
  - llm_role_mapping          JSON object: role -> {endpoint, model}
  - llm_endpoint_key__<name>  one secret per endpoint, the API key
  - llm_config_migrated_v1    flag set by migration.py, read here only
                              to decide whether resolve_role should
                              trigger migration on first call
  - llm_routes                JSON object: task class -> [{endpoint, model}, ...]
                              (primary first, then fallbacks). Seeded once
                              from llm_role_mapping (llm_routes_migrated_v1).
  - llm_model_profiles        JSON object: "endpoint/model" -> user-edited
                              capability profile (else known_models guess)
"""
from __future__ import annotations
import json
import logging
from dataclasses import dataclass

from secrets.vault import get_vault
from llm_config.models import (
    EndpointPublic, EndpointWithKey, ModelProfile, RoleAssignment, ROLES,
    ROLE_TO_CLASS, RouteEntry, TASK_CLASSES,
)

logger = logging.getLogger(__name__)

ENDPOINTS_KEY = "llm_saved_endpoints"
ROLE_MAPPING_KEY = "llm_role_mapping"
ROUTES_KEY = "llm_routes"
ROUTES_MIGRATED_KEY = "llm_routes_migrated_v1"
PROFILES_KEY = "llm_model_profiles"
ADVERTISED_KEY = "llm_model_advertised"   # endpoint/model -> capabilities the server published


def key_secret_name(endpoint_name: str) -> str:
    return f"llm_endpoint_key__{endpoint_name}"


def _ensure_migrated() -> None:
    """Lazy migration trigger. Idempotent — checks the flag first.
    Called from any store read that must surface legacy data."""
    vault = get_vault()
    if not vault.get_secret("llm_config_migrated_v1"):
        from llm_config.migration import migrate_from_legacy
        migrate_from_legacy()


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
    _ensure_migrated()
    vault = get_vault()
    raw = vault.get_secret(ENDPOINTS_KEY) or "[]"
    try:
        items = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("llm_saved_endpoints corrupt, returning empty list")
        return []
    out: list[EndpointPublic] = []
    for it in items:
        try:
            name = it.get("name", "")
            key_set = bool(vault.get_secret(key_secret_name(name)))
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
    # Migrate first: a later lazy migration rewrites the endpoints list and
    # would drop an endpoint saved before any read.
    _ensure_migrated()
    vault = get_vault()
    raw = vault.get_secret(ENDPOINTS_KEY) or "[]"
    items = json.loads(raw) if raw else []
    # Remove existing entry with same name (update).
    items = [i for i in items if i.get("name") != payload.name]
    items.append({
        "name": payload.name,
        "base_url": payload.base_url,
        "api_type": payload.api_type,
    })
    vault.set_secret(ENDPOINTS_KEY, json.dumps(items))
    if payload.api_key is not None:
        vault.set_secret(key_secret_name(payload.name), payload.api_key)
    return EndpointPublic(
        name=payload.name,
        base_url=payload.base_url,
        api_type=payload.api_type,
        api_key_set=bool(payload.api_key) or bool(vault.get_secret(key_secret_name(payload.name))),
    )


def delete_endpoint(name: str) -> None:
    """Delete an endpoint and its API key. Roles that reference it
    are unbound (endpoint set to "")."""
    _ensure_migrated()
    vault = get_vault()
    raw = vault.get_secret(ENDPOINTS_KEY) or "[]"
    items = [i for i in json.loads(raw or "[]") if i.get("name") != name]
    vault.set_secret(ENDPOINTS_KEY, json.dumps(items))
    vault.delete_secret(key_secret_name(name))
    # Unbind any roles pointing at this endpoint.
    rm = get_role_mapping()
    changed = False
    for role, binding in list(rm.items()):
        if binding.get("endpoint") == name:
            rm[role] = {"endpoint": "", "model": ""}
            changed = True
    if changed:
        vault.set_secret(ROLE_MAPPING_KEY, json.dumps(rm))
    routes = get_routes()
    scrubbed = {c: [e for e in entries if e["endpoint"] != name] for c, entries in routes.items()}
    if scrubbed != routes:
        vault.set_secret(ROUTES_KEY, json.dumps(scrubbed))
    record_advertised(name, {})


def get_endpoint(name: str) -> EndpointPublic | None:
    for e in list_endpoints():
        if e.name == name:
            return e
    return None


def get_endpoint_api_key(name: str) -> str | None:
    return get_vault().get_secret(key_secret_name(name))


# ── Role mapping ──────────────────────────────────────────────────

def get_role_mapping() -> dict[str, dict[str, str]]:
    _ensure_migrated()
    raw = get_vault().get_secret(ROLE_MAPPING_KEY) or "{}"
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
    vault = get_vault()
    vault.set_secret(ROLE_MAPPING_KEY, json.dumps(rm))
    # Legacy API: once routes exist, a role write sets the primary of the
    # role's task class (fallbacks are kept; empty binding clears it).
    if vault.get_secret(ROUTES_MIGRATED_KEY):
        routes = get_routes()
        for r in roles:
            cls = ROLE_TO_CLASS[r.role]
            rest = [e for e in routes[cls][1:]]
            routes[cls] = ([{"endpoint": r.endpoint, "model": r.model}] if r.endpoint and r.model else []) + rest
        vault.set_secret(ROUTES_KEY, json.dumps(routes))


def resolve_role(role: str) -> ResolvedRole | None:
    """Back-compat: legacy role -> primary entry of its task class."""
    if role not in ROLES:
        return None
    chain = resolve_route(ROLE_TO_CLASS[role])
    return chain[0] if chain else None


# ── Task-class routes ────────────────────────────────────────────

def _migrate_routes_from_roles() -> None:
    """One-shot: seed llm_routes from the legacy 5-role mapping."""
    vault = get_vault()
    if vault.get_secret(ROUTES_MIGRATED_KEY):
        return
    _ensure_migrated()
    rm = get_role_mapping()

    def entry(role: str) -> list[dict]:
        b = rm.get(role) or {}
        if b.get("endpoint") and b.get("model"):
            return [{"endpoint": b["endpoint"], "model": b["model"]}]
        return []

    routes = {c: [] for c in TASK_CLASSES}
    routes["agent"] = entry("chat")
    # Ingest topic extraction ran on the chat model; keep that so research
    # quality doesn't silently change. prefill was the summaries helper.
    routes["extract"] = entry("chat")
    routes["summarize"] = entry("prefill")
    routes["vision"] = entry("vision")
    routes["embed"] = entry("embed")
    routes["rerank"] = entry("rerank")
    # Only write if nothing is there yet (don't clobber a fresh config).
    if not vault.get_secret(ROUTES_KEY):
        vault.set_secret(ROUTES_KEY, json.dumps(routes))
    vault.set_secret(ROUTES_MIGRATED_KEY, "1")
    logger.info("LLM routing: seeded task-class routes from legacy role mapping")


def get_routes() -> dict[str, list[dict[str, str]]]:
    """Stored routing table: class -> [{endpoint, model}, ...] (all classes present)."""
    _migrate_routes_from_roles()
    raw = get_vault().get_secret(ROUTES_KEY) or "{}"
    try:
        stored = json.loads(raw)
    except json.JSONDecodeError:
        stored = {}
    out: dict[str, list[dict[str, str]]] = {}
    for c in TASK_CLASSES:
        entries = stored.get(c) or []
        out[c] = [
            {"endpoint": e.get("endpoint", ""), "model": e.get("model", "")}
            for e in entries if isinstance(e, dict) and e.get("endpoint") and e.get("model")
        ]
    return out


def set_routes(routes: dict[str, list[RouteEntry]]) -> list[str]:
    """Replace the routing table. Raises ValueError for unknown endpoints or
    too many entries; returns capability warnings (non-blocking)."""
    _migrate_routes_from_roles()
    existing_names = {e.name for e in list_endpoints()}
    out: dict[str, list[dict[str, str]]] = {c: [] for c in TASK_CLASSES}
    for cls, entries in routes.items():
        if cls not in TASK_CLASSES:
            raise ValueError(f"unknown task class {cls!r}")
        limit = TASK_CLASSES[cls]["max_entries"]
        if len(entries) > limit:
            raise ValueError(f"{cls!r} allows at most {limit} model(s)")
        for e in entries:
            if e.endpoint not in existing_names:
                raise ValueError(f"unknown endpoint {e.endpoint!r} in {cls!r}")
        out[cls] = [{"endpoint": e.endpoint, "model": e.model} for e in entries]
    get_vault().set_secret(ROUTES_KEY, json.dumps(out))
    return route_warnings(out)


def route_warnings(routes: dict[str, list[dict[str, str]]] | None = None) -> list[str]:
    """Entries whose model profile lacks the class's required capability."""
    routes = routes if routes is not None else get_routes()
    warnings: list[str] = []
    for cls, entries in routes.items():
        need = TASK_CLASSES[cls]["requires"]
        if not need:
            continue
        for e in entries:
            prof = get_profile(e["endpoint"], e["model"])
            if not getattr(prof, need, False):
                warnings.append(
                    f"{TASK_CLASSES[cls]['label']}: {e['endpoint']}/{e['model']} "
                    f"is not marked as supporting {need.replace('_', ' ')}"
                    + (" (unknown model — set its profile)" if prof.source == "unknown" else "")
                )
    return warnings


def resolve_route(task_class: str) -> list[ResolvedRole]:
    """Ordered candidates for a task class, following inheritance
    (code -> agent, extract -> summarize -> agent) when a class is empty.
    Entries whose endpoint no longer exists are skipped."""
    if task_class not in TASK_CLASSES:
        raise ValueError(f"unknown task class {task_class!r}")
    routes = get_routes()
    cls: str | None = task_class
    entries: list[dict[str, str]] = []
    while cls and not entries:
        entries = routes.get(cls) or []
        cls = TASK_CLASSES[cls]["inherits"]
    endpoints = {e.name: e for e in list_endpoints()}
    out: list[ResolvedRole] = []
    for e in entries:
        ep = endpoints.get(e["endpoint"])
        if ep is None:
            continue
        out.append(ResolvedRole(
            base_url=ep.base_url,
            api_key=get_endpoint_api_key(ep.name) or "",
            model=e["model"],
            api_type=ep.api_type,
            endpoint_name=ep.name,
        ))
    return out


# ── Model profiles ───────────────────────────────────────────────

def _profile_key(endpoint: str, model: str) -> str:
    return f"{endpoint}/{model}"


def _stored_profiles() -> dict[str, dict]:
    raw = get_vault().get_secret(PROFILES_KEY) or "{}"
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def get_profile(endpoint: str, model: str) -> ModelProfile:
    """Precedence: a user edit, else what the endpoint advertises (layered
    over the name-based guess for fields it didn't state), else the guess."""
    stored = _stored_profiles().get(_profile_key(endpoint, model))
    if stored:
        try:
            return ModelProfile(**{**stored, "source": "user"})
        except Exception:
            pass
    from llm_config.known_models import guess_profile
    guess = guess_profile(model)
    adv = _advertised().get(_profile_key(endpoint, model))
    if adv:
        fields = {k: v for k, v in adv.items() if k in _ADVERTISABLE}
        if fields:
            return guess.model_copy(update={**fields, "source": "endpoint"})
    return guess


_ADVERTISABLE = ("tools", "vision", "embedding", "image_gen", "context_window")


def _advertised() -> dict[str, dict]:
    raw = get_vault().get_secret(ADVERTISED_KEY) or "{}"
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def record_advertised(endpoint: str, capabilities: dict[str, dict]) -> int:
    """Replace what ``endpoint`` advertises (from a probe). Models the server
    no longer describes fall back to the name guess. Returns the count."""
    data = {k: v for k, v in _advertised().items() if not k.startswith(f"{endpoint}/")}
    for model, caps in capabilities.items():
        clean = {k: v for k, v in caps.items() if k in _ADVERTISABLE}
        if clean:
            data[_profile_key(endpoint, model)] = clean
    get_vault().set_secret(ADVERTISED_KEY, json.dumps(data))
    return sum(1 for k in data if k.startswith(f"{endpoint}/"))


def set_profiles(profiles: dict[str, ModelProfile]) -> None:
    """Merge user edits (keyed "endpoint/model") into stored profiles."""
    stored = _stored_profiles()
    for key, prof in profiles.items():
        if "/" not in key:
            raise ValueError(f"profile key must be 'endpoint/model', got {key!r}")
        stored[key] = prof.model_dump(exclude={"source"})
    get_vault().set_secret(PROFILES_KEY, json.dumps(stored))


def delete_profile(endpoint: str, model: str) -> None:
    """Forget a user edit (revert to the known-models guess)."""
    stored = _stored_profiles()
    if stored.pop(_profile_key(endpoint, model), None) is not None:
        get_vault().set_secret(PROFILES_KEY, json.dumps(stored))


async def refresh_advertised(endpoint_name: str | None = None) -> dict[str, int]:
    """Probe saved endpoints and record the capabilities they publish.
    Called by the Probe button and once at startup (best-effort)."""
    from llm_config import probe as _probe
    out: dict[str, int] = {}
    for ep in list_endpoints():
        if endpoint_name and ep.name != endpoint_name:
            continue
        try:
            res = await _probe.probe_models(base_url=ep.base_url, api_type=ep.api_type,
                                            api_key=get_endpoint_api_key(ep.name) or "")
        except Exception as e:
            logger.info("capability refresh for %s failed: %s", ep.name, e)
            continue
        if res.ok:
            out[ep.name] = record_advertised(ep.name, res.capabilities)
    return out
