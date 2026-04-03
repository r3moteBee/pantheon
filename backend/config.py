"""Central configuration loaded from environment variables.

Sensitive values (API keys, tokens, passwords) are resolved from the
encrypted vault first, with .env fields serving only as a migration
fallback.  Use ``get_secret()`` to read any sensitive value — never
access ``settings.<secret_field>`` directly in application code.
"""
from __future__ import annotations
import logging
import os
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field

_cfg_logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    # ── LLM Provider (non-sensitive) ─────────────────────────────────────
    llm_base_url: str = Field(default="https://api.openai.com/v1", env="LLM_BASE_URL")
    llm_model: str = Field(default="gpt-4o", env="LLM_MODEL")
    llm_prefill_model: str = Field(default="", env="LLM_PREFILL_MODEL")
    prefill_base_url: str = Field(default="", env="PREFILL_BASE_URL")
    embedding_model: str = Field(default="text-embedding-3-small", env="EMBEDDING_MODEL")
    embedding_base_url: str = Field(default="", env="EMBEDDING_BASE_URL")
    reranker_model: str = Field(default="", env="RERANKER_MODEL")
    reranker_base_url: str = Field(default="", env="RERANKER_BASE_URL")

    # ── Sensitive fields (migration fallback only — use get_secret()) ────
    # These are still read from .env so that ``secrets.setup --migrate``
    # can pick them up.  Application code must use get_secret() instead.
    llm_api_key: str = Field(default="", env="LLM_API_KEY")
    prefill_api_key: str = Field(default="", env="PREFILL_API_KEY")
    embedding_api_key: str = Field(default="", env="EMBEDDING_API_KEY")
    reranker_api_key: str = Field(default="", env="RERANKER_API_KEY")
    search_api_key: str = Field(default="", env="SEARCH_API_KEY")
    telegram_bot_token: str = Field(default="", env="TELEGRAM_BOT_TOKEN")
    secret_key: str = Field(default="dev-secret-key-change-in-production", env="SECRET_KEY")
    auth_password: str = Field(default="", env="AUTH_PASSWORD")

    # Vault master key — resolved separately by secrets.vault._resolve_master_key()
    vault_master_key: str = Field(default="dev-key-change-in-production-32x", env="VAULT_MASTER_KEY")

    # ── Non-sensitive config ─────────────────────────────────────────────
    telegram_allowed_chat_ids: str = Field(default="", env="TELEGRAM_ALLOWED_CHAT_IDS")
    app_env: str = Field(default="development", env="APP_ENV")
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    cors_origins: str = Field(default="http://localhost:3000,http://localhost:5173,http://localhost:80", env="CORS_ORIGINS")

    # Search
    search_url: str = Field(default="", env="SEARCH_URL")

    # ChromaDB
    chroma_host: str = Field(default="localhost", env="CHROMA_HOST")
    chroma_port: int = Field(default=8001, env="CHROMA_PORT")

    # Paths
    data_dir: Path = Field(default=Path("/app/data"), env="DATA_DIR")

    @property
    def db_dir(self) -> Path:
        return self.data_dir / "db"

    @property
    def personality_dir(self) -> Path:
        return self.data_dir / "personality"

    @property
    def projects_dir(self) -> Path:
        return self.data_dir / "projects"

    @property
    def workspace_dir(self) -> Path:
        return self.data_dir / "workspace"

    @property
    def episodic_db_path(self) -> str:
        return str(self.db_dir / "episodic.db")

    @property
    def graph_db_path(self) -> str:
        return str(self.db_dir / "graph.db")

    @property
    def vault_db_path(self) -> str:
        return str(self.db_dir / "vault.db")

    @property
    def scheduler_db_path(self) -> str:
        return str(self.db_dir / "scheduler.db")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def telegram_allowed_ids(self) -> list[int]:
        if not self.telegram_allowed_chat_ids:
            return []
        return [int(x.strip()) for x in self.telegram_allowed_chat_ids.split(",") if x.strip()]

    def ensure_dirs(self) -> None:
        """Create all required data directories."""
        for d in [self.db_dir, self.personality_dir, self.projects_dir, self.workspace_dir]:
            d.mkdir(parents=True, exist_ok=True)

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings


# ── Vault-first secret resolution ───────────────────────────────────────────
# Mapping of secret name → Settings field used as migration fallback.
# Keys that don't have a corresponding .env field use "" (no fallback).
_SECRET_ENV_FALLBACKS: dict[str, str] = {
    "llm_api_key": "llm_api_key",
    "secret_key": "secret_key",
    "auth_password": "auth_password",
    "embedding_api_key": "embedding_api_key",
    "prefill_api_key": "prefill_api_key",
    "reranker_api_key": "reranker_api_key",
    "search_api_key": "search_api_key",
    "telegram_bot_token": "telegram_bot_token",
}

# Values that should be treated as "not set" when found in .env
_INSECURE_DEFAULTS = {
    "secret_key": "dev-secret-key-change-in-production",
    "vault_master_key": "dev-key-change-in-production-32x",
}


def get_secret(key: str, default: str = "") -> str:
    """Resolve a sensitive value: vault → .env fallback → default.

    All application code should call this instead of reading
    ``settings.<secret_field>`` directly.  The .env fallback exists
    only to support the migration period; once secrets are in the
    vault the .env values are ignored.
    """
    # 1. Vault (authoritative)
    try:
        from secrets.vault import get_vault
        vault = get_vault()
        val = vault.get_secret(key)
        if val:
            return val
    except Exception:
        pass

    # 2. .env migration fallback
    field_name = _SECRET_ENV_FALLBACKS.get(key, "")
    if field_name:
        settings = get_settings()
        env_val = getattr(settings, field_name, "") or ""
        insecure = _INSECURE_DEFAULTS.get(key, "")
        if env_val and env_val != insecure:
            _cfg_logger.debug(
                "Secret '%s' resolved from .env (not yet migrated to vault)", key
            )
            return env_val

    return default
