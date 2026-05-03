"""Topic-extraction strategies for source adapters.

A TopicExtractor takes raw text (transcript, blog body, PDF text)
and returns the structured topics/speakers/claims that downstream
graph extraction expects.

Adapters declare a default extractor by name (class attribute
``extractor_strategy``). Skills can override per-call by passing
``extras={"extractor_strategy": "..."}`` on the IngestRequest.

Two built-in extractors:

  - ``llm_default``  — calls the curation LLM with a structured
    prompt, parses the JSON response. Reasonable accuracy, costs
    one LLM call per artifact, type-aware.

  - ``noop``         — returns empty lists. For sources whose
    topics are already in their metadata (some PDFs, RSS items
    with existing tags) and don't need LLM extraction.

To add another strategy (regex extraction, named entity
recognition, etc.), subclass TopicExtractor, set ``name``, and
call ``register_extractor(YourClass())`` at module import time.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ── Result type ────────────────────────────────────────────────────

@dataclass
class ExtractedFields:
    """What an extractor returns. Shape matches the typed-topics
    frontmatter — extractors fill in topics/speakers/claims; the
    artifact's existing source/url/title/published_at fields are
    NOT touched here.

    ``status`` is a free-form diagnostics dict. Conventional keys:
      strategy: name of the extractor that ran
      ok: bool — did the extractor finish without an error
      error: str — populated on failure
      raw_excerpt: short slice of the LLM response when parsing
        failed; lets the user diagnose 'why was topics: [] empty'.
      reason: optional extra context (e.g. 'truncated_to_60k',
        'no_provider_response')
    """
    topics: list[dict[str, Any]] = field(default_factory=list)
    speakers: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    status: dict[str, Any] = field(default_factory=dict)


# ── Base class + registry ──────────────────────────────────────────

class TopicExtractor:
    """Contract for topic-extraction strategies."""
    name: str = ""

    async def extract(
        self,
        text: str,
        *,
        title: str = "",
        source_type: str = "",
        max_topics: int = 12,
        hint: str | None = None,
    ) -> ExtractedFields:
        raise NotImplementedError


_EXTRACTORS: dict[str, TopicExtractor] = {}


def register_extractor(extractor: TopicExtractor) -> None:
    if not extractor.name:
        raise ValueError("Extractor missing name")
    _EXTRACTORS[extractor.name] = extractor
    logger.info("Registered topic extractor: %s", extractor.name)


def get_extractor(name: str | None) -> TopicExtractor:
    """Return the named extractor; falls back to llm_default. Never
    returns None — the default is always available."""
    if name and name in _EXTRACTORS:
        return _EXTRACTORS[name]
    if name and name not in _EXTRACTORS:
        logger.warning("Unknown extractor %r, falling back to llm_default", name)
    return _EXTRACTORS["llm_default"]


def list_extractors() -> list[str]:
    return sorted(_EXTRACTORS.keys())


# ── Built-in: noop ────────────────────────────────────────────────

class NoopExtractor(TopicExtractor):
    name = "noop"

    async def extract(self, text, **kwargs) -> ExtractedFields:
        return ExtractedFields(status={"strategy": "noop", "ok": True})


# ── Built-in: llm_default ─────────────────────────────────────────

_LLM_SYSTEM_PROMPT = """\
You are a structured-knowledge extractor. Given a document body,
extract the topics, speakers, and claims as STRICT JSON.

Output schema:

{
  "topics": [
    {
      "label": "<canonical phrase>",
      "type": "concept|technology|framework|vendor|person|organization|market|market_segment|metric|event|other",
      "confidence": 0.0-1.0,
      "topic_evidence": {
        "summary": "<one-sentence rationale>",
        "evidence_text": "<short verbatim or near-verbatim quote from the document>",
        "evidence_source": "transcript"
      }
    }
  ],
  "speakers": [
    {"name": "...", "role": "speaker|host|moderator|unknown", "confidence": 0.0-1.0}
  ],
  "claims": [
    {"claim": "...", "confidence": 0.0-1.0, "evidence_text": "...", "connected_topics": ["..."]}
  ]
}

