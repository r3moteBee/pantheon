"""MCP protocol client using Streamable HTTP transport.

Dual-era client (spec 2026-07-28 "Backward Compatibility"):

* **Modern** servers (revision 2026-07-28+) are stateless: no handshake, no
  session. Every request carries ``_meta`` (protocol version, client info,
  client capabilities) plus the mirrored headers ``MCP-Protocol-Version``,
  ``Mcp-Method``, ``Mcp-Name`` and ``Mcp-Param-*`` (from ``x-mcp-header``
  tool annotations). Results carry ``resultType``.
* **Legacy** servers (2025-11-25 and earlier) keep the ``initialize`` →
  ``notifications/initialized`` handshake, ``Mcp-Session-Id`` and the
  negotiated ``MCP-Protocol-Version`` header.

The era is detected once per client with a modern ``server/discover``: a
DiscoverResult or a recognised modern error (-32020..-32022) means modern;
anything else (a bare 4xx, a legacy JSON-RPC error such as "method not
found" or "not initialized") means legacy, and the client falls back to
``initialize``. The probe result is cached for the client's lifetime.

Lifecycle used by the manager: ``initialize()`` (= make ready in whichever
era) → ``discover_tools()`` → ``call_tool()``. Optional OAuth 2.1 bearer
tokens come from a ``token_getter`` callback.

Elicitation (MRTR ``input_required`` results carrying ``elicitation/create``
requests) is answered through the ``elicit`` callback a caller passes to
``call_tool`` (``mcp_client/elicitation.py``); the capability is declared
only on calls that have one. The tasks extension (``mcp_client/tasks.py``)
is always declared: a ``resultType: "task"`` answer is polled to its end
inside ``call_tool`` (progress via ``notify``, cancel via ``cancel_event``,
task-side input through the same elicitor). Not implemented yet:
``subscriptions/listen`` (task notifications are polled instead), resources
and prompts, and legacy (2025-11-25) server-initiated elicitation / tasks.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable


from mcp_client import elicitation as elicit_mod
from mcp_client import schema as schema_mod
from mcp_client import tasks as tasks_mod
from utils.http import pooled_client
from utils.progress import report_progress

logger = logging.getLogger(__name__)

# Modern revisions this client speaks, newest first. The first entry is what
# we ask for; a server that answers UnsupportedProtocolVersion gets a retry
# with the newest version both sides list.
MODERN_PROTOCOL_VERSIONS: tuple[str, ...] = ("2026-07-28",)
# The handshake revision we fall back to for legacy servers.
LEGACY_PROTOCOL_VERSION = "2025-11-25"
# Kept for callers/tests that reference the single constant.
MCP_PROTOCOL_VERSION = MODERN_PROTOCOL_VERSIONS[0]

CLIENT_INFO = {"name": "pantheon", "version": "1.0.0"}
# Declared on every modern request. The tasks extension
# (`extensions: {"io.modelcontextprotocol/tasks": {}}`) is always declared:
# polling is in-process, so any caller can wait on a task. `elicitation`
# (form + url) is added only when the request can be answered by a person -
# server/discover, tools/list and a tools/call made with an `elicit`
# callback; a tools/call from a bot or a background job omits it, so the
# server MUST NOT ask (spec: never rely on undeclared capabilities).
CLIENT_CAPABILITIES: dict[str, Any] = {"extensions": {tasks_mod.TASKS_EXTENSION: {}}}

META_VERSION = "io.modelcontextprotocol/protocolVersion"
META_CLIENT_INFO = "io.modelcontextprotocol/clientInfo"
META_CLIENT_CAPS = "io.modelcontextprotocol/clientCapabilities"
META_SERVER_INFO = "io.modelcontextprotocol/serverInfo"

# JSON-RPC error codes the 2026-07-28 spec reserves; receiving one proves the
# server is modern.
ERR_HEADER_MISMATCH = -32020
ERR_MISSING_CAPABILITY = -32021
ERR_UNSUPPORTED_VERSION = -32022
MODERN_ERROR_CODES = frozenset({ERR_HEADER_MISMATCH, ERR_MISSING_CAPABILITY, ERR_UNSUPPORTED_VERSION})
ERR_METHOD_NOT_FOUND = -32601

ERA_MODERN = "modern"
ERA_LEGACY = "legacy"

# Retry settings for rate-limited requests
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0  # seconds, doubles each retry


def tool_function_name(connection: str, tool: str) -> str:
    """The name the LLM sees for an MCP tool: ``mcp_<connection>_<tool>``.
    OpenAI caps function names at 64 chars; a longer name is cut to 55 and
    suffixed with a short hash of the full name, so it stays unique and
    MCPManager.resolve_tool_call can map it back."""
    full = f"mcp_{connection}_{tool}"
    if len(full) <= 64:
        return full
    import hashlib
    return f"{full[:55]}_{hashlib.sha1(full.encode()).hexdigest()[:8]}"


class _LegacyServer(Exception):
    """Raised inside the probe when the server turns out to be legacy."""


class MCPClient:
    """Client for a single remote MCP server connection."""

    def __init__(
        self,
        name: str,
        url: str,
        api_key: str = "",
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        request_interval_ms: int = 1000,
        token_getter: Callable[..., Awaitable[str | None]] | None = None,
    ) -> None:
        self.name = name
        self.url = url  # Preserve URL as-is (trailing slash matters for some servers)
        self.api_key = api_key
        self.extra_headers = headers or {}
        self.timeout = timeout
        # When set, called for every request to obtain a Bearer token. The
        # callback receives one kw-arg `force_refresh: bool` so the client
        # can force a refresh after a 401. Returning None falls back to the
        # static api_key (if any).
        self._token_getter = token_getter
        self._cached_bearer: str | None = None

        # Era + version. `era` is None until the probe ran; a client used
        # before that (tests poke `_initialized`/`session_id` directly)
        # behaves as legacy, which is what it always did.
        self.era: str | None = None
        self.protocol_version: str | None = None
        self.supported_versions: list[str] = []
        self.instructions: str = ""

        self.session_id: str | None = None
        self.negotiated_protocol_version: str | None = None
        self.server_info: dict[str, Any] = {}
        self.server_capabilities: dict[str, Any] = {}
        # Extension identifiers a modern server advertised (e.g. tasks).
        self.server_extensions: list[str] = []
        self.tools: list[dict[str, Any]] = []
        # tools/list freshness (modern: `ttlMs` on the result; 0 = no hint).
        self.tools_ttl_ms: int = 0
        self._tools_fetched_at: float = 0.0
        # Per-tool `x-mcp-header` mirroring: tool name → [(header part, path)].
        self._header_params: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        self._initialized = False
        self._request_id = 0
        self._last_request_time: float = 0.0
        self._min_request_interval: float = request_interval_ms / 1000.0

    # ── Small helpers ────────────────────────────────────────────────────

    @property
    def is_modern(self) -> bool:
        return self.era == ERA_MODERN

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _meta(self, extra: dict[str, Any] | None = None, *, elicitation: bool = False) -> dict[str, Any]:
        """The per-request protocol fields every modern request must carry."""
        # Caller-supplied keys (progressToken, trace context) are kept; the
        # protocol fields always reflect the client's current state.
        meta = dict(extra or {})
        meta[META_VERSION] = self.protocol_version or MODERN_PROTOCOL_VERSIONS[0]
        meta[META_CLIENT_INFO] = dict(CLIENT_INFO)
        caps = json.loads(json.dumps(CLIENT_CAPABILITIES))
        if elicitation:
            caps["elicitation"] = json.loads(json.dumps(elicit_mod.ELICITATION_CAPABILITY))
        meta[META_CLIENT_CAPS] = caps
        return meta

    def _build_headers(
        self,
        method: str | None = None,
        *,
        tool_name: str | None = None,
        param_headers: dict[str, str] | None = None,
        modern: bool | None = None,
    ) -> dict[str, str]:
        """Build request headers for MCP Streamable HTTP transport.

        Bearer token resolution order:
          1. OAuth access token from cache (refreshed via _resolve_bearer)
          2. Static api_key
        Only one Authorization header is sent.

        Modern requests mirror the JSON-RPC method and tool name into
        ``Mcp-Method`` / ``Mcp-Name`` (plus ``Mcp-Param-*``) so gateways can
        route without parsing the body; legacy requests carry the session id
        and the negotiated version instead.
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        bearer = self._cached_bearer or (self.api_key or None)
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        modern = self.is_modern if modern is None else modern
        if modern:
            headers["MCP-Protocol-Version"] = self.protocol_version or MODERN_PROTOCOL_VERSIONS[0]
            if method:
                headers["Mcp-Method"] = method
            if tool_name is not None:
                headers["Mcp-Name"] = schema_mod.encode_header_value(tool_name)
            if param_headers:
                headers.update(param_headers)
        else:
            # Session tracking
            if self.session_id:
                headers["Mcp-Session-Id"] = self.session_id
            # Required on every post-initialize request since spec 2025-06-18.
            # Strict servers reject requests missing this header.
            if self.negotiated_protocol_version:
                headers["MCP-Protocol-Version"] = self.negotiated_protocol_version
        # Extra headers (e.g., DEFAULT_PARAMETERS for Tavily)
        headers.update(self.extra_headers)
        return headers

    async def _resolve_bearer(self, *, force_refresh: bool = False) -> None:
        """Populate self._cached_bearer from the token_getter (if any).

        On a forced refresh the rejected token is passed along so the getter
        can skip refreshing when another request already rotated it.
        """
        if not self._token_getter:
            return
        try:
            if force_refresh:
                token = await self._token_getter(
                    force_refresh=True, failed_token=self._cached_bearer,
                )
            else:
                token = await self._token_getter(force_refresh=False)
        except Exception as e:
            logger.warning("MCP '%s' token_getter raised: %s", self.name, e)
            token = None
        self._cached_bearer = token or None

    def _build_url(self) -> str:
        """Build the endpoint URL, appending API key for services that need it."""
        url = self.url
        # Tavily-style: API key in query parameter
        if self.api_key and "tavily.com" in url and "tavilyApiKey" not in url:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}tavilyApiKey={self.api_key}"
        return url

    async def _throttle(self) -> None:
        """Enforce minimum interval between requests to avoid rate limits."""
        now = time.monotonic()
        elapsed = now - self._last_request_time
        if elapsed < self._min_request_interval:
            wait = self._min_request_interval - elapsed
            logger.debug("MCP '%s' throttling: waiting %.1fs", self.name, wait)
            await asyncio.sleep(wait)
        self._last_request_time = time.monotonic()

    @staticmethod
    def _jsonrpc_error_in(resp: Any) -> dict[str, Any] | None:
        """The JSON-RPC ``error`` object in a response body, if there is one
        (plain JSON or an SSE body). Used to tell a modern server's 400 from
        a legacy server's."""
        text = resp.text or ""
        try:
            data = json.loads(text)
            if isinstance(data, dict) and isinstance(data.get("error"), dict):
                return data["error"]
            return None
        except (json.JSONDecodeError, TypeError):
            pass
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict) and isinstance(data.get("error"), dict):
                    return data["error"]
        return None

    # ── Transport ────────────────────────────────────────────────────────

    async def _send_jsonrpc(
        self,
        method: str,
        params: dict | None = None,
        *,
        retry_on_429: bool = False,
        tool_name: str | None = None,
        param_headers: dict[str, str] | None = None,
        probe: bool = False,
        elicitation: bool = False,
    ) -> dict[str, Any]:
        """Send a JSON-RPC 2.0 request and return the result.

        If retry_on_429 is True, retries with exponential backoff on HTTP 429.
        ``probe=True`` sends a modern request whatever the era and raises
        ``_LegacyServer`` instead of failing when the reply is not a modern
        one (the era detection). ``elicitation=True`` declares the
        elicitation capability in ``_meta`` (modern only).
        """
        modern = self.is_modern or probe
        request_id = self._next_id()
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        params = dict(params or {})
        if modern:
            params["_meta"] = self._meta(params.get("_meta"), elicitation=elicitation)
        if params:
            payload["params"] = params

        url = self._build_url()
        # Pre-emptively resolve OAuth bearer (no-op if no token_getter).
        await self._resolve_bearer()
        headers = self._build_headers(method, tool_name=tool_name, param_headers=param_headers, modern=modern)
        # Track 401 refresh-and-retry — only attempt once per request.
        oauth_retried = False
        # Track 404-on-stale-session re-initialize — once per request (legacy).
        session_retried = False
        # Track UnsupportedProtocolVersion → retry with a listed version — once.
        version_retried = False

        # Debug logging — mask API key in URL and headers
        safe_url = url
        if self.api_key and self.api_key in safe_url:
            safe_url = safe_url.replace(self.api_key, "***")
        safe_headers = {
            k: ("***" if k.lower() in ("authorization", "x-api-key", "api-key") else v)
            for k, v in headers.items()
        }
        logger.debug(
            "MCP '%s' → %s %s | headers=%s | payload.method=%s",
            self.name, "POST", safe_url, safe_headers, method,
        )
        if method == "tools/call" and params:
            logger.info(
                "MCP '%s' tool_call detail: tool=%s args=%s",
                self.name,
                params.get("name", "?"),
                json.dumps(params.get("arguments", {}), default=str)[:500],
            )

        max_attempts = MAX_RETRIES if retry_on_429 else 1
        # Give OAuth-enabled clients one extra attempt for the 401 refresh.
        if self._token_getter is not None:
            max_attempts += 1
        # And one for re-initializing an expired session (404) or a version retry.
        max_attempts += 1

        for attempt in range(max_attempts):
            await self._throttle()

            async with pooled_client(timeout=self.timeout) as client:
                resp = await client.post(url, json=payload, headers=headers, follow_redirects=True)

                # Log response details
                logger.info(
                    "MCP '%s' ← %d %s | content-type=%s | session=%s | body=%s",
                    self.name,
                    resp.status_code,
                    method,
                    resp.headers.get("content-type", "?"),
                    resp.headers.get("mcp-session-id", "none"),
                    resp.text[:500] if resp.status_code != 200 or method == "tools/call" else "(ok)",
                )

                # Log redirect history if any
                if resp.history:
                    chain = " → ".join(
                        f"{r.status_code} {r.headers.get('location', '?')}"
                        for r in resp.history
                    )
                    logger.warning("MCP '%s' redirect chain: %s → %d (final)", self.name, chain, resp.status_code)

                # Handle HTTP 401 — force-refresh OAuth token and retry once.
                # Only fires if we have a token_getter; api_key-only clients
                # treat 401 as a hard error.
                if (
                    resp.status_code == 401
                    and self._token_getter is not None
                    and not oauth_retried
                ):
                    oauth_retried = True
                    logger.info(
                        "MCP '%s' got 401 — forcing token refresh and retrying",
                        self.name,
                    )
                    await self._resolve_bearer(force_refresh=True)
                    headers = self._build_headers(method, tool_name=tool_name, param_headers=param_headers, modern=modern)
                    continue

                # Legacy spec: a 404 on a request carrying Mcp-Session-Id means
                # the session is gone (e.g. server restarted). Start a new one
                # and retry, instead of failing every call until a manual
                # reconnect.
                if (
                    not modern
                    and resp.status_code == 404
                    and self.session_id
                    and method != "initialize"
                    and not session_retried
                ):
                    session_retried = True
                    logger.info("MCP '%s' session expired (404) — re-initializing", self.name)
                    self.session_id = None
                    self._initialized = False
                    await self._legacy_initialize()
                    headers = self._build_headers(method, tool_name=tool_name, param_headers=param_headers, modern=modern)
                    continue

                # Handle HTTP-level 429
                if resp.status_code == 429 and retry_on_429 and attempt < max_attempts - 1:
                    delay = RETRY_BASE_DELAY * (2 ** attempt)
                    retry_after = resp.headers.get("retry-after")
                    if retry_after and retry_after.isdigit():
                        delay = max(delay, float(retry_after))
                    logger.warning(
                        "MCP '%s' rate limited (HTTP 429), retrying in %.1fs (attempt %d/%d)",
                        self.name, delay, attempt + 1, max_attempts,
                    )
                    await asyncio.sleep(delay)
                    continue

                # Modern servers answer version / header / capability problems
                # and unknown methods with a 4xx that carries a JSON-RPC error.
                if modern and 400 <= resp.status_code < 500:
                    err = self._jsonrpc_error_in(resp)
                    code = err.get("code") if err else None
                    if code == ERR_UNSUPPORTED_VERSION and not version_retried:
                        version_retried = True
                        data = err.get("data") if isinstance(err.get("data"), dict) else {}
                        supported = [v for v in (data.get("supported") or []) if isinstance(v, str)]
                        self.supported_versions = supported
                        pick = next((v for v in MODERN_PROTOCOL_VERSIONS if v in supported), None)
                        if pick:
                            logger.info("MCP '%s' server wants protocol %s (asked %s) — retrying",
                                        self.name, pick, self.protocol_version)
                            self.protocol_version = pick
                            params["_meta"] = self._meta(params.get("_meta"), elicitation=elicitation)
                            payload["params"] = params
                            headers = self._build_headers(method, tool_name=tool_name,
                                                          param_headers=param_headers, modern=True)
                            continue
                        if probe:
                            # The server names only revisions we do not speak
                            # as modern; a legacy one among them means the
                            # handshake path still works.
                            if any(v <= LEGACY_PROTOCOL_VERSION for v in supported):
                                raise _LegacyServer(f"server supports {supported}")
                        raise MCPError(
                            f"server supports MCP {supported or 'unknown'}, this client speaks "
                            f"{list(MODERN_PROTOCOL_VERSIONS)} (+ legacy {LEGACY_PROTOCOL_VERSION})",
                            code=ERR_UNSUPPORTED_VERSION,
                        )
                    if code in MODERN_ERROR_CODES:
                        raise MCPError(err.get("message", "modern server rejected the request"), code=code)
                    if resp.status_code == 404 and code == ERR_METHOD_NOT_FOUND and not probe:
                        raise MCPError(err.get("message", f"method {method} not found"), code=code)
                    if probe:
                        raise _LegacyServer(f"HTTP {resp.status_code} without a modern error body")

                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")

                report_progress()  # a response arrived — not hung

                if not modern:
                    # Track session ID from server (legacy only; a modern
                    # server never mints one and we never echo one).
                    new_session = resp.headers.get("mcp-session-id")
                    if new_session:
                        self.session_id = new_session

                if "text/event-stream" in content_type:
                    try:
                        result = self._parse_sse_response(resp.text, request_id)
                    except MCPError as e:
                        if probe and e.code not in MODERN_ERROR_CODES:
                            raise _LegacyServer(str(e)) from e
                        raise
                else:
                    data = resp.json()
                    if "error" in data:
                        code = data["error"].get("code", -1)
                        if probe and code not in MODERN_ERROR_CODES:
                            raise _LegacyServer(data["error"].get("message", "legacy error"))
                        raise MCPError(
                            data["error"].get("message", "Unknown MCP error"),
                            code=code,
                        )
                    result = data.get("result", {})
                if modern and isinstance(result, dict):
                    info = (result.get("_meta") or {}).get(META_SERVER_INFO)
                    if isinstance(info, dict) and info:
                        self.server_info = info
                return result

        # Should not reach here, but just in case
        raise MCPError("Max retries exceeded", code=429)

    def _parse_sse_response(self, body: str, request_id: int) -> dict[str, Any]:
        """Parse an SSE response body and extract the JSON-RPC result."""
        result: dict[str, Any] = {}
        for line in body.split("\n"):
            line = line.strip()
            if line.startswith("data: "):
                data_str = line[6:]
                try:
                    data = json.loads(data_str)
                    # Match by request ID
                    if data.get("id") == request_id:
                        if "error" in data:
                            raise MCPError(
                                data["error"].get("message", "Unknown MCP error"),
                                code=data["error"].get("code", -1),
                            )
                        result = data.get("result", {})
                except json.JSONDecodeError:
                    continue
        return result

    # ── MCP Lifecycle ────────────────────────────────────────────────────

    async def initialize(self) -> dict[str, Any]:
        """Make the client ready in whichever era the server speaks.

        First use probes with a modern ``server/discover``; a legacy server
        gets the ``initialize`` handshake instead. Returns the discover (or
        initialize) result.
        """
        if self.era is None:
            return await self._probe()
        if self.is_modern:
            return {"protocolVersion": self.protocol_version, "capabilities": self.server_capabilities}
        if not self._initialized:
            return await self._legacy_initialize()
        return {"protocolVersion": self.negotiated_protocol_version, "capabilities": self.server_capabilities}

    async def _probe(self) -> dict[str, Any]:
        """Era detection: modern ``server/discover`` first, ``initialize`` on fallback."""
        self.protocol_version = MODERN_PROTOCOL_VERSIONS[0]
        try:
            result = await self._send_jsonrpc("server/discover", probe=True, elicitation=True)
        except _LegacyServer as why:
            logger.info("MCP '%s' is a legacy (handshake) server: %s", self.name, why)
            self.era = ERA_LEGACY
            self.protocol_version = None
            return await self._legacy_initialize()
        self.era = ERA_MODERN
        self.session_id = None
        self._initialized = True
        self.supported_versions = [v for v in (result.get("supportedVersions") or []) if isinstance(v, str)]
        self.server_capabilities = result.get("capabilities", {}) or {}
        self.instructions = str(result.get("instructions") or "")
        exts = self.server_capabilities.get("extensions")
        self.server_extensions = sorted(k for k in exts) if isinstance(exts, dict) else []
        info = (result.get("_meta") or {}).get(META_SERVER_INFO)
        if isinstance(info, dict):
            self.server_info = info
        logger.info(
            "MCP connection '%s' discovered (modern): server=%s, version=%s, supports=%s",
            self.name, self.server_info.get("name", "unknown"), self.protocol_version,
            self.supported_versions or "?",
        )
        return result

    async def _legacy_initialize(self) -> dict[str, Any]:
        """Send the MCP initialize handshake (revisions up to 2025-11-25)."""
        self.era = ERA_LEGACY
        result = await self._send_jsonrpc("initialize", {
            "protocolVersion": LEGACY_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": dict(CLIENT_INFO),
        })

        self.server_info = result.get("serverInfo", {})
        self.server_capabilities = result.get("capabilities", {})
        self.instructions = str(result.get("instructions") or "")
        # Server echoes back the version it actually accepted — that's what
        # we must send on every subsequent request, not our requested version.
        self.negotiated_protocol_version = (
            result.get("protocolVersion") or LEGACY_PROTOCOL_VERSION
        )
        self.protocol_version = self.negotiated_protocol_version
        self._initialized = True

        logger.info(
            "MCP connection '%s' initialized: server=%s, version=%s",
            self.name,
            self.server_info.get("name", "unknown"),
            result.get("protocolVersion", "unknown"),
        )

        # Send initialized notification (no response expected)
        try:
            await self._send_notification("notifications/initialized")
        except Exception:
            pass  # Some servers don't require this

        return result

    async def _send_notification(self, method: str, params: dict | None = None) -> None:
        """Send a JSON-RPC notification (no id, no response expected)."""
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params:
            payload["params"] = params

        url = self._build_url()
        headers = self._build_headers(method)

        async with pooled_client(timeout=self.timeout) as client:
            try:
                await client.post(url, json=payload, headers=headers, follow_redirects=True)
            except Exception:
                pass  # Notifications are fire-and-forget

    async def _ensure_ready(self) -> None:
        if self.era is None or (not self.is_modern and not self._initialized):
            await self.initialize()

    async def discover_tools(self) -> list[dict[str, Any]]:
        """Call tools/list and cache the results.

        Modern servers may annotate parameters with ``x-mcp-header``; a tool
        whose annotations are invalid is dropped (spec: MUST be excluded) and
        logged. The result's ``ttlMs`` becomes the list's freshness hint.
        """
        await self._ensure_ready()

        result = await self._send_jsonrpc("tools/list", elicitation=True)
        tools = [t for t in result.get("tools", []) if isinstance(t, dict)]
        header_params: dict[str, list[tuple[str, tuple[str, ...]]]] = {}
        kept: list[dict[str, Any]] = []
        for tool in tools:
            name = str(tool.get("name", ""))
            try:
                params = schema_mod.header_params(tool)
            except schema_mod.InvalidToolDefinition as e:
                logger.warning("MCP '%s' dropped tool %r: %s", self.name, name, e)
                continue
            if params:
                header_params[name] = params
            kept.append(tool)
        self.tools = kept
        self._header_params = header_params
        ttl = result.get("ttlMs") if self.is_modern else None
        self.tools_ttl_ms = int(ttl) if isinstance(ttl, (int, float)) and ttl > 0 else 0
        self._tools_fetched_at = time.monotonic()

        logger.info(
            "MCP '%s' discovered %d tools: %s",
            self.name,
            len(self.tools),
            [t.get("name") for t in self.tools],
        )
        return self.tools

    @property
    def tools_stale(self) -> bool:
        """True when the server's ``ttlMs`` for tools/list has lapsed."""
        if not self.tools_ttl_ms or not self._tools_fetched_at:
            return False
        return time.monotonic() - self._tools_fetched_at > self.tools_ttl_ms / 1000.0

    async def maybe_refresh_tools(self) -> bool:
        """Re-list tools when the freshness hint lapsed; keeps the old list on failure."""
        if not self.tools_stale:
            return False
        try:
            await self.discover_tools()
            return True
        except Exception as e:
            logger.warning("MCP '%s' tools/list refresh failed (keeping %d tools): %s",
                           self.name, len(self.tools), e)
            self._tools_fetched_at = time.monotonic()
            return False

    async def call_tool(
        self, tool_name: str, arguments: dict[str, Any] | None = None,
        *, elicit: elicit_mod.Elicitor | None = None,
        notify: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        cancel_event: asyncio.Event | None = None,
        max_wait_s: float | None = None,
    ) -> dict[str, Any]:
        """Call a tool on the remote MCP server.

        Returns:
            {"text": str, "structured": dict | list | None, "is_error": bool}

        - "text" — concatenated text blocks for display / LLM context
        - "structured" — typed result from spec 2025-06-18+ `structuredContent`
          field. None if the server didn't provide one (most servers today).
        - "is_error" — true if server returned isError=true on the tool result

        Retries on HTTP 429 with exponential backoff. Also detects rate-limit
        errors embedded in tool result content and retries those too.

        Modern results: ``resultType`` absent or "complete" is the ordinary
        case. "input_required" (MRTR) with only a ``requestState`` is retried
        with the state echoed; one that carries ``inputRequests`` (an
        elicitation) is put to the user through ``elicit`` and the call is
        retried with their ``inputResponses`` (up to MAX_INPUT_ROUNDS times;
        the elicitation capability is declared on the call only when
        ``elicit`` is given). Without ``elicit`` it is reported to the model
        as an error naming what the server asked. "task" (the tasks
        extension) is polled in ``_run_task`` until it is terminal: progress
        goes to ``notify``, ``cancel_event`` sends ``tasks/cancel``, and a
        task that needs input is answered through ``elicit`` + ``tasks/update``.
        Any other value is invalid.
        """
        await self._ensure_ready()
        if self.is_modern:
            await self.maybe_refresh_tools()
        arguments = arguments or {}

        last: dict[str, Any] = {"text": "", "structured": None, "is_error": False}
        header_retried = False
        state_retries = 0
        input_rounds = 0
        request_state: str | None = None
        input_responses: dict[str, Any] | None = None
        answered: list[str] = []   # model-facing notes on what the user answered
        attempt = 0
        while attempt < MAX_RETRIES:
            params: dict[str, Any] = {"name": tool_name, "arguments": arguments}
            if request_state is not None:
                params["requestState"] = request_state
            if input_responses is not None:
                params["inputResponses"] = input_responses
            param_headers = None
            if self.is_modern and self._header_params.get(tool_name):
                param_headers = schema_mod.mirrored_headers(self._header_params[tool_name], arguments)
            try:
                result = await self._send_jsonrpc(
                    "tools/call", params, retry_on_429=True,
                    tool_name=tool_name if self.is_modern else None,
                    param_headers=param_headers,
                    elicitation=elicit is not None,
                )
            except MCPError as e:
                # The server's tool definition changed under us (new or
                # different x-mcp-header annotations): re-list and retry once.
                if e.code == ERR_HEADER_MISMATCH and not header_retried:
                    header_retried = True
                    logger.info("MCP '%s' HeaderMismatch on %s — re-listing tools and retrying",
                                self.name, tool_name)
                    await self.discover_tools()
                    continue
                raise

            rtype = result.get("resultType") if isinstance(result, dict) else None
            if rtype == "input_required":
                requests = result.get("inputRequests") or {}
                # Echo exactly what the server sent (absent stays absent); the
                # last round's answers never travel with a new request.
                request_state = result.get("requestState")
                input_responses = None
                if not requests:
                    if request_state is None or state_retries >= 1:
                        return {"text": (f"[MCP server '{self.name}' answered '{tool_name}' with "
                                         "input_required but asked for nothing; giving up.]"),
                                "structured": None, "is_error": True}
                    state_retries += 1
                    continue
                try:
                    parsed = elicit_mod.parse_requests(requests)
                except elicit_mod.UnsupportedInputRequest as why:
                    return {"text": (f"[MCP server '{self.name}' needs input this client cannot provide "
                                     f"before '{tool_name}' can run: {why}.]"),
                            "structured": None, "is_error": True}
                if elicit is None:
                    return {
                        "text": _describe_input_required(self.name, tool_name, parsed),
                        "structured": None, "is_error": True,
                    }
                input_rounds += 1
                if input_rounds > elicit_mod.MAX_INPUT_ROUNDS:
                    return {"text": (f"[MCP server '{self.name}' kept asking for more input on '{tool_name}' "
                                     f"({input_rounds - 1} rounds); stopped. It last asked to "
                                     f"{elicit_mod.describe_requests(parsed)}.]"),
                            "structured": None, "is_error": True}
                prompt = elicit_mod.ElicitationPrompt(connection=self.name, tool=tool_name,
                                                      requests=parsed, round=input_rounds)
                logger.info("MCP '%s' elicitation round %d for %s: %s", self.name, input_rounds,
                            tool_name, elicit_mod.describe_requests(parsed))
                responses = await elicit(prompt)
                if responses is None:
                    responses = elicit_mod.cancel_all(prompt)
                answered.append(elicit_mod.summarize_answers(prompt, responses))
                input_responses = responses
                state_retries = 0
                continue
            if answered:
                # The call ran after the user answered the server's
                # questions: say so, so the model doesn't re-ask or guess.
                result = dict(result)
                note = f"[The user {'; then '.join(answered)}.]"
                result["content"] = [{"type": "text", "text": note}] + list(result.get("content") or [])
                answered = []
            if rtype == "task":
                outcome = await self._run_task(tool_name, result, elicit=elicit, notify=notify,
                                               cancel_event=cancel_event, max_wait_s=max_wait_s)
                if isinstance(outcome, str):
                    note = (f"[The user {'; then '.join(answered)}.] " if answered else "")
                    return {"text": note + outcome, "structured": None, "is_error": True}
                result, task_notes = outcome
                answered.extend(task_notes)
                rtype = result.get("resultType") if isinstance(result, dict) else None
                if answered:
                    result = dict(result)
                    result["content"] = [{"type": "text", "text": f"[The user {'; then '.join(answered)}.]"}] \
                        + list(result.get("content") or [])
                    answered = []
            if rtype not in (None, "complete"):
                raise MCPError(f"unknown resultType {rtype!r} from server", code=-32600)

            content_blocks = result.get("content", [])
            texts = []
            for block in content_blocks:
                if isinstance(block, dict):
                    if block.get("type") == "text":
                        texts.append(block.get("text", ""))
                    elif block.get("type") == "image":
                        texts.append(f"[Image: {block.get('mimeType', 'image')}]")
                    else:
                        texts.append(str(block))
                else:
                    texts.append(str(block))

            text_result = "\n".join(texts) if texts else ""
            structured = result.get("structuredContent")
            is_error = bool(result.get("isError", False))

            # Fall back to raw envelope string if neither text nor structured present
            if not text_result and structured is None:
                shown = {k: v for k, v in result.items() if k not in ("resultType", "_meta")}
                text_result = str(shown)

            last = {"text": text_result, "structured": structured, "is_error": is_error}

            # Check for rate-limit errors embedded in result content
            if self._is_rate_limited_result(text_result) and attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "MCP '%s' tool '%s' returned rate-limit error in content, "
                    "retrying in %.1fs (attempt %d/%d)",
                    self.name, tool_name, delay, attempt + 1, MAX_RETRIES,
                )
                await asyncio.sleep(delay)
                attempt += 1
                continue

            return last

        return last

    async def _run_task(
        self, tool_name: str, created: dict[str, Any], *,
        elicit: elicit_mod.Elicitor | None,
        notify: Callable[[dict[str, Any]], Awaitable[None]] | None,
        cancel_event: asyncio.Event | None,
        max_wait_s: float | None,
    ) -> tuple[dict[str, Any], list[str]] | str:
        """Drive a CreateTaskResult to its end.

        Returns (the task's CallToolResult, notes on input the user gave) on
        ``completed``; a model-facing error string when the task was
        cancelled (by the user, by us, or by the server), expired, or kept
        asking; raises MCPError on ``failed`` or a broken task object.
        """
        try:
            task = tasks_mod.parse_task(created)
        except tasks_mod.InvalidTask as why:
            raise MCPError(f"invalid task from server: {why}", code=-32600)
        task_id = task["taskId"]
        if max_wait_s is None:
            try:
                from config import get_settings
                max_wait_s = float(get_settings().mcp_task_max_wait_seconds)
            except Exception:
                max_wait_s = tasks_mod.DEFAULT_MAX_WAIT_S
        started = time.monotonic()
        answered_keys: set[str] = set()
        notes: list[str] = []
        polls = 0
        logger.info("MCP '%s' tool %s runs as task %s (ttl=%s, poll=%s ms)", self.name, tool_name,
                    task_id, task.get("ttlMs"), task.get("pollIntervalMs"))

        async def _tell(extra: dict[str, Any] | None = None) -> None:
            if notify is None:
                return
            try:
                await notify({**tasks_mod.public_status(task, elapsed_s=time.monotonic() - started), **(extra or {})})
            except Exception:
                logger.debug("task notify failed", exc_info=True)

        async def _cancel(reason: str) -> None:
            try:
                await self._send_jsonrpc("tasks/cancel", {"taskId": task_id}, tool_name=task_id)
            except Exception as e:
                logger.warning("MCP '%s' tasks/cancel %s failed: %s", self.name, task_id, e)
            logger.info("MCP '%s' task %s cancelled: %s", self.name, task_id, reason)

        while True:
            status = task["status"]
            if status == tasks_mod.STATUS_COMPLETED:
                await _tell()
                return dict(task["result"]), notes
            if status == tasks_mod.STATUS_FAILED:
                err = task["error"]
                await _tell()
                raise MCPError(str(err.get("message") or "task failed"), code=int(err.get("code", -32603)))
            if status == tasks_mod.STATUS_CANCELLED:
                await _tell()
                return (f"[MCP server '{self.name}' cancelled the background task for '{tool_name}'"
                        f"{': ' + str(task.get('statusMessage')) if task.get('statusMessage') else ''}.]")
            if cancel_event is not None and cancel_event.is_set():
                await _cancel("user")
                await _tell({"state": "cancelled"})
                return f"[The user cancelled the background task for '{tool_name}'; it did not finish.]"
            if tasks_mod.expired(task):
                await _cancel("ttl elapsed")
                return f"[MCP server '{self.name}' let the background task for '{tool_name}' expire before it finished.]"
            if time.monotonic() - started > max_wait_s:
                await _cancel("max wait")
                return (f"[Gave up waiting on the background task for '{tool_name}' after "
                        f"{int(max_wait_s)} s and cancelled it.]")
            if status == tasks_mod.STATUS_INPUT_REQUIRED:
                fresh = {k: v for k, v in task["inputRequests"].items() if k not in answered_keys}
                if fresh:
                    try:
                        parsed = elicit_mod.parse_requests(fresh)
                    except elicit_mod.UnsupportedInputRequest as why:
                        await _cancel(f"unsupported input request: {why}")
                        return (f"[MCP server '{self.name}' needed input this client cannot provide for the "
                                f"background task of '{tool_name}' ({why}); the task was cancelled.]")
                    if elicit is None:
                        await _cancel("no way to ask the user")
                        return (f"[MCP server '{self.name}' needed user input for the background task of "
                                f"'{tool_name}' - it asked to {elicit_mod.describe_requests(parsed)}. Forms can't "
                                "be shown here, so the task was cancelled; it works from the web chat.]")
                    await _tell()
                    prompt = elicit_mod.ElicitationPrompt(connection=self.name, tool=tool_name, requests=parsed,
                                                          round=len(answered_keys) + 1)
                    responses = await elicit(prompt)
                    if responses is None:
                        responses = elicit_mod.cancel_all(prompt)
                    notes.append(elicit_mod.summarize_answers(prompt, responses))
                    answered_keys.update(fresh.keys())
                    await self._send_jsonrpc("tasks/update", {"taskId": task_id, "inputResponses": responses},
                                             tool_name=task_id)
            else:
                await _tell()
            delay = tasks_mod.poll_delay(task)
            if cancel_event is not None:
                try:
                    await asyncio.wait_for(cancel_event.wait(), timeout=delay)
                except asyncio.TimeoutError:
                    pass
            else:
                await asyncio.sleep(delay)
            polls += 1
            got = await self._send_jsonrpc("tasks/get", {"taskId": task_id}, retry_on_429=True, tool_name=task_id)
            try:
                task = tasks_mod.parse_task(got)
            except tasks_mod.InvalidTask as why:
                raise MCPError(f"invalid tasks/get reply: {why}", code=-32600)
            if task["taskId"] != task_id:
                raise MCPError(f"tasks/get answered for task {task['taskId']}, asked {task_id}", code=-32600)

    @staticmethod
    def _is_rate_limited_result(text: str) -> bool:
        """Detect rate-limit errors embedded in tool result text."""
        # Many APIs return JSON with status 429 inside the text content
        try:
            data = json.loads(text)
            if isinstance(data, dict):
                status = data.get("status")
                if status == 429 or status == "429":
                    return True
                detail = data.get("detail", {})
                if isinstance(detail, dict) and ("429" in str(detail.get("status", "")) or "rate" in str(detail.get("error", "")).lower()):
                    return True
        except (json.JSONDecodeError, TypeError):
            pass
        # Also check for common rate-limit phrases in plain text
        lower = text.lower()
        if '"status":429' in lower or '"status": 429' in lower:
            return True
        if "excessive requests" in lower and "blocked" in lower:
            return True
        return False

    async def test_connection(self) -> dict[str, Any]:
        """Test the connection by making it ready and listing tools."""
        try:
            await self.initialize()
            tools = await self.discover_tools()
            return {
                "status": "ok",
                "server": self.server_info,
                "protocol_version": self.protocol_version,
                "protocol_era": self.era,
                "extensions": list(self.server_extensions),
                "tools_count": len(tools),
                "tool_names": [t.get("name") for t in tools],
            }
        except Exception as e:
            return {
                "status": "error",
                "message": str(e),
            }

    def get_openai_tool_schemas(self, excluded_tools: set[str] | None = None) -> list[dict[str, Any]]:
        """Convert MCP tool schemas to OpenAI function-calling format.

        MCP tools use `inputSchema` (JSON Schema); OpenAI uses
        `{"type": "function", "function": {"name", "description", "parameters"}}`.
        We prefix tool names with `mcp_{connection_name}_` to avoid collisions.
        Tools in `excluded_tools` (original MCP names) are skipped. The schema
        is simplified for the model (local $ref inlined, bookkeeping removed).
        """
        excluded = excluded_tools or set()
        schemas = []
        for tool in self.tools:
            if tool.get("name", "") in excluded:
                continue
            mcp_name = tool.get("name", "")
            prefixed_name = tool_function_name(self.name, mcp_name)

            schema = {
                "type": "function",
                "function": {
                    "name": prefixed_name,
                    "description": tool.get("description", ""),
                    "parameters": schema_mod.simplify_for_llm(
                        tool.get("inputSchema", {"type": "object", "properties": {}})),
                },
            }
            schemas.append(schema)
        return schemas

    @property
    def is_connected(self) -> bool:
        return self._initialized and len(self.tools) > 0


def _describe_input_required(connection: str, tool: str, requests: list[elicit_mod.ElicitRequest]) -> str:
    """The model-facing text for an elicitation that reached a caller with no
    way to ask the user (a bot channel, a background job, the REST API).
    Names what the server asked, so the model can relay it, without
    pretending the call ran."""
    return (f"[MCP server '{connection}' needs user input before '{tool}' can run — it asked to "
            f"{elicit_mod.describe_requests(requests)}. Forms can't be shown here; tell the user what the "
            "server asked for and that it works from the web chat.]")


class MCPError(Exception):
    """Error from an MCP server."""
    def __init__(self, message: str, code: int = -1):
        self.code = code
        super().__init__(f"MCP error ({code}): {message}")
