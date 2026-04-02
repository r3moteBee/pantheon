"""FastAPI application entry point."""
from __future__ import annotations
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import get_settings
from api.auth import router as auth_router, compute_token
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

# Routes that are always public (no auth token required)
_PUBLIC_PATHS = {
    "/",
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/auth/login",
    "/api/auth/config",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Starting Agent Harness backend...")
    settings.ensure_dirs()

    # Initialize default personality files if missing or empty
    import shutil
    soul_path = settings.personality_dir / "soul.md"
    agent_path = settings.personality_dir / "agent.md"
    default_dir = Path(__file__).parent / "data" / "personality"

    def _needs_init(dest: Path) -> bool:
        return not dest.exists() or not dest.read_text(encoding="utf-8").strip()

    if _needs_init(soul_path) and (default_dir / "soul.md").exists():
        shutil.copy(default_dir / "soul.md", soul_path)
        logger.info("Initialised soul.md from bundled template → %s", soul_path)
    if _needs_init(agent_path) and (default_dir / "agent.md").exists():
        shutil.copy(default_dir / "agent.md", agent_path)
        logger.info("Initialised agent.md from bundled template → %s", agent_path)

    # Start the task scheduler
    from tasks.scheduler import get_scheduler
    scheduler = get_scheduler()
    if not scheduler.running:
        scheduler.start()
        logger.info("Task scheduler started")

    logger.info("Agent Harness backend ready")
    import asyncio as _asyncio
    async def _warmup():
        try:
            from memory.semantic import SemanticMemory as _SM
            _sem = _SM(project_id="default")
            await _asyncio.to_thread(_sem._get_collection)
            logger.info("ChromaDB warmed up successfully")
        except Exception as _e:
            logger.warning("ChromaDB warmup skipped: %s", _e)
    _asyncio.create_task(_warmup())
    yield

    # Stop the task scheduler cleanly
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Task scheduler stopped")

    logger.info("Agent Harness backend shutdown complete")


app = FastAPI(
    title="Agent Harness",
    description="A production-ready agentic AI framework with 5-tier memory, project isolation, and autonomous tasks.",
    version="2026-04-02-04",
    lifespan=lifespan,
)

# ── CORS (must be added before auth middleware) ───────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth middleware ───────────────────────────────────────────────────────────
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    """Gate all non-public routes behind AUTH_PASSWORD when it is set."""
    cfg = get_settings()

    # Auth disabled — pass everything through
    if not cfg.auth_password:
        return await call_next(request)

    # Always allow public paths
    if request.url.path in _PUBLIC_PATHS:
        return await call_next(request)

    # WebSocket connections carry the token as a query parameter because
    # the WebSocket API does not support arbitrary headers.
    if request.url.path.startswith("/ws/"):
        token = request.query_params.get("token", "")
    else:
        auth_header = request.headers.get("Authorization", "")
        token = auth_header.removeprefix("Bearer ").strip()

    expected = compute_token(cfg.auth_password, cfg.secret_key)
    try:
        valid = hmac.compare_digest(token, expected)
    except (TypeError, ValueError):
        valid = False

    if not valid:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    return await call_next(request)


# ── API routers ───────────────────────────────────────────────────────────────
app.include_router(auth_router,        prefix="/api", tags=["auth"])
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
    return {"status": "ok", "version": "2026-04-02-04"}


@app.get("/")
async def root():
    return {"message": "Agent Harness API", "docs": "/docs"}
