"""One-shot migration from legacy flat vault keys → saved endpoints + role mapping.

Reads the per-role legacy keys (llm_*, prefill_*, vision_*, embedding_*,
reranker_*) and synthesizes saved endpoints + a role mapping. Idempotent
via the llm_config_migrated_v1 flag.
"""
from __future__ import annotations
import json
import logging

from secrets.vault import get_vault
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
    """Pull (base_url, api_key, model) for a legacy role from vault only.

    role_prefix is one of: 'llm', 'prefill', 'vision', 'embedding', 'reranker'.
    Returns {"base_url", "api_key", "model"} with empty strings for missing.

    Only vault entries are consulted — settings defaults are intentionally
    NOT used as a fallback so a fresh install (no legacy keys, just env
    defaults) doesn't synthesize a 'primary' endpoint nobody asked for.
    """
    vault = get_vault()
    if role_prefix == "llm":
        base = vault.get_secret("llm_base_url") or ""
        key = vault.get_secret("llm_api_key") or ""
        model = vault.get_secret("llm_model") or ""
    elif role_prefix == "prefill":
        base = vault.get_secret("prefill_base_url") or ""
        key = vault.get_secret("prefill_api_key") or ""
        model = vault.get_secret("llm_prefill_model") or ""
    elif role_prefix == "vision":
        base = vault.get_secret("vision_base_url") or ""
        key = vault.get_secret("vision_api_key") or ""
        model = vault.get_secret("llm_vision_model") or ""
    elif role_prefix == "embedding":
        base = vault.get_secret("embedding_base_url") or ""
        key = vault.get_secret("embedding_api_key") or ""
        model = vault.get_secret("embedding_model") or ""
    elif role_prefix == "reranker":
        base = vault.get_secret("reranker_base_url") or ""
        key = vault.get_secret("reranker_api_key") or ""
        model = vault.get_secret("reranker_model") or ""
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
        # else: no legacy config at all for this role — omit entirely so
        # we don't clobber a freshly-configured role mapping when migration
        # runs on a vault that already has new-style data.

    if endpoints:
        vault.set_secret(_ENDPOINTS_KEY, json.dumps(endpoints))
    if role_mapping:
        vault.set_secret(_ROLE_MAPPING_KEY, json.dumps(role_mapping))

    vault.set_secret(_FLAG, "true")
    logger.info(
        "llm_config: migrated legacy settings — %d endpoints, %d role bindings",
        len(endpoints), len(role_mapping),
    )
