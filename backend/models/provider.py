"""OpenAI-compatible model provider abstraction."""
from __future__ import annotations
import asyncio
import json
import logging
import re
import uuid
from typing import Any, AsyncGenerator

from collections import OrderedDict

import httpx

from utils.http import pooled_client

from config import get_settings


# Fields that define the request itself; extra_body can never replace them.
_PROTECTED_FIELDS = frozenset({"model", "messages", "stream", "tools"})


def _apply_request_extras(payload: dict, extra_body: dict | None) -> dict:
    """Merge caller-supplied request-body fields (e.g. {"chat_template_kwargs":
    {"enable_thinking": True}}, {"tool_choice": "none"}). They may override
    request options such as tool_choice, never model/messages/stream/tools.
    Only the agent loop passes any, and only when configured to:
    OpenAI-compatible providers may reject unknown fields."""
    for k, v in (extra_body or {}).items():
        if k not in _PROTECTED_FIELDS:
            payload[k] = v
    return payload

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
    if detail is not None and not isinstance(detail, str):
        detail = json.dumps(detail)[:500]  # e.g. FastAPI/pydantic 422 detail lists
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


# (base_url, model) -> "images" | "chat": which image API style worked.
_IMAGE_API_STYLE: dict[tuple[str, str], str] = {}
# (base_url, model) -> request field that carries a named size preset
# ("square_hd", "landscape_16_9", … — fal.ai-style), set once the backend
# has rejected WxH / W:H with an error naming those presets.
_IMAGE_WANTS_PRESET: dict[tuple[str, str], str] = {}

# Named size presets (fal.ai naming, used by RouteLLM and others) -> (w, h).
_SIZE_PRESETS: dict[str, tuple[int, int]] = {
    "square_hd": (1024, 1024),
    "square": (512, 512),
    "landscape_4_3": (1024, 768),
    "landscape_16_9": (1024, 576),
    "portrait_4_3": (768, 1024),
    "portrait_16_9": (576, 1024),
}
_PRESET_RE = re.compile(r"\b(square_hd|square|landscape_4_3|landscape_16_9|portrait_4_3|portrait_16_9)\b")


def _size_dims(size: str) -> tuple[int, int]:
    """(w, h) for "1536x1024", "16:9" or a named preset; (1024, 1024) if unparseable."""
    s = (size or "").strip().lower()
    if s in _SIZE_PRESETS:
        return _SIZE_PRESETS[s]
    for sep in ("x", ":"):
        if sep in s:
            try:
                w, h = (float(x) for x in s.split(sep, 1))
            except ValueError:
                break
            if w > 0 and h > 0:
                if sep == ":":  # ratio -> ~1MP-ish pixels on the long side 1024
                    scale = 1024 / max(w, h)
                    return int(round(w * scale / 8) * 8), int(round(h * scale / 8) * 8)
                return int(w), int(h)
    return 1024, 1024


def _pixel_size(size: str) -> str:
    """Any accepted size spelling -> "WxH" for /images/generations."""
    w, h = _size_dims(size)
    return f"{w}x{h}"


def _aspect_ratio(size: str) -> str:
    """"1536x1024" -> "3:2" (chat-style image APIs take a ratio, not pixels)."""
    from math import gcd
    w, h = _size_dims(size)
    g = gcd(w, h) or 1
    return f"{w // g}:{h // g}"


def _size_preset(size: str, allowed: list[str] | None = None) -> str:
    """Nearest named preset by aspect ratio, restricted to ``allowed`` when the
    backend's error listed the values it accepts."""
    s = (size or "").strip().lower()
    candidates = [p for p in (allowed or _SIZE_PRESETS) if p in _SIZE_PRESETS] or list(_SIZE_PRESETS)
    if s in candidates:
        return s
    w, h = _size_dims(size)
    want = w / h
    # Prefer square_hd over square unless the request is small.
    ranked = sorted(
        candidates,
        key=lambda p: (abs(_SIZE_PRESETS[p][0] / _SIZE_PRESETS[p][1] - want),
                       0 if (p == "square") == (max(w, h) <= 512) else 1),
    )
    return ranked[0]


def _presets_in_error(err: BaseException) -> list[str]:
    """Named presets mentioned in a provider error — non-empty means the
    backend wants preset names rather than WxH / W:H."""
    return list(dict.fromkeys(_PRESET_RE.findall(str(err).lower())))


