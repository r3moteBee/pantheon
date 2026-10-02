"""FastAPI application entry point."""
from __future__ import annotations
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

# ChromaDB phones home (posthog) unless told not to, and logs a noisy
# error when that fails offline. Must be set before chromadb is imported.
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")
logging.getLogger("chromadb.telemetry.product.posthog").setLevel(logging.CRITICAL)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import get_settings
from utils.paths import InvalidProjectId
from api.auth import router as auth_router, host_is_allowed, origin_is_allowed, request_token, token_is_valid
from api.chat import router as chat_router, websocket_chat
from api.files import router as files_router
from api.memory import router as memory_router
from api.personality import router as personality_router
from api.projects import router as projects_router
from api.settings import router as settings_router
from api.mcp import router as mcp_router
from api.mcp_oauth import router as mcp_oauth_router
from api.skills import router as skills_router
from api.tasks import router as tasks_router
from api.personas import router as personas_router
from api.system import router as system_router
from api.connections import router as connections_router
from api.artifacts import router as artifacts_router
from api.conversations import router as conversations_router
from api.jobs import router as jobs_router
from api.llm_endpoints import router as llm_endpoints_router
from api.messaging import router as messaging_router

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
    "/api/auth/logout",  # must work with an expired session to clear the cookie
    # The OAuth callback is the redirect_uri an external authorization
    # server hits on the user's browser — it cannot carry Pantheon's
    # Bearer token. Security comes from the PKCE code_verifier + state
    # parameter, not Pantheon's auth_password.
    "/api/mcp/oauth/callback",
}

# Resolve frontend dist directory (used by auth middleware and SPA serving)
_FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend" / "dist"


from utils.version import app_version  # noqa: E402

_APP_VERSION = app_version()

# httpx logs every request URL at INFO — including query-string credentials
# some services require (e.g. Tavily's ?tavilyApiKey=). Keep it to warnings.
logging.getLogger("httpx").setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    logger.info("Starting Pantheon backend...")
    settings.ensure_dirs()

    # Public default keys/password: the vault can be decrypted and the
    # login guessed by anyone who has read this repo. api.auth.remote_blocked
    # then serves local clients only.
    from api.auth import insecure_defaults
    _bad = insecure_defaults()
    if _bad:
        logger.warning(
            "SECURITY: %s use public default values — serving local (127.0.0.1) "
            "clients only%s. Set random values in .env (`openssl rand -hex 32`); "
            "change VAULT_MASTER_KEY with scripts/rotate_vault_key.py so stored "
            "secrets are re-encrypted, not lost.",
            ", ".join(_bad),
            " (ALLOW_INSECURE_DEFAULTS=true overrides this — not recommended)"
            if settings.allow_insecure_defaults else "",
        )

    # Learn model capabilities the configured endpoints publish (best-effort,
    # in the background so a slow or offline server can't delay startup).
    try:
        from llm_config.store import refresh_advertised
        from utils.background import spawn
        spawn(refresh_advertised(), name="llm-capability-refresh")
    except Exception:
        logger.warning("model capability refresh not started", exc_info=True)

    try:
        from memory.archival import migrate_stray_notes
        migrate_stray_notes()
    except Exception:
        logger.warning("archival stray-note migration failed", exc_info=True)

    try:
        from api.personas import migrate_project_personas
        migrate_project_personas()
    except Exception:
        logger.warning("persona presets migration failed", exc_info=True)

    # Initialize default personality files if missing or empty
    import shutil
    soul_path = settings.personality_dir / "soul.md"
    agent_path = settings.personality_dir / "agent.md"
    default_dir_docker = Path("/app/bundled_personality")
    default_dir_dev = Path(__file__).parent / "data" / "personality"
    default_dir = default_dir_docker if default_dir_docker.is_dir() else default_dir_dev

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

    # Start all configured messaging adapters (Telegram, Discord, etc.)
    from messaging.gateway import get_messaging_gateway
    messaging_gw = get_messaging_gateway()
    await messaging_gw.startup()

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
            # Recover jobs orphaned by the previous process dying mid-run —
            # must happen BEFORE the worker starts so a requeued job can't
            # race its own orphaned predecessor.
            from jobs.recovery import recover_orphaned_jobs
            try:
                recover_orphaned_jobs()
            except Exception:
                logger.exception("Orphan recovery failed")
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
    from utils.background import spawn
    spawn(_warmup(), name="warmup")
    yield

    # Phase H — stop jobs worker + watchdog
    try:
        from jobs.worker import get_worker
        from jobs.watchdog import get_watchdog
        await get_worker().stop()
        await get_watchdog().stop()
    except Exception:
        logger.debug("jobs shutdown swallowed", exc_info=True)

    # Stop all messaging adapters cleanly
    await messaging_gw.shutdown()

    # Stop the task scheduler cleanly
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("Task scheduler stopped")

    from utils.http import close_shared_clients
    await close_shared_clients()

    logger.info("Pantheon backend shutdown complete")


