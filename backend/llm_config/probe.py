"""Generic probe-models for any (base_url, api_type, api_key) tuple.

Different API types expose model lists differently:
  - openai (and OpenAI-compatible): GET /models -> {"data": [{"id": ...}]}
  - ollama: GET /api/tags -> {"models": [{"name": ...}]}
  - anthropic: no public list endpoint; we return a curated static list
  - custom: try the OpenAI shape first; if that fails, try the Ollama shape

Model capabilities are read from whatever the server publishes (see
``capabilities_from_entry``) and returned per model id, so Pantheon can
profile models without guessing from their names.
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    ok: bool
    models: list[str] = field(default_factory=list)
    error: str = ""
    base_url: str = ""
    api_type: str = ""
    # model id -> advertised capabilities (only fields the server stated):
    # tools, vision, embedding, image_gen (bool), context_window (int)
    capabilities: dict[str, dict] = field(default_factory=dict)


# ── Advertised capabilities ─────────────────────────────────────────────
# Accepted shapes on each /models entry (all optional, all additive —
# plain OpenAI clients ignore them):
#   OpenRouter:  supported_parameters: ["tools", ...]
#                architecture: {input_modalities: [...], output_modalities: [...]}
#                context_length: 131072
#   Ollama /api/show, LM Studio, generic:
#                capabilities: ["completion", "tools", "vision", "embedding"]
#                  (or a dict {"tools": true, ...})
#   LiteLLM:     supports_function_calling / supports_tool_choice,
#                supports_vision, max_input_tokens
#   vLLM:        max_model_len        LM Studio: max_context_length, type: llm|vlm|embeddings
_TOOL_WORDS = {"tools", "tool_use", "tool_calling", "function_calling", "functions", "tool_choice"}
_VISION_WORDS = {"vision", "image", "image_input", "multimodal"}
_EMBED_WORDS = {"embedding", "embeddings", "embed"}
_IMAGE_GEN_WORDS = {"image_generation", "image_gen", "image_output"}
_CTX_KEYS = ("context_length", "context_window", "max_context_length", "max_model_len",
             "max_input_tokens", "n_ctx")


def _as_int(v) -> int | None:
    try:
        n = int(v)
        return n if n > 0 else None
    except (TypeError, ValueError):
        return None


def capabilities_from_entry(entry: dict) -> dict:
    """Capabilities a model-list entry explicitly advertises. A list-style
    declaration counts absence as False; unknown stays unset."""
    if not isinstance(entry, dict):
        return {}
    caps: dict = {}

    def from_words(words) -> None:
        w = {str(x).lower() for x in words}
        caps["tools"] = bool(w & _TOOL_WORDS)
        caps["vision"] = bool(w & _VISION_WORDS)
        caps["embedding"] = bool(w & _EMBED_WORDS)
        caps["image_gen"] = bool(w & _IMAGE_GEN_WORDS)

    c = entry.get("capabilities")
    if isinstance(c, (list, tuple)):
        from_words(c)
    elif isinstance(c, dict):
        for k, v in c.items():
            k = str(k).lower()
            if k in _TOOL_WORDS:
                caps["tools"] = bool(v)
            elif k in _VISION_WORDS:
                caps["vision"] = bool(v)
            elif k in _EMBED_WORDS:
                caps["embedding"] = bool(v)
            elif k in _IMAGE_GEN_WORDS:
                caps["image_gen"] = bool(v)

    sp = entry.get("supported_parameters")
    if isinstance(sp, (list, tuple)):
        caps["tools"] = bool({str(x).lower() for x in sp} & _TOOL_WORDS)

    arch = entry.get("architecture")
    if isinstance(arch, dict):
        ins = arch.get("input_modalities")
        outs = arch.get("output_modalities")
        if isinstance(ins, (list, tuple)):
            caps["vision"] = "image" in {str(x).lower() for x in ins}
        if isinstance(outs, (list, tuple)):
            outs_l = {str(x).lower() for x in outs}
            caps["image_gen"] = "image" in outs_l
            if "embeddings" in outs_l or "embedding" in outs_l:
                caps["embedding"] = True

    for key, cap in (("supports_function_calling", "tools"), ("supports_tool_choice", "tools"),
                     ("supports_vision", "vision"), ("supports_embedding", "embedding")):
        if isinstance(entry.get(key), bool):
            caps[cap] = caps.get(cap, False) or entry[key]

    t = str(entry.get("type") or "").lower()
    if t in ("embeddings", "embedding"):
        caps["embedding"] = True
    elif t == "vlm":
        caps["vision"] = True

    for k in _CTX_KEYS:
        n = _as_int(entry.get(k))
        if n:
            caps["context_window"] = n
            break
    # Ollama /api/show: model_info["<arch>.context_length"]
    mi = entry.get("model_info")
    if isinstance(mi, dict) and "context_window" not in caps:
        for k, v in mi.items():
            if str(k).endswith(".context_length") and _as_int(v):
                caps["context_window"] = _as_int(v)
                break
    return caps


# Curated default for Anthropic; users can type any model id manually.
_ANTHROPIC_STATIC = [
    "claude-opus-4-7",
    "claude-sonnet-4-6",
    "claude-haiku-4-5-20251001",
]


async def _async_get(url: str, *, headers: dict, timeout: int = 15):
    """Indirection so tests can monkeypatch network access."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.get(url, headers=headers)


