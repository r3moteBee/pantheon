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

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

from agent.core import AgentCore
from config import get_settings
from memory.manager import create_memory_manager
from models.provider import get_provider
from skills.resolver import resolve_explicit, resolve_auto, build_skill_context
from skills.registry import get_skill_registry
from skills.models import SkillDiscoveryMode

from utils.background import spawn

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()

# Track message counts per session for interval-based extraction
_session_message_counts: dict[str, int] = {}

# Image extensions used to route attachments to the extraction pipeline
from utils.vision import IMAGE_EXTENSIONS as _IMAGE_EXTENSIONS


class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    project_id: str = "default"
    stream: bool = True


class ChatResponse(BaseModel):
    session_id: str
    response: str
    project_id: str
    route: dict[str, Any] | None = None


class HistoryRequest(BaseModel):
    session_id: str
    project_id: str = "default"
    limit: int = 50


# Active WebSocket connections
_active_connections: dict[str, WebSocket] = {}

# Pending skill suggestions awaiting user accept/decline
_pending_suggestions: dict[str, dict] = {}

async def _build_agent(
    *,
    provider,
    memory_manager,
    project_id,
    session_id,
    skill_context=None,
    active_skill_name=None,
):
    """Pick AgentCore() vs AgentCore.from_session() based on history.

    First turn: empty working_memory.
    Resume / subsequent turns: rehydrate working_memory from messages table
    so the agent sees the conversation so far.
    """
    from memory.episodic import EpisodicMemory
    from agent.tools import host_exec_allowed
    host_exec = host_exec_allowed("interactive")
    try:
        existing = await EpisodicMemory().get_history(session_id=session_id, limit=1)
    except Exception:
        existing = []
    if existing:
        return await AgentCore.from_session(
            provider=provider,
            project_id=project_id,
            session_id=session_id,
            memory_manager=memory_manager,
            skill_context=skill_context,
            active_skill_name=active_skill_name,
            host_exec=host_exec,
            interactive=True,
        )
    return AgentCore(
        provider=provider,
        memory_manager=memory_manager,
        project_id=project_id,
        session_id=session_id,
        skill_context=skill_context,
        active_skill_name=active_skill_name,
        host_exec=host_exec,
        interactive=True,
    )


# ── Per-turn model routing (llm_config/router.py) ─────────────────────

async def _route_turn(agent, message: str, session_id: str, skill_name: str | None):
    """Pick this turn's task class and point the agent at its provider.
    Runs before agent.chat so the model never changes inside a tool loop.
    Any router failure leaves the agent on its default (agent) provider."""
    from llm_config import router as chat_router
    from models.provider import get_provider_for
    try:
        skill = get_skill_registry().get(skill_name) if skill_name else None
        try:
            history_chars = sum(len(str(m.get("content") or "")) for m in agent._get_working_messages())
        except Exception:
            history_chars = 0
        decision = await chat_router.decide(
            message, session_id=session_id, history_chars=history_chars, skill=skill,
        )
    except Exception:
        logger.exception("chat router failed — using agent class")
        return None
    if decision.task_class != "agent":
        prov = get_provider_for(decision.task_class)
        if prov is not None:
            agent.provider = prov
    logger.info("chat route: %s via %s (%s) -> %s/%s", decision.task_class, decision.rule,
                decision.reason, decision.endpoint, decision.model)
    return decision


def _finish_route(decision, served: list, session_id: str, message: str) -> dict[str, Any] | None:
    """Log the decision (with the model that actually answered after any
    fallback) and return the dict sent to the UI / stored on the message."""
    if decision is None:
        return None
    from llm_config.usage import record_decision
    served_model = served[-1][1] if served else None
    record_decision(
        session_id=session_id, task_class=decision.task_class, rule=decision.rule,
        reason=decision.public()["reason"], endpoint=decision.endpoint, model=decision.model,
        served_model=served_model, message_chars=len(message or ""), est_tokens=decision.est_tokens,
    )
    route = decision.public()
    if served:
        route["served_endpoint"], route["served_model"] = served[-1]
    return route


async def _stream_turn(agent, message: str, session_id: str, send, *,
                       skill_name: str | None = None, stream: bool = True):
    """Route, run one agent turn and forward its events via ``send``.
    Returns (full_response, route). Keeps draining after a client
    disconnect (send drops silently) so pending tool calls finish and the
    reply is still saved."""
    from models.provider import served_models
    decision = await _route_turn(agent, message, session_id, skill_name)
    if decision is not None:
        await send({"type": "model_route", **decision.public()})
    served: list = []
    token = served_models.set(served)
    full_response, route, finished = "", None, False
    try:
        async for event in agent.chat(message, stream=stream):
            if event.get("type") == "done":
                full_response = event.get("full_response", "")
                route = _finish_route(decision, served, session_id, message)
                finished = True
                if route:
                    event = {**event, "route": route}
            await send(event)
    finally:
        served_models.reset(token)
        if not finished:
            route = _finish_route(decision, served, session_id, message)
    return full_response, route


