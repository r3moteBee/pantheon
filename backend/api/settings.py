"""Settings API — app settings, messaging, chunking, secrets, search providers.

LLM endpoints and routing live in api/llm_endpoints.py (/api/llm/*)."""
from __future__ import annotations
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from config import get_settings
from models.provider import reset_provider
from secrets.vault import get_vault
from security_log import sec_log
from utils.runtime_settings import get_active_chunk_settings

logger = logging.getLogger(__name__)
settings_config = get_settings()
router = APIRouter()


class SettingsUpdate(BaseModel):
    telegram_bot_token: str | None = None
    telegram_allowed_chat_ids: str | None = None
    discord_bot_token: str | None = None
    discord_allowed_guild_ids: str | None = None
    discord_command_scope: str | None = None
    slack_bot_token: str | None = None
    slack_app_token: str | None = None
    slack_allowed_channel_ids: str | None = None
    matrix_homeserver_url: str | None = None
    matrix_user_id: str | None = None
    matrix_access_token: str | None = None
    matrix_allowed_room_ids: str | None = None
    mattermost_allowed_channel_ids: str | None = None
    mattermost_url: str | None = None
    mattermost_bot_token: str | None = None
    mattermost_scheme: str | None = None
    mattermost_port: int | None = None
    messaging_default_project: str | None = None
    memory_recall_enabled: bool | None = None
    personality_weight: str | None = None
    context_focus: str | None = None
    file_chunk_size: int | None = None
    file_chunk_overlap: int | None = None
    file_chunk_strategy: str | None = None



class SecretUpdate(BaseModel):
    value: str


# In-memory settings overrides (persisted in vault)
_runtime_overrides: dict[str, str] = {}


def _get_effective_settings() -> dict[str, Any]:
    vault = get_vault()
    size, overlap, strategy = get_active_chunk_settings()
    return {
        "chroma_host": settings_config.chroma_host,
        "chroma_port": settings_config.chroma_port,
        "telegram_bot_token_set": bool(vault.get_secret("telegram_bot_token") or settings_config.telegram_bot_token),
        "telegram_allowed_chat_ids": vault.get_secret("telegram_allowed_chat_ids") or settings_config.telegram_allowed_chat_ids,
        "discord_bot_token_set": bool(vault.get_secret("discord_bot_token") or settings_config.discord_bot_token),
        "discord_allowed_guild_ids": vault.get_secret("discord_allowed_guild_ids") or getattr(settings_config, "discord_allowed_guild_ids", ""),
        "discord_command_scope": vault.get_secret("discord_command_scope") or "guild",
        "slack_bot_token_set": bool(vault.get_secret("slack_bot_token") or settings_config.slack_bot_token),
        "slack_app_token_set": bool(vault.get_secret("slack_app_token") or settings_config.slack_app_token),
        "slack_allowed_channel_ids": vault.get_secret("slack_allowed_channel_ids") or settings_config.slack_allowed_channel_ids,
        "matrix_homeserver_url": vault.get_secret("matrix_homeserver_url") or settings_config.matrix_homeserver_url,
        "matrix_user_id": vault.get_secret("matrix_user_id") or settings_config.matrix_user_id,
        "matrix_access_token_set": bool(vault.get_secret("matrix_access_token") or settings_config.matrix_access_token),
        "matrix_allowed_room_ids": vault.get_secret("matrix_allowed_room_ids") or settings_config.matrix_allowed_room_ids,
        "mattermost_allowed_channel_ids": vault.get_secret("mattermost_allowed_channel_ids") or settings_config.mattermost_allowed_channel_ids,
        "mattermost_url": vault.get_secret("mattermost_url") or settings_config.mattermost_url,
        "mattermost_bot_token_set": bool(vault.get_secret("mattermost_bot_token") or settings_config.mattermost_bot_token),
        "mattermost_scheme": vault.get_secret("mattermost_scheme") or settings_config.mattermost_scheme,
        "mattermost_port": int(vault.get_secret("mattermost_port") or settings_config.mattermost_port),
        "messaging_default_project": vault.get_secret("messaging_default_project") or "default",
        "app_env": settings_config.app_env,
        "memory_recall_enabled": (vault.get_secret("memory_recall_enabled") or "true").lower() == "true",
        "personality_weight": vault.get_secret("personality_weight") or settings_config.personality_weight,
        "context_focus": vault.get_secret("context_focus") or settings_config.context_focus,
        "file_chunk_size": size,
        "file_chunk_overlap": overlap,
        "file_chunk_strategy": strategy,
    }


