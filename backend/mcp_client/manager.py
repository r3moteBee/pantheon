"""MCP connection manager — manages multiple MCP server connections.

Stores connection configs in the vault, initializes clients on startup,
and provides a unified interface for the agent to discover and call
MCP-provided tools alongside built-in tools.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from mcp_client.client import MCPClient

logger = logging.getLogger(__name__)

# Vault key prefix for MCP connection configs
_VAULT_KEY = "mcp_connections"

# Cap structured content payload sent to the LLM so a giant tool result
# doesn't blow the context window. Server can still return huge structured
# results — we just truncate the rendered block.
_MAX_STRUCTURED_CHARS = 50_000


def _make_oauth_token_getter(name: str, oauth_cfg: dict[str, Any]):
    """Return an async callable that yields the current access token.

    Refreshes on demand if the cached token is near expiry, or if the
    caller passes force_refresh=True (used after a 401 from the MCP
    server). Returns None when there are no usable tokens at all — the
    caller will fall back to the static api_key or send no Authorization.
    """
    async def getter(*, force_refresh: bool = False) -> str | None:
        from mcp_client import oauth as oauth_mod

        tokens = oauth_mod.load_tokens(name)
        if not tokens:
            return None

        if force_refresh or not oauth_mod.is_token_fresh(tokens):
            refresh = tokens.get("refresh_token")
            if not refresh:
                logger.warning(
                    "MCP '%s' OAuth token expired and no refresh_token stored — "
                    "user must reauthorize",
                    name,
                )
                return tokens.get("access_token") or None
            try:
                new_raw = await oauth_mod.refresh_tokens(
                    token_endpoint=oauth_cfg["token_endpoint"],
                    client_id=oauth_cfg["client_id"],
                    refresh_token=refresh,
                    resource=oauth_cfg.get("resource", ""),
                    scopes=oauth_cfg.get("scopes") or None,
                    client_secret=oauth_mod.load_client_secret(name),
                )
                tokens = oauth_mod.save_tokens(name, new_raw)
            except Exception as e:
                logger.warning(
                    "MCP '%s' OAuth refresh failed: %s — returning stale token",
                    name, e,
                )

        return tokens.get("access_token") or None

    return getter


def _format_tool_result(call_result: dict[str, Any]) -> str:
    """Render an MCP call_tool dict as a single string for the LLM.

    Per MCP spec 2025-06-18+, a tool result may carry `structuredContent`
    (typed JSON) alongside `content` (text/image blocks). We render the
    text first and, if structured content is present, append it inside a
    delimited block so the model can parse it deterministically without
    losing the human-readable summary.
    """
    text = call_result.get("text") or ""
    structured = call_result.get("structured")
    is_error = bool(call_result.get("is_error"))

    parts: list[str] = []
    if text:
        parts.append(text)

    if structured is not None:
        try:
            payload = json.dumps(structured, indent=2, default=str)
        except (TypeError, ValueError):
            payload = str(structured)
        if len(payload) > _MAX_STRUCTURED_CHARS:
            payload = payload[:_MAX_STRUCTURED_CHARS] + "\n…[truncated]"
        parts.append(f"<structured-output>\n{payload}\n</structured-output>")

    if is_error:
        parts.append("[server reported isError=true on this tool result]")

    return "\n\n".join(parts) if parts else ""


class MCPManager:
    """Manages multiple MCP server connections."""

    def __init__(self) -> None:
        self._clients: dict[str, MCPClient] = {}
        self._configs: list[dict[str, Any]] = []

    # ── Config persistence (vault-backed) ────────────────────────────────

    def _load_configs(self) -> list[dict[str, Any]]:
        """Load MCP connection configs from the vault."""
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            raw = vault.get_secret(_VAULT_KEY)
            if raw:
                return json.loads(raw)
        except Exception as e:
            logger.warning("Failed to load MCP configs from vault: %s", e)
        return []

    def _save_configs(self) -> None:
        """Save MCP connection configs to the vault."""
        try:
            from secrets.vault import get_vault
            vault = get_vault()
            vault.set_secret(_VAULT_KEY, json.dumps(self._configs))
        except Exception as e:
            logger.warning("Failed to save MCP configs to vault: %s", e)

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """Load configs and connect to all enabled MCP servers."""
        self._configs = self._load_configs()
        connected = 0
        for cfg in self._configs:
            if not cfg.get("enabled", True):
                continue
            try:
                client = self._create_client(cfg)
                await client.initialize()
                await client.discover_tools()
                self._clients[cfg["name"]] = client
                connected += 1
                logger.info(
                    "MCP '%s' connected: %d tools",
                    cfg["name"],
                    len(client.tools),
                )
            except Exception as e:
                logger.warning("MCP '%s' failed to connect: %s", cfg.get("name", "?"), e)

        logger.info("MCP manager: %d/%d connections active", connected, len(self._configs))

    def _create_client(self, cfg: dict[str, Any]) -> MCPClient:
        """Create an MCPClient from a config dict.

        If the connection's auth_type is "oauth2", wire a token_getter that
        refreshes the persisted OAuth token on demand.
        """
        token_getter = None
        if cfg.get("auth_type") == "oauth2":
            name = cfg["name"]
            oauth_cfg = cfg.get("oauth") or {}
            token_getter = _make_oauth_token_getter(name, oauth_cfg)

        return MCPClient(
            name=cfg["name"],
            url=cfg["url"],
            api_key=cfg.get("api_key", ""),
            headers=cfg.get("headers", {}),
            timeout=cfg.get("timeout", 30.0),
            request_interval_ms=cfg.get("request_interval_ms", 1000),
            token_getter=token_getter,
        )

    # ── Connection CRUD ──────────────────────────────────────────────────

    def list_connections(self) -> list[dict[str, Any]]:
        """List all configured connections (no secrets)."""
        from mcp_client import oauth as oauth_mod

        result = []
        for cfg in self._configs:
            name = cfg["name"]
            auth_type = cfg.get("auth_type", "api_key")
            oauth_status = None
            oauth_meta = None
            if auth_type == "oauth2":
                tokens = oauth_mod.load_tokens(name)
                if not tokens:
                    oauth_status = "needs_auth"
                elif tokens.get("expires_at") and not oauth_mod.is_token_fresh(tokens):
                    # Stale tokens with refresh available are still "ok" — getter
                    # will refresh on the next request. Only flag if there's no
                    # refresh token to recover with.
                    oauth_status = "ok" if tokens.get("refresh_token") else "needs_auth"
                else:
                    oauth_status = "ok"
                oc = cfg.get("oauth") or {}
                oauth_meta = {
                    "issuer": oc.get("issuer"),
                    "client_id": oc.get("client_id"),
                    "scopes": oc.get("scopes", []),
                }
            entry = {
                "name": name,
                "url": cfg["url"],
                "enabled": cfg.get("enabled", True),
                "auth_type": auth_type,
                "has_api_key": bool(cfg.get("api_key")),
                "oauth_status": oauth_status,
                "oauth": oauth_meta,
                "headers": {k: "***" for k in cfg.get("headers", {})},
                "connected": name in self._clients,
                "tools_count": len(self._clients[name].tools) if name in self._clients else 0,
                "request_interval_ms": cfg.get("request_interval_ms", 1000),
                "excluded_tools": cfg.get("excluded_tools", []),
            }
            result.append(entry)
        return result

    async def add_connection(
        self,
        name: str,
        url: str,
        api_key: str = "",
        headers: dict[str, str] | None = None,
        enabled: bool = True,
        request_interval_ms: int = 1000,
        auth_type: str = "api_key",
    ) -> dict[str, Any]:
        """Add a new MCP connection.

        For auth_type="api_key" we immediately try to connect. For
        auth_type="oauth2" the caller must follow up with start_oauth() —
        we just persist the bare config so the user can complete auth in
        a second step.
        """
        # Check for duplicate name
        for cfg in self._configs:
            if cfg["name"] == name:
                raise ValueError(f"Connection '{name}' already exists")

        cfg: dict[str, Any] = {
            "name": name,
            "url": url,
            "api_key": api_key,
            "headers": headers or {},
            "enabled": enabled,
            "request_interval_ms": request_interval_ms,
            "auth_type": auth_type,
        }
        self._configs.append(cfg)
        self._save_configs()

        # OAuth connections need a separate /start-oauth call before they can
        # connect; just confirm we saved the config.
        if auth_type == "oauth2":
            return {"name": name, "status": "added", "next": "start_oauth"}

        # Try to connect
        result = {"name": name, "status": "added"}
        if enabled:
            try:
                client = self._create_client(cfg)
                test = await client.test_connection()
                if test["status"] == "ok":
                    self._clients[name] = client
                    result["status"] = "connected"
                    result["tools"] = test.get("tool_names", [])
                else:
                    result["status"] = "added_but_connection_failed"
                    result["error"] = test.get("message", "")
            except Exception as e:
                result["status"] = "added_but_connection_failed"
                result["error"] = str(e)

        return result

    async def update_connection(
        self,
        name: str,
        url: str | None = None,
        api_key: str | None = None,
        headers: dict[str, str] | None = None,
        enabled: bool | None = None,
        request_interval_ms: int | None = None,
    ) -> dict[str, Any]:
        """Update an existing MCP connection."""
        cfg = None
        for c in self._configs:
            if c["name"] == name:
                cfg = c
                break
        if not cfg:
            raise ValueError(f"Connection '{name}' not found")

        if url is not None:
            cfg["url"] = url
        if api_key is not None:
            cfg["api_key"] = api_key
        if headers is not None:
            cfg["headers"] = headers
        if enabled is not None:
            cfg["enabled"] = enabled
        if request_interval_ms is not None:
            cfg["request_interval_ms"] = request_interval_ms

        self._save_configs()

        # Reconnect if enabled
        if cfg.get("enabled", True):
            # Disconnect existing
            self._clients.pop(name, None)
            try:
                client = self._create_client(cfg)
                await client.initialize()
                await client.discover_tools()
                self._clients[name] = client
                return {"name": name, "status": "reconnected", "tools_count": len(client.tools)}
            except Exception as e:
                return {"name": name, "status": "updated_but_connection_failed", "error": str(e)}
        else:
            self._clients.pop(name, None)
            return {"name": name, "status": "disabled"}

    async def remove_connection(self, name: str) -> dict[str, str]:
        """Remove an MCP connection and any associated OAuth tokens."""
        from mcp_client import oauth as oauth_mod

        self._clients.pop(name, None)
        self._configs = [c for c in self._configs if c["name"] != name]
        self._save_configs()
        # Best-effort cleanup — safe to call even if there are no tokens.
        try:
            oauth_mod.delete_tokens(name)
        except Exception:
            pass
        return {"name": name, "status": "removed"}

    async def test_connection(self, name: str) -> dict[str, Any]:
        """Test a specific connection."""
        cfg = None
        for c in self._configs:
            if c["name"] == name:
                cfg = c
                break
        if not cfg:
            raise ValueError(f"Connection '{name}' not found")

        client = self._create_client(cfg)
        return await client.test_connection()

    # ── OAuth flow ───────────────────────────────────────────────────────

    async def start_oauth(self, name: str) -> dict[str, Any]:
        """Begin the OAuth dance for a connection: probe → PRM → AS → DCR → URL.

        Returns the authorization URL the user must visit. Persists the
        DCR client_id (and secret, if the AS forced a confidential client)
        and updates the connection config with auth_type=oauth2 + oauth meta.
        """
        from mcp_client import oauth as oauth_mod

        cfg = self._get_cfg(name)
        if not cfg:
            raise ValueError(f"Connection '{name}' not found")

        prm_url = await oauth_mod.probe_for_oauth(cfg["url"])
        if not prm_url:
            raise RuntimeError(
                f"MCP server at {cfg['url']} did not advertise OAuth "
                "(no 401 + WWW-Authenticate). Use api_key auth instead."
            )

        prm = await oauth_mod.fetch_resource_metadata(prm_url)
        if not prm.authorization_servers:
            raise RuntimeError(f"PRM at {prm_url} listed no authorization_servers")

        # Use first AS; ASes are listed in server-preference order per RFC 9728.
        issuer = prm.authorization_servers[0]
        as_meta = await oauth_mod.fetch_auth_server_metadata(issuer)

        client = await oauth_mod.register_client(
            as_meta=as_meta,
            redirect_uri=oauth_mod.DEFAULT_CALLBACK_URL,
            client_name=f"Pantheon ({name})",
            scopes=prm.scopes_supported or as_meta.scopes_supported,
        )
        if client.client_secret:
            oauth_mod.save_client_secret(name, client.client_secret)

        resource = prm.resource or oauth_mod._resource_id_for(cfg["url"])
        scopes = prm.scopes_supported or as_meta.scopes_supported

        auth_url = oauth_mod.build_authorize_url(
            name=name,
            as_meta=as_meta,
            client_id=client.client_id,
            redirect_uri=oauth_mod.DEFAULT_CALLBACK_URL,
            scopes=scopes,
            resource=resource,
        )

        # Persist OAuth metadata onto the connection.
        cfg["auth_type"] = "oauth2"
        cfg["oauth"] = {
            "issuer": as_meta.issuer,
            "authorization_endpoint": as_meta.authorization_endpoint,
            "token_endpoint": as_meta.token_endpoint,
            "registration_endpoint": as_meta.registration_endpoint,
            "client_id": client.client_id,
            "scopes": scopes,
            "resource": resource,
            "prm_url": prm_url,
            "registered_at": __import__("time").time(),
        }
        self._save_configs()

        return {"name": name, "authorize_url": auth_url}

    async def complete_oauth(self, code: str, state: str) -> dict[str, Any]:
        """Finish the OAuth dance after the browser redirects to our callback."""
        from mcp_client import oauth as oauth_mod

        pending = oauth_mod.take_pending(state)
        if not pending:
            raise RuntimeError(
                "Unknown or expired OAuth state. Restart the Authorize flow."
            )

        cfg = self._get_cfg(pending.name)
        if not cfg:
            raise RuntimeError(f"Connection '{pending.name}' disappeared during auth")

        client_secret = oauth_mod.load_client_secret(pending.name)
        token_response = await oauth_mod.exchange_code(
            pending=pending,
            code=code,
            client_secret=client_secret,
        )
        oauth_mod.save_tokens(pending.name, token_response)

        # Reconnect using the freshly-issued token.
        self._clients.pop(pending.name, None)
        try:
            client = self._create_client(cfg)
            await client.initialize()
            await client.discover_tools()
            self._clients[pending.name] = client
            return {
                "name": pending.name,
                "status": "connected",
                "tools_count": len(client.tools),
            }
        except Exception as e:
            logger.warning(
                "Token saved for '%s' but reconnect failed: %s", pending.name, e
            )
            return {
                "name": pending.name,
                "status": "token_saved_but_connect_failed",
                "error": str(e),
            }

    async def revoke_oauth(self, name: str) -> dict[str, str]:
        """Wipe persisted OAuth tokens (and DCR secret) for a connection."""
        from mcp_client import oauth as oauth_mod

        oauth_mod.delete_tokens(name)
        self._clients.pop(name, None)
        return {"name": name, "status": "tokens_revoked"}

    def _get_cfg(self, name: str) -> dict[str, Any] | None:
        for c in self._configs:
            if c["name"] == name:
                return c
        return None

    async def reconnect(self, name: str) -> dict[str, Any]:
        """Force reconnect a specific connection."""
        cfg = None
        for c in self._configs:
            if c["name"] == name:
                cfg = c
                break
        if not cfg:
            raise ValueError(f"Connection '{name}' not found")

        self._clients.pop(name, None)
        try:
            client = self._create_client(cfg)
            await client.initialize()
            await client.discover_tools()
            self._clients[name] = client
            return {"name": name, "status": "connected", "tools_count": len(client.tools)}
        except Exception as e:
            return {"name": name, "status": "error", "message": str(e)}

    # ── Tool integration ─────────────────────────────────────────────────

    def get_all_tool_schemas(self) -> list[dict[str, Any]]:
        """Get OpenAI-format tool schemas from all connected MCP servers."""
        schemas = []
        for client in self._clients.values():
            excluded = self._get_excluded_tools(client.name)
            schemas.extend(client.get_openai_tool_schemas(excluded_tools=excluded))
        return schemas

    def _get_excluded_tools(self, connection_name: str) -> set[str]:
        """Get the set of excluded tool names for a connection."""
        for cfg in self._configs:
            if cfg["name"] == connection_name:
                return set(cfg.get("excluded_tools", []))
        return set()

    def get_tool_names(self) -> list[str]:
        """Get all available MCP tool names (prefixed)."""
        names = []
        for client in self._clients.values():
            for tool in client.tools:
                names.append(f"mcp_{client.name}_{tool['name']}")
        return names

    def resolve_tool_call(self, prefixed_name: str) -> tuple[MCPClient, str] | None:
        """Resolve a prefixed tool name to (client, original_tool_name).

        Returns None if the tool doesn't belong to any MCP connection
        or if the tool is excluded.
        """
        for client in self._clients.values():
            prefix = f"mcp_{client.name}_"
            if prefixed_name.startswith(prefix):
                original_name = prefixed_name[len(prefix):]
                excluded = self._get_excluded_tools(client.name)
                if original_name in excluded:
                    return None
                # Verify the tool exists on this client
                for tool in client.tools:
                    if tool.get("name") == original_name:
                        return client, original_name
        return None

    def _is_tavily_tool(self, prefixed_name: str) -> bool:
        """Check if a prefixed tool name belongs to a Tavily connection."""
        for client in self._clients.values():
            prefix = f"mcp_{client.name}_"
            if prefixed_name.startswith(prefix):
                # Check if this client is a Tavily connection
                return "tavily" in client.url.lower() or "tavily" in client.name.lower()
        return False

    async def execute_tool(self, prefixed_name: str, arguments: dict[str, Any]) -> str:
        """Execute an MCP tool by its prefixed name.

        For Tavily tools, checks credit thresholds before execution and
        falls back to built-in web_search if limits are exceeded.
        """
        resolved = self.resolve_tool_call(prefixed_name)
        if not resolved:
            return f"Unknown MCP tool: {prefixed_name}"

        client, tool_name = resolved

        # ── Tavily credit threshold check ────────────────────────────
        if self._is_tavily_tool(prefixed_name):
            from mcp_client.tavily_credits import get_tavily_tracker
            tracker = get_tavily_tracker()
            threshold_check = tracker.check_threshold()

            if threshold_check["exceeded"]:
                reason = threshold_check["reason"]
                usage = threshold_check["usage"]
                logger.warning(
                    "Tavily threshold exceeded for '%s': %s",
                    tool_name, reason,
                )

                # Only fallback for search — other tools have no equivalent
                if "search" in tool_name.lower():
                    fallback_query = arguments.get("query", "")
                    if fallback_query:
                        from agent.tools import _web_search
                        fallback_result = await _web_search(fallback_query)
                        return (
                            f"[Tavily credit limit reached — {reason}. "
                            f"Falling back to built-in web search.]\n\n"
                            f"{fallback_result}"
                        )

                # For non-search tools, return a clear error
                return (
                    f"[Tavily credit limit reached — {reason}. "
                    f"Daily: {usage['daily_used']:.0f}/{usage['daily_limit']}, "
                    f"Monthly: {usage['monthly_used']:.0f}/{usage['monthly_limit']}. "
                    f"This tool call was blocked to stay within budget. "
                    f"Adjust limits in Settings → MCP or wait for the limit to reset.]"
                )

        # ── Execute the tool ─────────────────────────────────────────
        try:
            call_result = await client.call_tool(tool_name, arguments)
            result = _format_tool_result(call_result)

            # Record Tavily credit usage after successful call
            if self._is_tavily_tool(prefixed_name):
                from mcp_client.tavily_credits import get_tavily_tracker
                tracker = get_tavily_tracker()
                credits = tracker.record_usage(tool_name, arguments)

                # Check if we're approaching the limit (80% warning)
                usage = tracker.get_usage()
                for limit_type in ("daily", "monthly"):
                    limit = usage[f"{limit_type}_limit"]
                    used = usage[f"{limit_type}_used"]
                    if limit > 0 and used >= limit * 0.8 and used < limit:
                        remaining = limit - used
                        result += (
                            f"\n\n[Note: Tavily {limit_type} credits approaching limit — "
                            f"{used:.0f}/{limit:.0f} used, {remaining:.0f} remaining]"
                        )

            return result
        except Exception as e:
            logger.error("MCP tool '%s' on '%s' failed: %s", tool_name, client.name, e)
            return f"MCP tool error: {e}"

    def get_discovered_tools(self) -> list[dict[str, Any]]:
        """List all discovered tools with their connection source."""
        result = []
        for client in self._clients.values():
            excluded = self._get_excluded_tools(client.name)
            for tool in client.tools:
                tool_name = tool.get("name", "")
                result.append({
                    "connection": client.name,
                    "name": tool_name,
                    "prefixed_name": f"mcp_{client.name}_{tool_name}",
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema", {}),
                    # Preserved from spec 2025-06-18+ for UI inspection and
                    # future adapter use; the LLM tool schema doesn't need it.
                    "output_schema": tool.get("outputSchema") or None,
                    "excluded": tool_name in excluded,
                })
        return result


# ── Singleton ────────────────────────────────────────────────────────────────

_manager: MCPManager | None = None


def get_mcp_manager() -> MCPManager:
    """Get the global MCP manager singleton."""
    global _manager
    if _manager is None:
        _manager = MCPManager()
    return _manager
