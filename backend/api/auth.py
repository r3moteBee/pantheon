"""Authentication — POST /api/auth/login, GET /api/auth/config."""
from __future__ import annotations
import hmac
import hashlib
import logging

import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from config import get_settings
from security_log import sec_log

logger = logging.getLogger(__name__)
router = APIRouter()


def compute_token(password: str, secret: str) -> str:
    """Derive a stable auth token from password + secret key."""
    return hmac.new(secret.encode(), password.encode(), hashlib.sha256).hexdigest()


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str


@router.get("/auth/config")
async def auth_config():
    """Tell the frontend whether a password is required."""
    settings = get_settings()
    return {"auth_required": bool(settings.auth_password)}


# Failed-login throttle: at most _MAX_FAILURES per client IP per window.
_MAX_FAILURES = 10
_FAILURE_WINDOW_S = 300.0
_failures: dict[str, deque] = defaultdict(deque)


def _recent_failures(ip: str) -> deque:
    q = _failures[ip]
    cutoff = time.monotonic() - _FAILURE_WINDOW_S
    while q and q[0] < cutoff:
        q.popleft()
    return q


@router.post("/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request) -> LoginResponse:
    settings = get_settings()
    ip = request.client.host if request.client else "unknown"
    if len(_recent_failures(ip)) >= _MAX_FAILURES:
        sec_log.auth_login_failure(ip=ip, reason="rate_limited")
        raise HTTPException(status_code=429, detail="Too many failed attempts; try again later")

    # Auth disabled — any (or no) password works
    if not settings.auth_password:
        return LoginResponse(token="no-auth")

    expected = compute_token(settings.auth_password, settings.secret_key)
    given = compute_token(req.password, settings.secret_key)

    try:
        valid = hmac.compare_digest(given, expected)
    except (TypeError, ValueError):
        valid = False

    if not valid:
        _failures[ip].append(time.monotonic())
        sec_log.auth_login_failure(ip=ip, reason="bad_password")
        raise HTTPException(status_code=401, detail="Invalid password")

    sec_log.auth_login_success()
    return LoginResponse(token=expected)


def token_is_valid(token: str) -> bool:
    """True when auth is disabled or ``token`` matches the configured password."""
    settings = get_settings()
    if not settings.auth_password:
        return True
    expected = compute_token(settings.auth_password, settings.secret_key)
    try:
        return hmac.compare_digest(token or "", expected)
    except (TypeError, ValueError):
        return False


def origin_is_allowed(origin: str | None, host: str | None) -> bool:
    """Cross-site WebSocket guard.

    Browsers always send ``Origin`` on WebSocket handshakes and CORS does not
    apply to them, so a page on any site could otherwise open a socket to
    localhost. Allow: no Origin (non-browser client), same-host origin, or an
    origin listed in CORS_ORIGINS.
    """
    if not origin:
        return True
    from urllib.parse import urlparse
    if origin in get_settings().cors_origins_list:
        return True
    if not host:
        return False
    # Compare hostnames only: reverse proxies (nginx `$host`) drop the port
    # from Host while the browser keeps it in Origin.
    origin_host = (urlparse(origin).hostname or "").lower()
    host_only = urlparse(f"//{host}").hostname or ""
    return bool(origin_host) and origin_host == host_only.lower()


async def authorize_websocket(websocket) -> bool:
    """Validate Origin + ``?token=`` before accept().

    Starlette's ``@app.middleware("http")`` never runs for WebSocket scopes,
    so every WebSocket endpoint must call this itself. Closes the socket and
    returns False when the check fails.
    """
    origin = websocket.headers.get("origin")
    if not origin_is_allowed(origin, websocket.headers.get("host")):
        sec_log.auth_login_failure(reason=f"ws_bad_origin:{origin}")
        await websocket.close(code=1008)
        return False
    if not get_settings().auth_password and not host_is_allowed(websocket.headers.get("host")):
        await websocket.close(code=1008)
        return False
    token = websocket.query_params.get("token", "")
    if not token:
        token = websocket.headers.get("authorization", "").removeprefix("Bearer ").strip()
    if not token_is_valid(token):
        sec_log.auth_login_failure(reason="ws_bad_token")
        await websocket.close(code=1008)
        return False
    return True


_PRIVATE_SUFFIXES = (".local", ".lan", ".home.arpa", ".internal", ".localhost")


def host_is_allowed(host_header: str | None) -> bool:
    """DNS-rebinding guard for when AUTH_PASSWORD is empty.

    A rebinding attack needs a public, attacker-controlled DNS name pointing
    at this machine, so IP literals, single-label names (localhost) and
    private-TLD names are always fine; any other dotted name must be listed
    in ALLOWED_HOSTS.
    """
    import ipaddress
    host = (host_header or "").strip().lower()
    if not host:
        return True
    if host.startswith("["):  # [::1]:8000
        host = host[1:].split("]", 1)[0]
    elif host.count(":") == 1:
        host = host.rsplit(":", 1)[0]
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if "." not in host or host.endswith(_PRIVATE_SUFFIXES):
        return True
    allowed = {h.strip().lower() for h in get_settings().allowed_hosts.split(",") if h.strip()}
    return host in allowed
