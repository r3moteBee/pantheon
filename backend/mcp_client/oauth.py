"""OAuth 2.1 client for MCP servers — Protected Resource Metadata + DCR + PKCE.

Per MCP spec 2025-06-18 (auth) and 2025-11-25 (OIDC discovery fallback):

  1. Unauth'd request to MCP server → 401 with `WWW-Authenticate: Bearer
     resource_metadata="<url>"`. We fetch the PRM doc (RFC 9728) to learn
     which authorization servers serve this resource.
  2. Fetch each AS's metadata via `/.well-known/oauth-authorization-server`
     (RFC 8414), falling back to `/.well-known/openid-configuration` if the
     AS only advertises OIDC discovery (spec 2025-11-25 addition).
  3. Dynamic Client Registration (RFC 7591) — POST to `registration_endpoint`
     with our localhost callback URL. The AS returns a `client_id`
     (and `client_secret` if it insists on a confidential client; we prefer
     `token_endpoint_auth_method=none` + PKCE).
  4. Authorization Code Flow + PKCE S256: build the auth URL, user opens it
     in their browser, AS redirects back to our callback with a code, we
     exchange it for tokens.
  5. Tokens persist in the vault keyed by connection name.
  6. Refresh: pre-emptively refresh tokens within 60s of expiry; on a 401
     from the MCP server, force-refresh once and retry.

Single-user, single-process design — pending-auth state lives in module-level
dicts keyed by `state` param, swept every 10 minutes.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Optional
from urllib.parse import urlencode, urlparse

import httpx

# Pantheon ships a local `secrets/` package (backend/secrets/) which shadows
# the stdlib `secrets` module when uvicorn runs from inside backend/. Use
# os.urandom directly for token generation to avoid the collision.

logger = logging.getLogger(__name__)

# Where the FastAPI router serves the callback. If you change this, also
# update _CALLBACK_PATH in backend/api/mcp_oauth.py.
DEFAULT_CALLBACK_URL = "http://localhost:8000/api/mcp/oauth/callback"

# State entries older than this are swept from the pending dict.
_STATE_TTL_SECONDS = 600

# Refresh access tokens this many seconds before they actually expire so
# in-flight requests don't race past the expiry.
_REFRESH_BUFFER_SECONDS = 60


# ── Vault key helpers ─────────────────────────────────────────────────────────

def _tokens_key(name: str) -> str:
    return f"mcp_oauth_tokens__{name}"


def _client_secret_key(name: str) -> str:
    return f"mcp_oauth_client_secret__{name}"


# ── Discovery / DCR / PKCE ────────────────────────────────────────────────────


@dataclass
class ResourceMetadata:
    """Parsed Protected Resource Metadata (RFC 9728)."""
    resource: str
    authorization_servers: list[str]
    scopes_supported: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AuthServerMetadata:
    """Parsed AS metadata (RFC 8414) — superset that also fits OIDC discovery."""
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    registration_endpoint: Optional[str] = None
    scopes_supported: list[str] = field(default_factory=list)
    code_challenge_methods_supported: list[str] = field(default_factory=list)
    grant_types_supported: list[str] = field(default_factory=list)
    token_endpoint_auth_methods_supported: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class RegisteredClient:
    """Result of dynamic client registration (RFC 7591)."""
    client_id: str
    client_secret: Optional[str]  # None for public clients
    token_endpoint_auth_method: str  # "none", "client_secret_post", etc.
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingAuth:
    """In-flight authorization waiting for callback."""
    name: str  # MCP connection name
    issuer: str
    token_endpoint: str
    client_id: str
    redirect_uri: str
    code_verifier: str
    scopes: list[str]
    resource: str  # canonical resource identifier from PRM
    created_at: float


# Pending-auth registry keyed by `state` param.
_pending: dict[str, PendingAuth] = {}


def _sweep_pending() -> None:
    """Drop pending entries older than TTL."""
    now = time.time()
    stale = [k for k, v in _pending.items() if now - v.created_at > _STATE_TTL_SECONDS]
    for k in stale:
        _pending.pop(k, None)


def _pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) using S256."""
    verifier = base64.urlsafe_b64encode(os.urandom(48)).decode("ascii").rstrip("=")
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


def _random_state() -> str:
    """Stand-in for secrets.token_urlsafe — see import-collision note above."""
    return base64.urlsafe_b64encode(os.urandom(24)).decode("ascii").rstrip("=")


def _resource_id_for(url: str) -> str:
    """Canonical resource identifier for the MCP server (per RFC 8707).

    Strip query/fragment, keep scheme://host[:port][/path].
    """
    p = urlparse(url)
    netloc = p.netloc
    path = p.path or ""
    if path.endswith("/"):
        path = path[:-1]
    return f"{p.scheme}://{netloc}{path}" if path else f"{p.scheme}://{netloc}"