@router.get("/settings")
async def get_settings_endpoint() -> dict[str, Any]:
    """Get current configuration (no secrets)."""
    return _get_effective_settings()


@router.put("/settings")
async def update_settings(req: SettingsUpdate) -> dict[str, Any]:
    """Update configuration settings."""
    vault = get_vault()
    if req.telegram_bot_token is not None:
        vault.set_secret("telegram_bot_token", req.telegram_bot_token)
    if req.telegram_allowed_chat_ids is not None:
        vault.set_secret("telegram_allowed_chat_ids", req.telegram_allowed_chat_ids)
    if req.discord_bot_token is not None:
        vault.set_secret("discord_bot_token", req.discord_bot_token)
    if req.discord_allowed_guild_ids is not None:
        vault.set_secret("discord_allowed_guild_ids", req.discord_allowed_guild_ids)
    if req.discord_command_scope is not None:
        val = req.discord_command_scope.lower().strip()
        if val in ("guild", "global"):
            vault.set_secret("discord_command_scope", val)
    if req.messaging_default_project is not None:
        vault.set_secret("messaging_default_project", req.messaging_default_project)
    if req.slack_bot_token is not None:
        vault.set_secret("slack_bot_token", req.slack_bot_token)
    if req.slack_app_token is not None:
        vault.set_secret("slack_app_token", req.slack_app_token)
    if req.slack_allowed_channel_ids is not None:
        vault.set_secret("slack_allowed_channel_ids", req.slack_allowed_channel_ids)
    if req.matrix_allowed_room_ids is not None:
        vault.set_secret("matrix_allowed_room_ids", req.matrix_allowed_room_ids)
    if req.mattermost_allowed_channel_ids is not None:
        vault.set_secret("mattermost_allowed_channel_ids", req.mattermost_allowed_channel_ids)
    if req.matrix_homeserver_url is not None:
        vault.set_secret("matrix_homeserver_url", req.matrix_homeserver_url)
    if req.matrix_user_id is not None:
        vault.set_secret("matrix_user_id", req.matrix_user_id)
    if req.matrix_access_token is not None:
        vault.set_secret("matrix_access_token", req.matrix_access_token)
    if req.mattermost_url is not None:
        vault.set_secret("mattermost_url", req.mattermost_url)
    if req.mattermost_bot_token is not None:
        vault.set_secret("mattermost_bot_token", req.mattermost_bot_token)
    if req.mattermost_scheme is not None:
        vault.set_secret("mattermost_scheme", req.mattermost_scheme)
    if req.mattermost_port is not None:
        vault.set_secret("mattermost_port", str(req.mattermost_port))
    if req.memory_recall_enabled is not None:
        vault.set_secret("memory_recall_enabled", str(req.memory_recall_enabled).lower())
    if req.personality_weight is not None:
        val = req.personality_weight.lower().strip()
        if val in ("minimal", "balanced", "strong"):
            vault.set_secret("personality_weight", val)
    if req.context_focus is not None:
        val = req.context_focus.lower().strip()
        if val in ("broad", "balanced", "focused"):
            vault.set_secret("context_focus", val)
    if req.file_chunk_size is not None:
        vault.set_secret("file_chunk_size", str(req.file_chunk_size))
    if req.file_chunk_overlap is not None:
        vault.set_secret("file_chunk_overlap", str(req.file_chunk_overlap))
    if req.file_chunk_strategy is not None:
        val = req.file_chunk_strategy.lower().strip()
        if val in ("headings", "paragraphs", "fixed"):
            vault.set_secret("file_chunk_strategy", val)


    # Log which settings were changed
    changed = [k for k, v in req.model_dump(exclude_none=True).items() if v is not None]
    if changed:
        sec_log.settings_updated(changed_keys=changed)

    logger.info("Settings updated")
    return {"status": "updated", "settings": _get_effective_settings()}


# ── Security log ─────────────────────────────────────────────────────────────

