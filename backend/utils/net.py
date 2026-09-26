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


async def check_public_url(url: str) -> list[str]:
    """Raise UnsafeURLError unless ``url`` is http(s) and every address its
    host resolves to is public. Returns the validated addresses ([] when
    ALLOW_PRIVATE_FETCH skips the check)."""
    parsed = urlparse(url)
    if parsed.scheme.lower() not in ("http", "https"):
        raise UnsafeURLError(f"Only http(s) URLs are allowed: {url!r}")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError(f"URL has no host: {url!r}")
    if _private_fetch_allowed():
        return []
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(
            host, parsed.port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except socket.gaierror as e:
        raise UnsafeURLError(f"Cannot resolve {host!r}: {e}") from e
    ips: list[str] = []
    for info in infos:
        ip = info[4][0]
        if not _is_public_ip(ip):
            raise UnsafeURLError(
                f"Refusing to fetch {host!r}: resolves to non-public address {ip} "
                f"(set ALLOW_PRIVATE_FETCH=true to allow)"
            )
        if ip not in ips:
            ips.append(ip)
    return ips


def _proxied(url: str) -> bool:
    import urllib.request
    scheme = urlparse(url).scheme.lower()
    host = urlparse(url).hostname or ""
    proxies = urllib.request.getproxies()
    if not (proxies.get(scheme) or proxies.get("all")):
        return False
    return not urllib.request.proxy_bypass(host)


def _pinned_request_args(url: str, ip: str) -> tuple[str, dict[str, str], dict]:
    """Rewrite ``url`` to connect to the already-validated ``ip``.

    Closes the DNS-rebinding gap between check and connect (a second
    lookup could return 127.0.0.1). The original host is kept in the Host
    header and as the TLS SNI / certificate-verification name, so HTTPS
    still validates against the real hostname.
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    ip_host = f"[{ip}]" if ":" in ip else ip
    netloc = ip_host + (f":{parsed.port}" if parsed.port else "")
    pinned = parsed._replace(netloc=netloc).geturl()
    host_header = host + (f":{parsed.port}" if parsed.port else "")
    ext = {"sni_hostname": host} if parsed.scheme.lower() == "https" else {}
    return pinned, {"Host": host_header}, ext


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
            ips = await check_public_url(current)
            # Behind an HTTP(S) proxy the proxy does the DNS lookup, so
            # there's nothing to pin (and many proxies refuse IP targets).
            if ips and not _proxied(current):
                target, extra_headers, ext = _pinned_request_args(current, ips[0])
                resp = await client.get(
                    target, headers={**hdrs, **extra_headers},
                    follow_redirects=False, extensions=ext,
                )
            else:  # ALLOW_PRIVATE_FETCH — plain request
                resp = await client.get(current, headers=hdrs, follow_redirects=False)
            if resp.is_redirect and resp.headers.get("location"):
                # Join against the hostname URL, not the pinned IP URL.
                current = urljoin(current, resp.headers["location"])
                continue
            return resp
        raise httpx.TooManyRedirects(f"More than {_MAX_REDIRECTS} redirects", request=resp.request)
    finally:
        if own:
            await client.aclose()