def parse_www_authenticate(header: str) -> Optional[str]:
    """Extract resource_metadata URL from `WWW-Authenticate: Bearer ...`.

    Tolerates the various quoting / spacing styles servers use. Returns
    None if the header is missing or doesn't contain resource_metadata.
    """
    if not header:
        return None
    # Look for resource_metadata=<value> with optional quotes.
    parts = header.split(",")
    for part in parts + [header]:  # also try the whole header
        p = part.strip()
        if "resource_metadata" not in p:
            continue
        _, _, value = p.partition("resource_metadata")
        value = value.lstrip("=").strip()
        if value.startswith('"') and value.endswith('"'):
            value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        else:
            # Trim trailing comma / whitespace
            value = value.split(",", 1)[0].strip()
        if value:
            return value
    return None


async def probe_for_oauth(mcp_url: str, timeout: float = 10.0) -> Optional[str]:
    """Send an unauth'd initialize to the MCP server. Return PRM URL or None.

    Returns:
        The resource_metadata URL if the server responded 401 with a
        WWW-Authenticate Bearer challenge. None if no auth required, or
        if the server doesn't speak PRM.
    """
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-11-25",
            "capabilities": {},
            "clientInfo": {"name": "pantheon", "version": "1.0.0"},
        },
    }
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.post(mcp_url, json=payload, headers=headers)
    if resp.status_code != 401:
        return None
    return parse_www_authenticate(resp.headers.get("WWW-Authenticate", ""))


async def fetch_resource_metadata(prm_url: str, timeout: float = 10.0) -> ResourceMetadata:
    """Fetch and parse Protected Resource Metadata (RFC 9728)."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.get(prm_url, headers={"Accept": "application/json"})
        resp.raise_for_status()
        raw = resp.json()
    return ResourceMetadata(
        resource=raw.get("resource", ""),
        authorization_servers=list(raw.get("authorization_servers", [])),
        scopes_supported=list(raw.get("scopes_supported", [])),
        raw=raw,
    )


async def fetch_auth_server_metadata(
    issuer: str, timeout: float = 10.0
) -> AuthServerMetadata:
    """Fetch AS metadata, trying OAuth then OIDC discovery URLs.

    Per RFC 8414 the path is `/.well-known/oauth-authorization-server`. As of
    MCP spec 2025-11-25 clients should also fall back to OpenID Connect's
    `/.well-known/openid-configuration` since many OIDC providers only host
    metadata there.
    """
    issuer = issuer.rstrip("/")
    candidates = [
        f"{issuer}/.well-known/oauth-authorization-server",
        f"{issuer}/.well-known/openid-configuration",
    ]
    last_error: Exception | None = None
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        for url in candidates:
            try:
                resp = await client.get(url, headers={"Accept": "application/json"})
                if resp.status_code != 200:
                    last_error = RuntimeError(f"{url} → HTTP {resp.status_code}")
                    continue
                raw = resp.json()
                break
            except Exception as e:
                last_error = e
                continue
        else:
            raise RuntimeError(
                f"No AS metadata found at {candidates}: {last_error}"
            )
    return AuthServerMetadata(
        issuer=raw.get("issuer", issuer),
        authorization_endpoint=raw["authorization_endpoint"],
        token_endpoint=raw["token_endpoint"],
        registration_endpoint=raw.get("registration_endpoint"),
        scopes_supported=list(raw.get("scopes_supported", [])),
        code_challenge_methods_supported=list(
            raw.get("code_challenge_methods_supported", [])
        ),
        grant_types_supported=list(raw.get("grant_types_supported", [])),
        token_endpoint_auth_methods_supported=list(
            raw.get("token_endpoint_auth_methods_supported", [])
        ),
        raw=raw,
    )


async def register_client(
    as_meta: AuthServerMetadata,
    redirect_uri: str,
    client_name: str = "Pantheon",
    scopes: list[str] | None = None,
    timeout: float = 15.0,
) -> RegisteredClient:
    """Dynamic Client Registration (RFC 7591). Public client + PKCE preferred."""
    if not as_meta.registration_endpoint:
        raise RuntimeError(
            "Authorization server does not support Dynamic Client Registration. "
            "Manual client_id entry is not yet implemented."
        )

    body: dict[str, Any] = {
        "client_name": client_name,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        # Prefer public client (no secret) with PKCE; many ASes will downgrade
        # us to a confidential client and return a client_secret regardless,
        # which we handle in the response.
        "token_endpoint_auth_method": "none",
        "application_type": "native",
    }
    if scopes:
        body["scope"] = " ".join(scopes)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.post(
            as_meta.registration_endpoint,
            json=body,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        if resp.status_code not in (200, 201):
            raise RuntimeError(
                f"DCR failed: HTTP {resp.status_code} — {resp.text[:300]}"
            )
        raw = resp.json()

    return RegisteredClient(
        client_id=raw["client_id"],
        client_secret=raw.get("client_secret"),
        token_endpoint_auth_method=raw.get("token_endpoint_auth_method", "none"),
        raw=raw,
    )


# ── Authorization URL + code exchange ─────────────────────────────────────────


def build_authorize_url(
    *,
    name: str,
    as_meta: AuthServerMetadata,
    client_id: str,
    redirect_uri: str,
    scopes: list[str],
    resource: str,
) -> str:
    """Build the authorization URL and stash the PKCE verifier for callback."""
    _sweep_pending()

    state = _random_state()
    verifier, challenge = _pkce_pair()

    _pending[state] = PendingAuth(
        name=name,
        issuer=as_meta.issuer,
        token_endpoint=as_meta.token_endpoint,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_verifier=verifier,
        scopes=scopes,
        resource=resource,
        created_at=time.time(),
    )

    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    # Resource indicator (RFC 8707) — binds the token to this MCP server.
    if resource:
        params["resource"] = resource

    sep = "&" if "?" in as_meta.authorization_endpoint else "?"
    return f"{as_meta.authorization_endpoint}{sep}{urlencode(params)}"


def take_pending(state: str) -> Optional[PendingAuth]:
    """Pop a pending-auth entry by state. None if expired/unknown."""
    _sweep_pending()
    return _pending.pop(state, None)


async def exchange_code(
    *,
    pending: PendingAuth,
    code: str,
    client_secret: Optional[str] = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Trade an authorization code for tokens. Returns the token response dict."""
    body: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": pending.redirect_uri,
        "client_id": pending.client_id,
        "code_verifier": pending.code_verifier,
    }
    if pending.resource:
        body["resource"] = pending.resource

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    auth = None
    if client_secret:
        # Confidential client — Basic auth per RFC 6749 §2.3.1
        auth = (pending.client_id, client_secret)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.post(
            pending.token_endpoint, data=body, headers=headers, auth=auth
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"Token exchange failed: HTTP {resp.status_code} — {resp.text[:300]}"
            )
        return resp.json()


