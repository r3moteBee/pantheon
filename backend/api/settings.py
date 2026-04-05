"""Settings API — model config, endpoint management, secrets."""
from __future__ import annotations
import asyncio
import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from config import get_settings, get_secret
from models.provider import get_provider, get_embedding_provider, get_prefill_provider, get_vision_provider, get_reranker_provider, reset_provider
from secrets.vault import get_vault

logger = logging.getLogger(__name__)
settings_config = get_settings()
router = APIRouter()


class SettingsUpdate(BaseModel):
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None
    llm_prefill_model: str | None = None
    prefill_base_url: str | None = None
    prefill_api_key: str | None = None
    llm_vision_model: str | None = None
    vision_base_url: str | None = None
    vision_api_key: str | None = None
    embedding_model: str | None = None
    embedding_base_url: str | None = None
    embedding_api_key: str | None = None
    reranker_model: str | None = None
    reranker_base_url: str | None = None
    reranker_api_key: str | None = None
    search_url: str | None = None
    search_api_key: str | None = None
    telegram_bot_token: str | None = None
    telegram_allowed_chat_ids: str | None = None
    memory_recall_enabled: bool | None = None
    personality_weight: str | None = None
    context_focus: str | None = None


class SecretUpdate(BaseModel):
    value: str


# In-memory settings overrides (persisted in vault)
_runtime_overrides: dict[str, str] = {}


def _get_effective_settings() -> dict[str, Any]:
    vault = get_vault()
    return {
        "llm_base_url": vault.get_secret("llm_base_url") or settings_config.llm_base_url,
        # Never return the key value — only whether one is saved
        "llm_api_key_set": bool(get_secret("llm_api_key")),
        "llm_model": vault.get_secret("llm_model") or settings_config.llm_model,
        "llm_prefill_model": vault.get_secret("llm_prefill_model") or settings_config.llm_prefill_model,
        "prefill_base_url": vault.get_secret("prefill_base_url") or settings_config.prefill_base_url,
        "prefill_api_key_set": bool(get_secret("prefill_api_key")),
        "llm_vision_model": vault.get_secret("llm_vision_model") or settings_config.llm_vision_model,
        "vision_base_url": vault.get_secret("vision_base_url") or settings_config.vision_base_url,
        "vision_api_key_set": bool(get_secret("vision_api_key")),
        "embedding_model": vault.get_secret("embedding_model") or settings_config.embedding_model,
        "embedding_base_url": vault.get_secret("embedding_base_url") or settings_config.embedding_base_url,
        "embedding_api_key_set": bool(get_secret("embedding_api_key")),
        "reranker_model": vault.get_secret("reranker_model") or settings_config.reranker_model,
        "reranker_base_url": vault.get_secret("reranker_base_url") or settings_config.reranker_base_url,
        "reranker_api_key_set": bool(get_secret("reranker_api_key")),
        "search_url": vault.get_secret("search_url") or settings_config.search_url,
        "search_api_key_set": bool(get_secret("search_api_key")),
        "chroma_host": settings_config.chroma_host,
        "chroma_port": settings_config.chroma_port,
        "telegram_bot_token_set": bool(get_secret("telegram_bot_token")),
        "telegram_allowed_chat_ids": vault.get_secret("telegram_allowed_chat_ids") or settings_config.telegram_allowed_chat_ids,
        "app_env": settings_config.app_env,
        "memory_recall_enabled": (vault.get_secret("memory_recall_enabled") or "true").lower() == "true",
        "personality_weight": vault.get_secret("personality_weight") or settings_config.personality_weight,
        "context_focus": vault.get_secret("context_focus") or settings_config.context_focus,
    }


