"""Verify image_extraction handler updates image artifact + creates
sibling .extraction.md artifact."""
from __future__ import annotations

import asyncio
import os
import tempfile
import uuid
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))
os.environ.setdefault("AUTH_PASSWORD", "")

from artifacts.store import get_store as get_artifact_store  # noqa: E402
from jobs.store import get_store as get_job_store  # noqa: E402
from jobs.context import JobContext  # noqa: E402
from jobs.handlers import image_extraction  # noqa: E402  (registers handler)
from jobs.handlers import get_handler  # noqa: E402


def _png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
        b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc"
        b"\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def _seed_image_artifact() -> str:
    uid = uuid.uuid4().hex[:8]
    a = get_artifact_store().create(
        project_id="default",
        path=f"chat-attachments/2026-05-19/test-{uid}.png",
        content=_png_bytes(),
        content_type="image/png",
        title="test.png",
        tags=["chat-attachment"],
        edited_by="user",
    )
    return a["id"]


def _build_ctx(artifact_id: str) -> JobContext:
    job = get_job_store().create(
        job_type="image_extraction",
        project_id="default",
        title="test",
        payload={"artifact_id": artifact_id},
    )
    return JobContext(
        job_id=job["id"], job_type="image_extraction",
        project_id="default", payload={"artifact_id": artifact_id},
        store=get_job_store(),
    )


def test_handler_updates_image_and_creates_sibling():
    artifact_id = _seed_image_artifact()
    ctx = _build_ctx(artifact_id)

    fake_vision = {
        "caption": "A red square on a white background",
        "ocr_text": "PROTOTYPE",
        "topics": [
            {"label": "prototype", "type": "concept"},
            {"label": "color theory", "type": "concept"},
        ],
    }
    handler = get_handler("image_extraction")
    assert handler is not None

    with patch("jobs.handlers.image_extraction._call_vision_extractor",
               new=AsyncMock(return_value=fake_vision)):
        result = asyncio.run(handler.fn(ctx))

    assert result["status"] == "completed"
    assert result["caption"] == fake_vision["caption"]

    # Image artifact got new tags + caption-derived title
    store = get_artifact_store()
    img = store.get(artifact_id)
    assert "prototype" in (img["tags"] or [])
    assert "color theory" in (img["tags"] or [])
    assert "vision-extracted" in (img["tags"] or [])
    assert "red square" in (img["title"] or "").lower()

    # Sibling extraction artifact exists at <path>.extraction.md
    sibling = store.get_by_path("default", img["path"] + ".extraction.md")
    assert sibling is not None
    assert sibling["content_type"] == "text/markdown"
    assert "PROTOTYPE" in (sibling["content"] or "")
    assert "prototype" in (sibling["content"] or "").lower()


def test_handler_idempotent_on_same_sha():
    artifact_id = _seed_image_artifact()
    ctx = _build_ctx(artifact_id)

    fake = {"caption": "x", "ocr_text": "", "topics": []}
    handler = get_handler("image_extraction")
    with patch("jobs.handlers.image_extraction._call_vision_extractor",
               new=AsyncMock(return_value=fake)):
        first = asyncio.run(handler.fn(ctx))
        second = asyncio.run(handler.fn(_build_ctx(artifact_id)))

    assert first["status"] == "completed"
    assert second["status"] == "skipped"
    assert second["reason"] == "already extracted"


def test_handler_replaces_sibling_when_sha_changes():
    """Re-extraction with different image content should update the sibling, not crash."""
    store = get_artifact_store()

    # Seed image v1
    import uuid as _uuid
    uid = _uuid.uuid4().hex[:8]
    path = f"chat-attachments/2026-05-19/resha-{uid}.png"
    v1 = store.create(
        project_id="default", path=path, content=_png_bytes(),
        content_type="image/png", title="resha.png",
        tags=["chat-attachment"], edited_by="user",
    )

    handler = get_handler("image_extraction")
    fake_v1 = {"caption": "v1 caption", "ocr_text": "VONE", "topics": []}
    with patch("jobs.handlers.image_extraction._call_vision_extractor",
               new=AsyncMock(return_value=fake_v1)):
        first = asyncio.run(handler.fn(_build_ctx(v1["id"])))
    assert first["status"] == "completed"

    # Replace image content (different bytes → different sha)
    new_bytes = _png_bytes() + b"\x00"  # any modification produces a new sha
    store.update(v1["id"], content=new_bytes, edited_by="user")
    v2 = store.get(v1["id"])
    assert v2["sha256"] != v1["sha256"]

    # Re-run extraction
    fake_v2 = {"caption": "v2 caption", "ocr_text": "VTWO", "topics": []}
    with patch("jobs.handlers.image_extraction._call_vision_extractor",
               new=AsyncMock(return_value=fake_v2)):
        second = asyncio.run(handler.fn(_build_ctx(v1["id"])))
    assert second["status"] == "completed", second

    # Sibling at <path>.extraction.md has the v2 content (was updated, not duplicated)
    sibling = store.get_by_path("default", path + ".extraction.md")
    assert sibling is not None
    assert "VTWO" in (sibling["content"] or "")
    assert "VONE" not in (sibling["content"] or "")


def test_handler_topic_labels_with_special_chars_produce_valid_yaml():
    """Labels containing ':' or quotes must not break YAML frontmatter."""
    import yaml as _yaml

    artifact_id = _seed_image_artifact()
    ctx = _build_ctx(artifact_id)
    fake = {
        "caption": "img",
        "ocr_text": "",
        "topics": [
            {"label": "ratio: 16:9", "type": "concept"},
            {"label": "Bob's prototype", "type": "concept"},
        ],
    }
    handler = get_handler("image_extraction")
    with patch("jobs.handlers.image_extraction._call_vision_extractor",
               new=AsyncMock(return_value=fake)):
        result = asyncio.run(handler.fn(ctx))
    assert result["status"] == "completed"

    # Sibling frontmatter must be valid YAML
    store = get_artifact_store()
    image_path = store.get(artifact_id)["path"]
    sibling = store.get_by_path("default", image_path + ".extraction.md")
    assert sibling is not None
    body = sibling["content"]
    # Parse frontmatter (between leading --- and next ---)
    parts = body.split("---", 2)
    assert len(parts) >= 3
    fm = _yaml.safe_load(parts[1])
    assert isinstance(fm, dict)
    labels = [t["label"] for t in fm["topics"]]
    assert "ratio: 16:9" in labels
    assert "Bob's prototype" in labels
