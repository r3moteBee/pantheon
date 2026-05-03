"""FastAPI application entry point."""
from __future__ import annotations
import hmac
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import get_settings
from api.auth import router as auth_router, compute_token
from api.chat import router as chat_router, websocket_chat
from api.files import router as files_router
from api.memory import router as memory_router
from api.personality import router as personality_router
from api.projects import router as projects_router
from api.settings import router as settings_router
from api.mcp import router as mcp_router
from api.skills import router as skills_router
from api.tasks import router as tasks_router
from api.personas import router as personas_router
from api.system import router as system_router
from api.sources import router as sources_router
from api.connections import router as connections_router
from api.artifacts import router as artifacts_router
from api.conversations import router as conversations_router
from api.jobs import router as jobs_router

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
    "/api/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/auth/login",
    "/api/auth/config",
}

# Resolve frontend dist directory (used by auth middleware and SPA serving)
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Starting Pantheon backend...")
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

    # Start the Telegram bot (if configured)
    from telegram_bot.bot import start_telegram_bot, stop_telegram_bot
    await start_telegram_bot()

    # Initialize the skill registry
    from skills.registry import get_skill_registry
    skill_reg = get_skill_registry()
    logger.info("Skill registry loaded: %d skills", len(skill_reg.list_all()))

    # Load admin-configured skill registry hubs
    try:
        from skills.registries_config import load_skill_registries_from_disk
        load_skill_registries_from_disk()
    except Exception as e:
        logger.error("Failed to load skill registries: %s", e)

    # Initialize MCP connections
    from mcp_client.manager import get_mcp_manager
    mcp_mgr = get_mcp_manager()
    await mcp_mgr.startup()

    # Phase H — bootstrap handlers, start the jobs worker + stall watchdog.
    if __import__("os").getenv("JOB_WORKER_ENABLED", "true").lower() != "false":
        try:
            from jobs.handlers.bootstrap import bootstrap_handlers
            bootstrap_handlers()
            from jobs.worker import get_worker
            from jobs.watchdog import get_watchdog
            get_worker().start()
            get_watchdog().start()
        except Exception as e:
            logger.exception("Job worker startup failed: %s", e)

    logger.info("Pantheon backend ready")
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

    # Phase H — stop jobs worker + watchdog
    try:
        from jobs.worker import get_worker
        from jobs.watchdog import get_watchdog
        await get_worker().stop()
        await get_watchdog().stop()
    except Exception:
        logger.debug("jobs shutdown swallowed", exc_info=True)

    # Stop the Telegram bot cleanly
    await stop_telegram_bot()

    # Stop the task scheduler cleanly
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Task scheduler stopped")

    logger.info("Pantheon backend shutdown complete")


app = FastAPI(
    title="Pantheon",
    description="A production-ready agentic AI framework with 5-tier memory, project isolation, and autonomous tasks.",
    version="2026.05.02.H35",
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

    # Allow frontend static assets and SPA routes through (auth is
    # enforced by the API endpoints themselves, not the static shell).
    path = request.url.path
    if _FRONTEND_DIR.is_dir() and not path.startswith(("/api/", "/ws/")):
        return await call_next(request)

    # WebSocket and direct-URL endpoints (file view/download, used by
    # <img src>, <embed>, etc.) carry the token as a query parameter
    # because neither the WebSocket API nor bare HTML tags support
    # arbitrary request headers.
    _QUERY_TOKEN_PREFIXES = ("/ws/", "/api/files/view", "/api/files/download")
    if request.url.path.startswith(_QUERY_TOKEN_PREFIXES):
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
app.include_router(mcp_router,         prefix="/api", tags=["mcp"])
app.include_router(skills_router,      prefix="/api", tags=["skills"])
app.include_router(tasks_router,       prefix="/api", tags=["tasks"])
app.include_router(personas_router,    prefix="/api", tags=["personas"])
app.include_router(system_router, prefix="/api", tags=["system"])
app.include_router(sources_router, prefix="/api", tags=["sources"])
app.include_router(connections_router, prefix="/api", tags=["connections"])
app.include_router(artifacts_router, prefix="/api", tags=["artifacts"])
app.include_router(conversations_router, prefix="/api", tags=["conversations"])
app.include_router(jobs_router, prefix="/api", tags=["jobs"])

# ── WebSocket — registered directly at /ws/chat (no /api prefix) ─────────────
# The frontend derives the WS URL from window.location.host, so it always
# connects to /ws/chat regardless of the API base URL.
app.websocket("/ws/chat")(websocket_chat)


@app.get("/health")
@app.get("/api/health")
async def health_check():
    return {"status": "ok", "version": app.version}


# ── SPA static file serving (local mode) ──────────────────────────────────────
# When a frontend/dist directory exists next to the backend, serve it so that
# local-mode deployments work on a single port without a separate static server.
if _FRONTEND_DIR.is_dir():
    # Serve static assets (JS, CSS, images) at /assets
    _assets_dir = _FRONTEND_DIR / "assets"
    if _assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(_assets_dir)), name="frontend-assets")

    @app.get("/")
    async def serve_spa_root():
        return FileResponse(str(_FRONTEND_DIR / "index.html"))

    # Catch-all: any path not matched by API routes serves the SPA shell
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Try to serve a real file first (e.g. favicon.ico, manifest.json)
        file_path = _FRONTEND_DIR / full_path
        if file_path.is_file() and _FRONTEND_DIR in file_path.resolve().parents:
            return FileResponse(str(file_path))
        # Otherwise return index.html for client-side routing
        return FileResponse(str(_FRONTEND_DIR / "index.html"))
else:
    @app.get("/")
    async def root():
        return {"message": "Pantheon API", "docs": "/docs"}
