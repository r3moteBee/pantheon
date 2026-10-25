"""OAuth 2.1 client for MCP servers — Protected Resource Metadata, client
registration (pre-registered / Client ID Metadata Document / DCR) + PKCE.

Per MCP spec 2026-07-28 (client registration priority: pre-registered
credentials, then a Client ID Metadata Document when the AS advertises
``client_id_metadata_document_supported``, then Dynamic Client Registration,
else ask the user) on top of the 2025-06-18 / 2025-11-25 discovery flow:

  1. Unauth'd request to MCP server → 401 with `WWW-Authenticate: Bearer
     resource_metadata="<url>"`. We fetch the PRM doc (RFC 9728) to learn
     which authorization servers serve this resource.
  2. Fetch each AS's metadata via `/.well-known/oauth-authorization-server`
     (RFC 8414), falling back to `/.well-known/openid-configuration` if the
     AS only advertises OIDC discovery (spec 2025-11-25 addition).
  3. A client id: credentials the user pre-registered on the connection
     (bound to the AS issuer); else our Client ID Metadata Document URL
     (``cimd_client_id``: the HTTPS URL of /api/mcp/oauth/client-metadata.json,
     a public client with PKCE, portable across ASes); else Dynamic Client
     Registration (RFC 7591, deprecated in 2026-07-28 but kept for ASes without
     CIMD) — POST to `registration_endpoint` with our callback URL; the AS
     returns a `client_id` (and `client_secret` if it insists on a
     confidential client).
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

# Where the FastAPI router serves the callback (api/mcp_oauth.py).
CALLBACK_PATH = "/api/mcp/oauth/callback"
# Fallback for a bare local install when nothing better is known.
DEFAULT_CALLBACK_URL = "http://localhost:8000" + CALLBACK_PATH


def _origin_url(origin: tuple[str, str, int]) -> str:
    scheme, host, port = origin
    if ":" in host:              # IPv6 literal
        host = f"[{host}]"
    default = {"http": 80, "https": 443}.get(scheme)
    return f"{scheme}://{host}" + ("" if port == default else f":{port}")


def callback_url_for(conn=None) -> str:
    """The redirect_uri for an OAuth flow started by ``conn`` (the Request).

    Order: PUBLIC_BASE_URL; else the request's ``Origin`` when it passes the
    CSRF origin check (it is what the browser shows, including a port a
    reverse proxy drops from Host); else the request's own origin
    (api.auth.request_origin: Host, or X-Forwarded-* from a trusted proxy);
    else http://localhost:8000. The value is stored with the pending state so
    the token exchange sends the same redirect_uri.
    """
    from config import get_settings
    base = (getattr(get_settings(), "public_base_url", "") or "").strip().rstrip("/")
    if base:
        return base + CALLBACK_PATH
    if conn is None:
        return DEFAULT_CALLBACK_URL
    from api.auth import _origin_tuple, origin_is_allowed, request_origin
    origin = conn.headers.get("origin")
    if origin and origin_is_allowed(origin, conn=conn):
        parsed = _origin_tuple(origin)
        if parsed:
            return _origin_url(parsed) + CALLBACK_PATH
    own = request_origin(conn)
    if own:
        return _origin_url(own) + CALLBACK_PATH
    return DEFAULT_CALLBACK_URL

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


def _prereg_secret_key(name: str) -> str:
    return f"mcp_oauth_prereg_secret__{name}"


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
    # 2026-07-28: the AS resolves URL-shaped client ids (CIMD).
    client_id_metadata_document_supported: bool = False
    raw: dict[str, Any] = field(default_factory=dict)


# How a client id was obtained (connection config `oauth.registration`).
REG_PREREGISTERED = "preregistered"
REG_CIMD = "cimd"
REG_DCR = "dcr"


@dataclass
class RegisteredClient:
    """A client id to use with an AS, however it was obtained."""
    client_id: str
    client_secret: Optional[str]  # None for public clients
    token_endpoint_auth_method: str  # "none", "client_secret_post", etc.
    registration: str = REG_DCR
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
    # RFC 9207: the AS said it sends `iss` on the redirect, so a redirect
    # without one is rejected too (not only a mismatching one).
    iss_required: bool = False
    # How the client id was obtained; a CIMD id the AS rejects falls back to DCR.
    registration: str = REG_DCR


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


def auth_server_metadata_urls(issuer: str) -> list[str]:
    """Well-known URLs to try for an issuer, in the order MCP 2026-07-28
    requires: for an issuer with a path, OAuth metadata with path insertion,
    OIDC discovery with path insertion, then OIDC discovery with the path
    appended; for a bare issuer, OAuth then OIDC."""
    issuer = issuer.rstrip("/")
    p = urlparse(issuer)
    base = f"{p.scheme}://{p.netloc}"
    path = p.path or ""
    if path and path != "/":
        return [
            f"{base}/.well-known/oauth-authorization-server{path}",
            f"{base}/.well-known/openid-configuration{path}",
            f"{issuer}/.well-known/openid-configuration",
        ]
    return [
        f"{issuer}/.well-known/oauth-authorization-server",
        f"{issuer}/.well-known/openid-configuration",
    ]


def parse_auth_server_metadata(raw: dict[str, Any], issuer: str) -> AuthServerMetadata:
    """Build AuthServerMetadata from a document fetched for ``issuer``; the
    document's own ``issuer`` must be the same identifier (RFC 8414 §3.3)."""
    got = str(raw.get("issuer") or "")
    if got.rstrip("/") != issuer.rstrip("/"):
        raise RuntimeError(
            f"Authorization server metadata for {issuer} claims issuer {got or '(none)'!r}; refusing it")
    return AuthServerMetadata(
        issuer=got.rstrip("/") or issuer.rstrip("/"),
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
        client_id_metadata_document_supported=bool(raw.get("client_id_metadata_document_supported")),
        raw=raw,
    )


