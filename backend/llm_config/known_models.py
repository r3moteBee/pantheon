"""Best-guess capability profiles for common model ids.

Used only to SEED a model's profile when the user hasn't edited it. Matching
is by regex on the lowercased model id (after any "provider/" prefix), first
match wins, so put specific patterns before general ones. Values are
approximate — the UI lets the user correct anything wrong, and a user edit
always wins (profile.source == "user").
"""
from __future__ import annotations

import re

from llm_config.models import ModelProfile

# (pattern, fields). Order matters.
_RULES: list[tuple[str, dict]] = [
    # ── Non-chat models ──
    (r"rerank", dict(tier="fast")),
    (r"(text-embedding|embed|^bge-|/bge-|^e5-|gte-|nomic-embed|mxbai-embed|snowflake-arctic-embed)",
     dict(embedding=True, tier="fast")),
    (r"(gpt-image|dall-e|flux|stable-diffusion|sdxl|sd3|imagen|playground-v|recraft|ideogram)",
     dict(image_gen=True)),

    # ── OpenAI ──
    (r"gpt-5.*(mini|nano)", dict(tools=True, vision=True, tier="fast", context_window=400_000)),
    (r"gpt-5", dict(tools=True, vision=True, tier="frontier", context_window=400_000)),
    (r"gpt-4\.1-(mini|nano)", dict(tools=True, vision=True, tier="fast", context_window=1_000_000)),
    (r"gpt-4\.1", dict(tools=True, vision=True, tier="frontier", context_window=1_000_000)),
    (r"gpt-4o-mini", dict(tools=True, vision=True, tier="fast", context_window=128_000)),
    (r"gpt-4o", dict(tools=True, vision=True, tier="standard", context_window=128_000)),
    (r"(^|/)o[134](-mini)?\b", dict(tools=True, vision=True, tier="frontier", context_window=200_000)),
    (r"gpt-3\.5", dict(tools=True, tier="fast", context_window=16_000)),

    # ── Anthropic ──
    (r"claude.*haiku", dict(tools=True, vision=True, tier="fast", context_window=200_000)),
    (r"claude", dict(tools=True, vision=True, tier="frontier", context_window=200_000)),

    # ── Google ──
    (r"gemini.*(flash|lite)", dict(tools=True, vision=True, tier="fast", context_window=1_000_000)),
    (r"gemini", dict(tools=True, vision=True, tier="frontier", context_window=1_000_000)),
    (r"gemma-?3", dict(vision=True, tier="fast", context_window=128_000)),
    (r"gemma", dict(tier="fast", context_window=8_000)),

    # ── Open-weight families (Ollama / vLLM / OpenRouter ids) ──
    (r"(llava|bakllava|moondream|minicpm-v|-vl\b|vl-|vision)", dict(vision=True, tier="fast")),
    (r"llama-?3\.[123].*(1b|3b|8b)", dict(tools=True, tier="fast", context_window=128_000)),
    (r"llama-?3\.[123]", dict(tools=True, tier="standard", context_window=128_000)),
    (r"llama-?4", dict(tools=True, vision=True, tier="standard", context_window=1_000_000)),
    (r"qwen.*coder", dict(tools=True, tier="standard", context_window=32_000)),
    (r"qwen-?(2\.5|3).*(0\.5b|1\.5b|3b|4b|7b|8b)", dict(tools=True, tier="fast", context_window=32_000)),
    (r"qwen-?(2\.5|3)", dict(tools=True, tier="standard", context_window=32_000)),
    (r"deepseek.*r1", dict(tier="frontier", context_window=64_000)),
    (r"deepseek", dict(tools=True, tier="standard", context_window=64_000)),
    (r"(mistral-large|mistral-medium)", dict(tools=True, tier="standard", context_window=128_000)),
    (r"(mistral|mixtral|ministral|codestral)", dict(tools=True, tier="fast", context_window=32_000)),
    (r"phi-?[34]", dict(tier="fast", context_window=16_000)),
    (r"(grok)", dict(tools=True, tier="frontier", context_window=128_000)),
]

_COMPILED = [(re.compile(p), f) for p, f in _RULES]


def guess_profile(model_id: str) -> ModelProfile:
    """Seed profile for ``model_id``; source="unknown" when nothing matched."""
    mid = (model_id or "").lower()
    for rx, fields in _COMPILED:
        if rx.search(mid):
            return ModelProfile(**fields, source="known")
    return ModelProfile(source="unknown")
