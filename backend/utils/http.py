"""Shared, connection-pooled httpx client.

Creating an ``httpx.AsyncClient`` per request (the old pattern for every
LLM call, embedding, rerank and MCP request) pays a TCP + TLS handshake
each time. One client per event loop keeps connections alive.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import httpx

_CLIENTS: dict[int, tuple[asyncio.AbstractEventLoop, httpx.AsyncClient]] = {}
_LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=60)


def shared_client() -> httpx.AsyncClient:
    """The pooled client for the running event loop (created on demand)."""
    loop = asyncio.get_running_loop()
    entry = _CLIENTS.get(id(loop))
    if entry is None or entry[0] is not loop or entry[1].is_closed:
        # Drop clients whose loop has gone away (tests create many loops).
        for key, (lp, _c) in list(_CLIENTS.items()):
            if lp.is_closed():
                _CLIENTS.pop(key, None)
        client = httpx.AsyncClient(limits=_LIMITS, timeout=60.0)
        _CLIENTS[id(loop)] = (loop, client)
        return client
    return entry[1]


class _TimeoutClient:
    """Thin view over the shared client that applies a default timeout."""

    def __init__(self, client: httpx.AsyncClient, timeout: float | None):
        self._client = client
        self._timeout = timeout

    def _kw(self, kw: dict[str, Any]) -> dict[str, Any]:
        if self._timeout is not None:
            kw.setdefault("timeout", self._timeout)
        return kw

    async def get(self, *a: Any, **kw: Any) -> httpx.Response:
        return await self._client.get(*a, **self._kw(kw))

    async def post(self, *a: Any, **kw: Any) -> httpx.Response:
        return await self._client.post(*a, **self._kw(kw))

    def stream(self, *a: Any, **kw: Any):
        return self._client.stream(*a, **self._kw(kw))


@asynccontextmanager
async def pooled_client(timeout: float | None = None) -> AsyncIterator[_TimeoutClient]:
    """Drop-in for ``async with httpx.AsyncClient(timeout=...) as client``
    that reuses pooled connections instead of closing them."""
    yield _TimeoutClient(shared_client(), timeout)


async def close_shared_clients() -> None:
    """Close the pooled client(s); call on app shutdown."""
    for _key, (_loop, client) in list(_CLIENTS.items()):
        try:
            await client.aclose()
        except Exception:
            pass
    _CLIENTS.clear()