async def fetch_auth_server_metadata(
    issuer: str, timeout: float = 10.0
) -> AuthServerMetadata:
    """Fetch AS metadata, trying the well-known URLs auth_server_metadata_urls
    lists (RFC 8414 + OpenID Connect discovery), and validate its issuer."""
    candidates = auth_server_metadata_urls(issuer)
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
    return parse_auth_server_metadata(raw, issuer)


# ── Client ID Metadata Documents (CIMD) ──────────────────────────────────────

CIMD_PATH = "/api/mcp/oauth/client-metadata.json"
CLIENT_NAME = "Pantheon"

# Hosts an internet-facing authorization server cannot resolve or reach: a
# document there would make the authorize step fail with invalid_client.
_PRIVATE_SUFFIXES = (".ts.net", ".local", ".localhost", ".internal", ".lan", ".home.arpa", ".test", ".invalid")


def host_looks_public(host: str) -> bool:
    """False for loopback, private/link-local addresses and local-only names."""
    import ipaddress
    host = (host or "").strip("[]").lower()
    if not host or host == "localhost" or host.endswith(_PRIVATE_SUFFIXES) or "." not in host:
        return False
    try:
        return ipaddress.ip_address(host).is_global
    except ValueError:
        return True


def valid_cimd_url(url: str) -> bool:
    """The client_id URL rules of draft-ietf-oauth-client-id-metadata-document:
    https, a path, no dot segments, no fragment, no userinfo."""
    try:
        p = urlparse(url)
    except ValueError:
        return False
    if p.scheme != "https" or not p.hostname or not p.path or p.path == "/":
        return False
    if p.fragment or p.username or p.password or "#" in url:
        return False
    if any(seg in (".", "..") for seg in p.path.split("/")):
        return False
    return True


def cimd_client_id(base_url: str | None = None) -> Optional[str]:
    """The URL this Pantheon publishes its client metadata at, or None when
    CIMD cannot be used: MCP_CIMD_ENABLED is off, no HTTPS public base, or
    the base is a private / tailnet host no authorization server could fetch
    from. MCP_CLIENT_ID_METADATA_URL overrides the derived URL (it must
    still be a valid https client_id URL)."""
    from config import get_settings
    st = get_settings()
    if not getattr(st, "mcp_cimd_enabled", True):
        return None
    explicit = (getattr(st, "mcp_client_id_metadata_url", "") or "").strip()
    if explicit:
        if valid_cimd_url(explicit):
            return explicit
        # Development escape hatch: a plain-http document on a private host
        # (a lab authorization server on the same network). Never public.
        p = urlparse(explicit)
        if p.scheme == "http" and valid_cimd_url("https" + explicit[4:]) and not host_looks_public(p.hostname or ""):
            logger.warning("MCP_CLIENT_ID_METADATA_URL %s is plain http on a private host: development use only", explicit)
            return explicit
        return None
    base = (getattr(st, "public_base_url", "") or base_url or "").strip().rstrip("/")
    if not base:
        return None
    url = base + CIMD_PATH
    if not valid_cimd_url(url) or not host_looks_public(urlparse(url).hostname or ""):
        return None
    return url


