"""Authentication — POST /api/auth/login, GET /api/auth/config."""
from __future__ import annotations
import hmac
import hashlib
import logging
import base64
import os

import time
from collections import defaultdict, deque

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from config import get_settings
from security_log import sec_log

logger = logging.getLogger(__name__)
router = APIRouter()


SESSION_COOKIE = "pantheon_session"


def _session_ttl_seconds() -> int:
    days = getattr(get_settings(), "auth_session_days", 30) or 30
    return int(days) * 86400


# Public values — from config.py defaults and .env.example — that anyone who
# has read this repo knows. With any of them in use the vault is decryptable
# offline / the login guessable, so only local clients are served.
_PUBLIC_SECRET_VALUES = {
    "vault_master_key": {"dev-key-change-in-production-32x",
                         "change-this-to-a-random-64-char-hex-string-before-deploy"},
    "secret_key": {"dev-secret-key-change-in-production",
                   "change-this-to-another-random-secret-key-for-jwt"},
    "auth_password": {"insert-auth-password-here"},
}


def insecure_defaults() -> list[str]:
    """Env names whose values are public defaults/placeholders."""
    cfg = get_settings()
    return [f.upper() for f, values in _PUBLIC_SECRET_VALUES.items()
            if getattr(cfg, f, None) in values]


_LOOPBACK = {"127.0.0.1", "::1", "localhost", "testclient"}


def _is_loopback(host: str | None) -> bool:
    h = (host or "").strip().strip("[]").lower()
    return h in _LOOPBACK or h.startswith("127.") or h.startswith("::ffff:127.")


def remote_blocked(client_host: str | None, headers) -> str | None:
    """With insecure defaults, refuse anything but a direct local client
    (a reverse proxy's forwarded headers count as remote). Returns the
    reason to show, or None when the request may proceed."""
    bad = insecure_defaults()
    if not bad or get_settings().allow_insecure_defaults:
        return None
    forwarded = [headers.get(h) for h in ("x-forwarded-for", "x-real-ip", "forwarded") if headers.get(h)]
    remote = not _is_loopback(client_host) or any(
        not _is_loopback(v.split(",")[0].split(";")[0].replace("for=", "").strip('" '))
        for v in forwarded
    )
    if not remote:
        return None
    return (f"Pantheon is refusing remote access because {', '.join(bad)} "
            "still use public default values. Set random values in .env "
            "(scripts/rotate_vault_key.py changes VAULT_MASTER_KEY without losing "
            "stored secrets) and restart, or set ALLOW_INSECURE_DEFAULTS=true.")


def password_matches(password: str) -> bool:
    """Constant-time check of ``password`` against AUTH_PASSWORD."""
    settings = get_settings()
    key = settings.secret_key.encode()
    given = hmac.new(key, (password or "").encode(), hashlib.sha256).digest()
    expected = hmac.new(key, settings.auth_password.encode(), hashlib.sha256).digest()
    return hmac.compare_digest(given, expected)


# ── Session store ────────────────────────────────────────────────────────────
# Login issues a random token (not derived from the password, so a leaked
# token can't be brute-forced back to it and expires on its own). Only a
# SHA-256 of each token is stored.