app = FastAPI(
    title="Pantheon",
    description="A production-ready agentic AI framework with 5-tier memory, project isolation, and autonomous tasks.",
    version=_APP_VERSION,
    lifespan=lifespan,
)


@app.exception_handler(InvalidProjectId)
async def _invalid_project_id(request: Request, exc: InvalidProjectId):
    return JSONResponse({"detail": str(exc)}, status_code=400)

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

    from api.auth import remote_blocked
    reason = remote_blocked(request.client.host if request.client else None, request.headers)
    if reason:
        return JSONResponse({"error": reason}, status_code=503)

    # CSRF guard: browsers attach Origin to cross-site POST/PUT/DELETE. With
    # no password (or a query-string token) a form on any site could
    # otherwise trigger state changes.
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin = request.headers.get("origin")
        if origin and not origin_is_allowed(origin, request.headers.get("host")):
            return JSONResponse({"error": "Cross-origin request blocked"}, status_code=403)

    # Auth disabled — only reachable by hosts that can't be DNS-rebound
    if not cfg.auth_password:
        if not host_is_allowed(request.headers.get("host")):
            return JSONResponse(
                {"error": "Host not allowed. Set AUTH_PASSWORD or add it to ALLOWED_HOSTS."},
                status_code=421,
            )
        return await call_next(request)

    # Always allow public paths
    if request.url.path in _PUBLIC_PATHS:
        return await call_next(request)

    # Allow frontend static assets and SPA routes through (auth is
    # enforced by the API endpoints themselves, not the static shell).
    path = request.url.path
    if _FRONTEND_DIR.is_dir() and not path.startswith(("/api/", "/ws/")):
        return await call_next(request)

    # Session token from the Authorization header (API clients) or the
    # HttpOnly session cookie (browser, incl. <img src>/<a download>
    # direct-URL requests). Tokens are never accepted in the query string.
    # Cookie use can't be forged cross-site: SameSite=Strict, plus the
    # Origin check above for state-changing methods.
    valid = token_is_valid(request_token(request.headers, request.cookies))

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
app.include_router(mcp_oauth_router,   prefix="/api", tags=["mcp-oauth"])
app.include_router(skills_router,      prefix="/api", tags=["skills"])
app.include_router(tasks_router,       prefix="/api", tags=["tasks"])
app.include_router(personas_router,    prefix="/api", tags=["personas"])
app.include_router(system_router, prefix="/api", tags=["system"])
app.include_router(connections_router, prefix="/api", tags=["connections"])
app.include_router(artifacts_router, prefix="/api", tags=["artifacts"])
app.include_router(conversations_router, prefix="/api", tags=["conversations"])
app.include_router(jobs_router, prefix="/api", tags=["jobs"])
app.include_router(llm_endpoints_router, prefix="/api", tags=["llm"])
app.include_router(messaging_router,   prefix="/api", tags=["messaging"])

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