def _model_command_reply(session_id: str, message: str) -> tuple[str | None, str | None, str | None]:
    """Handle a leading "/model [class] [message]".
    Returns (reply, remaining_message, pinned_class); reply is None when the
    message isn't a /model command."""
    from llm_config import router as chat_router
    cmd = chat_router.parse_model_command(message)
    if cmd is None:
        return None, message, None
    cls, rest = cmd
    if cls is None:
        pinned = chat_router.get_override(session_id) or "auto"
        return (f"Model routing for this conversation: **{pinned}**. "
                f"Use `/model <{'|'.join(chat_router.CHAT_CLASSES)}|auto>`."), "", pinned
    try:
        chat_router.set_override(session_id, cls)
    except ValueError as e:
        return str(e), "", None
    pinned = chat_router.get_override(session_id) or "auto"
    reply = ("Routing is automatic again for this conversation." if pinned == "auto"
             else f"This conversation is pinned to **{pinned}**. `/model auto` to undo.")
    return reply, rest, pinned



@router.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    """Send a message to the agent and get a response (non-streaming).

    Mirrors the WebSocket path's skill-discovery flow:
      - explicit /skill-name invocations → activate that skill
      - auto-discovery when discovery_mode in ('suggest','auto') →
        fire the top match above threshold (REST has no chip UX so
        'suggest' is treated as 'auto' here)
    """
    session_id = req.session_id or str(uuid.uuid4())
    provider = get_provider()
    memory = create_memory_manager(
        project_id=req.project_id,
        session_id=session_id,
        provider=provider,
    )

    message = req.message
    skill_context: str | None = None
    active_skill_name: str | None = None

    reply, message, _pinned = _model_command_reply(session_id, message)
    if reply is not None and not message:
        return ChatResponse(session_id=session_id, response=reply, project_id=req.project_id)

    # 1. Explicit /skill-name invocation
    explicit_skill, remaining_message = resolve_explicit(message)
    if explicit_skill:
        registry = get_skill_registry()
        skill = registry.get(explicit_skill)
        if skill:
            active_skill_name = explicit_skill
            skill_context = build_skill_context(skill, project_id=req.project_id)
            message = remaining_message or message
            try:
                from skills import analytics as _sa
                _sa.record_fire(explicit_skill, source="explicit")
            except Exception:
                pass
    else:
        # 2. Auto-discovery
        try:
            from secrets.vault import get_vault as _gv
            discovery_mode = _gv().get_secret(f"skill_discovery_{req.project_id}") or "off"
        except Exception:
            discovery_mode = "off"
        if discovery_mode in ("suggest", "auto"):
            try:
                matches = resolve_auto(
                    message,
                    project_id=req.project_id,
                    mode=SkillDiscoveryMode(discovery_mode),
                    top_k=1,
                )
                if matches and matches[0]["score"] >= 3.0:
                    skill = matches[0]["skill"]
                    active_skill_name = skill.name
                    skill_context = build_skill_context(skill, project_id=req.project_id)
                    try:
                        from skills import analytics as _sa
                        # In REST we cannot pause-and-confirm, so mark
                        # the source as 'auto_rest' regardless of the
                        # configured mode.
                        _sa.record_fire(skill.name, source="auto_rest")
                    except Exception:
                        pass
                    logger.info(
                        "REST chat: auto-fired skill %s (score=%s, mode=%s)",
                        skill.name, matches[0]["score"], discovery_mode,
                    )
            except Exception:
                logger.exception("REST chat: skill auto-discovery failed")

    agent = await _build_agent(
        provider=provider,
        memory_manager=memory,
        project_id=req.project_id,
        session_id=session_id,
        skill_context=skill_context,
        active_skill_name=active_skill_name,
    )

    # Persist like the WebSocket path so the next REST call (and history,
    # extraction, recall) sees this turn. Saved after _build_agent so
    # from_session doesn't replay the message agent.chat is about to add.
    try:
        await memory.episodic.save_message(
            session_id=session_id, project_id=req.project_id,
            role="user", content=message,
        )
    except Exception as e:
        logger.warning("REST chat: failed to save user message: %s", e)

    errors: list[str] = []

    async def _collect(event: dict[str, Any]) -> None:
        if event.get("type") == "error":
            errors.append(event.get("message") or "agent error")

    full_response, route = await _stream_turn(
        agent, message, session_id, _collect, skill_name=active_skill_name, stream=False,
    )
    if errors:
        raise HTTPException(status_code=500, detail=errors[0])

    if full_response:
        try:
            await memory.episodic.save_message(
                session_id=session_id, project_id=req.project_id,
                role="assistant", content=full_response,
                metadata={"route": route} if route else None,
            )
        except Exception as e:
            logger.warning("REST chat: failed to save assistant message: %s", e)

    return ChatResponse(
        session_id=session_id,
        response=full_response,
        project_id=req.project_id,
        route=route,
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
    from api.auth import authorize_websocket
    if not await authorize_websocket(websocket):
        return
    await websocket.accept()
    connection_id = str(uuid.uuid4())

    client_gone = False

    async def _send(event: dict[str, Any]) -> None:
        """Send to the client; after the first failure, drop silently."""
        nonlocal client_gone
        if client_gone:
            return
        try:
            await websocket.send_json(event)
        except Exception:
            client_gone = True
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
                agent = await _build_agent(
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

                full_response, route = await _stream_turn(
                    agent, message, session_id, _send, skill_name=active_skill_name,
                )

                if full_response:
                    try:
                        await memory.episodic.save_message(
                            session_id=session_id, project_id=project_id,
                            role="assistant", content=full_response,
                            metadata={"route": route} if route else None,
                        )
                    except Exception as e:
                        logger.warning("Failed to save assistant message to episodic: %s", e)

                extraction_interval = settings.extraction_interval
                if extraction_interval > 0 and memory:
                    count = _session_message_counts.get(session_id, 0) + 2
                    _session_message_counts[session_id] = count
                    if count >= extraction_interval:
                        _session_message_counts[session_id] = 0
                        spawn(_run_background_extraction(memory, project_id, session_id), name="extraction")
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
                agent = await _build_agent(
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

                full_response, route = await _stream_turn(
                    agent, message, session_id, _send, skill_name=active_skill_name,
                )

                if full_response:
                    try:
                        await memory.episodic.save_message(
                            session_id=session_id, project_id=project_id,
                            role="assistant", content=full_response,
                            metadata={"route": route} if route else None,
                        )
                    except Exception as e:
                        logger.warning("Failed to save assistant message to episodic: %s", e)

                extraction_interval = settings.extraction_interval
                if extraction_interval > 0 and memory:
                    count = _session_message_counts.get(session_id, 0) + 2
                    _session_message_counts[session_id] = count
                    if count >= extraction_interval:
                        _session_message_counts[session_id] = 0
                        spawn(_run_background_extraction(memory, project_id, session_id), name="extraction")
                continue

            # Model pin from the chat UI's picker ("auto" clears it).
            if data.get("model_class") is not None:
                from llm_config import router as chat_router
                try:
                    chat_router.set_override(session_id, data.get("model_class"))
                except ValueError as e:
                    await websocket.send_json({"type": "error", "message": str(e)})
                    continue

            # "/model <class> [message]" — pin this conversation to a class.
            reply, message, pinned = _model_command_reply(session_id, message)
            if reply is not None:
                await websocket.send_json({"type": "session_start", "session_id": session_id})
                await websocket.send_json({"type": "model_pin", "task_class": pinned})
                if not message:
                    await websocket.send_json({"type": "text_delta", "content": reply})
                    await websocket.send_json({"type": "done", "full_response": reply})
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
                    if matches and matches[0]["score"] >= 3.0:
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

            # Get conversation metadata to check for active personas
            client_personas = data.get("active_personas")
            if client_personas is not None:
                try:
                    from api.conversations import _connect as _conv_connect
                    from datetime import datetime, timezone
                    now = datetime.now(timezone.utc).isoformat()
                    with _conv_connect() as conn:
                        conn.execute("""
                            INSERT INTO conversations (id, project_id, session_id, created_at, updated_at, metadata)
                            VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET metadata = ?
                        """, (session_id, project_id, session_id, now, now, json.dumps({"active_personas": client_personas}), json.dumps({"active_personas": client_personas})))
                except Exception as e:
                    logger.debug("Failed to pre-save conversation metadata: %s", e)

            active_personas = []
            try:
                conv = await memory.episodic.get_conversation(session_id)
                if conv and conv.get("metadata"):
                    conv_meta = json.loads(conv["metadata"])
                    active_personas = conv_meta.get("active_personas", [])
            except Exception as e:
                logger.debug("Failed to load active_personas: %s", e)


            if len(active_personas) > 1:
                # Determine first responder based on mentions
                next_responder = None
                from api.personas import _find_persona
                for p_id in active_personas:
                    if f"@{p_id}" in message.lower():
                        next_responder = p_id
                        break
                if not next_responder:
                    next_responder = active_personas[0]

                current_message = message
                max_turns = 3
                current_turn = 0
                
                while next_responder and current_turn < max_turns:
                    current_turn += 1
                    p_id = next_responder
                    next_responder = None
                    
                    persona_data, _ = _find_persona(p_id)
                    if not persona_data:
                        break
                    
                    p_soul = persona_data.get("soul", "")
                    p_name = persona_data.get("name", p_id)
                    p_icon = persona_data.get("icon", "🎭")
                    
                    # Notify client which persona is active
                    await websocket.send_json({
                        "type": "persona_active",
                        "persona_id": p_id,
                        "name": p_name,
                        "icon": p_icon,
                        "turn": current_turn,
                    })
                    
                    # Prepend persona prefix delta so the text flows after it
                    prefix = f"\n\n**{p_icon} {p_name}**: "
                    if current_turn == 1:
                        prefix = f"**{p_icon} {p_name}**: "
                    await websocket.send_json({
                        "type": "text_delta",
                        "content": prefix,
                    })
                    
                    # Build agent with custom soul
                    agent = await _build_agent(
                        provider=provider,
                        memory_manager=memory,
                        project_id=project_id,
                        session_id=session_id,
                        skill_context=skill_context,
                        active_skill_name=active_skill_name,
                    )
                    agent.custom_soul = p_soul
                    _decision = await _route_turn(agent, current_message, session_id, active_skill_name)
                    if _decision is not None:
                        await _send({"type": "model_route", **_decision.public()})
                        _finish_route(_decision, [], session_id, current_message)

                    # Save the user message only after the first agent has
                    # rehydrated from history — saving first made
                    # from_session replay it and agent.chat append it again,
                    # so the model saw the message twice.
                    if current_turn == 1:
                        try:
                            await memory.episodic.save_message(
                                session_id=session_id,
                                project_id=project_id,
                                role="user",
                                content=message,
                            )
                        except Exception as e:
                            logger.warning("Failed to save user message: %s", e)
                    
                    full_response = ""
                    async for event in agent.chat(current_message, stream=True):
                        # Forward delta events to client (keeps draining if the
                        # client disconnected so the reply is still saved).
                        if event.get("type") == "text_delta":
                            full_response += event.get("content", "")
                            await _send(event)
                        elif event.get("type") in ("tool_call", "tool_result"):
                            await _send(event)
                    
                    # Save persona's response to episodic memory
                    complete_content = prefix + full_response
                    try:
                        await memory.episodic.save_message(
                            session_id=session_id,
                            project_id=project_id,
                            role="assistant",
                            content=complete_content,
                            metadata={"persona_id": p_id, "persona_name": p_name},
                        )
                    except Exception as e:
                        logger.warning("Failed to save assistant message: %s", e)
                    
                    # Look for mentions of other active personas in the generated response
                    for other_p in active_personas:
                        if other_p != p_id and f"@{other_p}" in full_response.lower():
                            next_responder = other_p
                            current_message = f"[Collaboration loop: turn for @{other_p}. Please respond to the feedback/post above.]"
                            break
                
                # Send done event to client
                await websocket.send_json({"type": "done", "full_response": "Collaboration finished.", "iterations": current_turn})

            else:
                agent = await _build_agent(
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

                full_response, route = await _stream_turn(
                    agent, message, session_id, _send, skill_name=active_skill_name,
                )

                # Save assistant response to episodic memory
                if full_response:
                    try:
                        await memory.episodic.save_message(
                            session_id=session_id,
                            project_id=project_id,
                            role="assistant",
                            content=full_response,
                            metadata={"route": route} if route else None,
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
                    spawn(_run_background_extraction(memory, project_id, session_id), name="extraction")

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
    session_id: str | None = Query(default=None),
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
    desired_path = f"chat-attachments/{today}/{filename}"
    artifact_store = get_artifact_store()
    path = artifact_store._unique_path(project_id, desired_path)
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
        payload_dict: dict[str, Any] = {"artifact_id": artifact["id"]}
        if session_id:
            payload_dict["parent_session_id"] = session_id
        try:
            job = get_job_store().create(
                job_type="image_extraction",
                project_id=project_id,
                title=f"Extract: {filename}",
                description=f"Vision + OCR + topics for {path}",
                payload=payload_dict,
                timeout_seconds=300,
            )
            result["extraction_job_id"] = job["id"]
            result["indexing"] = True
        except Exception as e:
            logger.warning(
                "image_extraction job enqueue failed for artifact %s: %s",
                artifact["id"], e,
            )
            # Artifact is durable; user can retry extraction via the Jobs panel later.
    elif is_text_type(content_type):
        embedder.schedule_embed(artifact["id"], project_id)
        result["indexing"] = True

    return result


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
