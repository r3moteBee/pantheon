# Image Artifacts + Background Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist chat-attached images as first-class Pantheon artifacts (binary blob storage already supported by ArtifactStore) and decouple vision extraction from the chat SSE stream by running it as a background `image_extraction` job — so switching apps, losing the WebSocket, or having a slow vision model can no longer break the extraction.

**Architecture:** Three coordinated changes. (1) `POST /api/chat/attach` is rewritten to upload to ArtifactStore (`chat-attachments/YYYY-MM-DD/...`) instead of `workspace/uploads/`, and enqueue an `image_extraction` JobStore row. (2) A new `image_extraction` job handler runs offline: vision call → caption + OCR text + topic JSON → updates the image artifact's tags/title and creates a sibling `<path>.extraction.md` artifact (text-typed, which auto-routes through `_index_typed_topics_to_graph`). (3) `_build_user_content` in `agent/core.py` learns to load image bytes from `ArtifactStore._load_blob` when the message references `artifact:<id>` — preserving the snappy "what's in this photo" inline-vision UX for same-turn questions, while the durable extraction proceeds in the JobWorker independent of the SSE stream.

**Tech Stack:** Python 3.11 + FastAPI + SQLite (jobs + artifacts) + asyncio in-process worker; React + Vite frontend; existing `backend/utils/vision.describe_image` helper for the vision call; existing `LLMDefaultExtractor` pattern for structured-topic prompts.

---

## File Structure

**Create:**
- `backend/jobs/handlers/image_extraction.py` — new handler (caption + OCR + topics → update image tags/title + create `.extraction.md` sibling)
- `backend/tests/integration/test_image_extraction.py` — integration test against the handler with a fake vision provider

**Modify:**
- `backend/api/chat.py:547-619` — `/chat/attach` endpoint switches storage from `workspace/uploads/` to `ArtifactStore.create`, enqueues `image_extraction` job for image MIME types, returns `{artifact_id, path, content_type, …}` instead of workspace path
- `backend/agent/core.py:208-260` — `_build_user_content` adds `artifact:<id>` reference parsing + load via `ArtifactStore._load_blob` (workspace fallback retained for backcompat)
- `backend/jobs/handlers/bootstrap.py:24-49` — register `image_extraction` import
- `frontend/src/api/client.js:48-55` — `chatApi.attachFile` documentation/response shape comment
- `frontend/src/components/Chat.jsx:497-516, 633-660` — `uploadAttachments` reads `artifact_id` + `path` from response; `sendMessage` builds new attachment note format `"[image: <path> (artifact:<id>)]"`
- `frontend/package.json:4` — version bump `2026.05.17.H6` → `2026.05.19.H1`

---

## Conventions used in this plan

- **Artifact path convention.** Chat-attached images land at `chat-attachments/YYYY-MM-DD/<filename>`. The project slug is prepended automatically by `ArtifactStore.create` (consistent with all other artifact paths). Sibling text companion: same path + `.extraction.md` suffix → e.g. `chat-attachments/2026-05-19/screenshot.png.extraction.md`.
- **Message marker for vision-inline.** Frontend writes `[image: <relative-path> (artifact:<artifact_id>)]` into the user message. The agent's `_build_user_content` regex extracts both fields; it prefers loading bytes via `ArtifactStore._load_blob` keyed on `artifact_id`, falling back to `workspace/uploads/<filename>` if not found (so existing pre-deploy chats still work).
- **Idempotency.** The `image_extraction` handler reads `artifact["sha256"]`; if a sibling `<path>.extraction.md` already exists AND its frontmatter `parent_sha256` matches, return `{"status": "skipped", "reason": "already extracted"}`. Otherwise (re-)create.
- **Failure soft-lands.** Handler exceptions never raise out — they post a single `_image_extraction_failed:` line into `parent_session_id` (if present) and return `{"status": "failed", "error": ...}` so the job row records the error but the chat doesn't see a hard crash.

---

## Task 1: Switch `/chat/attach` to ArtifactStore + enqueue extraction job

**Files:**
- Modify: `backend/api/chat.py:547-619` (the `attach_file_to_chat` function)
- Test: `backend/tests/integration/test_chat_attach_artifacts.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_chat_attach_artifacts.py`:

```python
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
```

- [ ] **Step 2: Run test — confirm it fails**

Run: `cd /home/pan/pantheon/backend && /home/pan/pantheon/.venv/bin/python -m pytest tests/integration/test_chat_attach_artifacts.py -v`
Expected: FAIL with `KeyError: 'artifact_id'` or similar (current endpoint returns workspace `path`, no artifact_id).

