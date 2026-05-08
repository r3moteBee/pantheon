"""Generic probe-models for any (base_url, api_type, api_key) tuple.

Different API types expose model lists differently:
  - openai (and OpenAI-compatible): GET /v1/models -> {"data": [{"id": ...}]}
  - ollama: GET /api/tags -> {"models": [{"name": ...}]}
  - anthropic: no public list endpoint; we return a curated static list
  - custom: try /v1/models first, fall back to /models, then to anthropic's static set
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
        models = sorted({(m or {}).get("id", "") for m in data.get("data", []) if (m or {}).get("id")})
        return ProbeResult(ok=True, models=list(models), base_url=base_url, api_type="openai")
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
        return ProbeResult(ok=True, models=list(models), base_url=base_url, api_type="ollama")
    except Exception as e:
        return ProbeResult(ok=False, error=str(e), base_url=base_url, api_type="ollama")


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
