"""OpenAI-compatible model provider abstraction."""
from __future__ import annotations
import asyncio
import json
import logging
import uuid
from typing import Any, AsyncGenerator

from collections import OrderedDict

import httpx

from utils.http import pooled_client

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class LLMHTTPError(RuntimeError):
    """Upstream returned an HTTP error. ``status`` lets the router decide
    whether to fall back to the next model."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def _extract_error_message(raw_body: str | None) -> str | None:
    """Pull the human-readable message out of an OpenAI-compatible (or
    abacus-style) JSON error body. Returns None if the body isn't JSON or
    has no recognizable message field."""
    if not raw_body:
        return None
    try:
        data = json.loads(raw_body)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return err.get("message") or err.get("type")
        if isinstance(err, str):
            return err
        return data.get("message") or data.get("detail")
    return None


def _format_llm_error(status_code: int, raw_body: str | None) -> str:
    """Build a descriptive, user-facing error string from an LLM HTTP error.

    Surfaces the provider's own message instead of an opaque status code, and
    appends an actionable hint for the common failure modes (model can't do
    tool calling, bad key, wrong model id)."""
    detail = _extract_error_message(raw_body)
    if not detail:
        detail = (raw_body or "").strip()[:300] or "(no response body)"
    low = detail.lower()
    hint = ""
    if "tool" in low and ("support" in low or "calling" in low):
        hint = (
            " — This model can't be used with Pantheon's agent tools. "
            "Choose a tool-calling model for the chat role in "
            "Settings → Model Routing (Agent class)."
        )
    elif status_code in (401, 403):
        hint = " — Check this endpoint's API key in Settings → Endpoints."
    elif status_code == 404:
        hint = (
            " — Check the model id and base URL for this endpoint in "
            "Settings → Endpoints."
        )
    elif status_code == 429:
        hint = " — Rate limited or out of quota on this endpoint."
    return f"LLM API error {status_code}: {detail}{hint}"


_EMBED_CACHE: "OrderedDict[tuple[str, str, str], tuple[float, ...]]" = OrderedDict()
_EMBED_CACHE_SIZE = 512
_EMBED_CACHE_MAX_CHARS = 2000


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
            async with pooled_client(timeout=120.0) as client:
                async with client.stream(
                    "POST",
                    url,
                    headers=self._headers(),
                    json=payload,
                ) as resp:
                    if resp.status_code >= 400:
                        # Read the body BEFORE the stream context unwinds —
                        # once raise_for_status escapes the `async with`, the
                        # response is closed and the body is unreadable (this
                        # is what produced the old "(unreadable)" errors).
                        try:
                            await resp.aread()
                            body = resp.text
                        except Exception:
                            body = None
                        msg = _format_llm_error(resp.status_code, body)
                        logger.error(msg)
                        yield {"type": "error", "message": msg, "status": resp.status_code}
                        return
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
            # Defensive fallback — the inline status check above handles the
            # normal path, but read the body safely if a status error still
            # escapes from elsewhere.
            try:
                await e.response.aread()
                body = e.response.text
            except Exception:
                body = None
            msg = _format_llm_error(e.response.status_code, body)
            logger.error(msg)
            yield {"type": "error", "message": msg, "status": e.response.status_code}
        except httpx.RequestError as e:
            msg = f"Could not reach LLM endpoint ({self.base_url}): {e}"
            logger.error(msg)
            yield {"type": "error", "message": msg, "status": "network"}
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
            async with pooled_client(timeout=300.0) as client:
                resp = await client.post(url, headers=self._headers(), json=payload)
                if resp.status_code >= 400:
                    msg = _format_llm_error(resp.status_code, resp.text)
                    logger.error(msg)
                    raise LLMHTTPError(resp.status_code, msg)
                data = resp.json()

            usage = data.get("usage") or {}
            choices = data.get("choices", [])
            if not choices:
                return {"content": "", "tool_calls": [], "usage": usage}

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

            return {"content": content, "tool_calls": tool_calls, "usage": usage}

        except Exception as e:
            logger.error(f"Chat complete error: {e}", exc_info=True)
            raise

    async def embed(self, text: str) -> list[float]:
        """Get embedding for text.

        Short texts (queries, topic labels) are memoized: one recall embeds
        the same query for the semantic and episodic tiers, and the
        similarity pipeline re-embeds each topic label several times.
        """
        cache_key = (self.base_url, self.embedding_model, text) if len(text) <= _EMBED_CACHE_MAX_CHARS else None
        if cache_key is not None and cache_key in _EMBED_CACHE:
            _EMBED_CACHE.move_to_end(cache_key)
            return list(_EMBED_CACHE[cache_key])
        vec = await self._embed_uncached(text)
        if cache_key is not None:
            _EMBED_CACHE[cache_key] = tuple(vec)
            if len(_EMBED_CACHE) > _EMBED_CACHE_SIZE:
                _EMBED_CACHE.popitem(last=False)
        return vec

    async def embed_many(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        """Embed several texts with one /embeddings call per batch (the API
        accepts a list). Raises on failure like embed()."""
        out: list[list[float]] = []
        url = f"{self.base_url}/embeddings"
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            async with pooled_client(timeout=120.0) as client:
                resp = await client.post(url, headers=self._headers(),
                                         json={"model": self.embedding_model, "input": batch})
                resp.raise_for_status()
                data = resp.json()["data"]
            # Responses carry an index; don't assume order.
            data = sorted(data, key=lambda d: d.get("index", 0))
            if len(data) != len(batch):
                raise RuntimeError(f"embeddings returned {len(data)} vectors for {len(batch)} inputs")
            out.extend(d["embedding"] for d in data)
        return out

    async def _embed_uncached(self, text: str) -> list[float]:
        url = f"{self.base_url}/embeddings"
        payload = {"model": self.embedding_model, "input": text}
        logger.debug("Embedding request → %s (model: %s, %d chars)", url, self.embedding_model, len(text))
        try:
            async with pooled_client(timeout=30.0) as client:
                resp = await client.post(url, headers=self._headers(), json=payload)
                resp.raise_for_status()
                data = resp.json()
                dims = len(data["data"][0]["embedding"])
                logger.debug("Embedding response ← %d dimensions", dims)
                return data["data"][0]["embedding"]
        except Exception as e:
            # Raise instead of returning a zero vector: a fake vector either
            # has the wrong dimension (Chroma rejects it, or locks a new
            # collection to 1536) or is stored/queried as garbage and
            # silently corrupts recall. Callers skip or fall back.
            logger.warning("Embedding failed: %s", e)
            raise

    async def generate_image(
        self, prompt: str, *, size: str = "1024x1024", n: int = 1,
        quality: str | None = None,
    ) -> list[bytes]:
        """OpenAI-compatible /images/generations. Returns raw image bytes.

        Handles both response styles: ``b64_json`` (gpt-image-*, most local
        servers) and ``url`` (dall-e-*; fetched through the SSRF guard).
        """
        import base64
        payload: dict[str, Any] = {"model": self.model, "prompt": prompt, "n": n, "size": size}
        if quality:
            payload["quality"] = quality
        async with pooled_client(timeout=300.0) as client:
            resp = await client.post(f"{self.base_url}/images/generations",
                                     headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            raise LLMHTTPError(resp.status_code, _format_llm_error(resp.status_code, resp.text))
        images: list[bytes] = []
        for item in resp.json().get("data") or []:
            if item.get("b64_json"):
                images.append(base64.b64decode(item["b64_json"]))
            elif item.get("url"):
                from utils.net import safe_http_get
                r = await safe_http_get(item["url"], timeout=120)
                r.raise_for_status()
                images.append(r.content)
        if not images:
            raise RuntimeError("image endpoint returned no images")
        return images

    async def list_models(self) -> list[str]:
        """Fetch available models from the provider."""
        from models.discovery import fetch_models
        return await fetch_models(self.base_url, self.api_key)


# ── Task-class routing ───────────────────────────────────────────────
# Call sites ask for a provider by task class (agent, code, extract,
# summarize, vision, image_gen, embed, rerank). A class resolves to an
# ordered list of endpoint+model candidates; RoutedProvider tries them in
# order, falling back on rate limits, server errors, missing models and
# network failures, and logs every attempt to llm_calls.db.

_RETRYABLE_STATUS = {404, 408, 409, 425, 429}


def _is_retryable(status: Any) -> bool:
    if status == "network":
        return True
    try:
        code = int(status)
    except (TypeError, ValueError):
        return False
    return code in _RETRYABLE_STATUS or code >= 500


def _status_of(exc: BaseException) -> Any:
    if isinstance(exc, LLMHTTPError):
        return exc.status
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code
    if isinstance(exc, (httpx.RequestError, asyncio.TimeoutError)):
        return "network"
    return None


class RoutedProvider:
    """Drop-in for ModelProvider that fails over across a task class's
    candidates. Attribute access (model, base_url, api_key, …) reflects the
    primary candidate so existing call sites keep working."""

    def __init__(self, task_class: str, candidates: list[tuple[ModelProvider, str]]):
        if not candidates:
            raise ValueError("RoutedProvider needs at least one candidate")
        self.task_class = task_class
        self._candidates = candidates  # [(provider, endpoint_name)]

    # Primary's attributes (model, base_url, api_key, embedding_model, …)
    def __getattr__(self, name: str) -> Any:
        if name.startswith("__") or name == "_candidates":
            raise AttributeError(name)
        return getattr(self._candidates[0][0], name)

    @property
    def candidates(self) -> list[tuple[str, str]]:
        return [(ep, p.model) for p, ep in self._candidates]

    def _log(self, *, endpoint: str, model: str, op: str, attempt: int, ok: bool,
             started: float, status: Any = None, error: str | None = None,
             usage: dict | None = None) -> None:
        import time as _t
        from llm_config.usage import record
        record(
            task_class=self.task_class, endpoint=endpoint, model=model, operation=op,
            attempt=attempt, ok=ok, latency_ms=int((_t.monotonic() - started) * 1000),
            status=None if status is None else str(status), error=error,
            prompt_tokens=(usage or {}).get("prompt_tokens"),
            completion_tokens=(usage or {}).get("completion_tokens"),
        )

    async def chat_complete(self, messages: list[dict[str, Any]],
                            tools: list[dict] | None = None) -> dict[str, Any]:
        import time as _t
        last_exc: BaseException | None = None
        for i, (prov, ep) in enumerate(self._candidates):
            started = _t.monotonic()
            try:
                result = await prov.chat_complete(messages, tools)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                status = _status_of(e)
                self._log(endpoint=ep, model=prov.model, op="complete", attempt=i,
                          ok=False, started=started, status=status, error=str(e))
                last_exc = e
                if _is_retryable(status) and i + 1 < len(self._candidates):
                    logger.warning("%s: %s/%s failed (%s) — falling back",
                                   self.task_class, ep, prov.model, status)
                    continue
                raise
            self._log(endpoint=ep, model=prov.model, op="complete", attempt=i,
                      ok=True, started=started, usage=result.get("usage"))
            return result
        raise last_exc or RuntimeError("no candidates")

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict] | None = None,
                   stream: bool = True) -> AsyncGenerator[dict[str, Any], None]:
        import time as _t
        if not stream:
            result = await self.chat_complete(messages, tools)
            if result.get("content"):
                yield {"type": "text_delta", "content": result["content"]}
            for tc in result.get("tool_calls", []):
                yield {"type": "tool_call", **tc}
            yield {"type": "done"}
            return
        for i, (prov, ep) in enumerate(self._candidates):
            started = _t.monotonic()
            first = True
            failed_early = False
            async for event in prov.chat(messages, tools, stream=True):
                if event.get("type") == "error":
                    status = event.get("status")
                    self._log(endpoint=ep, model=prov.model, op="stream", attempt=i,
                              ok=False, started=started, status=status,
                              error=event.get("message"))
                    # Only fail over before anything reached the caller.
                    if first and _is_retryable(status) and i + 1 < len(self._candidates):
                        logger.warning("%s: %s/%s failed (%s) — falling back",
                                       self.task_class, ep, prov.model, status)
                        failed_early = True
                        break
                    yield event
                    return
                first = False
                if event.get("type") == "done":
                    self._log(endpoint=ep, model=prov.model, op="stream", attempt=i,
                              ok=True, started=started)
                yield event
            if not failed_early:
                return

    async def generate_image(self, prompt: str, **kw: Any) -> list[bytes]:
        import time as _t
        last_exc: BaseException | None = None
        for i, (prov, ep) in enumerate(self._candidates):
            started = _t.monotonic()
            try:
                images = await prov.generate_image(prompt, **kw)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                status = _status_of(e)
                self._log(endpoint=ep, model=prov.model, op="image", attempt=i,
                          ok=False, started=started, status=status, error=str(e))
                last_exc = e
                if _is_retryable(status) and i + 1 < len(self._candidates):
                    continue
                raise
            self._log(endpoint=ep, model=prov.model, op="image", attempt=i,
                      ok=True, started=started)
            return images
        raise last_exc or RuntimeError("no candidates")


# Cache per task class. Cleared by reset_provider().
_role_cache: dict[str, Any] = {}


def _build_route(task_class: str) -> RoutedProvider | None:
    from llm_config.store import resolve_route
    chain = resolve_route(task_class)
    if not chain:
        return None
    return RoutedProvider(task_class, [
        (ModelProvider(base_url=rr.base_url, api_key=rr.api_key,
                       model=rr.model, embedding_model=rr.model), rr.endpoint_name)
        for rr in chain
    ])


def get_provider_for(task_class: str) -> RoutedProvider | ModelProvider | None:
    """Provider for a task class.

    - agent/code/extract/summarize: always returns something (classes
      inherit down to agent; an unconfigured agent falls back to the
      legacy settings-based ModelProvider).
    - vision/image_gen/rerank: None when unconfigured.
    - embed: the single configured ModelProvider (no fallback — mixing
      embedding models would corrupt stored vectors), else settings.
    """
    if task_class in _role_cache:
        return _role_cache[task_class]
    routed = _build_route(task_class)
    if task_class in ("embed", "rerank"):
        prov: Any = routed._candidates[0][0] if routed else (ModelProvider() if task_class == "embed" else None)
    elif task_class in ("vision", "image_gen"):
        prov = routed
    else:
        prov = routed or ModelProvider()
    _role_cache[task_class] = prov
    return prov


# ── Legacy role getters (kept for existing call sites) ─────────────────

def get_provider() -> Any:
    """Agent-class provider (the old "chat" role)."""
    return get_provider_for("agent")


def get_embedding_provider() -> ModelProvider:
    return get_provider_for("embed")


def get_prefill_provider() -> Any:
    """Old "prefill" role → Summarize class (inherits Agent when empty)."""
    return get_provider_for("summarize")


def get_vision_provider() -> Any:
    """Vision class provider. None when unmapped."""
    return get_provider_for("vision")


def get_reranker_provider() -> ModelProvider | None:
    """Reranker provider. None when unmapped."""
    return get_provider_for("rerank")


def reset_provider() -> None:
    """Clear all cached providers. Called whenever endpoints, routes or
    profiles change so the next lookup rebuilds."""
    _role_cache.clear()