def _image_mime_of(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _image_prompt_content(prompt: str, images: list[bytes] | None) -> Any:
    """Chat-mode user content: plain prompt, or prompt + source images
    (image-edit models such as qwen-image-edit read them from here)."""
    if not images:
        return prompt
    import base64
    return [{"type": "text", "text": prompt}] + [
        {"type": "image_url",
         "image_url": {"url": f"data:{_image_mime_of(img)};base64,{base64.b64encode(img).decode()}"}}
        for img in images
    ]


def _image_refs_from_chat(data: dict[str, Any]) -> list[str]:
    """Image URLs / data URIs from a chat-completions image response.
    Handles message.images[].image_url.url, content parts of type
    image_url, and Gemini-style inline_data."""
    refs: list[str] = []
    try:
        msg = (data.get("choices") or [{}])[0].get("message") or {}
    except (AttributeError, IndexError):
        return refs
    for img in msg.get("images") or []:
        if isinstance(img, dict):
            url = (img.get("image_url") or {}).get("url") or img.get("url")
            if url:
                refs.append(url)
    content = msg.get("content")
    if isinstance(content, list):
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "image_url":
                url = (part.get("image_url") or {}).get("url")
                if url:
                    refs.append(url)
            inline = part.get("inline_data") or part.get("inlineData")
            if isinstance(inline, dict) and inline.get("data"):
                mime = inline.get("mime_type") or inline.get("mimeType") or "image/png"
                refs.append(f"data:{mime};base64,{inline['data']}")
    return refs


async def _fetch_image_ref(ref: str) -> bytes:
    """Bytes for a data: URI or an http(s) URL (via the SSRF guard)."""
    import base64
    if ref.startswith("data:"):
        _, _, b64 = ref.partition(",")
        return base64.b64decode(b64)
    from utils.net import safe_http_get
    r = await safe_http_get(ref, timeout=120)
    r.raise_for_status()
    return r.content


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
        extra_body: dict | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream chat completions. Yields event dicts."""
        if stream:
            async for event in self._stream_chat(messages, tools, extra_body):
                yield event
        else:
            async for event in self._stream_non_streaming(messages, tools, extra_body):
                yield event

    async def _stream_non_streaming(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        extra_body: dict | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        result = await self.chat_complete(messages, tools, extra_body)
        if result.get("content"):
            yield {"type": "text_delta", "content": result["content"]}
        for tc in result.get("tool_calls", []):
            yield {"type": "tool_call", **tc}
        yield {"type": "done", "reasoning": result.get("reasoning") or ""}

    async def _stream_chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict] | None = None,
        extra_body: dict | None = None,
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
        _apply_request_extras(payload, extra_body)

        # Accumulate tool call chunks
        tool_call_accum: dict[int, dict[str, Any]] = {}
        current_text = ""
        current_reasoning = ""   # reasoning_content deltas (thinking models); not shown to the user

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
                        # Don't stop on finish_reason — wait for [DONE].
                        # Some providers (Gemini, RouteLLM) send
                        # finish_reason in the same chunk as the last content
                        # token, so breaking there truncates the response.

                        # Text content
                        content = delta.get("content", "")
                        if content:
                            current_text += content
                            yield {"type": "text_delta", "content": content}
                        current_reasoning += delta.get("reasoning_content") or ""

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
                args, args_error = parse_tool_args(tc["args_str"])
                event = {
                    "type": "tool_call",
                    "id": tc["id"],
                    "name": tc["name"],
                    "args": args,
                }
                if args_error:
                    event["args_error"] = args_error
                yield event

            yield {"type": "done", "content": current_text, "reasoning": current_reasoning,
                   "finish_reason": finish_reason}

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
        extra_body: dict | None = None,
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
        _apply_request_extras(payload, extra_body)

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
                args, args_error = parse_tool_args(fn.get("arguments"))
                call = {
                    "id": tc.get("id", str(uuid.uuid4())),
                    "name": fn.get("name", ""),
                    "args": args,
                }
                if args_error:
                    call["args_error"] = args_error
                tool_calls.append(call)

            return {"content": content, "tool_calls": tool_calls, "usage": usage,
                    "reasoning": message.get("reasoning_content") or ""}

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
        quality: str | None = None, images: list[bytes] | None = None,
    ) -> list[bytes]:
        """Generate images; returns raw image bytes.

        Two API styles exist in the wild and we support both:
          - "images": OpenAI ``POST /images/generations`` (OpenAI, LocalAI, …)
          - "chat":   ``POST /chat/completions`` with ``modalities:
            ["image","text"]`` — images come back in
            ``choices[0].message.images[].image_url.url`` (Abacus RouteLLM,
            OpenRouter, Gemini-style multimodal models).
        The style that works is remembered per base_url+model; the first
        call tries /images/generations and falls back to chat on 400/404/405/422.
        """
        key = (self.base_url, self.model)
        style = _IMAGE_API_STYLE.get(key)
        if style != "chat":
            try:
                out = await self._images_with_preset_retry(
                    self._generate_image_openai, prompt, size=size, n=n, quality=quality,
                    images=images)
                _IMAGE_API_STYLE[key] = "images"
                return out
            except LLMHTTPError as e:
                if style == "images" or e.status not in (400, 404, 405, 422):
                    raise
                logger.info("%s: /images/generations failed (%s) — trying chat image mode",
                            self.model, e.status)
        out = await self._images_with_preset_retry(
            self._generate_image_chat, prompt, size=size, n=n, quality=quality, images=images)
        _IMAGE_API_STYLE[key] = "chat"
        return out

    async def _images_with_preset_retry(self, fn, prompt: str, *, size: str, n: int,
                                        quality: str | None,
                                        images: list[bytes] | None = None) -> list[bytes]:
        """Call ``fn`` with the size in its native spelling; if the backend
        rejects it with an error naming presets (``square_hd`` …), retry once
        with the nearest preset and remember that this model wants presets."""
        key = (self.base_url, self.model)
        field = _IMAGE_WANTS_PRESET.get(key)
        if field:
            return await fn(prompt, size=size, n=n, quality=quality, images=images,
                            preset=(field, _size_preset(size)))
        try:
            return await fn(prompt, size=size, n=n, quality=quality, images=images, preset=None)
        except LLMHTTPError as e:
            allowed = _presets_in_error(e)
            if e.status not in (400, 422) or not allowed:
                raise
            field = "image_size" if "image_size" in str(e).lower() else "aspect_ratio"
            preset = _size_preset(size, allowed)
            logger.info("%s wants named image sizes — retrying with %s=%s", self.model, field, preset)
            out = await fn(prompt, size=size, n=n, quality=quality, images=images, preset=(field, preset))
            _IMAGE_WANTS_PRESET[key] = field
            return out

    async def _generate_image_openai(self, prompt: str, *, size: str, n: int,
                                     quality: str | None,
                                     preset: tuple[str, str] | None = None,
                                     images: list[bytes] | None = None) -> list[bytes]:
        import base64
        # /images/generations always carries size in "size", preset or pixels.
        payload: dict[str, Any] = {"model": self.model, "prompt": prompt, "n": n,
                                   "size": preset[1] if preset else _pixel_size(size)}
        if quality:
            payload["quality"] = quality
        async with pooled_client(timeout=300.0) as client:
            if images:
                # Edit: OpenAI /images/edits takes multipart with the source image(s).
                headers = {k: v for k, v in self._headers().items() if k.lower() != "content-type"}
                files = [("image[]" if len(images) > 1 else "image",
                          (f"source-{i}.png", img, _image_mime_of(img))) for i, img in enumerate(images)]
                resp = await client.post(f"{self.base_url}/images/edits", headers=headers,
                                         data={k: str(v) for k, v in payload.items()}, files=files)
            else:
                resp = await client.post(f"{self.base_url}/images/generations",
                                         headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            raise LLMHTTPError(resp.status_code, _format_llm_error(resp.status_code, resp.text))
        images: list[bytes] = []
        for item in resp.json().get("data") or []:
            if item.get("b64_json"):
                images.append(base64.b64decode(item["b64_json"]))
            elif item.get("url"):
                images.append(await _fetch_image_ref(item["url"]))
        if not images:
            raise RuntimeError("image endpoint returned no images")
        return images

    async def _generate_image_chat(self, prompt: str, *, size: str, n: int,
                                   quality: str | None,
                                   preset: tuple[str, str] | None = None,
                                   images: list[bytes] | None = None) -> list[bytes]:
        image_config: dict[str, Any] = {"num_images": n, "aspect_ratio": _aspect_ratio(size)}
        if preset:
            image_config[preset[0]] = preset[1]
        if quality:
            image_config["quality"] = quality
        payload: dict[str, Any] = {
            "model": self.model,
            "modalities": ["image", "text"],
            "messages": [{"role": "user", "content": _image_prompt_content(prompt, images)}],
            "image_config": image_config,
            "stream": False,
        }
        async with pooled_client(timeout=300.0) as client:
            resp = await client.post(f"{self.base_url}/chat/completions",
                                     headers=self._headers(), json=payload)
        if resp.status_code >= 400:
            raise LLMHTTPError(resp.status_code, _format_llm_error(resp.status_code, resp.text))
        data = resp.json()
        refs = _image_refs_from_chat(data)
        if not refs:
            text = ""
            try:
                text = (data["choices"][0]["message"].get("content") or "")
                text = text if isinstance(text, str) else ""
            except (KeyError, IndexError, TypeError, AttributeError):
                pass
            raise RuntimeError(
                "model returned no image"
                + (f" (it replied: {text[:200]!r})" if text else "")
                + " — check that this model supports image output"
            )
        return [await _fetch_image_ref(r) for r in refs[:max(1, n)]]


# ── Task-class routing ───────────────────────────────────────────────
# Call sites ask for a provider by task class (agent, code, extract,
# summarize, vision, image_gen, embed, rerank). A class resolves to an
# ordered list of endpoint+model candidates; RoutedProvider tries them in
# order, falling back on rate limits, server errors, missing models and
# network failures, and logs every attempt to llm_calls.db.

_RETRYABLE_STATUS = {404, 408, 409, 425, 429}

# Set to a list by a caller (the chat router) that wants to know which
# endpoint/model actually served its calls after fallbacks. A list rather
# than a value so tasks spawned from the turn (copied contexts) still
# report into it.
from contextvars import ContextVar
served_models: ContextVar[list | None] = ContextVar("served_models", default=None)


def _note_served(endpoint: str, model: str) -> None:
    sink = served_models.get()
    if sink is not None:
        sink.append((endpoint, model))


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
                            tools: list[dict] | None = None,
                            extra_body: dict | None = None) -> dict[str, Any]:
        import time as _t
        last_exc: BaseException | None = None
        for i, (prov, ep) in enumerate(self._candidates):
            started = _t.monotonic()
            try:
                result = await prov.chat_complete(messages, tools, extra_body=extra_body)
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
            _note_served(ep, prov.model)
            return result
        raise last_exc or RuntimeError("no candidates")

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict] | None = None,
                   stream: bool = True,
                   extra_body: dict | None = None) -> AsyncGenerator[dict[str, Any], None]:
        import time as _t
        if not stream:
            result = await self.chat_complete(messages, tools, extra_body=extra_body)
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
            async for event in prov.chat(messages, tools, stream=True, extra_body=extra_body):
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
                    _note_served(ep, prov.model)
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


def parse_tool_args(raw: Any) -> tuple[dict, str | None]:
    """Tool-call arguments as a dict, plus an error when they weren't a
    JSON object. The agent loop reports that error back to the model
    (instead of running the tool with {} and surfacing a KeyError)."""
    if raw is None or raw == "":
        return {}, None
    if isinstance(raw, dict):
        return raw, None
    try:
        val = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        # Some servers append junk after a complete object.
        try:
            val, _ = json.JSONDecoder().raw_decode(str(raw).strip())
        except (json.JSONDecodeError, TypeError):
            return {}, f"arguments were not valid JSON: {str(raw)[:200]}"
    if not isinstance(val, dict):
        return {}, f"arguments must be a JSON object, got {type(val).__name__}"
    return val, None


def _fallback_embedder() -> "ModelProvider":
    """Settings-based embedder for installs with no embed route yet.
    EMBEDDING_BASE_URL (+ its own key, never the LLM key) wins over the
    LLM endpoint, e.g. an Ollama embedder next to a hosted chat model."""
    s = get_settings()
    prov = ModelProvider(base_url=s.embedding_base_url or None)
    if s.embedding_base_url:
        prov.api_key = s.embedding_api_key
    return prov


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
        prov: Any = routed._candidates[0][0] if routed else (_fallback_embedder() if task_class == "embed" else None)
    elif task_class in ("vision", "image_gen"):
        prov = routed
    else:
        prov = routed or ModelProvider()
    _role_cache[task_class] = prov
    return prov


# ── Legacy role getters: back-compat aliases for get_provider_for(<class>).
# New code (and every in-tree call site) should call get_provider_for directly.

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
