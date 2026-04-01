"""FastAPI application entry point."""
from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import get_settings
from api.chat import router as chat_router, websocket_chat
from api.files import router as files_router
from api.memory import router as memory_router
from api.personality import router as personality_router
from api.projects import router as projects_router
from api.settings import router as settings_router
from api.tasks import router as tasks_router

settings = get_settings()

logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Starting Agent Harness backend...")
    settings.ensure_dirs()

    # Initialize default personality files if missing
    soul_path = settings.personality_dir / "soul.md"
    agent_path = settings.personality_dir / "agent.md"
    default_dir = Path(__file__).parent / "data" / "personality"
    if not soul_path.exists() and (default_dir / "soul.md").exists():
        import shutil
        shutil.copy(default_dir / "soul.md", soul_path)
    if not agent_path.exists() and (default_dir / "agent.md").exists():
        import shutil
        shutil.copy(default_dir / "agent.md", agent_path)

    logger.info("Agent Harness backend ready")
    yield

    logger.info("Agent Harness backend shutdown complete")


app = FastAPI(
    title="Agent Harness",
    description="A production-ready agentic AI framework with 5-tier memory, project isolation, and autonomous tasks.",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── API routers ───────────────────────────────────────────────────────────────
app.include_router(chat_router,        prefix="/api", tags=["chat"])
app.include_router(files_router,       prefix="/api", tags=["files"])
app.include_router(memory_router,      prefix="/api", tags=["memory"])
app.include_router(personality_router, prefix="/api", tags=["personality"])
app.include_router(projects_router,    prefix="/api", tags=["projects"])
app.include_router(settings_router,    prefix="/api", tags=["settings"])
app.include_router(tasks_router,       prefix="/api", tags=["tasks"])

# ── WebSocket — registered directly at /ws/chat (no /api prefix) ─────────────
# The frontend derives the WS URL from window.location.host, so it always
# connects to /ws/chat regardless of the API base URL.
app.websocket("/ws/chat")(websocket_chat)


@app.get("/health")
async def health_check():
    return {"status": "ok", "version": "1.0.0"}


@app.get("/")
async def root():
    return {"message": "Agent Harness API", "docs": "/docs"}
