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
        """Create an MCPClient from a config dict."""
        return MCPClient(
            name=cfg["name"],
            url=cfg["url"],
            api_key=cfg.get("api_key", ""),
            headers=cfg.get("headers", {}),
            timeout=cfg.get("timeout", 30.0),
        )

    # ── Connection CRUD ──────────────────────────────────────────────────

    def list_connections(self) -> list[dict[str, Any]]:
        """List all configured connections (no secrets)."""
        result = []
        for cfg in self._configs:
            entry = {
                "name": cfg["name"],
                "url": cfg["url"],
                "enabled": cfg.get("enabled", True),
                "has_api_key": bool(cfg.get("api_key")),
                "headers": {k: "***" for k in cfg.get("headers", {})},
                "connected": cfg["name"] in self._clients,
                "tools_count": len(self._clients[cfg["name"]].tools) if cfg["name"] in self._clients else 0,
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
    ) -> dict[str, Any]:
        """Add a new MCP connection and attempt to connect."""
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
        }
        self._configs.append(cfg)
        self._save_configs()

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
        """Remove an MCP connection."""
        self._clients.pop(name, None)
        self._configs = [c for c in self._configs if c["name"] != name]
        self._save_configs()
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
            schemas.extend(client.get_openai_tool_schemas())
        return schemas

    def get_tool_names(self) -> list[str]:
        """Get all available MCP tool names (prefixed)."""
        names = []
        for client in self._clients.values():
            for tool in client.tools:
                names.append(f"mcp_{client.name}_{tool['name']}")
        return names

    def resolve_tool_call(self, prefixed_name: str) -> tuple[MCPClient, str] | None:
        """Resolve a prefixed tool name to (client, original_tool_name).

        Returns None if the tool doesn't belong to any MCP connection.
        """
        for client in self._clients.values():
            prefix = f"mcp_{client.name}_"
            if prefixed_name.startswith(prefix):
                original_name = prefixed_name[len(prefix):]
                # Verify the tool exists on this client
                for tool in client.tools:
                    if tool.get("name") == original_name:
                        return client, original_name
        return None

    async def execute_tool(self, prefixed_name: str, arguments: dict[str, Any]) -> str:
        """Execute an MCP tool by its prefixed name."""
        resolved = self.resolve_tool_call(prefixed_name)
        if not resolved:
            return f"Unknown MCP tool: {prefixed_name}"

        client, tool_name = resolved
        try:
            return await client.call_tool(tool_name, arguments)
        except Exception as e:
            logger.error("MCP tool '%s' on '%s' failed: %s", tool_name, client.name, e)
            return f"MCP tool error: {e}"

    def get_discovered_tools(self) -> list[dict[str, Any]]:
        """List all discovered tools with their connection source."""
        result = []
        for client in self._clients.values():
            for tool in client.tools:
                result.append({
                    "connection": client.name,
                    "name": tool.get("name", ""),
                    "prefixed_name": f"mcp_{client.name}_{tool['name']}",
                    "description": tool.get("description", ""),
                    "input_schema": tool.get("inputSchema", {}),
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
