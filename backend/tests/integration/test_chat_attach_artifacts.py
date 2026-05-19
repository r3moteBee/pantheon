"""Verify /chat/attach uploads images to ArtifactStore and enqueues
an image_extraction job. The synchronous vision call is gone."""
from __future__ import annotations

import io
import os
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))
os.environ.setdefault("AUTH_PASSWORD", "")

from fastapi.testclient import TestClient  # noqa: E402

from main import app  # noqa: E402
from artifacts.store import get_store as get_artifact_store  # noqa: E402
from jobs.store import get_store as get_job_store  # noqa: E402


@pytest.fixture
def client():
    return TestClient(app)


def _png_bytes() -> bytes:
    # 1x1 transparent PNG (smallest valid PNG)
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00"
        b"\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc"
        b"\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_attach_creates_artifact_and_enqueues_job(client):
    files = {"file": ("test.png", io.BytesIO(_png_bytes()), "image/png")}
    res = client.post("/api/chat/attach", files=files, params={"project_id": "default"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["status"] == "uploaded"
    assert "artifact_id" in body
    assert body["path"].startswith("chat-attachments/")
    assert body["content_type"] == "image/png"

    # Artifact actually persisted
    artifact_store = get_artifact_store()
    a = artifact_store.get(body["artifact_id"])
    assert a is not None
    assert a["content_type"] == "image/png"
    assert a["blob_path"]  # binary stored, not text column

    # image_extraction job enqueued (status: queued or running — worker may have grabbed it)
    job_store = get_job_store()
    jobs = job_store.list(job_type="image_extraction", limit=10)
    assert any(j.get("payload", {}).get("artifact_id") == body["artifact_id"]
               for j in jobs), "no image_extraction job for this artifact"


def test_attach_non_image_skips_extraction(client):
    files = {"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")}
    res = client.post("/api/chat/attach", files=files, params={"project_id": "default"})
    assert res.status_code == 200
    body = res.json()
    # Text upload still produces an artifact, but no extraction job
    assert "artifact_id" in body
    job_store = get_job_store()
    jobs = job_store.list(job_type="image_extraction", limit=10)
    assert not any(j.get("payload", {}).get("artifact_id") == body["artifact_id"]
                   for j in jobs)
