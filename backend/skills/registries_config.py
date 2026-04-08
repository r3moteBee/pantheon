"""Persisted configuration for skill registry hubs.

Stores admin-configured skill registries in `data_dir/skill_registries.json`
and keeps the in-memory adapter table (importer._ADAPTERS) in sync. Bearer
tokens are stored in the secrets vault under keys `skill_registry:<id>`,
never in the JSON file.

Schema (skill_registries.json):
    {
      "registries": [
        {
          "id": "acme",
          "url": "https://skills.acme.internal",
          "display_name": "Acme Internal Skills",
          "auth": {"type": "bearer", "token_ref": "vault:skill_registry:acme"}
        }
      ]
    }
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from config import get_settings
from skills.importer import register_skill_registry, unregister_skill_registry

logger = logging.getLogger(__name__)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_RESERVED = {"skill_md", "github", "local"}


def _config_path() -> Path:
    return get_settings().data_dir / "skill_registries.json"


def _vault_key(registry_id: str) -> str:
    return f"skill_registry:{registry_id}"


def _read_file() -> list[dict[str, Any]]:
    p = _config_path()
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text("utf-8"))
        return list(data.get("registries", []))
    except Exception as e:
        logger.error("Failed to read %s: %s", p, e)
        return []


def _write_file(registries: list[dict[str, Any]]) -> None:
    p = _config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"registries": registries}, indent=2), "utf-8")


def _redact(entry: dict[str, Any]) -> dict[str, Any]:
    """Return entry safe for API responses (no raw tokens)."""
    out = {
        "id": entry["id"],
        "url": entry["url"],
        "display_name": entry.get("display_name"),
        "auth": {"type": (entry.get("auth") or {}).get("type", "none")},
    }
    if out["auth"]["type"] == "bearer":
        out["auth"]["token_set"] = bool((entry.get("auth") or {}).get("token_ref"))
    return out


def _resolve_token(entry: dict[str, Any]) -> str | None:
    auth = entry.get("auth") or {}
    if auth.get("type") != "bearer":
        return None
    ref = auth.get("token_ref", "")
    if ref.startswith("vault:"):
        from secrets.vault import get_vault
        return get_vault().get_secret(ref[len("vault:"):])
    return None


def _apply(entry: dict[str, Any]) -> None:
    register_skill_registry(
        entry["id"],
        entry["url"],
        display_name=entry.get("display_name"),
        auth_token=_resolve_token(entry),
    )


# ── Public API ──────────────────────────────────────────────────────────────

def load_skill_registries_from_disk() -> None:
    """Read the JSON file and register every entry. Call at startup."""
    for entry in _read_file():
        try:
            _apply(entry)
        except Exception as e:
            logger.error("Failed to register skill registry %r: %s",
                         entry.get("id"), e)


def list_registries() -> list[dict[str, Any]]:
    """Return built-in hubs (read-only) followed by user-configured registries."""
    from skills.importer import list_hubs
    out: list[dict[str, Any]] = []
    for hub in list_hubs():
        if hub.get("builtin"):
            out.append({
                "id": hub["id"],
                "url": None,
                "display_name": hub["name"],
                "auth": {"type": "none"},
                "builtin": True,
                "searchable": hub.get("searchable", False),
            })
    for e in _read_file():
        out.append({**_redact(e), "builtin": False, "searchable": True})
    return out


def add_registry(
    registry_id: str,
    url: str,
    *,
    display_name: str | None = None,
    auth_type: str = "none",
    bearer_token: str | None = None,
) -> dict[str, Any]:
    if not _ID_RE.match(registry_id or ""):
        raise ValueError(
            "id must be 2-64 chars, lowercase alphanumeric, '-' or '_'"
        )
    if registry_id in _RESERVED:
        raise ValueError(f"'{registry_id}' is a reserved built-in hub id")
    if not (url.startswith("https://") or url.startswith("http://localhost")
            or url.startswith("http://127.0.0.1")):
        raise ValueError("url must be https:// (http allowed only for localhost)")

    registries = _read_file()
    if any(e["id"] == registry_id for e in registries):
        raise ValueError(f"registry id '{registry_id}' already exists")

    entry: dict[str, Any] = {
        "id": registry_id,
        "url": url.rstrip("/"),
        "display_name": display_name,
        "auth": {"type": auth_type},
    }
    if auth_type == "bearer":
        if not bearer_token:
            raise ValueError("bearer_token is required when auth_type='bearer'")
        from secrets.vault import get_vault
        get_vault().set_secret(_vault_key(registry_id), bearer_token)
        entry["auth"]["token_ref"] = f"vault:{_vault_key(registry_id)}"

    registries.append(entry)
    _write_file(registries)
    _apply(entry)
    logger.info("Added skill registry '%s' → %s", registry_id, url)
    return _redact(entry)


def update_registry(
    registry_id: str,
    *,
    url: str | None = None,
    display_name: str | None = None,
    auth_type: str | None = None,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    registries = _read_file()
    for i, entry in enumerate(registries):
        if entry["id"] != registry_id:
            continue
        if url is not None:
            entry["url"] = url.rstrip("/")
        if display_name is not None:
            entry["display_name"] = display_name
        if auth_type is not None:
            entry["auth"] = {"type": auth_type}
            if auth_type == "bearer":
                if bearer_token:
                    from secrets.vault import get_vault
                    get_vault().set_secret(_vault_key(registry_id), bearer_token)
                entry["auth"]["token_ref"] = f"vault:{_vault_key(registry_id)}"
            else:
                from secrets.vault import get_vault
                try:
                    get_vault().delete_secret(_vault_key(registry_id))
                except Exception:
                    pass
        elif bearer_token and (entry.get("auth") or {}).get("type") == "bearer":
            from secrets.vault import get_vault
            get_vault().set_secret(_vault_key(registry_id), bearer_token)

        registries[i] = entry
        _write_file(registries)
        unregister_skill_registry(registry_id)
        _apply(entry)
        return _redact(entry)
    raise KeyError(f"Unknown registry: {registry_id}")


def remove_registry(registry_id: str) -> None:
    registries = _read_file()
    new = [e for e in registries if e["id"] != registry_id]
    if len(new) == len(registries):
        raise KeyError(f"Unknown registry: {registry_id}")
    _write_file(new)
    unregister_skill_registry(registry_id)
    try:
        from secrets.vault import get_vault
        get_vault().delete_secret(_vault_key(registry_id))
    except Exception:
        pass
    logger.info("Removed skill registry '%s'", registry_id)
