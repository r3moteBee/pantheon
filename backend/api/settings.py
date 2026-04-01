"""Settings API — model config, endpoint management, secrets."""
from __future__ import annotations
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import get_settings
from models.provider import get_provider, reset_provider
from secrets.vault import get_vault

logger = logging.getLogger(__name__)
settings_config = get_settings()
router = APIRouter()


class SettingsUpdate(BaseModel):
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_prefill_model: str | None = None
    embedding_model: str | None = None
    search_url: str | None = None
    search_api_key: str | None = None


class SecretUpdate(BaseModel):
    value: str


# In-memory settings overrides (persisted in vault)
_runtime_overrides: dict[str, str] = {}


def _get_effective_settings() -> dict[str, Any]:
    vault = get_vault()
    return {
        "llm_base_url": vault.get_secret("llm_base_url") or settings_config.llm_base_url,
        # Never return the key value — only whether one is saved
        "llm_api_key_set": bool(vault.get_secret("llm_api_key") or settings_config.llm_api_key),
        "llm_model": vault.get_secret("llm_model") or settings_config.llm_model,
        "llm_prefill_model": vault.get_secret("llm_prefill_model") or settings_config.llm_prefill_model,
        "embedding_model": vault.get_secret("embedding_model") or settings_config.embedding_model,
        "search_url": vault.get_secret("search_url") or settings_config.search_url,
        "search_api_key_set": bool(vault.get_secret("search_api_key") or settings_config.search_api_key),
        "chroma_host": settings_config.chroma_host,
        "chroma_port": settings_config.chroma_port,
        "telegram_configured": bool(settings_config.telegram_bot_token),
        "app_env": settings_config.app_env,
    }


@router.get("/settings")
async def get_settings_endpoint() -> dict[str, Any]:
    """Get current configuration (no secrets)."""
    return _get_effective_settings()


@router.put("/settings")
async def update_settings(req: SettingsUpdate) -> dict[str, Any]:
    """Update configuration settings."""
    vault = get_vault()
    if req.llm_base_url is not None:
        vault.set_secret("llm_base_url", req.llm_base_url)
    if req.llm_api_key is not None:
        vault.set_secret("llm_api_key", req.llm_api_key)
    if req.llm_model is not None:
        vault.set_secret("llm_model", req.llm_model)
    if req.llm_prefill_model is not None:
        vault.set_secret("llm_prefill_model", req.llm_prefill_model)
    if req.embedding_model is not None:
        vault.set_secret("embedding_model", req.embedding_model)
    if req.search_url is not None:
        vault.set_secret("search_url", req.search_url)
    if req.search_api_key is not None:
        vault.set_secret("search_api_key", req.search_api_key)

    # Reset provider so it picks up new settings
    reset_provider()
    logger.info("Settings updated, provider reset")
    return {"status": "updated", "settings": _get_effective_settings()}


@router.get("/settings/models")
async def list_models() -> dict[str, Any]:
    """Fetch available models from the configured LLM provider."""
    vault = get_vault()
    base_url = vault.get_secret("llm_base_url") or settings_config.llm_base_url
    api_key = vault.get_secret("llm_api_key") or settings_config.llm_api_key
    try:
        from models.discovery import fetch_models
        models = await fetch_models(base_url, api_key)
        return {"models": models, "base_url": base_url, "count": len(models)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch models: {e}")


@router.get("/settings/test-connection")
async def test_connection() -> dict[str, Any]:
    """Test the connection to the LLM provider."""
    provider = get_provider()
    try:
        models = await provider.list_models()
        return {
            "status": "ok",
            "base_url": provider.base_url,
            "model": provider.model,
            "available_models": len(models),
        }
    except Exception as e:
        return {"status": "error", "message": str(e), "base_url": provider.base_url}


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
    # Reset provider if it's an LLM-related secret
    if key in ("llm_base_url", "llm_api_key", "llm_model"):
        reset_provider()
    return {"status": "set", "key": key}


@router.delete("/secrets/{key}")
async def delete_secret(key: str) -> dict[str, str]:
    """Delete a secret."""
    vault = get_vault()
    deleted = vault.delete_secret(key)
    if not deleted:
        raise HTTPException(status_code=404, detail="Secret not found")
    return {"status": "deleted", "key": key}
