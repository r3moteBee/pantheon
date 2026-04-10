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
from skills.resolver import resolve_explicit, resolve_auto, build_skill_context
from skills.registry import get_skill_registry
from skills.models import SkillDiscoveryMode

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()

# Track message counts per session for interval-based extraction
_session_message_counts: dict[str, int] = {}

# Image extensions that get vision-described
from utils.vision import IMAGE_EXTENSIONS as _IMAGE_EXTENSIONS, describe_image as _describe_image


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

# Pending skill suggestions awaiting user accept/decline
_pending_suggestions: dict[str, dict] = {}


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

            # Handle keepalive pings from the frontend
            if data.get("type") == "ping":
                await websocket.send_json({"type": "pong"})
                continue

            # ── Skill accept / decline from interactive suggestion ───
            if data.get("type") == "skill_accept":
                suggestion_id = data.get("suggestion_id")
                pending = _pending_suggestions.pop(suggestion_id, None)
                if not pending:
                    await websocket.send_json({"type": "error", "message": "Suggestion expired or not found"})
                    continue
                # Re-use the original message, session, and project but now with skill context
                message = pending["message"]
                session_id = pending["session_id"]
                project_id = pending["project_id"]
                skill_name = pending["skill"]
                registry = get_skill_registry()
                skill = registry.get(skill_name)
                skill_context = build_skill_context(skill, project_id=project_id) if skill else None
                active_skill_name = skill_name if skill else None
                try:
                    from skills import analytics as _sa
                    _sa.record_fire(skill_name, source="suggest_accepted")
                except Exception:
                    pass
                await websocket.send_json({"type": "session_start", "session_id": session_id})
                if skill:
                    await websocket.send_json({
                        "type": "skill_active",
                        "skill": skill_name,
                        "description": skill.manifest.description,
                    })

                provider = get_provider()
                memory = create_memory_manager(
                    project_id=project_id, session_id=session_id, provider=provider,
                )
                # Jump straight to agent run with skill context
                agent = AgentCore(
                    provider=provider,
                    memory_manager=memory,
                    project_id=project_id,
                    session_id=session_id,
                    skill_context=skill_context,
                    active_skill_name=active_skill_name,
                )

                try:
                    await memory.episodic.save_message(
                        session_id=session_id, project_id=project_id,
                        role="user", content=message,
                    )
                except Exception as e:
                    logger.warning("Failed to save user message to episodic: %s", e)

                full_response = ""
                async for event in agent.chat(message, stream=True):
                    try:
                        await websocket.send_json(event)
                    except Exception:
                        break
                    if event.get("type") == "done":
                        full_response = event.get("full_response", "")

                if full_response:
                    try:
                        await memory.episodic.save_message(
                            session_id=session_id, project_id=project_id,
                            role="assistant", content=full_response,
                        )
                    except Exception as e:
                        logger.warning("Failed to save assistant message to episodic: %s", e)

                extraction_interval = settings.extraction_interval
                if extraction_interval > 0 and memory:
                    count = _session_message_counts.get(session_id, 0) + 2
                    _session_message_counts[session_id] = count
                    if count >= extraction_interval:
                        _session_message_counts[session_id] = 0
                        asyncio.ensure_future(_run_background_extraction(memory, project_id, session_id))
                continue

            if data.get("type") == "skill_decline":
                suggestion_id = data.get("suggestion_id")
                pending = _pending_suggestions.pop(suggestion_id, None)
                if not pending:
                    await websocket.send_json({"type": "error", "message": "Suggestion expired or not found"})
                    continue
                # Run the original message WITHOUT skill context
                message = pending["message"]
                session_id = pending["session_id"]
                project_id = pending["project_id"]
                skill_context = None
                active_skill_name = None
                try:
                    from skills import analytics as _sa
                    _sa.record_suggestion(pending["skill"], declined=True)
                except Exception:
                    pass
                await websocket.send_json({"type": "session_start", "session_id": session_id})

                provider = get_provider()
                memory = create_memory_manager(
                    project_id=project_id, session_id=session_id, provider=provider,
                )
                agent = AgentCore(
                    provider=provider,
                    memory_manager=memory,
                    project_id=project_id,
                    session_id=session_id,
                )

                try:
                    await memory.episodic.save_message(
                        session_id=session_id, project_id=project_id,
                        role="user", content=message,
                    )
                except Exception as e:
                    logger.warning("Failed to save user message to episodic: %s", e)

                full_response = ""
                async for event in agent.chat(message, stream=True):
                    try:
                        await websocket.send_json(event)
                    except Exception:
                        break
                    if event.get("type") == "done":
                        full_response = event.get("full_response", "")

                if full_response:
                    try:
                        await memory.episodic.save_message(
                            session_id=session_id, project_id=project_id,
                            role="assistant", content=full_response,
                        )
                    except Exception as e:
                        logger.warning("Failed to save assistant message to episodic: %s", e)

                extraction_interval = settings.extraction_interval
                if extraction_interval > 0 and memory:
                    count = _session_message_counts.get(session_id, 0) + 2
                    _session_message_counts[session_id] = count
                    if count >= extraction_interval:
                        _session_message_counts[session_id] = 0
                        asyncio.ensure_future(_run_background_extraction(memory, project_id, session_id))
                continue

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

            # ── Skill resolution ─────────────────────────────────────
            skill_context = None
            active_skill_name = None

            # 1. Check for explicit /skill-name invocation
            explicit_skill, remaining_message = resolve_explicit(message)
            if explicit_skill:
                registry = get_skill_registry()
                skill = registry.get(explicit_skill)
                if skill:
                    active_skill_name = explicit_skill
                    skill_context = build_skill_context(skill, project_id=project_id)
                    message = remaining_message or message
                    try:
                        from skills import analytics as _sa
                        _sa.record_fire(explicit_skill, source="explicit")
                    except Exception:
                        pass
                    await websocket.send_json({
                        "type": "skill_active",
                        "skill": explicit_skill,
                        "description": skill.manifest.description,
                    })
            else:
                # 2. Check for auto-discovery if enabled
                try:
                    from secrets.vault import get_vault as _gv
                    _vault = _gv()
                    discovery_mode = _vault.get_secret(f"skill_discovery_{project_id}") or "off"
                except Exception:
                    discovery_mode = "off"

                if discovery_mode in ("suggest", "auto"):
                    matches = resolve_auto(
                        message,
                        project_id=project_id,
                        mode=SkillDiscoveryMode(discovery_mode),
                        top_k=1,
                    )
                    if matches and matches[0]["score"] >= 2.0:
                        best = matches[0]
                        skill = best["skill"]
                        if discovery_mode == "auto":
                            active_skill_name = skill.name
                            skill_context = build_skill_context(skill, project_id=project_id)
                            try:
                                from skills import analytics as _sa
                                _sa.record_fire(skill.name, source="auto")
                            except Exception:
                                pass
                            await websocket.send_json({
                                "type": "skill_active",
                                "skill": skill.name,
                                "description": skill.manifest.description,
                                "auto": True,
                            })
                        else:
                            # suggest mode — pause and wait for accept/decline
                            suggestion_id = str(uuid.uuid4())
                            _pending_suggestions[suggestion_id] = {
                                "message": message,
                                "session_id": session_id,
                                "project_id": project_id,
                                "skill": skill.name,
                            }
                            try:
                                from skills import analytics as _sa
                                _sa.record_suggestion(skill.name)
                            except Exception:
                                pass
                            await websocket.send_json({
                                "type": "skill_suggestion",
                                "skill": skill.name,
                                "description": skill.manifest.description,
                                "score": best["score"],
                                "reason": best["reason"],
                                "suggestion_id": suggestion_id,
                            })
                            # Don't run agent yet — wait for skill_accept or skill_decline
                            continue

            agent = AgentCore(
                provider=provider,
                memory_manager=memory,
                project_id=project_id,
                session_id=session_id,
                skill_context=skill_context,
                active_skill_name=active_skill_name,
            )

            # Save user message to episodic memory
            try:
                await memory.episodic.save_message(
                    session_id=session_id,
                    project_id=project_id,
                    role="user",
                    content=message,
                )
            except Exception as e:
                logger.warning("Failed to save user message to episodic: %s", e)

            full_response = ""
            async for event in agent.chat(message, stream=True):
                try:
                    await websocket.send_json(event)
                except Exception:
                    break
                if event.get("type") == "done":
                    full_response = event.get("full_response", "")

            # Save assistant response to episodic memory
            if full_response:
                try:
                    await memory.episodic.save_message(
                        session_id=session_id,
                        project_id=project_id,
                        role="assistant",
                        content=full_response,
                    )
                except Exception as e:
                    logger.warning("Failed to save assistant message to episodic: %s", e)

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


# _describe_image is now imported from utils.vision


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
