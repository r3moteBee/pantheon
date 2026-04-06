"""MCP protocol client using Streamable HTTP transport.

Implements the client side of the MCP Streamable HTTP transport:
  - POST JSON-RPC messages to the server endpoint
  - Accept both application/json and text/event-stream responses
  - Track Mcp-Session-Id for stateful sessions
  - initialize → tools/list → tools/call lifecycle

Reference: MCP Specification 2025-03-26 — Streamable HTTP Transport
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-03-26"

# Retry settings for rate-limited requests
MAX_RETRIES = 3
RETRY_BASE_DELAY = 2.0  # seconds, doubles each retry


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
    ) -> None:
        self.name = name
        self.url = url  # Preserve URL as-is (trailing slash matters for some servers)
        self.api_key = api_key
        self.extra_headers = headers or {}
        self.timeout = timeout

        self.session_id: str | None = None
        self.server_info: dict[str, Any] = {}
        self.server_capabilities: dict[str, Any] = {}
        self.tools: list[dict[str, Any]] = []
        self._initialized = False
        self._request_id = 0
        self._last_request_time: float = 0.0
        self._min_request_interval: float = request_interval_ms / 1000.0

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _build_headers(self) -> dict[str, str]:
        """Build request headers for MCP Streamable HTTP transport."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        # API key auth — try Bearer header first
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        # Session tracking
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        # Extra headers (e.g., DEFAULT_PARAMETERS for Tavily)
        headers.update(self.extra_headers)
        return headers

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

    async def _send_jsonrpc(
        self, method: str, params: dict | None = None, *, retry_on_429: bool = False
    ) -> dict[str, Any]:
        """Send a JSON-RPC 2.0 request and return the result.

        If retry_on_429 is True, retries with exponential backoff on HTTP 429.
        """
        request_id = self._next_id()
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params:
            payload["params"] = params

        url = self._build_url()
        headers = self._build_headers()

        # Debug logging — mask API key in URL and headers
        safe_url = url
        if self.api_key and self.api_key in safe_url:
            safe_url = safe_url.replace(self.api_key, self.api_key[:6] + "***")
        safe_headers = {
            k: (v[:10] + "***" if k.lower() == "authorization" else v)
            for k, v in headers.items()
        }
        logger.info(
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

        for attempt in range(max_attempts):
            await self._throttle()

            async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
                resp = await client.post(url, json=payload, headers=headers)

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

                resp.raise_for_status()

                content_type = resp.headers.get("content-type", "")

                # Track session ID from server
                new_session = resp.headers.get("mcp-session-id")
                if new_session:
                    self.session_id = new_session

                if "text/event-stream" in content_type:
                    return self._parse_sse_response(resp.text, request_id)
                else:
                    data = resp.json()
                    if "error" in data:
                        raise MCPError(
                            data["error"].get("message", "Unknown MCP error"),
                            code=data["error"].get("code", -1),
                        )
                    return data.get("result", {})

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
        """Send the MCP initialize handshake."""
        result = await self._send_jsonrpc("initialize", {
            "protocolVersion": MCP_PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {
                "name": "pantheon",
                "version": "1.0.0",
            },
        })

        self.server_info = result.get("serverInfo", {})
        self.server_capabilities = result.get("capabilities", {})
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
        headers = self._build_headers()

        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:
            try:
                await client.post(url, json=payload, headers=headers)
            except Exception:
                pass  # Notifications are fire-and-forget

    async def discover_tools(self) -> list[dict[str, Any]]:
        """Call tools/list and cache the results."""
        if not self._initialized:
            await self.initialize()

        result = await self._send_jsonrpc("tools/list")
        self.tools = result.get("tools", [])

        logger.info(
            "MCP '%s' discovered %d tools: %s",
            self.name,
            len(self.tools),
            [t.get("name") for t in self.tools],
        )
        return self.tools

    async def call_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> str:
        """Call a tool on the remote MCP server and return the text result.

        Retries on HTTP 429 with exponential backoff. Also detects rate-limit
        errors embedded in tool result content (e.g. Tavily returns 429 errors
        as tool output) and retries those too.
        """
        if not self._initialized:
            await self.initialize()

        for attempt in range(MAX_RETRIES):
            result = await self._send_jsonrpc(
                "tools/call",
                {"name": tool_name, "arguments": arguments or {}},
                retry_on_429=True,
            )

            # Extract text content from the MCP response
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

            text_result = "\n".join(texts) if texts else str(result)

            # Check for rate-limit errors embedded in the result content
            # (e.g. Tavily returns {"error":...,"status":429,...} as tool text)
            if self._is_rate_limited_result(text_result) and attempt < MAX_RETRIES - 1:
                delay = RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "MCP '%s' tool '%s' returned rate-limit error in content, "
                    "retrying in %.1fs (attempt %d/%d)",
                    self.name, tool_name, delay, attempt + 1, MAX_RETRIES,
                )
                await asyncio.sleep(delay)
                continue

            return text_result

        return text_result  # Return last result even if still rate-limited

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
        """Test the connection by initializing and listing tools."""
        try:
            init_result = await self.initialize()
            tools = await self.discover_tools()
            return {
                "status": "ok",
                "server": self.server_info,
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
        Tools in `excluded_tools` (original MCP names) are skipped.
        """
        excluded = excluded_tools or set()
        schemas = []
        for tool in self.tools:
            if tool.get("name", "") in excluded:
                continue
            mcp_name = tool.get("name", "")
            # Prefix with connection name to namespace
            prefixed_name = f"mcp_{self.name}_{mcp_name}"
            # Keep it under 64 chars (OpenAI limit)
            if len(prefixed_name) > 64:
                prefixed_name = prefixed_name[:64]

            schema = {
                "type": "function",
                "function": {
                    "name": prefixed_name,
                    "description": tool.get("description", ""),
                    "parameters": tool.get("inputSchema", {"type": "object", "properties": {}}),
                },
            }
            schemas.append(schema)
        return schemas

    @property
    def is_connected(self) -> bool:
        return self._initialized and len(self.tools) > 0


class MCPError(Exception):
    """Error from an MCP server."""
    def __init__(self, message: str, code: int = -1):
        self.code = code
        super().__init__(f"MCP error ({code}): {message}")
