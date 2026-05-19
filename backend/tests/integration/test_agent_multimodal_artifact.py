"""Verify AgentCore._build_user_content loads images from ArtifactStore
when the message references artifact:<id>."""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))
os.environ.setdefault("AUTH_PASSWORD", "")

from agent.core import AgentCore  # noqa: E402
from artifacts.store import get_store as get_artifact_store  # noqa: E402


def _png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
        b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc"
        b"\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_build_user_content_inlines_artifact_image():
    import uuid as _uuid
    uid = _uuid.uuid4().hex[:8]
    path = f"chat-attachments/2026-05-19/inline-{uid}.png"
    artifact = get_artifact_store().create(
        project_id="default",
        path=path,
        content=_png_bytes(),
        content_type="image/png",
        title="inline.png", tags=["chat-attachment"], edited_by="user",
    )

    agent = AgentCore(provider=None, memory_manager=None, project_id="default")
    msg = (
        "what is in this image?\n\n"
        f"[image: {path} (artifact:{artifact['id']})]"
    )
    blocks = agent._build_user_content(msg)
    assert isinstance(blocks, list)
    assert blocks[0] == {"type": "text", "text": msg}
    assert len(blocks) == 2
    assert blocks[1]["type"] == "image_url"
    assert blocks[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_build_user_content_plain_text_unchanged():
    agent = AgentCore(provider=None, memory_manager=None, project_id="default")
    msg = "hello, no images here"
    result = agent._build_user_content(msg)
    assert result == msg  # returned as plain string


def test_build_user_content_missing_artifact_falls_back_to_text():
    agent = AgentCore(provider=None, memory_manager=None, project_id="default")
    msg = "look at this [image: foo/bar.png (artifact:does-not-exist)]"
    result = agent._build_user_content(msg)
    # Missing artifact — no image block; should return plain string (no crash)
    assert result == msg
