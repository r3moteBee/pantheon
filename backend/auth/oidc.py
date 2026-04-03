"""OIDC/OAuth2 authentication module.

Supports configurable identity providers (Google, GitHub, any
OIDC-compliant IdP).  Provider credentials are stored in the
encrypted vault under keys like ``oidc_<provider>_client_id``.

Flow:
  1. Frontend calls GET /api/auth/oidc/providers → list of enabled providers
  2. Frontend redirects user to GET /api/auth/oidc/<provider>/authorize
  3. IdP redirects back to GET /api/auth/oidc/<provider>/callback
  4. Backend validates the token, creates a session JWT, redirects to frontend

Session tokens are signed JWTs (HS256) with the vault ``secret_key``.
"""
from __future__ import annotations
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from authlib.jose import jwt as jose_jwt
from authlib.jose.errors import JoseError

from config import get_secret

logger = logging.getLogger(__name__)

# JWT lifetime: 7 days
JWT_LIFETIME = 7 * 24 * 60 * 60

# ── Well-known OIDC provider configs ────────────────────────────────────────
# Users can also configure a custom provider by setting oidc_custom_* vault keys.
WELL_KNOWN_PROVIDERS: dict[str, dict[str, str]] = {
    "google": {
        "display_name": "Google",
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "userinfo_url": "https://openidconnect.googleapis.com/v1/userinfo",
        "scopes": "openid email profile",
    },
    "github": {
        "display_name": "GitHub",
        "authorize_url": "https://github.com/login/oauth/authorize",
        "token_url": "https://github.com/login/oauth/access_token",
        "userinfo_url": "https://api.github.com/user",
        "scopes": "read:user user:email",
    },
}


@dataclass
class OIDCProvider:
    """Resolved OIDC provider with credentials."""
    name: str
    display_name: str
    client_id: str
    client_secret: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scopes: str
    allowed_emails: list[str] = field(default_factory=list)
    allowed_domains: list[str] = field(default_factory=list)


def get_enabled_providers() -> list[OIDCProvider]:
    """Return all OIDC providers that have client_id + client_secret configured."""
    providers: list[OIDCProvider] = []

    # Check well-known providers
    for name, info in WELL_KNOWN_PROVIDERS.items():
        client_id = get_secret(f"oidc_{name}_client_id")
        client_secret = get_secret(f"oidc_{name}_client_secret")
        if client_id and client_secret:
            allowed_emails = _parse_list(get_secret(f"oidc_{name}_allowed_emails"))
            allowed_domains = _parse_list(get_secret(f"oidc_{name}_allowed_domains"))
            providers.append(OIDCProvider(
                name=name,
                display_name=info["display_name"],
                client_id=client_id,
                client_secret=client_secret,
                authorize_url=info["authorize_url"],
                token_url=info["token_url"],
                userinfo_url=info["userinfo_url"],
                scopes=info["scopes"],
                allowed_emails=allowed_emails,
                allowed_domains=allowed_domains,
            ))

    # Check custom OIDC provider
    custom_id = get_secret("oidc_custom_client_id")
    custom_secret = get_secret("oidc_custom_client_secret")
    custom_authorize = get_secret("oidc_custom_authorize_url")
    custom_token = get_secret("oidc_custom_token_url")
    if custom_id and custom_secret and custom_authorize and custom_token:
        providers.append(OIDCProvider(
            name="custom",
            display_name=get_secret("oidc_custom_display_name") or "SSO",
            client_id=custom_id,
            client_secret=custom_secret,
            authorize_url=custom_authorize,
            token_url=custom_token,
            userinfo_url=get_secret("oidc_custom_userinfo_url") or "",
            scopes=get_secret("oidc_custom_scopes") or "openid email profile",
            allowed_emails=_parse_list(get_secret("oidc_custom_allowed_emails")),
            allowed_domains=_parse_list(get_secret("oidc_custom_allowed_domains")),
        ))

    return providers


def get_provider_by_name(name: str) -> OIDCProvider | None:
    """Look up a single enabled provider by name."""
    for p in get_enabled_providers():
        if p.name == name:
            return p
    return None


# ── Token exchange ──────────────────────────────────────────────────────────

async def exchange_code(provider: OIDCProvider, code: str, redirect_uri: str) -> dict[str, Any]:
    """Exchange an authorization code for tokens at the provider's token endpoint."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        headers = {"Accept": "application/json"}
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": provider.client_id,
            "client_secret": provider.client_secret,
        }
        resp = await client.post(provider.token_url, data=data, headers=headers)
        resp.raise_for_status()
        return resp.json()


async def fetch_userinfo(provider: OIDCProvider, access_token: str) -> dict[str, Any]:
    """Fetch user profile from the provider's userinfo endpoint."""
    if not provider.userinfo_url:
        return {}
    async with httpx.AsyncClient(timeout=10.0) as client:
        headers = {"Authorization": f"Bearer {access_token}"}
        resp = await client.get(provider.userinfo_url, headers=headers)
        resp.raise_for_status()
        user = resp.json()

    # GitHub: email may not be in profile — fetch from /user/emails
    if provider.name == "github" and not user.get("email"):
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    "https://api.github.com/user/emails",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                resp.raise_for_status()
                emails = resp.json()
                primary = next((e for e in emails if e.get("primary")), None)
                if primary:
                    user["email"] = primary["email"]
        except Exception as e:
            logger.warning("Failed to fetch GitHub email: %s", e)

    return user


def check_access(provider: OIDCProvider, email: str) -> bool:
    """Check if the user's email is allowed by the provider's access policy.

    If no allowed_emails and no allowed_domains are configured, all
    authenticated users are allowed (open access).
    """
    if not provider.allowed_emails and not provider.allowed_domains:
        return True  # No restrictions configured

    email_lower = email.lower()

    if email_lower in [e.lower() for e in provider.allowed_emails]:
        return True

    domain = email_lower.split("@", 1)[-1] if "@" in email_lower else ""
    if domain in [d.lower() for d in provider.allowed_domains]:
        return True

    return False


# ── Session JWT ─────────────────────────────────────────────────────────────

def create_session_token(
    email: str,
    name: str = "",
    provider: str = "",
    extra: dict[str, Any] | None = None,
) -> str:
    """Create a signed JWT session token."""
    secret = get_secret("secret_key")
    if not secret:
        raise RuntimeError("secret_key not configured — cannot sign session tokens")

    now = int(time.time())
    payload = {
        "sub": email,
        "name": name or email,
        "provider": provider,
        "iat": now,
        "exp": now + JWT_LIFETIME,
    }
    if extra:
        payload.update(extra)

    header = {"alg": "HS256"}
    return jose_jwt.encode(header, payload, secret.encode()).decode("utf-8")


def verify_session_token(token: str) -> dict[str, Any] | None:
    """Verify and decode a session JWT. Returns claims or None."""
    secret = get_secret("secret_key")
    if not secret:
        return None

    try:
        claims = jose_jwt.decode(token, secret.encode())
        claims.validate()
        return dict(claims)
    except JoseError as e:
        logger.debug("JWT validation failed: %s", e)
        return None
    except Exception as e:
        logger.debug("JWT decode error: %s", e)
        return None


# ── Helpers ─────────────────────────────────────────────────────────────────

def _parse_list(value: str) -> list[str]:
    """Parse a comma-separated string into a list of stripped, non-empty items."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]
