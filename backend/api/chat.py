"""Chat API — POST /api/chat, GET /api/chat/history, WebSocket /ws/chat.

Enhanced with automatic memory extraction after conversations and
file attachment support with semantic indexing.
"""
from __future__ import annotations
import asyncio
import base64
import json
import logging
import uuid
from pathlib import Path
from typing import Any

import aiofiles
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

from agent.core import AgentCore
from config import get_settings
from memory.manager import create_memory_manager
from models.provider import get_provider

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()

# Track message counts per session for interval-based extraction
_session_message_counts: dict[str, int] = {}

# Image extensions that get vision-described
_IMAGE_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp'}


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    project_id: str = "default"
    stream: bool = True


class ChatResponse(BaseModel):
    session_id: str
    response: str
    project_id: str


class HistoryRequest(BaseModel):
    session_id: str
    project_id: str = "default"
    limit: int = 50


# Active WebSocket connections
_active_connections: dict[str, WebSocket] = {}


@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Send a message to the agent and get a response (non-streaming)."""
    session_id = req.session_id or str(uuid.uuid4())
    provider = get_provider()
    memory = create_memory_manager(
        project_id=req.project_id,
        session_id=session_id,
        provider=provider,
    )

    agent = AgentCore(
        provider=provider,
        memory_manager=memory,
        project_id=req.project_id,
        session_id=session_id,
    )

    full_response = ""
    async for event in agent.chat(req.message, stream=False):
        if event["type"] == "done":
            full_response = event.get("full_response", "")
        elif event["type"] == "error":
            raise HTTPException(status_code=500, detail=event["message"])

    return ChatResponse(
        session_id=session_id,
        response=full_response,
        project_id=req.project_id,
    )


@router.get("/chat/history")
async def get_history(
    session_id: str = Query(...),
    project_id: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """Get conversation history for a session."""
    from memory.episodic import EpisodicMemory
    episodic = EpisodicMemory()
    messages = await episodic.get_history(session_id=session_id, limit=limit)
    return {"session_id": session_id, "messages": messages, "count": len(messages)}


@router.get("/chat/sessions")
async def list_sessions(
    project_id: str = Query(default="default"),
    limit: int = Query(default=20, ge=1, le=100),
) -> dict[str, Any]:
    """List recent chat sessions for a project."""
    from memory.episodic import EpisodicMemory
    episodic = EpisodicMemory()
    sessions = await episodic.get_sessions(project_id=project_id, limit=limit)
    return {"sessions": sessions, "project_id": project_id}


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket) -> None:
    """WebSocket endpoint for streaming chat with the agent."""
    await websocket.accept()
    connection_id = str(uuid.uuid4())
    _active_connections[connection_id] = websocket
    logger.info(f"WebSocket connected: {connection_id}")

    try:
        while True:
            raw = await websocket.receive_text()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "message": "Invalid JSON"})
                continue

            message = data.get("message", "")
            session_id = data.get("session_id") or str(uuid.uuid4())
            project_id = data.get("project_id", "default")

            if not message:
                await websocket.send_json({"type": "error", "message": "Empty message"})
                continue

            # Send session_id back to client
            await websocket.send_json({"type": "session_start", "session_id": session_id})

            provider = get_provider()
            memory = create_memory_manager(
                project_id=project_id,
                session_id=session_id,
                provider=provider,
            )
            agent = AgentCore(
                provider=provider,
                memory_manager=memory,
                project_id=project_id,
                session_id=session_id,
            )

            async for event in agent.chat(message, stream=True):
                try:
                    await websocket.send_json(event)
                except Exception:
                    break

            # Track messages and trigger extraction if interval is set
            extraction_interval = settings.extraction_interval
            if extraction_interval > 0 and memory:
                count = _session_message_counts.get(session_id, 0) + 2  # user + assistant
                _session_message_counts[session_id] = count
                if count >= extraction_interval:
                    _session_message_counts[session_id] = 0
                    # Fire-and-forget extraction
                    asyncio.ensure_future(_run_background_extraction(memory, project_id, session_id))

    except WebSocketDisconnect:
        logger.info(f"WebSocket disconnected: {connection_id}")
    except Exception as e:
        logger.error(f"WebSocket error: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": str(e)})
        except Exception:
            pass
    finally:
        _active_connections.pop(connection_id, None)


@router.post("/chat/attach")
async def attach_file_to_chat(
    file: UploadFile = File(...),
    project_id: str = Query(default="default"),
) -> dict[str, Any]:
    """Upload a file as a chat attachment.

    Saves to workspace/uploads/, auto-indexes into semantic memory,
    and for images generates a text description via the prefill model.
    """
    filename = file.filename or "attachment"
    filename = Path(filename).name  # Strip path components

    # Determine workspace uploads directory
    if project_id and project_id != "default":
        base = settings.projects_dir / project_id / "workspace"
    else:
        base = settings.workspace_dir
    uploads_dir = base / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    dest_path = uploads_dir / filename
    # Handle conflicts
    if dest_path.exists():
        stem = dest_path.stem
        suffix = dest_path.suffix
        counter = 1
        while dest_path.exists():
            dest_path = uploads_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    content = await file.read()
    async with aiofiles.open(dest_path, "wb") as f:
        await f.write(content)

    rel_path = str(dest_path.relative_to(base))
    result: dict[str, Any] = {
        "status": "uploaded",
        "filename": dest_path.name,
        "path": rel_path,
        "size": len(content),
        "indexing": False,
        "description": None,
    }

    # For images: generate a text description via the prefill/vision model
    ext = dest_path.suffix.lower()
    if ext in _IMAGE_EXTENSIONS:
        try:
            description = await _describe_image(dest_path, content)
            if description:
                result["description"] = description
                # Store description as semantic memory
                memory = create_memory_manager(project_id=project_id)
                await memory.semantic.store(
                    content=f"Image '{dest_path.name}': {description}",
                    metadata={
                        "type": "image_description",
                        "source_file": dest_path.name,
                        "source_path": rel_path,
                        "project_id": project_id,
                    },
                )
                result["indexing"] = True
        except Exception as e:
            logger.warning("Image description failed for %s: %s", filename, e)

    # For documents: auto-index if enabled
    if ext not in _IMAGE_EXTENSIONS and settings.auto_index_uploads:
        asyncio.ensure_future(_index_attachment(dest_path, project_id))
        result["indexing"] = True

    return result


async def _describe_image(file_path: Path, content: bytes) -> str | None:
    """Generate a text description of an image using the prefill model.

    Uses base64 vision if the model supports it, otherwise returns a
    basic file metadata description.
    """
    try:
        from models.provider import get_prefill_provider
        provider = get_prefill_provider()

        b64 = base64.b64encode(content).decode("utf-8")
        ext = file_path.suffix.lower().lstrip(".")
        mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"

        result = await provider.chat_complete([
            {"role": "system", "content": (
                "You are a visual analysis assistant. Describe this image concisely "
                "in 1-3 sentences. Focus on the key content, any text visible, "
                "diagrams, charts, or notable elements. Be factual and specific."
            )},
            {"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                {"type": "text", "text": f"Describe this image ({file_path.name}):"},
            ]},
        ])
        desc = (result.get("content") or "").strip()
        if desc and len(desc) > 10:
            logger.info("Generated description for %s: %s", file_path.name, desc[:100])
            return desc
    except Exception as e:
        logger.debug("Vision description failed (model may not support images): %s", e)

    # Fallback: basic metadata description
    size_kb = len(content) / 1024
    return f"Image file ({file_path.suffix.upper()}, {size_kb:.0f}KB)"


async def _index_attachment(file_path: Path, project_id: str) -> None:
    """Background task to index a chat attachment into semantic memory."""
    try:
        from memory.file_indexer import SUPPORTED_EXTENSIONS
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return

        memory = create_memory_manager(project_id=project_id)
        from memory.file_indexer import FileIndexer
        indexer = FileIndexer(
            memory_manager=memory,
            project_id=project_id,
            chunk_size=settings.file_chunk_size,
            chunk_overlap=settings.file_chunk_overlap,
        )
        result = await indexer.index_file(file_path)
        if not result.get("skipped"):
            logger.info(
                "Indexed chat attachment %s: %d chunks",
                file_path.name, result.get("chunks_stored", 0),
            )
    except Exception as e:
        logger.warning("Attachment indexing failed for %s: %s", file_path.name, e)


async def _run_background_extraction(
    memory_manager: Any,
    project_id: str,
    session_id: str,
) -> None:
    """Run extraction in the background without blocking chat."""
    try:
        stats = await memory_manager.run_extraction_on_recent(message_count=20)
        total = sum(stats.values())
        if total > 0:
            logger.info(
                "Background extraction for session %s: %s",
                session_id[:8], stats,
            )
    except Exception as e:
        logger.warning("Background extraction failed: %s", e)
