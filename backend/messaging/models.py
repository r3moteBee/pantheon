"""Pydantic models for the messaging gateway."""
from __future__ import annotations

from pydantic import BaseModel, Field


class ChannelMapping(BaseModel):
    """Persistent mapping from a messaging channel to a Pantheon project."""

    channel_id: str = Field(
        ...,
        description="Platform-prefixed channel ID, e.g. 'discord:123456' or 'telegram:-100123'.",
    )
    platform: str = Field(..., description="Platform name: 'telegram', 'discord', etc.")
    channel_name: str = Field(default="", description="Human-readable channel/chat name.")
    project_id: str = Field(..., description="Pantheon project ID this channel is mapped to.")


class ChannelInfo(BaseModel):
    """Discovered channel from a running adapter."""

    channel_id: str = Field(..., description="Platform-prefixed channel ID.")
    raw_id: str = Field(..., description="Platform-native ID (no prefix).")
    name: str = Field(default="")
    platform: str = Field(...)


class AdapterStatus(BaseModel):
    """Runtime status of a messaging adapter."""

    name: str
    display_name: str
    running: bool = False
    configured: bool = False
    channel_count: int = 0
    error: str | None = None


class Attachment(BaseModel):
    """File attachment on an inbound message."""

    filename: str = ""
    content_type: str = ""
    url: str = ""
    data: bytes | None = None


class InboundMessage(BaseModel):
    """Normalised inbound message from any platform."""

    platform: str
    channel_id: str
    user_id: str
    user_display_name: str = ""
    text: str = ""
    attachments: list[Attachment] = []
