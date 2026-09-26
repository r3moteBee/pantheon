"""Outbound-fetch guard (SSRF).

Agent tools and source adapters fetch URLs chosen by the model, and the
model can be steered by ingested content. Without a guard those fetches can
reach this machine's own API, LAN services, or cloud metadata
(169.254.169.254). ``safe_http_get`` resolves every hop — including
redirects — and refuses non-public addresses unless ALLOW_PRIVATE_FETCH is
set.
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import httpx

_MAX_REDIRECTS = 5
_DEFAULT_UA = "Pantheon/1.0"


class UnsafeURLError(ValueError):
    """URL scheme or destination address is not allowed."""


def _private_fetch_allowed() -> bool:
    from config import get_settings
    return bool(getattr(get_settings(), "allow_private_fetch", False))


def _is_public_ip(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


async def check_public_url(url: str) -> None:
    """Raise UnsafeURLError unless ``url`` is http(s) and every address its
    host resolves to is public."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise UnsafeURLError(f"Only http(s) URLs are allowed: {url!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError(f"URL has no host: {url!r}")
    if _private_fetch_allowed():
        return
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as e:
        raise UnsafeURLError(f"Cannot resolve {host!r}: {e}") from e
    for info in infos:
        ip = info[4][0]
        if not _is_public_ip(ip):
            raise UnsafeURLError(
                f"Refusing to fetch {host!r}: resolves to non-public address {ip} "
                f"(set ALLOW_PRIVATE_FETCH=true to allow)"
            )


async def safe_http_get(
    url: str,
    *,
    timeout: float = 60.0,
    headers: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> httpx.Response:
    """GET with SSRF checks on the URL and on every redirect hop."""
    hdrs = {"User-Agent": _DEFAULT_UA, **(headers or {})}
    own = client is None
    client = client or httpx.AsyncClient(timeout=timeout, follow_redirects=False)
    try:
        current = url
        for _ in range(_MAX_REDIRECTS + 1):
            await check_public_url(current)
            resp = await client.get(current, headers=hdrs, follow_redirects=False)
            if resp.is_redirect and resp.headers.get("location"):
                current = urljoin(str(resp.url), resp.headers["location"])
                continue
            return resp
        raise httpx.TooManyRedirects(f"More than {_MAX_REDIRECTS} redirects", request=resp.request)
    finally:
        if own:
            await client.aclose()