def cimd_document(client_id: str, redirect_uris: list[str]) -> dict[str, Any]:
    """The Client ID Metadata Document served at ``client_id``: a public
    client (PKCE, no secret) with exactly the callback URIs we use."""
    p = urlparse(client_id)
    return {
        "client_id": client_id,
        "client_name": CLIENT_NAME,
        "client_uri": f"{p.scheme}://{p.netloc}",
        "redirect_uris": list(dict.fromkeys(redirect_uris)),
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }


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
            "Authorization server does not support Dynamic Client Registration."
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
        # MCP 2026-07-28: say what kind of app we are, or an OIDC AS applies
        # "web" rules to a localhost redirect (or "native" rules to our https
        # tailnet one) and rejects it. Retry once with the other kind.
        "application_type": application_type_for(redirect_uri),
    }
    if scopes:
        body["scope"] = " ".join(scopes)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        tried: list[str] = []
        while True:
            tried.append(body["application_type"])
            resp = await client.post(
                as_meta.registration_endpoint,
                json=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
            )
            if resp.status_code in (200, 201):
                break
            other = "web" if body["application_type"] == "native" else "native"
            if resp.status_code == 400 and other not in tried:
                logger.info("DCR rejected application_type=%s (%s); retrying as %s",
                            body["application_type"], resp.text[:120], other)
                body["application_type"] = other
                continue
            raise RuntimeError(
                f"DCR failed: HTTP {resp.status_code} — {resp.text[:300]} "
                f"(redirect_uri {redirect_uri}, application_type tried: {', '.join(tried)})"
            )
        raw = resp.json()

    return RegisteredClient(
        client_id=raw["client_id"],
        client_secret=raw.get("client_secret"),
        token_endpoint_auth_method=raw.get("token_endpoint_auth_method", "none"),
        registration=REG_DCR,
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
    registration: str = REG_DCR,
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
        iss_required=bool((as_meta.raw or {}).get("authorization_response_iss_parameter_supported")),
        registration=registration,
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


def verify_issuer(pending: PendingAuth, iss: Optional[str]) -> None:
    """RFC 9207 (MCP 2026-07-28): a present ``iss`` on the authorization
    response must equal the issuer the flow started with, and an AS that
    advertised the parameter must send it. Raises RuntimeError otherwise."""
    if iss is None or iss == "":
        if pending.iss_required:
            raise RuntimeError(
                "Authorization response is missing the `iss` parameter the "
                f"authorization server ({pending.issuer}) said it sends; refusing the code."
            )
        return
    if iss.rstrip("/") != pending.issuer.rstrip("/"):
        raise RuntimeError(
            f"Authorization response came from issuer {iss!r}, but this flow was started "
            f"with {pending.issuer!r} (possible mix-up attack); refusing the code."
        )


def application_type_for(redirect_uri: str) -> str:
    """OIDC `application_type` for a redirect URI: "native" for loopback /
    localhost callbacks, "web" for anything served from a real host."""
    try:
        host = (urlparse(redirect_uri).hostname or "").lower()
    except ValueError:
        host = ""
    if host in ("localhost", "127.0.0.1", "::1", "[::1]") or host.endswith(".localhost"):
        return "native"
    return "web"


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


def mark_refresh_failed(name: str, error: str) -> None:
    """Flag stored tokens as unrecoverable (refresh rejected) so the
    connection reports needs_auth. The next successful save_tokens()
    (after re-authorizing) writes a fresh record without the flag."""
    from secrets.vault import get_vault

    tokens = load_tokens(name)
    if not tokens:
        return
    tokens["refresh_failed"] = True
    tokens["refresh_error"] = (error or "")[:300]
    get_vault().set_secret(_tokens_key(name), json.dumps(tokens))


def delete_tokens(name: str) -> None:
    """Wipe persisted tokens for a connection. The client registration (DCR
    secret, pre-registered credentials) stays: revoking a token is not
    un-registering, and the next Authorize reuses the client id."""
    from secrets.vault import get_vault

    get_vault().delete_secret(_tokens_key(name))


def delete_registration(name: str) -> None:
    """Forget a DCR / CIMD registration's secret (the AS changed, or the
    connection is being removed). Pre-registered credentials are the
    user's and are kept unless delete_preregistered is called."""
    from secrets.vault import get_vault

    get_vault().delete_secret(_client_secret_key(name))


def save_preregistered_secret(name: str, secret: str) -> None:
    from secrets.vault import get_vault
    get_vault().set_secret(_prereg_secret_key(name), secret)


def load_preregistered_secret(name: str) -> Optional[str]:
    from secrets.vault import get_vault
    return get_vault().get_secret(_prereg_secret_key(name)) or None


def delete_preregistered(name: str) -> None:
    from secrets.vault import get_vault
    get_vault().delete_secret(_prereg_secret_key(name))


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