Rules:
- Use canonical labels ("Anthropic", not "the Anthropic company").
- type='person' is for people who are themselves the topic; speakers go in speakers[].
- Only include speakers when the document explicitly attributes
  utterances to a named individual ("MARK:", "Q:", channel
  metadata). Do NOT infer speakers from topic discussions.
- Confidence: 1.0 = explicit statement, 0.7 = strong implication,
  0.4 = weak inference. Skip below 0.3.
- Limit to the {max_topics} most-significant topics. Drop noise.
- Return ONLY the JSON object, no markdown fences or commentary.
"""


def _strip_fence(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(r"^```(?:json|markdown|md)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    return s.strip()


class LLMDefaultExtractor(TopicExtractor):
    """Default — single LLM call with a structured prompt.

    Truncates the document body to a budget if it exceeds the
    context window we want to spend. For very long inputs the
    skill should chunk and merge before calling, but we cap here
    too as a backstop."""
    name = "llm_default"

    BODY_BUDGET_CHARS = 60_000

    async def extract(
        self,
        text: str,
        *,
        title: str = "",
        source_type: str = "",
        max_topics: int = 12,
        hint: str | None = None,
    ) -> ExtractedFields:
        status: dict[str, Any] = {"strategy": self.name, "ok": False}
        if not text or not text.strip():
            status["error"] = "empty_input"
            return ExtractedFields(status=status)
        body = text.strip()
        truncated = False
        if len(body) > self.BODY_BUDGET_CHARS:
            body = body[: self.BODY_BUDGET_CHARS] + "\n\n[…truncated…]"
            truncated = True

        system = _LLM_SYSTEM_PROMPT.replace("{max_topics}", str(max_topics))
        user_lines = [
            f"Document title: {title}" if title else "",
            f"Source type: {source_type}" if source_type else "",
            f"Hint: {hint}" if hint else "",
            "",
            body,
        ]
        user = "\n".join(line for line in user_lines if line is not None).strip()

        raw_content = ""
        try:
            from models.provider import get_provider
            provider = get_provider()
            result = await provider.chat_complete([
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ])
            raw_content = (result or {}).get("content") or ""
            content = _strip_fence(raw_content)
            if not content:
                status["error"] = "empty_llm_response"
                status["raw_excerpt"] = (raw_content or "")[:300]
                if truncated:
                    status["reason"] = "input_truncated_to_60k"
                logger.warning("Topic extractor: empty LLM response (model=%s)",
                               getattr(provider, "model", "?"))
                return ExtractedFields(status=status)
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as je:
                # Try to recover JSON from prose: many models wrap
                # the JSON in chatter despite the prompt.
                m = re.search(r"\{[\s\S]*\}\s*$", content)
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        parsed = None
                else:
                    parsed = None
                if parsed is None:
                    status["error"] = f"json_parse_failed: {je}"
                    status["raw_excerpt"] = content[:300]
                    logger.warning(
                        "Topic extractor: LLM did not return parseable JSON "
                        "(error=%s, excerpt=%r)", je, content[:200],
                    )
                    return ExtractedFields(status=status)
        except Exception as ex:
            status["error"] = f"llm_call_failed: {type(ex).__name__}: {ex}"
            status["raw_excerpt"] = (raw_content or "")[:300]
            logger.warning("Topic extractor: LLM call failed: %s", ex)
            return ExtractedFields(status=status)

        topics = list(parsed.get("topics") or [])
        speakers = list(parsed.get("speakers") or [])
        claims = list(parsed.get("claims") or [])
        status.update({
            "ok": True,
            "topic_count": len(topics),
            "speaker_count": len(speakers),
            "claim_count": len(claims),
        })
        if truncated:
            status["reason"] = "input_truncated_to_60k"
        return ExtractedFields(
            topics=topics, speakers=speakers, claims=claims, status=status,
        )


# Register defaults at import time.
register_extractor(NoopExtractor())
register_extractor(LLMDefaultExtractor())
