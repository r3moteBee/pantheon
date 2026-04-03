"""Authentication API — password login + OIDC/OAuth2 flows.

Routes:
  GET  /api/auth/config              — auth mode and available providers
  POST /api/auth/login               — password login (legacy)
  GET  /api/auth/oidc/providers      — list enabled OIDC providers
  GET  /api/auth/oidc/<name>/authorize — redirect to IdP
  GET  /api/auth/oidc/<name>/callback  — IdP callback → session JWT
  GET  /api/auth/me                  — current user from session JWT
"""
from __future__ import annotations
import hashlib
import hmac
import logging
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from config import get_secret
from auth.oidc import (
    check_access,
    create_session_token,
    exchange_code,
    fetch_userinfo,
    get_enabled_providers,
    get_provider_by_name,
    verify_session_token,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# In-memory CSRF state store (state → provider_name).
# Entries are short-lived; cleaned up on use.
_oauth_states: dict[str, str] = {}


# ── Helpers ─────────────────────────────────────────────────────────────────

def compute_token(password: str, secret: str) -> str:
    """Derive a stable auth token from password + secret key (legacy)."""
    return hmac.new(secret.encode(), password.encode(), hashlib.sha256).hexdigest()


def _get_auth_mode() -> str:
    """Determine current auth mode: 'none', 'password', 'oidc', or 'both'."""
    has_password = bool(get_secret("auth_password"))
    has_oidc = len(get_enabled_providers()) > 0
    if has_password and has_oidc:
        return "both"
    if has_oidc:
        return "oidc"
    if has_password:
        return "password"
    return "none"


# ── Models ──────────────────────────────────────────────────────────────────

class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    token: str
    mode: str = "password"


# ── Config endpoint ─────────────────────────────────────────────────────────

@router.get("/auth/config")
async def auth_config():
    """Tell the frontend what authentication methods are available."""
    mode = _get_auth_mode()
    providers = []
    if mode in ("oidc", "both"):
        providers = [
            {"name": p.name, "display_name": p.display_name}
            for p in get_enabled_providers()
        ]
    return {
        "auth_required": mode != "none",
        "mode": mode,
        "oidc_providers": providers,
    }


# ── Password login (legacy) ────────────────────────────────────────────────

@router.post("/auth/login", response_model=LoginResponse)
async def login(req: LoginRequest) -> LoginResponse:
    auth_pw = get_secret("auth_password")
    secret = get_secret("secret_key")

    # Auth disabled — any (or no) password works
    if not auth_pw:
        return LoginResponse(token="no-auth", mode="none")

    expected = compute_token(auth_pw, secret)
    given = compute_token(req.password, secret)

    try:
        valid = hmac.compare_digest(given, expected)
    except (TypeError, ValueError):
        valid = False

    if not valid:
        raise HTTPException(status_code=401, detail="Invalid password")

    return LoginResponse(token=expected, mode="password")


# ── OIDC flows ──────────────────────────────────────────────────────────────

@router.get("/auth/oidc/providers")
async def oidc_providers():
    """List enabled OIDC providers."""
    return {
        "providers": [
            {"name": p.name, "display_name": p.display_name}
            for p in get_enabled_providers()
        ]
    }


@router.get("/auth/oidc/{provider_name}/authorize")
async def oidc_authorize(provider_name: str, request: Request):
    """Redirect the user to the IdP's authorization page."""
    provider = get_provider_by_name(provider_name)
    if not provider:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_name}' not configured")

    state = secrets.token_urlsafe(32)
    _oauth_states[state] = provider_name

    # Build callback URL from the current request
    callback_url = str(request.url_for("oidc_callback", provider_name=provider_name))

    params = {
        "client_id": provider.client_id,
        "redirect_uri": callback_url,
        "response_type": "code",
        "scope": provider.scopes,
        "state": state,
    }
    # Google-specific: request offline access for refresh token
    if provider_name == "google":
        params["access_type"] = "offline"
        params["prompt"] = "select_account"

    authorize_url = f"{provider.authorize_url}?{urlencode(params)}"
    return RedirectResponse(url=authorize_url)


@router.get("/auth/oidc/{provider_name}/callback")
async def oidc_callback(provider_name: str, request: Request, code: str = "", state: str = "", error: str = ""):
    """Handle the IdP callback after user authorization."""
    if error:
        logger.warning("OIDC callback error from %s: %s", provider_name, error)
        return RedirectResponse(url=f"/?auth_error={error}")

    # Validate state
    expected_provider = _oauth_states.pop(state, None)
    if not expected_provider or expected_provider != provider_name:
        logger.warning("Invalid OIDC state for %s", provider_name)
        return RedirectResponse(url="/?auth_error=invalid_state")

    provider = get_provider_by_name(provider_name)
    if not provider:
        return RedirectResponse(url="/?auth_error=provider_not_found")

    try:
        callback_url = str(request.url_for("oidc_callback", provider_name=provider_name))

        # Exchange code for tokens
        token_data = await exchange_code(provider, code, callback_url)
        access_token = token_data.get("access_token", "")
        if not access_token:
            logger.error("No access_token in OIDC response from %s", provider_name)
            return RedirectResponse(url="/?auth_error=no_token")

        # Fetch user info
        userinfo = await fetch_userinfo(provider, access_token)
        email = userinfo.get("email", "")
        name = userinfo.get("name", "") or userinfo.get("login", "")

        if not email:
            logger.warning("No email returned from %s for user", provider_name)
            return RedirectResponse(url="/?auth_error=no_email")

        # Check access control
        if not check_access(provider, email):
            logger.warning("Access denied for %s via %s", email, provider_name)
            return RedirectResponse(url="/?auth_error=access_denied")

        # Create session JWT
        session_token = create_session_token(
            email=email,
            name=name,
            provider=provider_name,
        )

        # Redirect to frontend with token
        return RedirectResponse(url=f"/?token={session_token}&provider={provider_name}")

    except Exception as e:
        logger.error("OIDC callback failed for %s: %s", provider_name, e, exc_info=True)
        return RedirectResponse(url=f"/?auth_error=callback_failed")


# ── Session info ────────────────────────────────────────────────────────────

@router.get("/auth/me")
async def auth_me(request: Request):
    """Return current user info from session JWT."""
    auth_header = request.headers.get("Authorization", "")
    token = auth_header.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=401, detail="No token provided")

    claims = verify_session_token(token)
    if claims:
        return {
            "authenticated": True,
            "mode": "oidc",
            "email": claims.get("sub", ""),
            "name": claims.get("name", ""),
            "provider": claims.get("provider", ""),
        }

    # Could be a legacy password token — still valid
    auth_pw = get_secret("auth_password")
    secret = get_secret("secret_key")
    if auth_pw and secret:
        expected = compute_token(auth_pw, secret)
        try:
            if hmac.compare_digest(token, expected):
                return {
                    "authenticated": True,
                    "mode": "password",
                    "email": "",
                    "name": "Admin",
                    "provider": "password",
                }
        except (TypeError, ValueError):
            pass

    if token == "no-auth":
        return {
            "authenticated": True,
            "mode": "none",
            "email": "",
            "name": "",
            "provider": "",
        }

    raise HTTPException(status_code=401, detail="Invalid token")
