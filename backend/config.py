"""Central configuration loaded from environment variables."""
from __future__ import annotations
import os
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    # LLM Provider
    llm_base_url: str = Field(default="https://api.openai.com/v1", env="LLM_BASE_URL")
    llm_api_key: str = Field(default="", env="LLM_API_KEY")
    llm_model: str = Field(default="gpt-4o", env="LLM_MODEL")
    # Optional cheaper/faster model for summarisation, memory consolidation, etc.
    # Falls back to llm_model when not set.
    llm_prefill_model: str = Field(default="", env="LLM_PREFILL_MODEL")
    embedding_model: str = Field(default="text-embedding-3-small", env="EMBEDDING_MODEL")

    # Security
    vault_master_key: str = Field(default="dev-key-change-in-production-32x", env="VAULT_MASTER_KEY")
    secret_key: str = Field(default="dev-secret-key-change-in-production", env="SECRET_KEY")
    # Set AUTH_PASSWORD to require a password on the web interface.
    # Leave empty to disable authentication (not recommended on public servers).
    auth_password: str = Field(default="", env="AUTH_PASSWORD")

    # Telegram
    telegram_bot_token: str = Field(default="", env="TELEGRAM_BOT_TOKEN")
    telegram_allowed_chat_ids: str = Field(default="", env="TELEGRAM_ALLOWED_CHAT_IDS")

    # Application
    app_env: str = Field(default="development", env="APP_ENV")
    log_level: str = Field(default="INFO", env="LOG_LEVEL")
    cors_origins: str = Field(default="http://localhost:3000,http://localhost:5173,http://localhost:80", env="CORS_ORIGINS")

    # Search
    # URL of a search backend (SearXNG, Brave, or any OpenSearch-compatible JSON API).
    # Leave empty to fall back to DuckDuckGo HTML scraping.
    # Examples:
    #   SearXNG:  http://localhost:8080
    #   Brave:    https://api.search.brave.com/res/v1/web
    search_url: str = Field(default="", env="SEARCH_URL")
    # Optional API key — sent as  X-Subscription-Token  (Brave)
    # or  Authorization: Bearer  header depending on backend.
    search_api_key: str = Field(default="", env="SEARCH_API_KEY")

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
