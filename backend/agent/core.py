"""Core agent loop with tool dispatch and streaming."""
from __future__ import annotations
import asyncio
import base64
import json
import logging
import re
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator

from agent.personality import get_full_personality
from agent.prompts import build_system_prompt
from agent.tools import TOOL_SCHEMAS, execute_tool, get_all_tool_schemas
from config import get_settings
from models.provider import ModelProvider

logger = logging.getLogger(__name__)
settings = get_settings()

MAX_TOOL_ITERATIONS = 50

_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}

# Max image size to inline as base64 (5 MB)
_MAX_IMAGE_SIZE = 5 * 1024 * 1024


class AgentCore:
    """The main agent loop."""

    def __init__(
        self,
        provider: ModelProvider,
        project_id: str = "default",
        project_name: str | None = None,
        session_id: str | None = None,
        memory_manager: Any = None,
        skill_context: str | None = None,
        active_skill_name: str | None = None,
    ):
        self.provider = provider
        self.project_id = project_id
        self.project_name = project_name
        self.session_id = session_id or str(uuid.uuid4())
        self.memory_manager = memory_manager
        self.skill_context = skill_context
        self.active_skill_name = active_skill_name
        self.working_memory: list[dict[str, str]] = []

    def _add_working_message(self, role: str, content: Any) -> None:
        """Add message to working memory."""
        self.working_memory.append({"role": role, "content": content})

    def _build_user_content(self, message: str) -> str | list[dict]:
        """Build multimodal content blocks if the message references image attachments.

        If image files are found in workspace/uploads/, they're inlined as
        base64 image_url blocks alongside the text — enabling vision models
        to actually see the images rather than just reading their filenames.
        """
        # Look for image file references in the attachment note
        # Filenames may contain spaces (e.g. "Screenshot 2026-03-24 at 3.04.17 AM.png")
        image_paths: list[Path] = []
        pattern = re.compile(
            r"uploads/(.+?\.(?:png|jpe?g|gif|webp|bmp))",
            re.IGNORECASE,
        )

        # Resolve workspace base once
        if self.project_id and self.project_id != "default":
            base = settings.projects_dir / self.project_id / "workspace"
        else:
            base = settings.workspace_dir

        seen: set[str] = set()
        for match in pattern.finditer(message):
            filename = match.group(1)
            if filename in seen:
                continue
            seen.add(filename)
            candidate = base / "uploads" / filename
            if candidate.exists() and candidate.stat().st_size <= _MAX_IMAGE_SIZE:
                image_paths.append(candidate)
            else:
                logger.debug("Image not found or too large: %s", candidate)

        if not image_paths:
            return message  # Plain text — no images found

        # Build multimodal content array
        content_blocks: list[dict] = [{"type": "text", "text": message}]
        for img_path in image_paths:
            try:
                raw = img_path.read_bytes()
                b64 = base64.b64encode(raw).decode("utf-8")
                ext = img_path.suffix.lower().lstrip(".")
                mime = f"image/{ext}" if ext != "jpg" else "image/jpeg"
                content_blocks.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{b64}"},
                })
                logger.info("Inlined image for vision: %s (%d KB)", img_path.name, len(raw) // 1024)
            except Exception as e:
                logger.warning("Failed to inline image %s: %s", img_path.name, e)

        return content_blocks

    def _get_working_messages(self) -> list[dict[str, str]]:
        """Get working memory messages."""
        return self.working_memory.copy()

    async def chat(
        self,
        user_message: str,
        stream: bool = True,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Process a user message and yield streaming events.

        Event types:
          {"type": "text_delta", "content": "..."}
          {"type": "tool_call", "name": "...", "args": {...}}
          {"type": "tool_result", "name": "...", "result": "..."}
          {"type": "done", "full_response": "..."}
          {"type": "error", "message": "..."}
        """
        try:
            # Load conversation behaviour settings
            from secrets.vault import get_vault
            _vault = get_vault()
            _personality_weight = _vault.get_secret("personality_weight") or "balanced"
            _context_focus = _vault.get_secret("context_focus") or "balanced"

            # Pre-recall relevant memories to inject into system prompt context
            recalled_memories = None
            try:
                from api.settings import is_memory_recall_enabled
                if is_memory_recall_enabled() and self.memory_manager:
                    mgr = self.memory_manager
                    try:
                        results = await asyncio.wait_for(
                            mgr.recall(
                                query=user_message,
                                tiers=["semantic", "episodic", "graph"],
                                project_id=self.project_id or "default",
                                limit_per_tier=5,
                                context_focus=_context_focus,
                            ),
                            timeout=4.0,
                        )
                    except asyncio.TimeoutError:
                        results = None
                        logger.warning("Pre-recall timed out, proceeding without context")
                    if results:
                        recalled_memories = results
                        logger.debug("Pre-recalled %d memories for context", len(results))
                        summary_lines = [f"[{r.get('tier','?')}] {r.get('content','')[:120]}" for r in results]
                        yield {
                            "type": "tool_call",
                            "name": "context_loaded",
                            "args": {"sources": len(results), "tiers": list({r.get("tier") for r in results})},
                        }
                        yield {
                            "type": "tool_result",
                            "name": "context_loaded",
                            "result": "\n\n".join(summary_lines),
                        }
                else:
                    logger.debug("Memory pre-recall disabled or no memory_manager available")
            except Exception as e:
                logger.warning("Failed to pre-recall memories: %s", e)

            # Build system prompt (inject skill instructions if a skill is active)
            system_prompt = build_system_prompt(
                project_id=self.project_id,
                project_name=self.project_name,
                recalled_memories=recalled_memories,
                extra_context=self.skill_context,
                personality_weight=_personality_weight,
            )

            # Get conversation history from working memory
            history = self._get_working_messages()

            # Add current user message — inline images for vision models
            user_content = self._build_user_content(user_message)
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_content})

            # Store plain text version in working memory (not base64 blobs)
            self._add_working_message("user", user_message)

            full_response = ""
            iterations = 0

            # Resolve all available tools (built-in + MCP)
            all_tools = get_all_tool_schemas()

            while iterations < MAX_TOOL_ITERATIONS:
                iterations += 1
                tool_calls_this_round: list[dict] = []
                current_text = ""

                if stream:
                    # Streaming mode
                    stream_error = False
                    async for chunk in self.provider.chat(
                        messages=messages,
                        tools=all_tools,
                        stream=True,
                    ):
                        if chunk["type"] == "text_delta":
                            current_text += chunk["content"]
                            yield chunk
                        elif chunk["type"] == "tool_call":
                            tool_calls_this_round.append(chunk)
                            yield chunk
                        elif chunk["type"] == "error":
                            yield chunk
                            stream_error = True
                        elif chunk["type"] == "done":
                            pass
                    if stream_error:
                        break
                else:
                    # Non-streaming mode
                    response = await self.provider.chat_complete(
                        messages=messages,
                        tools=all_tools,
                    )
                    current_text = response.get("content", "")
                    tool_calls_this_round = response.get("tool_calls", [])
                    if current_text:
                        yield {"type": "text_delta", "content": current_text}

                if current_text:
                    full_response = current_text

                if not tool_calls_this_round:
                    # No tool calls, we're done
                    break

                # Add assistant message with tool calls to conversation
                assistant_msg: dict[str, Any] = {
                    "role": "assistant",
                    "content": current_text or "",
                    "tool_calls": [
                        {
                            "id": tc.get("id", str(uuid.uuid4())),
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc.get("args", {})),
                            },
                        }
                        for tc in tool_calls_this_round
                    ],
                }
                messages.append(assistant_msg)

                # Execute each tool call
                for tc in tool_calls_this_round:
                    tool_name = tc["name"]
                    tool_args = tc.get("args", {})
                    tool_id = tc.get("id", str(uuid.uuid4()))

                    logger.info(f"Executing tool: {tool_name} with args: {tool_args}")
                    # Find the most recent assistant text for save_last_response
                    last_assistant_text = ""
                    for _m in reversed(messages):
                        if _m.get("role") == "assistant" and _m.get("content"):
                            last_assistant_text = _m["content"]
                            break
                    result = await execute_tool(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        memory_manager=self.memory_manager,
                        project_id=self.project_id,
                        session_id=self.session_id,
                        last_assistant_text=last_assistant_text,
                    )
                    yield {"type": "tool_result", "name": tool_name, "result": result, "tool_id": tool_id}

                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_id,
                        "content": result,
                    })

            # Save assistant response
            if full_response:
                self._add_working_message("assistant", full_response)

            yield {"type": "done", "full_response": full_response}

        except Exception as e:
            logger.error(f"Agent error: {e}", exc_info=True)
            yield {"type": "error", "message": str(e)}

    async def run_autonomous(self, task_description: str) -> str:
        """Run a task autonomously (no streaming, returns final response)."""
        full_response = ""
        async for event in self.chat(task_description, stream=False):
            if event["type"] == "done":
                full_response = event.get("full_response", "")
        return full_response
