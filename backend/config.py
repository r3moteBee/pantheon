"""Central configuration loaded from environment variables."""
from __future__ import annotations
import os
from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # LLM provider — bootstrap fallback for the agent class (see embedding_model)
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o"
    # Bootstrap fallbacks: used only when no route is configured for the
    # class in Settings → LLMs (/api/llm/*). Routes are the real config.
    embedding_model: str = "text-embedding-3-small"
    # Separate embedding endpoint for that fallback (the installer asks for it)
    embedding_base_url: str = ""
    embedding_api_key: str = ""

    # Security
    vault_master_key: str = "dev-key-change-in-production-32x"
    secret_key: str = "dev-secret-key-change-in-production"
    # Set AUTH_PASSWORD to require a password on the web interface.
    # Leave empty to disable authentication (not recommended on public servers).
    auth_password: str = ""
    # Login sessions expire after this many days.
    auth_session_days: int = 30
    # Extra public hostnames (comma-separated) this server may be reached by
    # when AUTH_PASSWORD is empty, e.g. a Caddy domain. Guards DNS rebinding.
    allowed_hosts: str = ""

    # Telegram
    telegram_bot_token: str = ""
    telegram_allowed_chat_ids: str = ""

    # Discord
    discord_bot_token: str = ""
    discord_allowed_guild_ids: str = ""

    # Slack
    slack_bot_token: str = ""
    slack_app_token: str = ""
    slack_allowed_channel_ids: str = ""

    # Matrix
    matrix_homeserver_url: str = ""
    matrix_user_id: str = ""
    matrix_access_token: str = ""
    matrix_allowed_room_ids: str = ""

    # Mattermost
    mattermost_url: str = ""
    mattermost_bot_token: str = ""
    mattermost_scheme: str = "https"
    mattermost_port: int = 443
    mattermost_allowed_channel_ids: str = ""

    # Application
    app_env: str = "development"
    log_level: str = "INFO"
    cors_origins: str = "http://localhost:8000,http://localhost:5173,http://localhost:80"

    # Host-execution tools (run_command, code_execute, git_*) run shell on the
    # host. Content the agent reads (web pages, transcripts, recalled memory)
    # can carry prompt-injected instructions, so these are only offered where
    # a human is driving:
    #   interactive — web UI chat + coding_task jobs (default)
    #   always      — also autonomous/scheduled jobs and messaging bots
    #   never       — disabled everywhere
    agent_host_exec: str = "interactive"
    # Ask the AGENT-class model to reason before acting (sends chat_template_kwargs
    # {"enable_thinking": true}; only for backends that accept it, e.g. llama.cpp /
    # vLLM serving Qwen-family models). Measured on a 9B model: tool use for
    # current-fact questions 8/14 -> 10/10. Off by default: other OpenAI-compatible
    # providers may reject the extra field.
    agent_thinking: bool = False
    # For time-sensitive questions (agent/freshness.py) the agent's first round
    # must call a tool (tool_choice "required"), so it can't answer "who is the
    # president" from its training data. Needs a server that honours
    # tool_choice=required (OpenAI, llama.cpp, vLLM).
    agent_force_search: bool = True
    # Let agent tools / source adapters fetch private, loopback and
    # link-local addresses (intranet ingest). Off by default: SSRF guard.
    allow_private_fetch: bool = False
    # Serve non-local clients even while VAULT_MASTER_KEY / SECRET_KEY /
    # AUTH_PASSWORD are public defaults or .env.example placeholders.
    allow_insecure_defaults: bool = False

    # Search
    # web_search appends endoflife.date's release table when a query asks for
    # a product's version (agent/release_facts.py); sends the product name there.
    search_release_facts: bool = True
    # URL of a search backend (SearXNG, Brave, or any OpenSearch-compatible JSON API).
    # Leave empty to fall back to DuckDuckGo HTML scraping.
    # Examples:
    #   SearXNG:  http://localhost:8080
    #   Brave:    https://api.search.brave.com/res/v1/web
    search_url: str = ""
    # Optional API key — sent as  X-Subscription-Token  (Brave)
    # or  Authorization: Bearer  header depending on backend.
    search_api_key: str = ""

    # ChromaDB
    # Empty = embedded ChromaDB under data/chroma; set a host for a Chroma server
    chroma_host: str = ""
    chroma_port: int = 8001

    # Active Memory settings
    # Auto-extraction: run LLM extraction after this many messages (0 = disabled, only on consolidation)
    extraction_interval: int = 0

    # Conversation behaviour tuning
    # Personality presence: how prominently soul.md identity is injected.
    #   "minimal"  = tone only, never reference identity in analytical content
    #   "balanced" = light personality, focus on the task (default)
    #   "strong"   = freely express identity and values in responses
    personality_weight: str = "balanced"
    # Context focus: how aggressively recent messages are favoured over older context.
    #   "broad"    = full history weighted equally (good for brainstorming)
    #   "balanced" = moderate recency boost (default)
    #   "focused"  = strong recency boost, older turns compressed (good for debugging)
    context_focus: str = "balanced"
    # Pre-recall: total seconds a chat turn waits for memory recall (search +
    # rerank) before answering without memory context.
    pre_recall_timeout_seconds: float = 4.0
    # Rerank: seconds to wait for the reranker before keeping the original
    # order. Must stay well under pre_recall_timeout_seconds — a reranker that
    # is still loading (15-20 s cold) would otherwise blow the pre-recall budget
    # and the turn would lose ALL recalled memories, not just the ordering.
    # memory.manager caps it at pre_recall_timeout_seconds - 1.5.
    rerank_timeout_seconds: float = 2.5
    # Pre-recall: drop recalled items the reranker scores below this
    # relevance (0-1; raw reranker logits are mapped through a sigmoid).
    # Measured with bge-reranker-v2-m3: relevant memories 0.14-0.997, unrelated
    # ones < 0.05, small talk matches nothing. 0 disables. Only applies when the
    # rerank succeeded, and not to the agent's own recall tool.
    recall_min_relevance: float = 0.05
    # Conversation history sent per turn, in tokens: >0 fixed, 0 auto (a
    # quarter of the agent model's context window, 2K-24K), <0 no limit.
    # Older turns are dropped in blocks (agent/history.py) and stay
    # reachable through recall.
    history_token_budget: int = 0
    # Turns of the CURRENT conversation dropped by that budget come back
    # through recall when their embedding similarity to the message is at
    # least this (the reranker scores "did I mention X earlier?" far too low
    # to pass RECALL_MIN_RELEVANCE). Measured: relevant 0.49-0.75, unrelated
    # <= 0.40. 0 disables.
    recall_session_min_similarity: float = 0.45
    # File indexing: auto-index uploaded files (true/false)
    auto_index_uploads: bool = True
    # File indexing: chunk size in tokens
    file_chunk_size: int = 500
    # File indexing: chunk overlap in tokens
    file_chunk_overlap: int = 50
    # File indexing: chunking strategy (headings, paragraphs, fixed)
    file_chunk_strategy: str = "headings"


    # Paths
    data_dir: Path = Path("/app/data")

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

    # Env var = field name upper-cased (case-insensitive), e.g. LLM_BASE_URL.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache()
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
