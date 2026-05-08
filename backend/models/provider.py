"""OpenAI-compatible model provider abstraction."""
from __future__ import annotations
import asyncio
import json
import logging
import uuid
from typing import Any, AsyncGenerator

import httpx

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class ModelProvider:
    """Wraps any OpenAI-compatible LLM API for chat and embeddings."""

    def __init__(
        self,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        embedding_model: str | None = None,
    ):
        self.base_url = (base_url or settings.llm_base_url).rstrip("/")
        self.api_key = api_key or settings.llm_api_key
        self.model = model or settings.llm_model
        self.embedding_model = embedding_model or settings.embedding_model

    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self.api_key and self.api_key.lower() not in ("", "none", "ollama"):
            h["Authorization"] = f"Bearer {self.api_key}"
        elif self.api_key and self.api_key.lower() == "ollama":
            h["Authorization"] = "Bearer ollama"
        return h

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        stream: bool = True,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream chat completions. Yields event dicts."""
        if stream:
            async for event in self._stream_chat(messages, tools):
                yield event
        else:
            async for event in self._stream_non_streaming(messages, tools):
                yield event

    async def _stream_non_streaming(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        result = await self.chat_complete(messages, tools)
        if result.get("content"):
            yield {"type": "text_delta", "content": result["content"]}
        for tc in result.get("tool_calls", []):
            yield {"type": "tool_call", **tc}
        yield {"type": "done"}

    async def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream via SSE."""
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "max_tokens": 4096,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        # Accumulate tool call chunks
        tool_call_accum: dict[int, dict[str, Any]] = {}
        current_text = ""
        finish_reason: str | None = None

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers=self._headers(),
                    json=payload,
                ) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.startswith("data: "):
                            continue
                        data_str = line[6:].strip()
                        if data_str == "[DONE]":
                            break
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        choices = data.get("choices", [])
                        if not choices:
                            continue
                        delta = choices[0].get("delta", {})
                        # Track finish_reason but don't break early — wait for
                        # [DONE]. Some providers (Gemini, RouteLLM) send
                        # finish_reason in the same chunk as the last content
                        # token, so breaking here truncates the response.
                        chunk_finish = choices[0].get("finish_reason")
                        if chunk_finish:
                            finish_reason = chunk_finish

                        # Text content
                        content = delta.get("content", "")
                        if content:
                            current_text += content
                            yield {"type": "text_delta", "content": content}

                        # Tool calls
                        for tc_delta in delta.get("tool_calls", []):
                            idx = tc_delta.get("index", 0)
                            if idx not in tool_call_accum:
                                tool_call_accum[idx] = {
                                    "id": tc_delta.get("id", str(uuid.uuid4())),
                                    "name": "",
                                    "args_str": "",
                                }
                            fn_delta = tc_delta.get("function", {})
                            if fn_delta.get("name"):
                                tool_call_accum[idx]["name"] += fn_delta["name"]
                            if fn_delta.get("arguments"):
                                tool_call_accum[idx]["args_str"] += fn_delta["arguments"]

            # Emit completed tool calls
            for idx in sorted(tool_call_accum.keys()):
                tc = tool_call_accum[idx]
                try:
                    args = json.loads(tc["args_str"]) if tc["args_str"] else {}
                except json.JSONDecodeError:
                    args = {}
                yield {
                    "type": "tool_call",
                    "id": tc["id"],
                    "name": tc["name"],
                    "args": args,
                }

            yield {"type": "done", "content": current_text}

        except httpx.HTTPStatusError as e:
            # In streaming mode the response body hasn't been read — read it safely
            try:
                await e.response.aread()
                body = e.response.text[:500]
            except Exception:
                body = "(unreadable)"
            logger.error(f"HTTP error from LLM API: {e.response.status_code} {body}")
            yield {"type": "error", "message": f"LLM API error {e.response.status_code}: {body}"}
        except Exception as e:
            logger.error(f"Streaming error: {e}", exc_info=True)
            yield {"type": "error", "message": str(e)}

    async def chat_complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
    ) -> dict[str, Any]:
        """Non-streaming chat completion. Returns dict with content and tool_calls."""
        url = f"{self.base_url}/chat/completions"
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(url, headers=self._headers(), json=payload)
                resp.raise_for_status()
                data = resp.json()

            choices = data.get("choices", [])
            if not choices:
                return {"content": "", "tool_calls": []}

            message = choices[0].get("message", {})
            content = message.get("content", "") or ""
            raw_tool_calls = message.get("tool_calls", []) or []

            tool_calls = []
            for tc in raw_tool_calls:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except json.JSONDecodeError:
                    args = {}
                tool_calls.append({
                    "id": tc.get("id", str(uuid.uuid4())),
                    "name": fn.get("name", ""),
                    "args": args,
                })

            return {"content": content, "tool_calls": tool_calls}

        except Exception as e:
            logger.error(f"Chat complete error: {e}", exc_info=True)
            raise

    async def embed(self, text: str) -> list[float]:
        """Get embedding for text."""
        url = f"{self.base_url}/embeddings"
        payload = {"model": self.embedding_model, "input": text}
        logger.debug("Embedding request → %s (model: %s, %d chars)", url, self.embedding_model, len(text))
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, headers=self._headers(), json=payload)
                resp.raise_for_status()
                data = resp.json()
                dims = len(data["data"][0]["embedding"])
                logger.debug("Embedding response ← %d dimensions", dims)
                return data["data"][0]["embedding"]
        except Exception as e:
            logger.warning(f"Embedding failed, using zero vector: {e}")
            return [0.0] * 1536

    async def list_models(self) -> list[str]:
        """Fetch available models from the provider."""
        from models.discovery import fetch_models
        return await fetch_models(self.base_url, self.api_key)