def _sessions_db():
    import sqlite3
    from db_utils import apply_sqlite_pragmas, ClosingConnection
    settings = get_settings()
    settings.db_dir.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(settings.db_dir / "auth_sessions.db"))
    apply_sqlite_pragmas(conn)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sessions (
               token_hash TEXT PRIMARY KEY,
               created_at REAL NOT NULL,
               expires_at REAL NOT NULL,
               pw_fp TEXT NOT NULL DEFAULT ''
           )"""
    )
    return ClosingConnection(conn)  # type: ignore


def _password_fingerprint() -> str:
    """Tag sessions with the password they were issued under, so changing
    AUTH_PASSWORD (or SECRET_KEY) logs every existing session out."""
    s = get_settings()
    return hmac.new(s.secret_key.encode(), s.auth_password.encode(), hashlib.sha256).hexdigest()[:32]


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session() -> str:
    """Issue a new random session token (valid for AUTH_SESSION_DAYS)."""
    # (stdlib `secrets` is shadowed by this app's secrets/ package)
    token = base64.urlsafe_b64encode(os.urandom(32)).rstrip(b"=").decode()
    now = time.time()
    with _sessions_db() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))
        conn.execute(
            "INSERT INTO sessions (token_hash, created_at, expires_at, pw_fp) VALUES (?, ?, ?, ?)",
            (_hash_token(token), now, now + _session_ttl_seconds(), _password_fingerprint()),
        )
        conn.commit()
    return token


def revoke_session(token: str) -> None:
    with _sessions_db() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_token(token),))
        conn.commit()


def revoke_all_sessions() -> None:
    with _sessions_db() as conn:
        conn.execute("DELETE FROM sessions")
        conn.commit()


def _session_valid(token: str) -> bool:
    if not token:
        return False
    with _sessions_db() as conn:
        row = conn.execute(
            "SELECT expires_at, pw_fp FROM sessions WHERE token_hash = ?", (_hash_token(token),)
        ).fetchone()
    return (
        bool(row)
        and row[0] > time.time()
        and hmac.compare_digest(row[1] or "", _password_fingerprint())
    )


def request_token(headers, cookies) -> str:
    """Session token from ``Authorization: Bearer`` or the session cookie.
    Never from the query string — URLs end up in access logs and history."""
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return cookies.get(SESSION_COOKIE, "") or ""


def _set_session_cookie(response: Response, request: Request, token: str) -> None:
    secure = (
        request.url.scheme == "https"
        or request.headers.get("x-forwarded-proto", "").lower() == "https"
    )
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=_session_ttl_seconds(), httponly=True, samesite="strict",
        secure=secure, path="/",
    )


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str


@router.get("/auth/config")
async def auth_config():
    """Tell the frontend whether a password is required."""
    settings = get_settings()
    return {"auth_required": bool(settings.auth_password)}


# Failed-password throttle: at most _MAX_FAILURES per client IP per window.
# Shared by login and every other endpoint that checks AUTH_PASSWORD
# (e.g. POST /api/system/update/execute). The IP is client_ip(): the peer
# address, or the forwarded client when the peer is a TRUSTED_PROXIES entry.
_MAX_FAILURES = 10
_FAILURE_WINDOW_S = 300.0
_failures: dict[str, deque] = defaultdict(deque)


def _recent_failures(ip: str) -> deque:
    q = _failures[ip]
    cutoff = time.monotonic() - _FAILURE_WINDOW_S
    while q and q[0] < cutoff:
        q.popleft()
    if not q:
        _failures.pop(ip, None)   # don't keep a key per IP forever
        return deque()
    return q


def check_password_throttle(conn, reason: str = "rate_limited") -> str:
    """Raise 429 when the caller's IP has too many recent password
    failures. Returns that IP for record_password_failure()."""
    ip = client_ip(conn)
    if len(_recent_failures(ip)) >= _MAX_FAILURES:
        sec_log.auth_login_failure(ip=ip, reason=reason)
        raise HTTPException(status_code=429, detail="Too many failed attempts; try again later")
    return ip


def record_password_failure(ip: str) -> None:
    _failures[ip].append(time.monotonic())


@router.post("/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest, request: Request, response: Response) -> LoginResponse:
    settings = get_settings()
    ip = check_password_throttle(request)

    # Auth disabled — any (or no) password works
    if not settings.auth_password:
        return LoginResponse(token="no-auth")

    if not password_matches(req.password):
        record_password_failure(ip)
        sec_log.auth_login_failure(ip=ip, reason="bad_password")
        raise HTTPException(status_code=401, detail="Invalid password")

    sec_log.auth_login_success()
    token = create_session()
    # The browser UI authenticates with this HttpOnly cookie (script can't
    # read it; SameSite=Strict keeps other sites from using it). The token
    # is also returned for API/script clients using a Bearer header.
    _set_session_cookie(response, request, token)
    return LoginResponse(token=token)


@router.get("/auth/session")
async def session_check() -> dict:
    """200 when the caller is authenticated (the middleware enforces it)."""
    return {"ok": True}


@router.post("/auth/logout")
async def logout(request: Request, response: Response) -> dict:
    token = request_token(request.headers, request.cookies)
    if token:
        revoke_session(token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


def token_is_valid(token: str) -> bool:
    """True when auth is disabled or ``token`` is a live session token."""
    if not get_settings().auth_password:
        return True
    return _session_valid(token)


# ── Proxy trust + request origin ─────────────────────────────────────────────
# X-Forwarded-* headers are honoured only when the direct peer (the TCP
# client) is listed in TRUSTED_PROXIES (IPs/CIDRs, default loopback, like
# uvicorn's --forwarded-allow-ips). Otherwise anyone could pick their own
# client IP for the login throttle. Trusted: X-Forwarded-For (client IP:
# the right-most address that is not itself a trusted proxy),
# X-Forwarded-Proto, X-Forwarded-Host and X-Forwarded-Port (origin).

_DEFAULT_PORTS = {"http": 80, "https": 443}


def _trusted_networks() -> list:
    import ipaddress
    raw = getattr(get_settings(), "trusted_proxies", "127.0.0.1,::1")
    nets = []
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError:
            logger.warning("TRUSTED_PROXIES: ignoring invalid entry %r", part)
    return nets


def _ip_in(ip: str | None, nets: list) -> bool:
    import ipaddress
    try:
        addr = ipaddress.ip_address((ip or "").strip().strip("[]"))
    except ValueError:
        return False
    if getattr(addr, "ipv4_mapped", None):
        addr = addr.ipv4_mapped
    return any(addr.version == n.version and addr in n for n in nets)


def peer_is_trusted_proxy(conn) -> bool:
    """True when the direct peer of ``conn`` (Request/WebSocket) may set
    X-Forwarded-* headers."""
    peer = conn.client.host if getattr(conn, "client", None) else None
    return _ip_in(peer, _trusted_networks())


def client_ip(conn) -> str:
    """The client's IP: the peer address, or — when the peer is a trusted
    proxy — the right-most X-Forwarded-For entry that isn't a trusted proxy."""
    peer = conn.client.host if getattr(conn, "client", None) else None
    if not peer:
        return "unknown"
    nets = _trusted_networks()
    if not _ip_in(peer, nets):
        return peer
    chain = [h.strip() for v in conn.headers.getlist("x-forwarded-for")
             for h in v.split(",") if h.strip()]
    for hop in reversed(chain):
        if not _ip_in(hop, nets):
            return hop
    return chain[0] if chain else peer