@router.get("/settings/security-log")
async def get_security_log(
    limit: int = Query(default=200, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """Read the security audit log (most recent entries first)."""
    import json as _json

    log_file = settings_config.data_dir / "logs" / "security.log"
    if not log_file.exists():
        return {"entries": [], "total": 0}

    lines = log_file.read_text(encoding="utf-8").strip().splitlines()
    total = len(lines)

    # Most recent first
    lines.reverse()
    page = lines[offset:offset + limit]

    entries = []
    for line in page:
        try:
            entries.append(_json.loads(line))
        except Exception:
            entries.append({"raw": line})

    return {"entries": entries, "total": total}


@router.delete("/settings/security-log")
async def clear_security_log() -> dict[str, str]:
    """Clear the security audit log."""
    log_file = settings_config.data_dir / "logs" / "security.log"
    if log_file.exists():
        log_file.write_text("", encoding="utf-8")
    return {"status": "cleared"}


# ── Secrets ──────────────────────────────────────────────────────────────────

@router.get("/secrets")
async def list_secrets() -> dict[str, Any]:
    """List secret keys (never values)."""
    vault = get_vault()
    keys = vault.list_secrets()
    return {"keys": keys, "count": len(keys)}


@router.put("/secrets/{key}")
async def set_secret(key: str, req: SecretUpdate) -> dict[str, str]:
    """Set or update a secret value."""
    if not key or not key.replace("_", "").replace("-", "").isalnum():
        raise HTTPException(status_code=400, detail="Invalid secret key format")
    vault = get_vault()
    vault.set_secret(key, req.value)
    sec_log.secret_set(key=key)
    # An endpoint's API key edited on the Secrets page: rebuild providers.
    if key.startswith("llm_endpoint_key__"):
        reset_provider()
    return {"status": "set", "key": key}


@router.delete("/secrets/{key}")
async def delete_secret(key: str) -> dict[str, str]:
    """Delete a secret."""
    vault = get_vault()
    deleted = vault.delete_secret(key)
    if not deleted:
        raise HTTPException(status_code=404, detail="Secret not found")
    sec_log.secret_deleted(key=key)
    return {"status": "deleted", "key": key}


# ── Web search providers ─────────────────────────────────────────────────────

class SearchProviderConfig(BaseModel):
    name: str
    type: str  # brave | searxng | ddg | generic
    url: str = ""
    api_key_vault_key: str = ""
    api_key: str | None = None  # if set, write into vault under api_key_vault_key
    daily_limit: int = 0
    monthly_limit: int = 0
    rps: float = 0
    enabled: bool = True


class SearchProvidersUpdate(BaseModel):
    providers: list[SearchProviderConfig]


@router.get("/settings/search/providers")
async def get_search_providers() -> dict[str, Any]:
    from agent.search_providers import get_search_manager
    mgr = get_search_manager()
    return {
        "providers": mgr.get_providers(),
        "usage": mgr.get_usage(),
    }


@router.put("/settings/search/providers")
async def set_search_providers(req: SearchProvidersUpdate) -> dict[str, Any]:
    from agent.search_providers import default_search_key_name, get_search_manager, is_search_key_name
    vault = get_vault()
    cleaned: list[dict[str, Any]] = []
    for p in req.providers:
        if p.api_key and not p.api_key_vault_key:
            p.api_key_vault_key = default_search_key_name(p.name)
        if p.api_key_vault_key and not is_search_key_name(p.api_key_vault_key):
            raise HTTPException(
                status_code=400,
                detail=(f"Provider {p.name!r}: vault key {p.api_key_vault_key!r} isn't a search key. "
                        "Use a name ending in _api_key (e.g. brave_api_key) or leave it empty."),
            )
        if p.api_key and p.api_key_vault_key:
            vault.set_secret(p.api_key_vault_key, p.api_key)
        d = p.model_dump()
        d.pop("api_key", None)  # never persist plaintext key in providers config
        cleaned.append(d)
    mgr = get_search_manager()
    mgr.set_providers(cleaned)
    sec_log.settings_updated(changed_keys=["search_providers"])
    return {"providers": mgr.get_providers(), "usage": mgr.get_usage()}


@router.post("/settings/search/providers/{name}/reset")
async def reset_search_provider(name: str, period: str = "daily") -> dict[str, Any]:
    from agent.search_providers import get_search_manager
    mgr = get_search_manager()
    mgr.reset_provider_usage(name, period=period)
    return {"status": "reset", "provider": name, "period": period, "usage": mgr.get_usage()}


@router.post("/settings/search/test")
async def test_search_chain(query: str = Query("test query")) -> dict[str, Any]:
    """Run a search through the provider chain to verify it works."""
    from agent.search_providers import get_search_manager
    mgr = get_search_manager()
    result = await mgr.search(query)
    return {"query": query, "result": result, "usage": mgr.get_usage()}