async def _async_post(url: str, *, json: dict, headers: dict, timeout: int = 15):
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await client.post(url, json=json, headers=headers)


def _bearer(api_key: str) -> dict[str, str]:
    if not api_key or api_key.lower() in ("", "none"):
        return {}
    return {"Authorization": f"Bearer {api_key}"}


async def _probe_openai(base_url: str, api_key: str) -> ProbeResult:
    url = base_url.rstrip("/") + "/models"
    try:
        r = await _async_get(url, headers=_bearer(api_key), timeout=15)
        r.raise_for_status()
        data = r.json() or {}
        entries = [m for m in data.get("data", []) if isinstance(m, dict) and m.get("id")]
        models = sorted({m["id"] for m in entries})
        caps = {m["id"]: c for m in entries if (c := capabilities_from_entry(m))}
        return ProbeResult(ok=True, models=list(models), base_url=base_url, api_type="openai",
                           capabilities=caps)
    except Exception as e:
        return ProbeResult(ok=False, error=str(e), base_url=base_url, api_type="openai")


async def _probe_ollama(base_url: str) -> ProbeResult:
    # Ollama's /api/tags is sibling to /v1; trim a trailing /v1 if present.
    root = base_url.rstrip("/")
    if root.endswith("/v1"):
        root = root[: -len("/v1")]
    url = root + "/api/tags"
    try:
        r = await _async_get(url, headers={}, timeout=15)
        r.raise_for_status()
        data = r.json() or {}
        models = sorted({(m or {}).get("name", "") for m in data.get("models", []) if (m or {}).get("name")})
        caps = await _ollama_capabilities(root, models)
        return ProbeResult(ok=True, models=list(models), base_url=base_url, api_type="ollama",
                           capabilities=caps)
    except Exception as e:
        return ProbeResult(ok=False, error=str(e), base_url=base_url, api_type="ollama")


_OLLAMA_SHOW_LIMIT = 40


async def _ollama_capabilities(root: str, models: list[str]) -> dict[str, dict]:
    """/api/show per model (capabilities + context length). Best-effort,
    bounded: a failure just leaves that model to the name-based guess."""
    import asyncio
    sem = asyncio.Semaphore(4)

    async def one(name: str):
        async with sem:
            try:
                r = await _async_post(root + "/api/show", json={"model": name}, headers={}, timeout=10)
                r.raise_for_status()
                return name, capabilities_from_entry(r.json() or {})
            except Exception as e:
                logger.debug("ollama /api/show %s failed: %s", name, e)
                return name, {}

    results = await asyncio.gather(*(one(m) for m in models[:_OLLAMA_SHOW_LIMIT]))
    return {n: c for n, c in results if c}


async def probe_models(*, base_url: str, api_type: str, api_key: str) -> ProbeResult:
    """Discover available models for an endpoint.

    api_type semantics:
      - 'openai': GET /v1/models (works for OpenAI, LM Studio, vLLM, OpenRouter, etc.)
      - 'ollama': GET /api/tags
      - 'anthropic': static curated list (no public listing endpoint)
      - 'custom': try /v1/models, fall back to ollama-style /api/tags
    """
    if api_type == "anthropic":
        return ProbeResult(ok=True, models=list(_ANTHROPIC_STATIC), base_url=base_url, api_type="anthropic")
    if api_type == "ollama":
        return await _probe_ollama(base_url)
    if api_type == "openai":
        return await _probe_openai(base_url, api_key)
    # custom
    r1 = await _probe_openai(base_url, api_key)
    if r1.ok:
        return r1
    r2 = await _probe_ollama(base_url)
    if r2.ok:
        return r2
    return ProbeResult(ok=False, error=r1.error or r2.error, base_url=base_url, api_type="custom")