def is_memory_recall_enabled() -> bool:
    """Check whether the memory pre-recall augmentation toggle is on."""
    try:
        vault = get_vault()
        return (vault.get_secret("memory_recall_enabled") or "true").lower() == "true"
    except Exception:
        return False


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
    if req.prefill_base_url is not None:
        vault.set_secret("prefill_base_url", req.prefill_base_url)
    if req.prefill_api_key is not None:
        vault.set_secret("prefill_api_key", req.prefill_api_key)
    if req.llm_vision_model is not None:
        vault.set_secret("llm_vision_model", req.llm_vision_model)
    if req.vision_base_url is not None:
        vault.set_secret("vision_base_url", req.vision_base_url)
    if req.vision_api_key is not None:
        vault.set_secret("vision_api_key", req.vision_api_key)
    if req.embedding_model is not None:
        vault.set_secret("embedding_model", req.embedding_model)
    if req.embedding_base_url is not None:
        vault.set_secret("embedding_base_url", req.embedding_base_url)
    if req.embedding_api_key is not None:
        vault.set_secret("embedding_api_key", req.embedding_api_key)
    if req.reranker_model is not None:
        vault.set_secret("reranker_model", req.reranker_model)
    if req.reranker_base_url is not None:
        vault.set_secret("reranker_base_url", req.reranker_base_url)
    if req.reranker_api_key is not None:
        vault.set_secret("reranker_api_key", req.reranker_api_key)
    if req.search_url is not None:
        vault.set_secret("search_url", req.search_url)
    if req.search_api_key is not None:
        vault.set_secret("search_api_key", req.search_api_key)
    if req.telegram_bot_token is not None:
        vault.set_secret("telegram_bot_token", req.telegram_bot_token)
    if req.telegram_allowed_chat_ids is not None:
        vault.set_secret("telegram_allowed_chat_ids", req.telegram_allowed_chat_ids)
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

    # Reset provider so it picks up new settings
    reset_provider()
    logger.info("Settings updated, provider reset")
    return {"status": "updated", "settings": _get_effective_settings()}


@router.get("/settings/models")
async def list_models() -> dict[str, Any]:
    """Fetch available models from the configured LLM provider."""
    vault = get_vault()
    base_url = vault.get_secret("llm_base_url") or settings_config.llm_base_url
    api_key = get_secret("llm_api_key")
    try:
        from models.discovery import fetch_models
        models = await fetch_models(base_url, api_key)
        return {"models": models, "base_url": base_url, "count": len(models)}
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch models: {e}")


@router.get("/settings/test-connection")
async def test_connection() -> dict[str, Any]:
    """Test the connection to all configured providers."""

    async def _test_provider(name: str, prov: ModelProvider | None) -> dict[str, Any]:
        if prov is None:
            return {"name": name, "status": "not_configured"}
        try:
            models = await prov.list_models()
            return {
                "name": name,
                "status": "ok",
                "base_url": prov.base_url,
                "model": prov.model,
                "available_models": len(models),
            }
        except Exception as e:
            return {"name": name, "status": "error", "message": str(e), "base_url": prov.base_url}

    primary = get_provider()
    embedding = get_embedding_provider()
    prefill = get_prefill_provider()
    vision = get_vision_provider()
    reranker = get_reranker_provider()

    results = await asyncio.gather(
        _test_provider("primary", primary),
        _test_provider("embedding", embedding),
        _test_provider("prefill", prefill),
        _test_provider("vision", vision),
        _test_provider("reranker", reranker),
    )

    providers = {r["name"]: r for r in results}
    all_ok = all(r["status"] in ("ok", "not_configured") for r in results)
    return {
        "status": "ok" if all_ok else "partial",
        "providers": providers,
    }


@router.post("/settings/restart-telegram")
async def restart_telegram() -> dict[str, str]:
    """Restart the Telegram bot with current settings (no server restart needed)."""
    try:
        from telegram_bot.bot import restart_telegram_bot
        return await restart_telegram_bot()
    except Exception as e:
        logger.error(f"Telegram restart failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


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
    if key in ("llm_base_url", "llm_api_key", "llm_model",
                "embedding_base_url", "embedding_api_key", "embedding_model",
                "prefill_base_url", "prefill_api_key", "llm_prefill_model",
                "vision_base_url", "vision_api_key", "llm_vision_model"):
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