- [ ] **Step 3: Rewrite `attach_file_to_chat` in `backend/api/chat.py`**

Replace lines 547-619 (`@router.post("/chat/attach")` block) with:

```python
@router.post("/chat/attach")
async def attach_file_to_chat(
    file: UploadFile = File(...),
    project_id: str = Query(default="default"),
) -> dict[str, Any]:
    """Upload a chat attachment into the artifact store.

    Images: stored as binary artifact under chat-attachments/YYYY-MM-DD/,
    AND an `image_extraction` job is enqueued for offline vision + OCR +
    topic extraction. The job survives chat SSE drops.

    Non-images: stored as artifact; if text-y, embedder schedules a
    semantic embed.

    Returns {artifact_id, path, content_type, size, filename, indexing,
             extraction_job_id?}.
    """
    from datetime import datetime, timezone
    from artifacts.store import get_store as get_artifact_store, is_text_type
    from artifacts import embedder
    from jobs.store import get_store as get_job_store

    filename = Path(file.filename or "attachment").name
    content = await file.read()
    content_type = file.content_type or "application/octet-stream"
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = f"chat-attachments/{today}/{filename}"

    artifact_store = get_artifact_store()
    try:
        artifact = artifact_store.create(
            project_id=project_id,
            path=path,
            content=content,
            content_type=content_type,
            title=filename,
            tags=["chat-attachment"],
            source={"kind": "chat-attach", "filename": filename},
            edited_by="user",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result: dict[str, Any] = {
        "status": "uploaded",
        "artifact_id": artifact["id"],
        "path": artifact["path"],
        "content_type": artifact["content_type"],
        "size": artifact["size_bytes"],
        "filename": filename,
        "indexing": False,
    }

    ext = Path(filename).suffix.lower()
    if ext in _IMAGE_EXTENSIONS:
        # Enqueue background extraction — vision call decoupled from chat SSE
        job = get_job_store().create(
            job_type="image_extraction",
            project_id=project_id,
            title=f"Extract: {filename}",
            description=f"Vision + OCR + topics for {path}",
            payload={"artifact_id": artifact["id"]},
            timeout_seconds=300,
        )
        result["extraction_job_id"] = job["id"]
        result["indexing"] = True
    elif is_text_type(content_type):
        embedder.schedule_embed(artifact["id"], project_id)
        result["indexing"] = True

    return result
```

Also remove the now-dead helpers `_describe_image` import + the `_index_attachment` function below the endpoint (lines 622-647). Keep `_IMAGE_EXTENSIONS` (still used).

Run: `cd /home/pan/pantheon/backend && /home/pan/pantheon/.venv/bin/python -c "from api.chat import attach_file_to_chat; print('import ok')"`
Expected: `import ok` (no NameError from removed helpers).

- [ ] **Step 4: Skeleton image_extraction handler so the test sees a job_type that handler-bootstrap won't reject**

Create `backend/jobs/handlers/image_extraction.py`:

```python
"""image_extraction handler — offline vision + OCR + topic extraction
for image artifacts. Full implementation lands in Task 2; this skeleton
exists so /chat/attach can enqueue jobs without 'No handler registered'.
"""
from __future__ import annotations

import logging
from typing import Any

from jobs.context import JobContext
from jobs.handlers import register

logger = logging.getLogger(__name__)


@register("image_extraction", default_timeout_seconds=300,
          description="Vision + OCR + topic extraction for an image artifact.")
async def handle_image_extraction(ctx: JobContext) -> dict[str, Any]:
    artifact_id = (ctx.payload or {}).get("artifact_id")
    if not artifact_id:
        return {"status": "skipped", "reason": "missing artifact_id"}
    await ctx.heartbeat(progress="(skeleton — implemented in Task 2)")
    return {"status": "skipped", "reason": "handler skeleton", "artifact_id": artifact_id}
```

Then register it in `backend/jobs/handlers/bootstrap.py` by inserting after the `file_indexing` block (around line 41):

```python
    try:
        from jobs.handlers import image_extraction   # noqa: F401
    except Exception as e:
        logger.debug("image_extraction handler unavailable: %s", e)
```

- [ ] **Step 5: Run tests — confirm passes**

Run: `cd /home/pan/pantheon/backend && /home/pan/pantheon/.venv/bin/python -m pytest tests/integration/test_chat_attach_artifacts.py -v`
Expected: both tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/pan/pantheon
git add backend/api/chat.py backend/jobs/handlers/image_extraction.py \
        backend/jobs/handlers/bootstrap.py \
        backend/tests/integration/test_chat_attach_artifacts.py
