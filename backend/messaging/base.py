"""Base class for all messaging platform adapters."""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from messaging.models import ChannelInfo

logger = logging.getLogger(__name__)


class BaseMessagingAdapter(ABC):
    """Abstract base every messaging adapter must implement.

    Provides a shared ``resolve_project`` helper that delegates to the
    central :class:`~messaging.channel_store.ChannelStore` so every adapter
    uses the same channel→project mapping table.
    """

    name: str = ""
    display_name: str = ""

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def start(self, *, raise_on_error: bool = False) -> None:
        """Connect to the platform and begin processing messages."""

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully disconnect."""

    @abstractmethod
    async def is_running(self) -> bool:
        """Return True if the adapter is currently connected and processing."""

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if the adapter has the credentials it needs to start."""

    # ------------------------------------------------------------------
    # Channel discovery
    # ------------------------------------------------------------------

    @abstractmethod
    async def list_channels(self) -> list[ChannelInfo]:
        """Return all channels/chats the adapter can see."""

    # ------------------------------------------------------------------
    # Outbound messaging
    # ------------------------------------------------------------------

    @abstractmethod
    async def send_message(self, channel_id: str, text: str) -> None:
        """Send *text* to the channel identified by its **raw** (unprefixed) ID."""

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    def prefixed_channel_id(self, raw_id: str | int) -> str:
        """Return a globally-unique channel ID: ``<platform>:<raw_id>``."""
        return f"{self.name}:{raw_id}"

    def resolve_project(self, raw_channel_id: str | int) -> str:
        """Look up the project for *raw_channel_id* via :class:`ChannelStore`.

        Falls back to the configured default project.
        """
        from messaging.channel_store import get_channel_store

        store = get_channel_store()
        return store.resolve(self.name, str(raw_channel_id))

    def set_channel_project(self, raw_channel_id: str | int, project_id: str) -> None:
        """Persist a channel→project mapping (e.g. from ``/project`` command)."""
        from messaging.channel_store import get_channel_store

        store = get_channel_store()
        prefixed = self.prefixed_channel_id(raw_channel_id)
        store.set_mapping(prefixed, project_id, platform=self.name)
