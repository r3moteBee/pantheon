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


import httpx

from mcp_client import elicitation as elicit_mod
from mcp_client import schema as schema_mod
from mcp_client import task_store
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

# Characters of resource / prompt text handed to the model at most.
MAX_RESOURCE_CHARS = 60_000
# Per-connection confirmation policy for tools that change data (tool annotations).
CONFIRM_DESTRUCTIVE = "destructive"
CONFIRM_WRITES = "writes"
CONFIRM_OFF = "off"
CONFIRM_POLICIES = (CONFIRM_DESTRUCTIVE, CONFIRM_WRITES, CONFIRM_OFF)
# Synthetic tools that only read (never need approval). Prompts are
# user-chosen commands (mcp_client/prompts.py), not model tools.
SYNTHETIC_READS = frozenset({"read_resource", "list_resources"})
# subscriptions/listen reconnect backoff (seconds).
LISTEN_RETRY_MIN_S = 5.0
LISTEN_RETRY_MAX_S = 300.0

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
        # OAuth step-up (spec 2026-07-28 "Scope Challenge Handling"): called
        # with the scopes a 403 insufficient_scope challenge asked for; the
        # manager records them and flags the connection for re-authorization.
        self.on_insufficient_scope: Callable[[list[str], str | None], None] | None = None
        # Legacy (2025-11-25) servers elicit mid-call on the tools/call
        # response stream; notes on what the user answered collect here.
        self._legacy_notes: list[str] = []
        self._legacy_tool: str | None = None

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
        # Resources and prompts the server lists (served to the model as the
        # synthetic tools in SYNTHETIC_TOOLS when the capability is there).
        self.resources: list[dict[str, Any]] = []
        self.resource_templates: list[dict[str, Any]] = []
        self.prompts: list[dict[str, Any]] = []
        # subscriptions/listen stream for list_changed notifications (modern).
        self._listener: asyncio.Task | None = None
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
        elicit: elicit_mod.Elicitor | None = None,
        on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    ) -> dict[str, Any]:
        """Send a JSON-RPC 2.0 request and return the result.

        If retry_on_429 is True, retries with exponential backoff on HTTP 429.
        ``probe=True`` sends a modern request whatever the era and raises
        ``_LegacyServer`` instead of failing when the reply is not a modern
        one (the era detection). ``elicitation=True`` declares the
        elicitation capability in ``_meta`` (modern only). ``elicit`` answers
        a legacy server's mid-call ``elicitation/create`` on the response
        stream (legacy tools/call only).
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
                streamed: dict[str, Any] | None = None
                if method == "tools/call":
                    # Read the response as it arrives: progress notifications
                    # (both eras) and, on a legacy server, its server->client
                    # requests (elicitation/create, ping) precede the result.
                    async with client.stream("POST", url, json=payload, headers=headers,
                                             follow_redirects=True) as sresp:
                        if sresp.status_code == 200 and "text/event-stream" in sresp.headers.get("content-type", ""):
                            streamed = await self._read_call_stream(sresp, request_id, elicit, client, url,
                                                                    modern=modern, on_progress=on_progress)
                        else:
                            await sresp.aread()
                        resp = sresp
                else:
                    resp = await client.post(url, json=payload, headers=headers, follow_redirects=True)

                # Log response details
                logger.info(
                    "MCP '%s' ← %d %s | content-type=%s | session=%s | body=%s",
                    self.name,
                    resp.status_code,
                    method,
                    resp.headers.get("content-type", "?"),
                    resp.headers.get("mcp-session-id", "none"),
                    self._body_preview(resp) if resp.status_code != 200 or method == "tools/call" else "(ok)",
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

                # Insufficient scope (403 + WWW-Authenticate error="insufficient_scope"):
                # the token is valid but too narrow. Record the scopes the
                # server wants so the next Authorize asks for them, and tell
                # the caller (spec: step-up needs the user, so no silent retry).
                if resp.status_code == 403 and self._token_getter is not None:
                    from mcp_client import oauth as oauth_mod
                    ch = oauth_mod.parse_bearer_challenge(resp.headers.get("WWW-Authenticate", ""))
                    if ch.error == "insufficient_scope":
                        if self.on_insufficient_scope is not None:
                            try:
                                self.on_insufficient_scope(ch.scopes, ch.error_description)
                            except Exception:
                                logger.debug("insufficient_scope hook failed", exc_info=True)
                        raise MCPError(
                            f"the server needs more permissions than the current token grants"
                            f"{' (' + ' '.join(ch.scopes) + ')' if ch.scopes else ''}"
                            f"{': ' + ch.error_description if ch.error_description else ''}. "
                            "Re-authorize the connection in Connections → MCP servers.", code=403)

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

                if streamed is not None:
                    if "error" in streamed:
                        raise MCPError(streamed["error"].get("message", "Unknown MCP error"),
                                       code=streamed["error"].get("code", -1))
                    result = streamed.get("result", {})
                elif "text/event-stream" in content_type:
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

    @staticmethod
    def _body_preview(resp: Any) -> str:
        try:
            return resp.text[:500]
        except Exception:
            return "(streamed)"

    async def _read_call_stream(self, resp: Any, request_id: int, elicit: elicit_mod.Elicitor | None,
                                client: Any, url: str, *, modern: bool = False,
                                on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None) -> dict[str, Any]:
        """Read a tools/call SSE response as it arrives - progress
        notifications go to ``on_progress``; a legacy server's requests on it
        (``elicitation/create`` through ``elicit``, ``ping``) are answered -
        until the message that answers ``request_id``."""
        buf: list[str] = []
        async for raw in resp.aiter_lines():
            line = raw.rstrip("\r\n")
            if line.startswith("data:"):
                buf.append(line[5:].lstrip())
                continue
            if line != "":
                continue   # event:/id:/retry: fields and comments
            data = "\n".join(buf)
            buf = []
            if not data:
                continue
            try:
                msg = json.loads(data)
            except json.JSONDecodeError:
                continue
            report_progress()
            done = await self._stream_message(msg, request_id, elicit, client, url, modern=modern,
                                              on_progress=on_progress)
            if done is not None:
                return done
        raise MCPError("the server closed the stream before answering the tool call", code=-32000)

    async def _stream_message(self, msg: Any, request_id: int, elicit: elicit_mod.Elicitor | None,
                              client: Any, url: str, *, modern: bool = False,
                              on_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None) -> dict[str, Any] | None:
        if not isinstance(msg, dict):
            return None
        if msg.get("id") == request_id and ("result" in msg or "error" in msg):
            return msg
        method = msg.get("method")
        if not method:
            return None
        if "id" not in msg:
            if method == "notifications/progress" and on_progress is not None:
                try:
                    await on_progress(msg.get("params") or {})
                except Exception:
                    logger.debug("progress hook failed", exc_info=True)
            return None   # notifications (progress, logging)
        if modern:
            return None   # no back-channel in the stateless era; servers must use MRTR
        reply: dict[str, Any] = {"jsonrpc": "2.0", "id": msg["id"]}
        if method == "ping":
            reply["result"] = {}
        elif method == "elicitation/create":
            reply["result"] = await self._answer_legacy_elicitation(msg.get("params") or {}, elicit)
        else:
            reply["error"] = {"code": ERR_METHOD_NOT_FOUND, "message": f"{method} is not supported by this client"}
        try:
            await client.post(url, json=reply, headers=self._build_headers(None, modern=False),
                              follow_redirects=True)
        except Exception as e:
            logger.warning("MCP '%s' could not answer the server's %s request: %s", self.name, method, e)
        return None

    async def _answer_legacy_elicitation(self, params: dict[str, Any], elicit: elicit_mod.Elicitor | None) -> dict[str, Any]:
        """One legacy elicitation/create: show it like a modern one and return
        the ElicitResult; cancel when there is nobody to ask."""
        try:
            parsed = elicit_mod.parse_requests({"elicitation": {"method": "elicitation/create", "params": params}})
        except elicit_mod.UnsupportedInputRequest as why:
            logger.info("MCP '%s' legacy elicitation refused: %s", self.name, why)
            self._legacy_notes.append(f"could not be shown the server's request ({why}); it was dismissed")
            return {"action": "cancel"}
        what = elicit_mod.describe_requests(parsed)
        if elicit is None:
            self._legacy_notes.append(f"could not be asked to {what} (forms can't be shown here; it was dismissed)")
            return {"action": "cancel"}
        prompt = elicit_mod.ElicitationPrompt(connection=self.name, tool=self._legacy_tool or "?",
                                              requests=parsed, round=len(self._legacy_notes) + 1)
        logger.info("MCP '%s' legacy elicitation for %s: %s", self.name, prompt.tool, what)
        responses = await elicit(prompt)
        if responses is None:
            responses = elicit_mod.cancel_all(prompt)
        self._legacy_notes.append(elicit_mod.summarize_answers(prompt, responses))
        return responses.get("elicitation") or {"action": "cancel"}

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
            # Session-wide (not per request): a form or link the server asks
            # for mid-call is shown when the call came from the web chat and
            # cancelled otherwise.
            "capabilities": {"elicitation": json.loads(json.dumps(elicit_mod.ELICITATION_CAPABILITY))},
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

        result = await self._list_pages("tools/list", "tools", elicitation=True)
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
        for fn in (self.discover_resources, self.discover_prompts):
            try:
                await fn()
            except Exception as e:
                logger.warning("MCP '%s' %s failed: %s", self.name, fn.__name__, e)
        return self.tools

    @property
    def has_resources(self) -> bool:
        return isinstance(self.server_capabilities.get("resources"), dict)

    @property
    def has_prompts(self) -> bool:
        return isinstance(self.server_capabilities.get("prompts"), dict)

    async def discover_resources(self) -> list[dict[str, Any]]:
        """resources/list + resources/templates/list when the server has the capability."""
        if not self.has_resources:
            self.resources, self.resource_templates = [], []
            return []
        res = await self._list_pages("resources/list", "resources", elicitation=True)
        self.resources = [r for r in res.get("resources", []) if isinstance(r, dict) and r.get("uri")]
        try:
            tpl = await self._list_pages("resources/templates/list", "resourceTemplates", elicitation=True)
            self.resource_templates = [t for t in tpl.get("resourceTemplates", [])
                                       if isinstance(t, dict) and t.get("uriTemplate")]
        except MCPError as e:
            logger.debug("MCP '%s' has no resource templates: %s", self.name, e)
            self.resource_templates = []
        logger.info("MCP '%s' lists %d resources, %d templates", self.name, len(self.resources),
                    len(self.resource_templates))
        return self.resources

    async def discover_prompts(self) -> list[dict[str, Any]]:
        """prompts/list when the server has the capability."""
        if not self.has_prompts:
            self.prompts = []
            return []
        res = await self._list_pages("prompts/list", "prompts", elicitation=True)
        self.prompts = [p for p in res.get("prompts", []) if isinstance(p, dict) and p.get("name")]
        logger.info("MCP '%s' lists %d prompts", self.name, len(self.prompts))
        return self.prompts

    # ── Tool annotations: human in the loop for writes ───────────────────

    def tool_definition(self, name: str) -> dict[str, Any] | None:
        return next((t for t in self.tools if t.get("name") == name), None)

    def tool_annotations(self, name: str) -> dict[str, Any]:
        t = self.tool_definition(name)
        ann = (t or {}).get("annotations")
        return ann if isinstance(ann, dict) else {}

    def needs_confirmation(self, name: str, policy: str = CONFIRM_DESTRUCTIVE) -> bool:
        """Whether the person should approve this call first.

        Policies: ``destructive`` (default) - the server marked the tool
        ``destructiveHint: true`` (and not read-only); ``writes`` - every
        tool not marked ``readOnlyHint: true``, annotated or not; ``off`` -
        never. Synthetic resource/prompt tools are reads and never ask.
        """
        if policy == CONFIRM_OFF or name in SYNTHETIC_READS:
            return False
        ann = self.tool_annotations(name)
        if ann.get("readOnlyHint") is True:
            return False
        if policy == CONFIRM_WRITES:
            return True
        return ann.get("destructiveHint") is True

    # ── Synthetic tools: resources and prompts for the model ─────────────

    def synthetic_tools(self) -> dict[str, dict[str, Any]]:
        """OpenAI-style function definitions (keyed by bare name) the model
        gets for this server's resources. A real tool of the same name
        wins. (Prompts are not here - see below.)"""
        real = {t.get("name") for t in self.tools}
        out: dict[str, dict[str, Any]] = {}
        if self.has_resources and (self.resources or self.resource_templates):
            lines = [f"- {r['uri']}" + (f" — {r.get('title') or r.get('name')}" if r.get('title') or r.get('name') else "")
                     + (f": {str(r.get('description'))[:120]}" if r.get("description") else "")
                     for r in self.resources[:25]]
            if len(self.resources) > 25:
                lines.append(f"- … {len(self.resources) - 25} more (call list_resources)")
            lines += [f"- template {t['uriTemplate']}" + (f" — {t.get('title') or t.get('name')}" if t.get('title') or t.get('name') else "")
                      for t in self.resource_templates[:10]]
            out["read_resource"] = {
                "description": (f"Read a resource (document, record, file) published by MCP server '{self.name}' "
                                "by URI. Available:\n" + "\n".join(lines)),
                "parameters": {"type": "object", "properties": {
                    "uri": {"type": "string", "description": "The resource URI (or a template with its variables filled in)"}},
                    "required": ["uri"]},
            }
            out["list_resources"] = {
                "description": f"List every resource and resource template MCP server '{self.name}' publishes, with descriptions.",
                "parameters": {"type": "object", "properties": {}},
            }
        # Prompts are user-controlled (spec): the person picks one from the
        # composer (/connection:prompt, mcp_client/prompts.py); the model gets
        # no tool for them. call_synthetic("get_prompt") stays for that path.
        return {k: v for k, v in out.items() if k not in real}

    async def call_synthetic(self, name: str, arguments: dict[str, Any] | None = None, *,
                             elicit: elicit_mod.Elicitor | None = None) -> dict[str, Any]:
        """Run one of synthetic_tools(); same result shape as call_tool."""
        await self._ensure_ready()
        arguments = arguments or {}
        if name == "list_resources":
            if not self.resources and not self.resource_templates:
                await self.discover_resources()
            lines = [f"{r['uri']}" + (f"  [{r['mimeType']}]" if r.get("mimeType") else "")
                     + (f"  {r.get('title') or r.get('name')}" if r.get('title') or r.get('name') else "")
                     + (f"\n    {r['description']}" if r.get("description") else "") for r in self.resources]
            lines += [f"template {t['uriTemplate']}" + (f"  {t.get('title') or t.get('name')}" if t.get('title') or t.get('name') else "")
                      + (f"\n    {t['description']}" if t.get("description") else "") for t in self.resource_templates]
            return {"text": "\n".join(lines) or "(no resources)", "structured": None, "is_error": False}
        if name == "list_prompts":
            if not self.prompts:
                await self.discover_prompts()
            lines = []
            for pr in self.prompts:
                args = "; ".join(f"{a.get('name')}{' (required)' if a.get('required') else ''}"
                                 + (f": {a['description']}" if a.get("description") else "")
                                 for a in (pr.get("arguments") or []) if isinstance(a, dict))
                lines.append(f"{pr['name']}" + (f" — {pr['description']}" if pr.get("description") else "")
                             + (f"\n    arguments: {args}" if args else ""))
            return {"text": "\n".join(lines) or "(no prompts)", "structured": None, "is_error": False}
        if name == "read_resource":
            uri = str(arguments.get("uri") or "").strip()
            if not uri:
                return {"text": "read_resource needs a uri", "structured": None, "is_error": True}
            res, notes = await self._request_with_input("resources/read", {"uri": uri}, elicit=elicit,
                                                        what=f"resource {uri}")
            parts = []
            total = 0
            for c in res.get("contents") or []:
                if not isinstance(c, dict):
                    continue
                head = f"=== {c.get('uri') or uri}" + (f" ({c['mimeType']})" if c.get("mimeType") else "") + " ==="
                if isinstance(c.get("text"), str):
                    body = c["text"]
                elif isinstance(c.get("blob"), str):
                    body = f"[binary content, {len(c['blob']) * 3 // 4} bytes base64-encoded; not shown]"
                else:
                    body = "(empty)"
                room = MAX_RESOURCE_CHARS - total
                if room <= 0:
                    parts.append("[… further contents omitted]")
                    break
                if len(body) > room:
                    body = body[:room] + f"\n[… truncated at {MAX_RESOURCE_CHARS} characters]"
                total += len(body)
                parts.append(f"{head}\n{body}")
            text = "\n\n".join(parts) if parts else f"(resource {uri} has no contents)"
            if notes:
                text = f"[The user {'; then '.join(notes)}.]\n" + text
            return {"text": text, "structured": None, "is_error": False}
        if name == "get_prompt":
            pname = str(arguments.get("name") or "").strip()
            if not pname:
                return {"text": "get_prompt needs a name", "structured": None, "is_error": True}
            args = arguments.get("arguments")
            params: dict[str, Any] = {"name": pname}
            if isinstance(args, dict) and args:
                params["arguments"] = {str(k): "" if v is None else str(v) for k, v in args.items()}
            res, notes = await self._request_with_input("prompts/get", params, elicit=elicit,
                                                        what=f"prompt {pname}")
            lines = []
            if res.get("description"):
                lines.append(f"({res['description']})")
            for m in res.get("messages") or []:
                if not isinstance(m, dict):
                    continue
                content = m.get("content")
                blocks = content if isinstance(content, list) else [content]
                texts = []
                for b in blocks:
                    if not isinstance(b, dict):
                        continue
                    t = b.get("type")
                    if t == "text":
                        texts.append(str(b.get("text", "")))
                    elif t == "resource" and isinstance(b.get("resource"), dict):
                        r = b["resource"]
                        texts.append(r.get("text") if isinstance(r.get("text"), str)
                                     else f"[embedded binary resource {r.get('uri', '')}]")
                    elif t == "resource_link":
                        texts.append(f"[resource {b.get('uri', '')}: read it with read_resource]")
                    else:
                        texts.append(f"[{t or 'content'} omitted]")
                lines.append(f"{m.get('role', 'user')}: " + "\n".join(texts))
            text = "\n\n".join(lines) or f"(prompt {pname} has no messages)"
            if len(text) > MAX_RESOURCE_CHARS:
                text = text[:MAX_RESOURCE_CHARS] + "\n[… truncated]"
            if notes:
                text = f"[The user {'; then '.join(notes)}.]\n" + text
            return {"text": text, "structured": None, "is_error": False}
        return {"text": f"Unknown synthetic tool {name}", "structured": None, "is_error": True}

    async def _request_with_input(self, method: str, params: dict[str, Any], *,
                                  elicit: elicit_mod.Elicitor | None, what: str) -> tuple[dict[str, Any], list[str]]:
        """A request that may answer input_required (resources/read,
        prompts/get): the MRTR rounds, like tools/call's, without the tool
        retry machinery. Returns (final result, notes on the user's answers)."""
        state: str | None = None
        responses: dict[str, Any] | None = None
        rounds = 0
        state_retries = 0
        notes: list[str] = []
        while True:
            p = dict(params)
            if state is not None:
                p["requestState"] = state
            if responses is not None:
                p["inputResponses"] = responses
            # Streamable HTTP header mirroring: Mcp-Name carries the resource
            # uri / prompt name the way it carries a tool name on tools/call.
            name_hdr = p.get("uri") if method == "resources/read" else p.get("name")
            res = await self._send_jsonrpc(method, p, retry_on_429=True, elicitation=elicit is not None,
                                           tool_name=str(name_hdr) if self.is_modern and name_hdr else None)
            if not isinstance(res, dict) or res.get("resultType") != "input_required":
                return (res if isinstance(res, dict) else {}), notes
            state = res.get("requestState")
            responses = None
            reqs = res.get("inputRequests") or {}
            if not reqs:
                if state is None or state_retries >= 1:
                    raise MCPError(f"the server asked for input for {what} but listed none", code=-32600)
                state_retries += 1
                continue
            try:
                parsed = elicit_mod.parse_requests(reqs)
            except elicit_mod.UnsupportedInputRequest as why:
                raise MCPError(f"the server needs input this client cannot provide for {what}: {why}", code=-32600)
            if elicit is None:
                raise MCPError(f"the server needs user input for {what}: it asked to "
                               f"{elicit_mod.describe_requests(parsed)} (works from the web chat)", code=-32600)
            rounds += 1
            if rounds > elicit_mod.MAX_INPUT_ROUNDS:
                raise MCPError(f"the server kept asking for input for {what}; stopped", code=-32600)
            prompt = elicit_mod.ElicitationPrompt(connection=self.name, tool=method, requests=parsed, round=rounds)
            got = await elicit(prompt)
            if got is None:
                got = elicit_mod.cancel_all(prompt)
            notes.append(elicit_mod.summarize_answers(prompt, got))
            responses = got
            state_retries = 0

    # ── Subscriptions (modern): list_changed and task notifications ───────

    def _listen_filter(self) -> dict[str, Any]:
        caps = self.server_capabilities or {}
        flt: dict[str, Any] = {}
        if isinstance(caps.get("tools"), dict) and caps["tools"].get("listChanged"):
            flt["toolsListChanged"] = True
        if isinstance(caps.get("prompts"), dict) and caps["prompts"].get("listChanged"):
            flt["promptsListChanged"] = True
        if isinstance(caps.get("resources"), dict) and caps["resources"].get("listChanged"):
            flt["resourcesListChanged"] = True
        return flt

    def start_listener(self) -> bool:
        """Open a subscriptions/listen stream for the list_changed
        notifications the server advertises (modern servers only). Idempotent;
        the stream reconnects with backoff and stops on close()."""
        if not self.is_modern or self._listener is not None:
            return False
        flt = self._listen_filter()
        if not flt:
            return False
        self._listener = asyncio.get_running_loop().create_task(self._listen_loop(flt), name=f"mcp-listen-{self.name}")
        return True

    async def close(self) -> None:
        """Stop background streams (the connection is being dropped)."""
        task, self._listener = self._listener, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass

    async def _listen_loop(self, flt: dict[str, Any]) -> None:
        delay = LISTEN_RETRY_MIN_S
        while True:
            try:
                ended = await self._listen_once(flt, self._on_notification)
                delay = LISTEN_RETRY_MIN_S if ended == "graceful" else min(delay * 2, LISTEN_RETRY_MAX_S)
            except asyncio.CancelledError:
                raise
            except MCPError as e:
                if e.code in (ERR_METHOD_NOT_FOUND, ERR_MISSING_CAPABILITY, 404, 405, 501):
                    logger.info("MCP '%s' has no subscriptions/listen (%s); polling only", self.name, e)
                    self._listener = None
                    return
                logger.warning("MCP '%s' subscription stream failed: %s", self.name, e)
                delay = min(delay * 2, LISTEN_RETRY_MAX_S)
            except Exception as e:
                logger.warning("MCP '%s' subscription stream dropped: %s", self.name, e)
                delay = min(delay * 2, LISTEN_RETRY_MAX_S)
            await asyncio.sleep(delay)

    async def _listen_once(self, flt: dict[str, Any], handler) -> str:
        """One subscriptions/listen stream until it ends. Returns "graceful"
        when the server answered the request (its way of closing), "closed"
        when the transport ended. Raises MCPError on a refusal."""
        request_id = self._next_id()
        payload = {"jsonrpc": "2.0", "id": request_id, "method": "subscriptions/listen",
                   "params": {"notifications": flt, "_meta": self._meta(elicitation=True)}}
        await self._resolve_bearer()
        headers = self._build_headers("subscriptions/listen", modern=True)
        async with pooled_client(timeout=None) as client:
            async with client.stream("POST", self._build_url(), json=payload, headers=headers, follow_redirects=True,
                                     timeout=httpx.Timeout(20.0, read=None)) as resp:
                if resp.status_code != 200:
                    await resp.aread()
                    err = self._jsonrpc_error_in(resp)
                    raise MCPError(err.get("message", f"HTTP {resp.status_code}") if err else f"HTTP {resp.status_code}",
                                   code=int(err.get("code", resp.status_code)) if err else resp.status_code)
                if "text/event-stream" not in resp.headers.get("content-type", ""):
                    await resp.aread()
                    err = self._jsonrpc_error_in(resp)
                    if err:
                        raise MCPError(err.get("message", "subscriptions/listen refused"), code=int(err.get("code", -1)))
                    return "graceful"
                buf: list[str] = []
                async for raw in resp.aiter_lines():
                    line = raw.rstrip("\r\n")
                    if line.startswith("data:"):
                        buf.append(line[5:].lstrip())
                        continue
                    if line != "":
                        continue
                    data = "\n".join(buf)
                    buf = []
                    if not data:
                        continue
                    try:
                        msg = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("id") == request_id:
                        if "error" in msg:
                            raise MCPError(msg["error"].get("message", "subscriptions/listen failed"),
                                           code=int(msg["error"].get("code", -1)))
                        return "graceful"
                    method = msg.get("method")
                    if method:
                        try:
                            await handler(method, msg.get("params") or {})
                        except Exception:
                            logger.debug("notification handler failed", exc_info=True)
        return "closed"

    async def _on_notification(self, method: str, params: dict[str, Any]) -> None:
        if method == "notifications/subscriptions/acknowledged":
            logger.info("MCP '%s' subscribed to %s", self.name, sorted((params.get("notifications") or {}).keys()))
        elif method == "notifications/tools/list_changed":
            logger.info("MCP '%s' tools changed - re-listing", self.name)
            await self.discover_tools()
        elif method == "notifications/prompts/list_changed":
            await self.discover_prompts()
        elif method == "notifications/resources/list_changed":
            await self.discover_resources()

    async def _watch_task(self, task_id: str, slot: dict[str, Any], wake: asyncio.Event) -> None:
        """Listen for notifications/tasks on one task so the poll loop wakes
        on a change instead of waiting out the interval. Best effort: a
        server without the stream just leaves polling in charge."""
        async def handler(method: str, params: dict[str, Any]) -> None:
            if method == "notifications/tasks" and params.get("taskId") == task_id:
                slot["task"] = {k: v for k, v in params.items() if k != "_meta"}
                wake.set()
        try:
            await self._listen_once({"taskIds": [task_id]}, handler)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.debug("MCP '%s' task notifications unavailable for %s: %s", self.name, task_id, e)

    @staticmethod
    async def _wait_any(delay: float, *events: asyncio.Event | None) -> None:
        """Sleep ``delay`` seconds or until one of ``events`` is set."""
        waits = [asyncio.ensure_future(e.wait()) for e in events if e is not None]
        if not waits:
            await asyncio.sleep(delay)
            return
        try:
            await asyncio.wait(waits, timeout=delay, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for w in waits:
                if not w.done():
                    w.cancel()

    async def _list_pages(self, method: str, key: str, *, elicitation: bool = False,
                          max_pages: int = 100) -> dict[str, Any]:
        """A paginated list (tools/list, resources/list, prompts/list, ...):
        follows ``nextCursor`` until the server stops sending one. Returns
        the first page's envelope (ttlMs, cacheScope) with ``key`` holding
        every item."""
        items: list[dict[str, Any]] = []
        first: dict[str, Any] | None = None
        cursor: str | None = None
        for _page in range(max_pages):
            params = {"cursor": cursor} if cursor else None
            res = await self._send_jsonrpc(method, params, elicitation=elicitation)
            if not isinstance(res, dict):
                break
            if first is None:
                first = res
            items.extend(i for i in (res.get(key) or []) if isinstance(i, dict))
            nxt = res.get("nextCursor")
            if not isinstance(nxt, str) or not nxt or nxt == cursor:
                break
            cursor = nxt
        else:
            logger.warning("MCP '%s' %s: stopped after %d pages", self.name, method, max_pages)
        merged = dict(first or {})
        merged[key] = items
        merged.pop("nextCursor", None)
        return merged

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
            if notify is not None:
                # Ask for notifications/progress on the response stream.
                params["_meta"] = {"progressToken": f"pt{self._request_id + 1}"}
            if request_state is not None:
                params["requestState"] = request_state
            if input_responses is not None:
                params["inputResponses"] = input_responses
            param_headers = None
            if self.is_modern and self._header_params.get(tool_name):
                param_headers = schema_mod.mirrored_headers(self._header_params[tool_name], arguments)
            self._legacy_notes = []
            self._legacy_tool = tool_name
            try:
                result = await self._send_jsonrpc(
                    "tools/call", params, retry_on_429=True,
                    tool_name=tool_name if self.is_modern else None,
                    param_headers=param_headers,
                    elicitation=elicit is not None,
                    elicit=elicit,
                    on_progress=self._progress_notifier(notify) if notify is not None else None,
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

            if self._legacy_notes:
                answered.extend(self._legacy_notes)
                self._legacy_notes = []
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

            last = self._parse_call_result(result)
            mismatch = self._output_mismatch(tool_name, last.get("structured"))
            if mismatch:
                last = {**last, "text": (last["text"] + "\n\n" if last["text"] else "") +
                        f"[The structured output did not match the tool's outputSchema: {mismatch}]"}
            text_result = last["text"]

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
        # Durable record so a restart can resume polling (task_store).
        ctx = task_store.CALL_CONTEXT.get()
        if ctx:
            task_store.remember(self.name, tool_name, task_id, ctx)
        # Task notifications wake the loop early; polling stays the fallback.
        slot: dict[str, Any] = {}
        wake = asyncio.Event()
        watcher: asyncio.Task | None = None
        if tasks_mod.TASKS_EXTENSION in self.server_extensions and task["status"] not in tasks_mod.TERMINAL:
            watcher = asyncio.get_running_loop().create_task(self._watch_task(task_id, slot, wake))

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

        try:
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
                if "task" not in slot:
                    await self._wait_any(delay, cancel_event, wake)
                wake.clear()
                polls += 1
                if "task" in slot:
                    got = slot.pop("task")
                else:
                    got = await self._send_jsonrpc("tasks/get", {"taskId": task_id}, retry_on_429=True, tool_name=task_id)
                try:
                    task = tasks_mod.parse_task(got)
                except tasks_mod.InvalidTask as why:
                    raise MCPError(f"invalid tasks/get reply: {why}", code=-32600)
                if task["taskId"] != task_id:
                    raise MCPError(f"tasks/get answered for task {task['taskId']}, asked {task_id}", code=-32600)
        finally:
            if watcher is not None:
                watcher.cancel()
            if ctx:
                task_store.forget(task_id)

    def _progress_notifier(self, notify: Callable[[dict[str, Any]], Awaitable[None]]):
        """notifications/progress params -> the UI's tool status shape."""
        started = time.monotonic()

        async def on_progress(params: dict[str, Any]) -> None:
            status: dict[str, Any] = {"kind": "progress", "state": "working",
                                      "elapsed_s": int(time.monotonic() - started)}
            prog, total = params.get("progress"), params.get("total")
            if isinstance(prog, (int, float)) and not isinstance(prog, bool):
                status["progress"] = prog
                if isinstance(total, (int, float)) and not isinstance(total, bool) and total > 0:
                    status["total"] = total
                    status["percent"] = int(min(100, max(0, prog * 100 / total)))
            msg = params.get("message")
            if isinstance(msg, str) and msg.strip():
                status["message"] = msg.strip()[:500]
            await notify(status)
        return on_progress

    def _output_mismatch(self, tool_name: str, structured: Any) -> str | None:
        """First validation error of ``structuredContent`` against the tool's
        ``outputSchema`` (spec: clients SHOULD validate), or None. Schemas
        with network references or an unavailable validator are skipped."""
        if structured is None:
            return None
        schema = (self.tool_definition(tool_name) or {}).get("outputSchema")
        if not isinstance(schema, dict) or not schema:
            return None
        try:
            import jsonschema
            from jsonschema import Draft202012Validator
            validator = Draft202012Validator(schema)
            err = next(iter(validator.iter_errors(structured)), None)
        except ImportError:
            return None
        except Exception as e:   # unresolvable $ref, invalid schema: not the tool's fault
            logger.debug("MCP '%s' outputSchema for %s not checkable: %s", self.name, tool_name, e)
            return None
        if err is None:
            return None
        where = "/".join(str(p) for p in err.absolute_path) or "(root)"
        logger.info("MCP '%s' %s structured output mismatch at %s: %s", self.name, tool_name, where, err.message)
        return f"at {where}: {err.message[:200]}"

    @staticmethod
    def _parse_call_result(result: dict[str, Any]) -> dict[str, Any]:
        """A CallToolResult -> {"text", "structured", "is_error"}."""
        texts = []
        for block in result.get("content", []) or []:
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
        return {"text": text_result, "structured": structured, "is_error": is_error}

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
