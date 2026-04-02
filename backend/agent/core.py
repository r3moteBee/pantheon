"""Core agent loop with tool dispatch and streaming."""
from __future__ import annotations
import asyncio
import json
import logging
import uuid
from typing import Any, AsyncGenerator

from agent.personality import get_full_personality
from agent.prompts import build_system_prompt
from agent.tools import TOOL_SCHEMAS, execute_tool
from config import get_settings
from models.provider import ModelProvider

logger = logging.getLogger(__name__)
settings = get_settings()

MAX_TOOL_ITERATIONS = 50


class AgentCore:
    """The main agent loop."""

    def __init__(
        self,
        provider: ModelProvider,
        project_id: str = "default",
        project_name: str | None = None,
        session_id: str | None = None,
        memory_manager: Any = None,
    ):
        self.provider = provider
        self.project_id = project_id
        self.project_name = project_name
        self.session_id = session_id or str(uuid.uuid4())
        self.memory_manager = memory_manager
        self.working_memory: list[dict[str, str]] = []

    def _add_working_message(self, role: str, content: str) -> None:
        """Add message to working memory."""
        self.working_memory.append({"role": role, "content": content})

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
            # Pre-recall relevant memories to inject into system prompt context
            recalled_memories = None
            try:
                from memory.manager import create_memory_manager
                mgr = create_memory_manager(project_id=self.project_id or "default")
                import asyncio as _asyncio
                try:
                    results = await _asyncio.wait_for(
                        mgr.recall(
                            query=user_message,
                            tiers=["semantic", "episodic", "graph"],
                            project_id=self.project_id or "default",
                            limit_per_tier=5,
                        ),
                        timeout=8.0,
                    )
                except _asyncio.TimeoutError:
                    results = None
                    logger.warning("Pre-recall timed out, proceeding without context")
                if results:
                    recalled_memories = results
                    logger.debug("Pre-recalled %d memories for context", len(results))
                    # Emit a visible context_loaded event so the UI can show what was injected
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
            except Exception as e:
                logger.warning("Failed to pre-recall memories: %s", e)

            # Build system prompt
            system_prompt = build_system_prompt(
                project_id=self.project_id,
                project_name=self.project_name,
                recalled_memories=recalled_memories,
            )

            # Get conversation history from working memory
            history = self._get_working_messages()

            # Add current user message
            messages = [{"role": "system", "content": system_prompt}]
            messages.extend(history)
            messages.append({"role": "user", "content": user_message})

            # Add to working memory
            self._add_working_message("user", user_message)

            full_response = ""
            iterations = 0

            while iterations < MAX_TOOL_ITERATIONS:
                iterations += 1
                tool_calls_this_round: list[dict] = []
                current_text = ""

                if stream:
                    # Streaming mode
                    async for chunk in self.provider.chat(
                        messages=messages,
                        tools=TOOL_SCHEMAS,
                        stream=True,
                    ):
                        if chunk["type"] == "text_delta":
                            current_text += chunk["content"]
                            yield chunk
                        elif chunk["type"] == "tool_call":
                            tool_calls_this_round.append(chunk)
                            yield chunk
                        elif chunk["type"] == "done":
                            pass
                else:
                    # Non-streaming mode
                    response = await self.provider.chat_complete(
                        messages=messages,
                        tools=TOOL_SCHEMAS,
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
                    result = await execute_tool(
                        tool_name=tool_name,
                        tool_args=tool_args,
                        memory_manager=self.memory_manager,
                        project_id=self.project_id,
                        session_id=self.session_id,
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