git commit -m "backend/chat: chat-attach uploads to ArtifactStore + enqueues image_extraction job"
```

---

## Task 2: Implement `image_extraction` handler (vision + OCR + topics + sibling artifact)

**Files:**
- Modify: `backend/jobs/handlers/image_extraction.py` (replace skeleton from Task 1)
- Test: `backend/tests/integration/test_image_extraction.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_image_extraction.py`:

```python
"""Verify image_extraction handler updates image artifact + creates
sibling .extraction.md artifact."""
from __future__ import annotations

import asyncio
import os
import tempfile
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
    a = get_artifact_store().create(
        project_id="default",
        path="chat-attachments/2026-05-19/test.png",
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
    sibling = store.get_by_path("default", "chat-attachments/2026-05-19/test.png.extraction.md")
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
```

- [ ] **Step 2: Run test — confirm it fails**

Run: `cd /home/pan/pantheon/backend && /home/pan/pantheon/.venv/bin/python -m pytest tests/integration/test_image_extraction.py -v`
Expected: FAIL — handler is still the skeleton that returns `status: skipped, reason: handler skeleton`.

- [ ] **Step 3: Replace `backend/jobs/handlers/image_extraction.py` with the real implementation**

```python
"""image_extraction handler — offline vision + OCR + topic extraction.

Reads the image bytes from ArtifactStore, runs a vision-capable LLM to
produce caption + OCR text + structured topics, then:
  1. Updates the image artifact's tags (topics + 'vision-extracted')
     and title (first 60 chars of caption).
  2. Creates a sibling text artifact at <path>.extraction.md with full
     frontmatter — flows through the standard typed-topics graph
     extractor via the embedder/file-index path.

Idempotent: if the sibling already exists with parent_sha256 matching
the current image, returns {status: skipped, reason: already extracted}.
"""
from __future__ import annotations

import base64
import json
import logging
from typing import Any

from jobs.context import JobContext, pinger_for
from jobs.handlers import register

logger = logging.getLogger(__name__)

_VISION_SYSTEM_PROMPT = (
    "You are a visual analysis assistant. Given an image, produce a "
    "JSON object with three fields:\n"
    '  - "caption": one or two sentences describing the image\n'
    '  - "ocr_text": any visible text in the image, verbatim (empty string if none)\n'
    '  - "topics": an array of {label, type} where type is one of: '
    "concept, technology, vendor, organization, person, market_segment, framework, product\n"
    "Return ONLY the JSON object, no preamble. Topics: 3-7 items, lowercase labels."
)


async def _call_vision_extractor(image_bytes: bytes, mime: str) -> dict[str, Any]:
    """Run vision model and return parsed {caption, ocr_text, topics}.

    Tries providers in order: vision → primary → prefill. Raises on
    total failure so the handler can record an error.
    """
    from models.provider import get_vision_provider, get_provider, get_prefill_provider

    b64 = base64.b64encode(image_bytes).decode("utf-8")
    messages = [
        {"role": "system", "content": _VISION_SYSTEM_PROMPT},
        {"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
            {"type": "text", "text": "Extract caption, OCR text, and topics."},
        ]},
    ]

    providers: list[tuple[str, Any]] = []
    vp = get_vision_provider()
    if vp:
        providers.append(("vision", lambda: vp))
    providers.append(("primary", get_provider))
    providers.append(("prefill", get_prefill_provider))

    last_err: Exception | None = None
    for label, get_prov in providers:
        try:
            provider = get_prov()
            resp = await provider.chat_complete(messages)
            text = (resp.get("content") or "").strip()
            # Strip code fences if present
            if text.startswith("```"):
                text = text.split("```", 2)[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip().rstrip("`").strip()
            data = json.loads(text)
            return {
                "caption": str(data.get("caption", "")).strip(),
                "ocr_text": str(data.get("ocr_text", "")).strip(),
                "topics": [t for t in (data.get("topics") or [])
                           if isinstance(t, dict) and t.get("label")],
            }
        except Exception as e:
            logger.debug("vision via %s failed: %s", label, e)
            last_err = e
            continue
    raise RuntimeError(f"all vision providers failed; last error: {last_err}")


def _build_extraction_markdown(*, image_path: str, parent_id: str,
                               parent_sha: str, vision: dict[str, Any]) -> str:
    """Render the .extraction.md sibling artifact body."""
    topics_yaml = "\n".join(
        f"  - label: {t['label']}\n    type: {t.get('type', 'concept')}"
        for t in vision["topics"]
    )
    return (
        "---\n"
        f"parent_artifact_id: {parent_id}\n"
        f"parent_sha256: {parent_sha}\n"
        f"source_image: {image_path}\n"
        "extraction_kind: image_vision\n"
        "topics:\n"
        f"{topics_yaml}\n"
        "---\n\n"
        f"# {vision['caption'] or 'Image extraction'}\n\n"
        f"**Source image:** `{image_path}`\n\n"
        "## Caption\n\n"
        f"{vision['caption']}\n\n"
        "## OCR Text\n\n"
        f"{vision['ocr_text'] or '_(no text detected)_'}\n"
    )


@register("image_extraction", default_timeout_seconds=300,
          description="Vision + OCR + topic extraction for an image artifact.")
async def handle_image_extraction(ctx: JobContext) -> dict[str, Any]:
    pl = ctx.payload or {}
    artifact_id = pl.get("artifact_id")
    if not artifact_id:
        return {"status": "skipped", "reason": "missing artifact_id"}

    from artifacts.store import get_store as get_artifact_store
    store = get_artifact_store()
    artifact = store.get(artifact_id)
    if not artifact:
        return {"status": "skipped", "reason": "artifact not found", "artifact_id": artifact_id}

    image_path: str = artifact["path"]
    parent_sha: str = artifact.get("sha256") or ""
    sibling_path = f"{image_path}.extraction.md"

    # Idempotency check
    existing = store.get_by_path(artifact["project_id"], sibling_path)
    if existing:
        existing_body = existing.get("content") or ""
        if f"parent_sha256: {parent_sha}" in existing_body:
            return {"status": "skipped", "reason": "already extracted",
                    "artifact_id": artifact_id,
                    "extraction_artifact_id": existing["id"]}

    # Load image bytes
    blob_path = artifact.get("blob_path")
    if not blob_path:
        return {"status": "failed", "error": "image artifact has no blob_path",
                "artifact_id": artifact_id}
    await ctx.heartbeat(progress="Loading image bytes…")
    image_bytes = store._load_blob(blob_path)

    # Vision call (long single-await — wrap in pinger so watchdog stays happy)
    await ctx.heartbeat(progress="Running vision extraction…")
    try:
        async with pinger_for(ctx, interval=20.0):
            vision = await _call_vision_extractor(
                image_bytes, artifact["content_type"]
            )
    except Exception as e:
        logger.warning("image_extraction failed for %s: %s", artifact_id, e)
        return {"status": "failed", "error": str(e)[:500],
                "artifact_id": artifact_id}

    # 1. Update image artifact: tags = old + topic labels + sentinel
    await ctx.heartbeat(progress="Updating image artifact metadata…")
    existing_tags = list(artifact.get("tags") or [])
    topic_tags = [t["label"] for t in vision["topics"]]
    new_tags = list(dict.fromkeys(existing_tags + topic_tags + ["vision-extracted"]))
    caption_title = (vision["caption"] or artifact["title"] or "").strip()
    if len(caption_title) > 80:
        caption_title = caption_title[:77] + "…"
    store.update(
        artifact_id,
        title=caption_title or artifact["title"],
        tags=new_tags,
        edit_summary="vision-extracted",
        edited_by="image_extraction",
    )

    # 2. Create sibling extraction artifact (text/markdown → embedder picks it up)
    body = _build_extraction_markdown(
        image_path=image_path, parent_id=artifact_id,
        parent_sha=parent_sha, vision=vision,
    )
    sibling = store.create(
        project_id=artifact["project_id"],
        path=sibling_path,
        content=body,
        content_type="text/markdown",
        title=f"Extraction: {image_path.rsplit('/', 1)[-1]}",
        tags=["image-extraction", "chat-attachment"] + topic_tags,
        source={"kind": "image_extraction", "parent_artifact_id": artifact_id},
        edited_by="image_extraction",
    )

    # 3. Optional: schedule semantic embed for the new sibling so RAG can find it
    try:
        from artifacts import embedder
        embedder.schedule_embed(sibling["id"], artifact["project_id"])
    except Exception:
        logger.debug("schedule_embed for sibling failed", exc_info=True)

    # 4. Notify parent session if this came from a chat upload
    parent_session_id = pl.get("parent_session_id")
    if parent_session_id:
        try:
            from memory.episodic import EpisodicMemory
            ep = EpisodicMemory()
            topics_str = ", ".join(topic_tags[:5]) or "(no topics)"
            msg = (
                f"📷 **Image analyzed:** {caption_title}\n\n"
                f"_Topics: {topics_str}_  "
                f"·  _Extraction artifact:_ `{sibling_path}`"
            )
            await ep.save_message(
                session_id=parent_session_id,
                project_id=artifact["project_id"],
                role="assistant", content=msg,
                metadata={
                    "kind": "image_extraction_completion_notice",
                    "image_artifact_id": artifact_id,
                    "extraction_artifact_id": sibling["id"],
                },
            )
        except Exception:
            logger.debug("parent-session notify failed", exc_info=True)

    return {
        "status": "completed",
        "artifact_id": artifact_id,
        "extraction_artifact_id": sibling["id"],
        "caption": vision["caption"],
        "topic_count": len(vision["topics"]),
    }
```

- [ ] **Step 4: Run tests — confirm passes**

Run: `cd /home/pan/pantheon/backend && /home/pan/pantheon/.venv/bin/python -m pytest tests/integration/test_image_extraction.py -v`
Expected: both `test_handler_updates_image_and_creates_sibling` and `test_handler_idempotent_on_same_sha` PASS.

- [ ] **Step 5: Run full integration suite to catch regressions**

Run: `cd /home/pan/pantheon/backend && /home/pan/pantheon/.venv/bin/python -m pytest tests/integration/ -v`
Expected: all tests PASS (186 prior tests + 2 from Task 1 + 2 from this task = 190).

- [ ] **Step 6: Commit**

```bash
cd /home/pan/pantheon
git add backend/jobs/handlers/image_extraction.py \
        backend/tests/integration/test_image_extraction.py
git commit -m "backend/jobs: implement image_extraction handler (caption + OCR + topics + sibling artifact)"
```

---

## Task 3: Teach `_build_user_content` to load images from ArtifactStore

**Files:**
- Modify: `backend/agent/core.py:208-260` (the `_build_user_content` method)
- Test: `backend/tests/integration/test_agent_multimodal_artifact.py` (new)

- [ ] **Step 1: Write the failing test**

Create `backend/tests/integration/test_agent_multimodal_artifact.py`:

```python
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
    artifact = get_artifact_store().create(
        project_id="default",
        path="chat-attachments/2026-05-19/inline.png",
        content=_png_bytes(),
        content_type="image/png",
        title="inline.png", tags=["chat-attachment"], edited_by="user",
    )

    agent = AgentCore(provider=None, memory_manager=None, project_id="default")
    msg = (
        "what is in this image?\n\n"
        f"[image: chat-attachments/2026-05-19/inline.png (artifact:{artifact['id']})]"
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
```

- [ ] **Step 2: Run test — confirm it fails**

Run: `cd /home/pan/pantheon/backend && /home/pan/pantheon/.venv/bin/python -m pytest tests/integration/test_agent_multimodal_artifact.py -v`
Expected: `test_build_user_content_inlines_artifact_image` FAILS — current regex only matches `uploads/<filename>`, not the new `[image: ... (artifact:<id>)]` form.

- [ ] **Step 3: Update `_build_user_content` in `backend/agent/core.py`**

Replace lines 208-260 (the entire `_build_user_content` method) with:

```python
    def _build_user_content(self, message: str) -> str | list[dict]:
        """Build multimodal content blocks if the message references images.

        Two supported reference forms:
          1. New (artifact-backed):
             "[image: <path> (artifact:<artifact_id>)]"
             — bytes loaded from ArtifactStore._load_blob
          2. Legacy (workspace-backed, pre-2026.05.19):
             "uploads/<filename>.png"
             — bytes loaded from workspace/uploads/

        Returns the original string when no images are found, otherwise a
        list of content blocks (text + image_url).
        """
        image_blocks: list[dict] = []

        # ── Form 1: artifact references ────────────────────────────────
        artifact_pattern = re.compile(
            r"\[image:[^\]]*?\(artifact:([a-zA-Z0-9_\-]+)\)\]",
            re.IGNORECASE,
        )
        seen_artifacts: set[str] = set()
        artifact_store = None
        for match in artifact_pattern.finditer(message):
            artifact_id = match.group(1)
            if artifact_id in seen_artifacts:
                continue
            seen_artifacts.add(artifact_id)
            try:
                if artifact_store is None:
                    from artifacts.store import get_store as _gs
                    artifact_store = _gs()
                a = artifact_store.get(artifact_id)
                if not a or not a.get("blob_path"):
                    logger.debug("Artifact %s not found or has no blob", artifact_id)
                    continue
                if not (a.get("content_type") or "").startswith("image/"):
                    logger.debug("Artifact %s is not an image", artifact_id)
                    continue
                raw = artifact_store._load_blob(a["blob_path"])
                if len(raw) > _MAX_IMAGE_SIZE:
                    logger.debug("Artifact %s exceeds inline size", artifact_id)
                    continue
                b64 = base64.b64encode(raw).decode("utf-8")
                image_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{a['content_type']};base64,{b64}"},
                })
                logger.info("Inlined artifact image %s (%d KB)", artifact_id, len(raw) // 1024)
            except Exception as e:
                logger.warning("Failed to inline artifact %s: %s", artifact_id, e)

        # ── Form 2: legacy workspace uploads ──────────────────────────
        workspace_pattern = re.compile(
            r"uploads/(.+?\.(?:png|jpe?g|gif|webp|bmp))",
            re.IGNORECASE,
        )
        if self.project_id and self.project_id != "default":
            base = settings.projects_dir / self.project_id / "workspace"
        else:
            base = settings.workspace_dir
        seen_files: set[str] = set()
        for match in workspace_pattern.finditer(message):
            filename = match.group(1)
            if filename in seen_files:
                continue
            seen_files.add(filename)
            candidate = base / "uploads" / filename
            if candidate.exists() and candidate.stat().st_size <= _MAX_IMAGE_SIZE:
                try:
                    raw = candidate.read_bytes()
                    b64 = base64.b64encode(raw).decode("utf-8")
                    ext = candidate.suffix.lower().lstrip(".")
                    mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                    image_blocks.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:{mime};base64,{b64}"},
                    })
                    logger.info("Inlined workspace image %s (%d KB)", candidate.name, len(raw) // 1024)
                except Exception as e:
                    logger.warning("Failed to inline workspace image %s: %s", filename, e)

        if not image_blocks:
            return message

        return [{"type": "text", "text": message}, *image_blocks]
```

- [ ] **Step 4: Run tests — confirm passes**

Run: `cd /home/pan/pantheon/backend && /home/pan/pantheon/.venv/bin/python -m pytest tests/integration/test_agent_multimodal_artifact.py -v`
Expected: all three tests PASS.

- [ ] **Step 5: Run full integration suite to catch regressions**

Run: `cd /home/pan/pantheon/backend && /home/pan/pantheon/.venv/bin/python -m pytest tests/integration/ -v`
Expected: all tests PASS (192 total now).

- [ ] **Step 6: Commit**

```bash
cd /home/pan/pantheon
git add backend/agent/core.py backend/tests/integration/test_agent_multimodal_artifact.py
git commit -m "backend/agent: _build_user_content loads images from ArtifactStore by id"
```

---

## Task 4: Frontend — use artifact_id in attachment flow

**Files:**
- Modify: `frontend/src/api/client.js:48-55` (jsdoc on `chatApi.attachFile`)
- Modify: `frontend/src/components/Chat.jsx:497-516, 633-660` (`uploadAttachments` + `sendMessage` building the attachment note)

- [ ] **Step 1: Update `chatApi.attachFile` jsdoc in `frontend/src/api/client.js`**

Replace lines 48-55 with:

```javascript
  /**
   * Upload a file as a chat attachment. Backend stores it in ArtifactStore
   * under chat-attachments/YYYY-MM-DD/ and, for images, enqueues a
   * background image_extraction job.
   *
   * Response shape:
   *   { status, artifact_id, path, content_type, size, filename,
   *     indexing, extraction_job_id? }
   */
  attachFile: (file, projectId) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post('/api/chat/attach', formData, {
      params: { project_id: projectId },
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
```

- [ ] **Step 2: Update `uploadAttachments` in `frontend/src/components/Chat.jsx`**

Replace lines 497-516 with:

```javascript
  const uploadAttachments = async (files) => {
    if (files.length === 0) return []
    const projectId = activeProject?.id || 'default'
    const results = []
    for (const file of files) {
      try {
        const res = await chatApi.attachFile(file, projectId)
        results.push({
          name: res.data.filename,
          path: res.data.path,
          size: res.data.size,
          artifactId: res.data.artifact_id,
          contentType: res.data.content_type,
          indexing: res.data.indexing || false,
          extractionJobId: res.data.extraction_job_id || null,
        })
      } catch (err) {
        addNotification({ type: 'error', message: `Failed to upload ${file.name}: ${err.message}` })
      }
    }
    return results
  },
```

- [ ] **Step 3: Update the attachment-note builder in `sendMessage`**

Find the block around lines 646-655 (the `if (uploadedFiles.length > 0)` section in `sendMessage`) and replace it with:

```javascript
    // Build message with attachment context
    let fullMessage = msg
    if (uploadedFiles.length > 0) {
      const lines = uploadedFiles.map((f) => {
        const isImage = (f.contentType || '').startsWith('image/')
        if (isImage) {
          // Marker the backend agent's _build_user_content regex picks up:
          //   [image: <path> (artifact:<id>)]
          const status = f.extractionJobId
            ? '_extracting in background…_'
            : '_no extraction queued_'
          return `- [image: ${f.path} (artifact:${f.artifactId})] ${status}`
        }
        return `- ${f.name} (artifact:${f.artifactId} — ${f.path})`
      }).join('\n')
      const note = `\n\n[Attached files — saved as artifacts]\n${lines}`
      fullMessage = msg ? msg + note : `Please review the attached files:\n${lines}`
    }
```

- [ ] **Step 4: Manual UI verification**

Restart the local stack (Pantheon convention — Brent runs this, but the worker can verify the build succeeds):

```bash
cd /home/pan/pantheon/frontend && VITE_API_URL="" npm run build
```

Expected: clean build, no missing-import errors. (Do NOT restart the backend in this task — Task 6 owns the full deploy.)

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/api/client.js frontend/src/components/Chat.jsx
git commit -m "frontend/chat: attachments reference artifact_id; new marker for agent multimodal parse"
```

---

## Task 5: Wire `parent_session_id` through chat-attach for completion notice

**Files:**
- Modify: `backend/api/chat.py` — `/chat/attach` endpoint (extend Task 1's rewrite)

This task only matters when the user-facing flow is "paste, then send" — the upload happens before the message is sent, so the frontend doesn't know the eventual session_id yet. We make the frontend send the active session_id alongside the upload so the extraction handler can post the completion notice into the right chat.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/integration/test_chat_attach_artifacts.py`:

```python
def test_attach_threads_parent_session_id(client):
    files = {"file": ("test2.png", io.BytesIO(_png_bytes()), "image/png")}
    res = client.post(
        "/api/chat/attach",
        files=files,
        params={"project_id": "default", "session_id": "sess-abc-123"},
    )
    assert res.status_code == 200
    body = res.json()
    assert "extraction_job_id" in body

    # Job payload includes parent_session_id
    from jobs.store import get_store as get_job_store
    job = get_job_store().get(body["extraction_job_id"])
    assert job["payload"].get("parent_session_id") == "sess-abc-123"
```

- [ ] **Step 2: Run test — confirm it fails**

Run: `cd /home/pan/pantheon/backend && /home/pan/pantheon/.venv/bin/python -m pytest tests/integration/test_chat_attach_artifacts.py::test_attach_threads_parent_session_id -v`
Expected: FAIL — current endpoint doesn't accept `session_id`.

- [ ] **Step 3: Add `session_id` param to `/chat/attach`**

In `backend/api/chat.py`, update the signature of `attach_file_to_chat`:

```python
@router.post("/chat/attach")
async def attach_file_to_chat(
    file: UploadFile = File(...),
    project_id: str = Query(default="default"),
    session_id: str | None = Query(default=None),
) -> dict[str, Any]:
```

In the image-enqueue block (the `if ext in _IMAGE_EXTENSIONS:` branch from Task 1), update the payload to include `parent_session_id`:

```python
        payload_dict: dict[str, Any] = {"artifact_id": artifact["id"]}
        if session_id:
            payload_dict["parent_session_id"] = session_id
        job = get_job_store().create(
            job_type="image_extraction",
            project_id=project_id,
            title=f"Extract: {filename}",
            description=f"Vision + OCR + topics for {path}",
            payload=payload_dict,
            timeout_seconds=300,
        )
```

- [ ] **Step 4: Update frontend to pass `sessionId`**

In `frontend/src/api/client.js`, update `attachFile`:

```javascript
  attachFile: (file, projectId, sessionId = null) => {
    const formData = new FormData()
    formData.append('file', file)
    const params = { project_id: projectId }
    if (sessionId) params.session_id = sessionId
    return api.post('/api/chat/attach', formData, {
      params,
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
```

In `frontend/src/components/Chat.jsx`, update the call site in `uploadAttachments` (the one you edited in Task 4):

```javascript
        const res = await chatApi.attachFile(file, projectId, sessionId)
```

(`sessionId` is the existing piece of state from `useStore` — already in scope in the `Chat` component.)

- [ ] **Step 5: Run tests — confirm passes**

Run: `cd /home/pan/pantheon/backend && /home/pan/pantheon/.venv/bin/python -m pytest tests/integration/test_chat_attach_artifacts.py -v`
Expected: all three tests PASS.

- [ ] **Step 6: Commit**

```bash
cd /home/pan/pantheon
git add backend/api/chat.py backend/tests/integration/test_chat_attach_artifacts.py \
        frontend/src/api/client.js frontend/src/components/Chat.jsx
git commit -m "backend+frontend: thread session_id through chat-attach for extraction completion notice"
```

---

## Task 6: Version bump + full integration sweep + deploy command

**Files:**
- Modify: `frontend/package.json:4`

- [ ] **Step 1: Bump version**

In `frontend/package.json`, change line 4:

```json
  "version": "2026.05.19.H1",
```

- [ ] **Step 2: Run the complete integration test suite**

Run: `cd /home/pan/pantheon/backend && /home/pan/pantheon/.venv/bin/python -m pytest tests/integration/ -v`
Expected: ALL tests pass (186 prior + 4 chat-attach + 2 image-extraction + 3 multimodal = ~195 total). Zero failures, zero errors.

- [ ] **Step 3: Build frontend**

Run: `cd /home/pan/pantheon/frontend && VITE_API_URL="" npm run build`
Expected: clean build with no errors. Note the build output bundle hash in your handoff for Brent.

- [ ] **Step 4: Commit version bump**

```bash
cd /home/pan/pantheon
git add frontend/package.json
git commit -m "release: bump frontend for image artifacts + background extraction (2026.05.19.H1)"
```

- [ ] **Step 5: Output the deploy command for Brent**

In your final summary message, give Brent this block to run himself (per memory: he runs deploys; do not ssh):

```bash
cd ~/pantheon && git pull
~/pantheon/.venv/bin/pip install -r backend/requirements.txt
cd frontend && VITE_API_URL="" npm run build && cd ..
./stop.sh && pkill -f "uvicorn main:app" 2>/dev/null
find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
./start.sh && sleep 3 && curl -s http://localhost:8000/api/health
```

Expected: the curl returns JSON containing `"version":"2026.05.19.H1"`.

- [ ] **Step 6: Smoke checklist for Brent (post-deploy verification)**

Provide this checklist in the handoff message:

1. Open Pantheon UI, paste a screenshot into chat, send a message asking "what's in this image?"
2. Expect: immediate inline vision answer (same UX as today, but now backed by an artifact)
3. Open the Artifacts page — find the image under `chat-attachments/2026-05-19/`
4. Open the Tasks tab — there should be a completed `image_extraction` job
5. Within ~30s, a follow-up assistant message lands in the same chat: `📷 **Image analyzed:** …`
6. Open the sibling `<image>.extraction.md` artifact — frontmatter should contain `parent_artifact_id`, `parent_sha256`, `topics`, and the OCR + caption body
7. Switch apps mid-extraction (paste a fresh screenshot, immediately switch to a different desktop window for 30s) — confirm the extraction completes regardless

---

## Self-Review Notes

Coverage check against spec:
- ✅ "Save photos along with other artifacts" — Task 1 routes uploads to ArtifactStore
- ✅ "Background task for extraction" — Tasks 1+2 enqueue + implement `image_extraction` job
- ✅ "Fails when switching apps / times out" — JobWorker runs in-process asyncio loop, decoupled from SSE; heartbeats keep watchdog happy
- ✅ "Run inference on the primary source file" — image bytes live in content-addressed blob; `_build_user_content` in Task 3 loads them on demand
- ✅ Inline vision + background extraction (Q&A from Q1) — both paths kept; Task 3 covers inline, Task 2 covers offline
- ✅ Both image-metadata + text companion artifact (Q2) — Task 2 updates image tags+title AND creates `.extraction.md` sibling

Type/name consistency:
- `artifact_id` used everywhere (not `artifactId` in Python, `artifactId` in JS — convention-correct per language)
- `parent_session_id` matches existing autonomous_task handler convention
- `chat-attachments/YYYY-MM-DD/` path used consistently across Tasks 1, 2, 3
- `[image: <path> (artifact:<id>)]` marker format identical in Task 3 regex and Task 4 frontend builder

Risks / things to watch in review:
- The skeleton handler in Task 1 is replaced in Task 2 — if subagents run out of order, the test in Task 1 needs the skeleton present. Task 2 must run AFTER Task 1.
- `_call_vision_extractor` is mocked in tests because the local vision model is environment-dependent. Real-traffic verification happens in Task 6 Step 6.
- The legacy `workspace/uploads/` fallback in `_build_user_content` (Task 3) prevents breaking ongoing chats during the deploy.