def _first(value: str | None) -> str:
    return (value or "").split(",")[0].strip()


def _origin_tuple(url: str | None) -> tuple[str, str, int] | None:
    """(scheme, host, port) of an origin/URL, default port filled in;
    None for "null", opaque or malformed origins."""
    from urllib.parse import urlsplit
    try:
        parts = urlsplit((url or "").strip())
        scheme = {"ws": "http", "wss": "https"}.get(parts.scheme.lower(), parts.scheme.lower())
        host = (parts.hostname or "").lower()
        port = parts.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        return None
    if scheme not in _DEFAULT_PORTS or not host or not port:
        return None
    return scheme, host, port


def _host_origin(scheme: str, host_header: str | None, port_hint: str | None = None
                 ) -> tuple[str, str, int] | None:
    """Origin tuple for a Host header value. A Host without a port means
    the scheme's default port unless ``port_hint`` (X-Forwarded-Port) says
    otherwise."""
    from urllib.parse import urlsplit
    scheme = {"ws": "http", "wss": "https"}.get((scheme or "http").lower(), (scheme or "http").lower())
    if scheme not in _DEFAULT_PORTS or not host_header:
        return None
    try:
        parts = urlsplit(f"//{host_header.strip()}")
        host = (parts.hostname or "").lower()
        port = parts.port
    except ValueError:
        return None
    if not host:
        return None
    if port is None and port_hint and port_hint.isdigit():
        port = int(port_hint)
    return scheme, host, port or _DEFAULT_PORTS[scheme]


def request_origin(conn) -> tuple[str, str, int] | None:
    """The origin (scheme, host, port) this request was addressed to: the
    Host header and URL scheme, or X-Forwarded-Host/-Proto/-Port when the
    peer is a trusted proxy."""
    headers = conn.headers
    scheme = conn.url.scheme
    host = headers.get("host")
    port_hint = None
    if peer_is_trusted_proxy(conn):
        scheme = _first(headers.get("x-forwarded-proto")) or scheme
        host = _first(headers.get("x-forwarded-host")) or host
        port_hint = _first(headers.get("x-forwarded-port")) or None
    return _host_origin(scheme, host, port_hint)


def origin_is_allowed(origin: str | None, host: str | None = None, *, conn=None) -> bool:
    """Cross-site guard for state-changing requests and WebSockets.

    Browsers always send ``Origin`` on WebSocket handshakes and CORS does not
    apply to them. Allow: no Origin (non-browser client), an origin listed in
    CORS_ORIGINS, or the request's own origin. Origins compare as
    (scheme, host, port): another service on the same hostname but a
    different port (e.g. Nextcloud at :8443) is a different origin. With
    ``conn`` the request's origin comes from request_origin(conn); the bare
    ``host`` form assumes plain http.
    """
    if not origin:
        return True
    want = _origin_tuple(origin)
    if want is None:
        return False
    cfg_origins = get_settings().cors_origins_list
    if origin in cfg_origins or want in {_origin_tuple(o) for o in cfg_origins}:
        return True
    own = request_origin(conn) if conn is not None else _host_origin("http", host)
    return own is not None and want == own


async def authorize_websocket(websocket) -> bool:
    """Validate Origin + session (cookie or Bearer) before accept().

    Starlette's ``@app.middleware("http")`` never runs for WebSocket scopes,
    so every WebSocket endpoint must call this itself. Closes the socket and
    returns False when the check fails.
    """
    if remote_blocked(websocket.client.host if websocket.client else None, websocket.headers):
        await websocket.close(code=1008)
        return False
    origin = websocket.headers.get("origin")
    if not origin_is_allowed(origin, conn=websocket):
        sec_log.auth_login_failure(reason=f"ws_bad_origin:{origin}")
        await websocket.close(code=1008)
        return False
    if not get_settings().auth_password and not host_is_allowed(websocket.headers.get("host")):
        await websocket.close(code=1008)
        return False
    # Browsers send the session cookie on the same-origin WS handshake.
    token = request_token(websocket.headers, websocket.cookies)
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