async def refresh_tokens(
    *,
    token_endpoint: str,
    client_id: str,
    refresh_token: str,
    resource: str = "",
    scopes: list[str] | None = None,
    client_secret: Optional[str] = None,
    timeout: float = 15.0,
) -> dict[str, Any]:
    """Exchange a refresh token for a new access token (and possibly new refresh)."""
    body: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if resource:
        body["resource"] = resource
    if scopes:
        body["scope"] = " ".join(scopes)

    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    auth = (client_id, client_secret) if client_secret else None

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        resp = await client.post(token_endpoint, data=body, headers=headers, auth=auth)
        if resp.status_code != 200:
            raise RuntimeError(
                f"Token refresh failed: HTTP {resp.status_code} — {resp.text[:300]}"
            )
        return resp.json()


# ── Token store ──────────────────────────────────────────────────────────────


def _normalize_token_response(raw: dict[str, Any], now: float | None = None) -> dict[str, Any]:
    """Normalize an OAuth token response into our stored shape."""
    now = now if now is not None else time.time()
    expires_in = raw.get("expires_in")
    expires_at = (now + float(expires_in)) if expires_in else 0.0
    return {
        "access_token": raw["access_token"],
        "refresh_token": raw.get("refresh_token", ""),
        "token_type": raw.get("token_type", "Bearer"),
        "expires_at": expires_at,
        "scope": raw.get("scope", ""),
        "issued_at": now,
    }


def save_tokens(name: str, token_response: dict[str, Any]) -> dict[str, Any]:
    """Persist a token response to the vault. Returns the normalized form."""
    from secrets.vault import get_vault

    normalized = _normalize_token_response(token_response)
    # Don't lose a previously-issued refresh_token if the AS only returned a
    # new access_token (common — many servers don't rotate refresh tokens).
    if not normalized["refresh_token"]:
        existing = load_tokens(name)
        if existing and existing.get("refresh_token"):
            normalized["refresh_token"] = existing["refresh_token"]

    get_vault().set_secret(_tokens_key(name), json.dumps(normalized))
    return normalized


def load_tokens(name: str) -> Optional[dict[str, Any]]:
    """Load persisted tokens for a connection. None if none stored."""
    from secrets.vault import get_vault

    raw = get_vault().get_secret(_tokens_key(name))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("Stored tokens for '%s' are corrupt JSON", name)
        return None


def delete_tokens(name: str) -> None:
    """Wipe persisted tokens (and any DCR secret) for a connection."""
    from secrets.vault import get_vault

    vault = get_vault()
    vault.delete_secret(_tokens_key(name))
    vault.delete_secret(_client_secret_key(name))


def save_client_secret(name: str, secret: str) -> None:
    from secrets.vault import get_vault
    get_vault().set_secret(_client_secret_key(name), secret)


def load_client_secret(name: str) -> Optional[str]:
    from secrets.vault import get_vault
    return get_vault().get_secret(_client_secret_key(name)) or None


def is_token_fresh(tokens: dict[str, Any]) -> bool:
    """True if the access token has more than _REFRESH_BUFFER_SECONDS left."""
    expires_at = float(tokens.get("expires_at") or 0.0)
    if expires_at <= 0:
        # Server didn't tell us an expiry — assume fresh, let 401 force refresh.
        return True
    return time.time() + _REFRESH_BUFFER_SECONDS < expires_at
