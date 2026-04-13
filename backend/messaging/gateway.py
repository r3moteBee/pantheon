"""MessagingGateway — central registry that manages all messaging adapters."""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

from messaging.base import BaseMessagingAdapter
from messaging.models import AdapterStatus, ChannelInfo

logger = logging.getLogger(__name__)


class MessagingGateway:
    """Singleton that owns the lifecycle of every messaging adapter.

    On :meth:`startup` it discovers and starts all configured adapters.
    On :meth:`shutdown` it stops them cleanly.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, BaseMessagingAdapter] = {}

    # ------------------------------------------------------------------
    # Adapter registration
    # ------------------------------------------------------------------

    def register(self, adapter: BaseMessagingAdapter) -> None:
        """Register an adapter (called once at import time or startup)."""
        self._adapters[adapter.name] = adapter
        logger.debug("Registered messaging adapter: %s", adapter.name)

    def get_adapter(self, name: str) -> BaseMessagingAdapter | None:
        return self._adapters.get(name)

    @property
    def adapters(self) -> dict[str, BaseMessagingAdapter]:
        return dict(self._adapters)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def startup(self) -> None:
        """Discover and start all configured adapters."""
        self._auto_register()
        for name, adapter in self._adapters.items():
            if not adapter.is_configured():
                logger.info("Messaging adapter '%s' not configured, skipping.", name)
                continue
            try:
                await adapter.start()
                logger.info("Messaging adapter '%s' started.", name)
            except Exception as exc:
                logger.error("Failed to start messaging adapter '%s': %s", name, exc)

    async def shutdown(self) -> None:
        """Stop all running adapters."""
        for name, adapter in self._adapters.items():
            try:
                if await adapter.is_running():
                    await adapter.stop()
                    logger.info("Messaging adapter '%s' stopped.", name)
            except Exception as exc:
                logger.warning("Error stopping adapter '%s': %s", name, exc)

    async def restart_adapter(self, name: str) -> dict[str, str]:
        """Stop and restart a single adapter.  Returns status dict."""
        adapter = self._adapters.get(name)
        if adapter is None:
            return {"status": "error", "message": f"Unknown adapter: {name}"}
        try:
            if await adapter.is_running():
                await adapter.stop()
            await adapter.start(raise_on_error=True)
        except Exception as exc:
            return {"status": "error", "message": str(exc)}
        if await adapter.is_running():
            return {"status": "ok", "message": f"{adapter.display_name} restarted successfully."}
        return {"status": "error", "message": f"{adapter.display_name} did not start (unknown reason)."}

    # ------------------------------------------------------------------
    # Status & channels
    # ------------------------------------------------------------------

    async def status(self) -> list[AdapterStatus]:
        """Return the status of every registered adapter."""
        result: list[AdapterStatus] = []
        for name, adapter in self._adapters.items():
            running = False
            channel_count = 0
            error: str | None = None
            try:
                running = await adapter.is_running()
                if running:
                    channels = await adapter.list_channels()
                    channel_count = len(channels)
            except Exception as exc:
                error = str(exc)
            result.append(AdapterStatus(
                name=name,
                display_name=adapter.display_name,
                running=running,
                configured=adapter.is_configured(),
                channel_count=channel_count,
                error=error,
            ))
        return result

    async def list_all_channels(self) -> list[ChannelInfo]:
        """Aggregate channels across all running adapters."""
        channels: list[ChannelInfo] = []
        for adapter in self._adapters.values():
            try:
                if await adapter.is_running():
                    channels.extend(await adapter.list_channels())
            except Exception as exc:
                logger.warning("Failed to list channels for '%s': %s", adapter.name, exc)
        return channels

    async def broadcast(self, message: str, project_id: str | None = None) -> None:
        """Send *message* to all channels (optionally filtered by project)."""
        from messaging.channel_store import get_channel_store

        store = get_channel_store()
        mappings = store.get_mappings()

        for mapping in mappings:
            if project_id and mapping.project_id != project_id:
                continue
            adapter = self._adapters.get(mapping.platform)
            if adapter is None:
                continue
            try:
                if await adapter.is_running():
                    # Extract raw ID from prefixed channel_id
                    raw_id = mapping.channel_id.split(":", 1)[-1]
                    await adapter.send_message(raw_id, message)
            except Exception as exc:
                logger.warning(
                    "Failed to broadcast to %s: %s", mapping.channel_id, exc
                )

    # ------------------------------------------------------------------
    # Auto-discovery
    # ------------------------------------------------------------------

    def _auto_register(self) -> None:
        """Import and register built-in adapters that aren't already registered."""
        if "telegram" not in self._adapters:
            try:
                from messaging.adapters.telegram import TelegramAdapter
                self.register(TelegramAdapter())
            except Exception as exc:
                logger.debug("Telegram adapter not available: %s", exc)

        if "discord" not in self._adapters:
            try:
                from messaging.adapters.discord import DiscordAdapter
                self.register(DiscordAdapter())
            except Exception as exc:
                logger.debug("Discord adapter not available: %s", exc)


@lru_cache()
def get_messaging_gateway() -> MessagingGateway:
    """Return the singleton :class:`MessagingGateway`."""
    return MessagingGateway()