# Per-role provider cache. Cleared by reset_provider().
_role_cache: dict[str, ModelProvider] = {}


def _build_for_role(role: str) -> ModelProvider | None:
    """Resolve role → endpoint+model from the llm_config store and
    instantiate a ModelProvider. Returns None when the role is
    unmapped (acceptable for vision and reranker)."""
    from llm_config.store import resolve_role
    rr = resolve_role(role)
    if rr is None:
        return None
    # Pass model in both slots so the embed role gets its embedding_model
    # populated — ModelProvider uses `model` for chat and `embedding_model`
    # for embed() calls. For non-embed roles the embedding_model field is
    # set but unused.
    return ModelProvider(
        base_url=rr.base_url,
        api_key=rr.api_key,
        model=rr.model,
        embedding_model=rr.model,
    )


def get_provider() -> ModelProvider:
    """Primary chat provider. Falls back to a no-op-ish ModelProvider
    constructed from settings if the role isn't mapped — same shape
    as before to keep existing call sites working when the user hasn't
    finished configuring."""
    if "chat" not in _role_cache:
        built = _build_for_role("chat")
        _role_cache["chat"] = built or ModelProvider()
    return _role_cache["chat"]


def get_embedding_provider() -> ModelProvider:
    """Embedding provider. Falls back to ModelProvider() with settings."""
    if "embed" not in _role_cache:
        built = _build_for_role("embed")
        _role_cache["embed"] = built or ModelProvider()
    return _role_cache["embed"]


def get_prefill_provider() -> ModelProvider:
    """Prefill / fallback provider. Falls back to the chat provider
    (same as the legacy behavior when prefill_* keys were empty)."""
    if "prefill" not in _role_cache:
        built = _build_for_role("prefill")
        _role_cache["prefill"] = built or get_provider()
    return _role_cache["prefill"]


def get_vision_provider() -> ModelProvider | None:
    """Optional vision provider. None when unmapped."""
    if "vision" not in _role_cache:
        _role_cache["vision"] = _build_for_role("vision")  # may be None
    return _role_cache["vision"]


def get_reranker_provider() -> ModelProvider | None:
    """Optional reranker provider. None when unmapped."""
    if "rerank" not in _role_cache:
        _role_cache["rerank"] = _build_for_role("rerank")  # may be None
    return _role_cache["rerank"]


def reset_provider() -> None:
    """Clear all cached per-role providers. Called whenever settings
    or role mapping change so the next get_*_provider() rebuilds."""
    _role_cache.clear()
