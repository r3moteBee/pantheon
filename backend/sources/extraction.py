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
    artifact\'s existing source/url/title/published_at fields are
    NOT touched here.

    ``status`` is a free-form diagnostics dict. Conventional keys:
      strategy, ok, error, raw_excerpt, reason, *_count

    ``frontmatter_additions`` is for specialized extractors that
    produce structured fields beyond topics/speakers/claims —
    e.g. llm_structured_specs returns {specifications: [...]},
    llm_research_paper returns {abstract: "...", methodology: ...},
    llm_changelog returns {version: "1.2.0", breaking_changes: [...]}.
    Whatever\'s here gets merged into the artifact\'s frontmatter
    by registry.ingest() at top-level (after the source/topics/
    etc. block). Use a key namespace that won\'t collide with the
    standard typed-topics shape.
    """
    topics: list[dict[str, Any]] = field(default_factory=list)
    speakers: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    status: dict[str, Any] = field(default_factory=dict)
    frontmatter_additions: dict[str, Any] = field(default_factory=dict)


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



# ── Built-in: llm_announcement ───────────────────────────────────

_ANNOUNCEMENT_SYSTEM_PROMPT = """\
You are a structured-knowledge extractor for VENDOR / EVENT
ANNOUNCEMENTS (product launches, partnerships, milestones, release
news). Extract the topics + speakers + claims AND a structured
\"announcement\" block with the canonical announcement metadata.

Output STRICT JSON:

{
  "topics": [...],         // typed-topics shape, see below
  "speakers": [...],
  "claims": [...],
  "announcement": {
    "announced_at": "YYYY-MM-DD or null",
    "announced_by": "Vendor or organization name",
    "what": "<one sentence: what was announced>",
    "partners": ["..."],
    "products": ["..."],
    "dollar_amounts": [
      {"amount": "$X", "context": "what the money refers to"}
    ],
    "key_dates": [
      {"date": "YYYY-MM-DD", "event": "GA, beta, embargo, ..."}
    ]
  }
}

topics[] type vocabulary: concept | technology | framework | vendor |
person | organization | market | market_segment | metric | event |
other.

Rules: canonical labels, evidence-based confidence, only attributable
speakers, drop noise. Limit topics to {max_topics}. Return ONLY JSON.
"""


class LLMAnnouncementExtractor(LLMDefaultExtractor):
    """Variant that emphasizes who/what/when/dollars/partners.
    Inherits the chunking + JSON-recovery logic from the default."""
    name = "llm_announcement"

    async def extract(
        self, text, *, title="", source_type="", max_topics=12, hint=None,
    ) -> ExtractedFields:
        # Run the parent\'s LLM call with our specialized system prompt.
        # We don\'t need the full body of LLMDefaultExtractor.extract —
        # we override the prompt and parsing.
        status = {"strategy": self.name, "ok": False}
        if not text or not text.strip():
            status["error"] = "empty_input"
            return ExtractedFields(status=status)
        body = text.strip()
        truncated = False
        if len(body) > self.BODY_BUDGET_CHARS:
            body = body[: self.BODY_BUDGET_CHARS] + "\n\n[…truncated…]"
            truncated = True
        system = _ANNOUNCEMENT_SYSTEM_PROMPT.replace("{max_topics}", str(max_topics))
        user_lines = [
            f"Document title: {title}" if title else "",
            f"Source type: {source_type}" if source_type else "",
            f"Hint: {hint}" if hint else "",
            "", body,
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
                return ExtractedFields(status=status)
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as je:
                m = re.search(r"\{[\s\S]*\}\s*$", content)
                parsed = None
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        parsed = None
                if parsed is None:
                    status["error"] = f"json_parse_failed: {je}"
                    status["raw_excerpt"] = content[:300]
                    return ExtractedFields(status=status)
        except Exception as ex:
            status["error"] = f"llm_call_failed: {type(ex).__name__}: {ex}"
            status["raw_excerpt"] = (raw_content or "")[:300]
            return ExtractedFields(status=status)

        topics = list(parsed.get("topics") or [])
        speakers = list(parsed.get("speakers") or [])
        claims = list(parsed.get("claims") or [])
        announcement = parsed.get("announcement") or {}
        status.update({
            "ok": True,
            "topic_count": len(topics),
            "speaker_count": len(speakers),
            "claim_count": len(claims),
        })
        if truncated:
            status["reason"] = "input_truncated_to_60k"
        return ExtractedFields(
            topics=topics, speakers=speakers, claims=claims,
            status=status,
            frontmatter_additions={"announcement": announcement} if announcement else {},
        )


# ── Built-in: llm_structured_specs ───────────────────────────────

_SPECS_SYSTEM_PROMPT = """\
You are a structured-data extractor for PRODUCT / SERVICE / DATASHEET
content. Capture the typed topics AND a structured \"specifications\"
block plus pricing and feature lists.

Output STRICT JSON:

{
  "topics": [...],
  "claims": [...],
  "specifications": [
    {
      "name": "<spec name, e.g. 'CPU', 'Power consumption', 'API rate limit'>",
      "value": "<value as string, e.g. '2.4 GHz', '750 W', '1000 req/min'>",
      "unit": "<optional unit if it makes sense; can be empty>",
      "category": "<optional grouping: 'compute', 'storage', 'network', 'pricing', 'limits', 'compliance', ...>"
    }
  ],
  "pricing": [
    {"tier": "<tier or plan name>", "price": "<value>", "interval": "<month, year, one-time, hour, ...>", "notes": "<optional>"}
  ],
  "features": [
    "<one feature description per item, short>"
  ],
  "compatibility": [
    "<system / product / standard this is compatible with>"
  ]
}

topics[] type vocabulary: same as default.

Rules: only include facts present in the document. Numbers stay as
strings (\"2.4 GHz\", \"$99\") to preserve units. Drop marketing
fluff that isn\'t a quantifiable spec. Limit topics to {max_topics}.
Return ONLY JSON.
"""


class LLMStructuredSpecsExtractor(LLMDefaultExtractor):
    """For datasheets, product pages, service pages — anything that
    has structured specs / pricing / features."""
    name = "llm_structured_specs"

    async def extract(
        self, text, *, title="", source_type="", max_topics=12, hint=None,
    ) -> ExtractedFields:
        status = {"strategy": self.name, "ok": False}
        if not text or not text.strip():
            status["error"] = "empty_input"
            return ExtractedFields(status=status)
        body = text.strip()
        truncated = False
        if len(body) > self.BODY_BUDGET_CHARS:
            body = body[: self.BODY_BUDGET_CHARS] + "\n\n[…truncated…]"
            truncated = True
        system = _SPECS_SYSTEM_PROMPT.replace("{max_topics}", str(max_topics))
        user_lines = [
            f"Document title: {title}" if title else "",
            f"Source type: {source_type}" if source_type else "",
            f"Hint: {hint}" if hint else "",
            "", body,
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
                return ExtractedFields(status=status)
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as je:
                m = re.search(r"\{[\s\S]*\}\s*$", content)
                parsed = None
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        parsed = None
                if parsed is None:
                    status["error"] = f"json_parse_failed: {je}"
                    status["raw_excerpt"] = content[:300]
                    return ExtractedFields(status=status)
        except Exception as ex:
            status["error"] = f"llm_call_failed: {type(ex).__name__}: {ex}"
            status["raw_excerpt"] = (raw_content or "")[:300]
            return ExtractedFields(status=status)

        topics = list(parsed.get("topics") or [])
        claims = list(parsed.get("claims") or [])
        specifications = list(parsed.get("specifications") or [])
        pricing = list(parsed.get("pricing") or [])
        features = list(parsed.get("features") or [])
        compatibility = list(parsed.get("compatibility") or [])
        status.update({
            "ok": True,
            "topic_count": len(topics),
            "speaker_count": 0,
            "claim_count": len(claims),
            "specifications_count": len(specifications),
            "pricing_count": len(pricing),
            "features_count": len(features),
        })
        if truncated:
            status["reason"] = "input_truncated_to_60k"

        additions: dict[str, Any] = {}
        if specifications:
            additions["specifications"] = specifications
        if pricing:
            additions["pricing"] = pricing
        if features:
            additions["features"] = features
        if compatibility:
            additions["compatibility"] = compatibility

        return ExtractedFields(
            topics=topics, speakers=[], claims=claims,
            status=status, frontmatter_additions=additions,
        )


# ── Built-in: llm_research_paper ─────────────────────────────────

_RESEARCH_SYSTEM_PROMPT = """\
You are a structured-knowledge extractor for ACADEMIC / RESEARCH
papers (arxiv preprints, conference papers, journal articles).

Output STRICT JSON:

{
  "topics": [...],
  "claims": [...],
  "research": {
    "abstract": "<verbatim or near-verbatim abstract>",
    "research_questions": ["<question or hypothesis>"],
    "methodology": "<one paragraph summary of how the work was done>",
    "findings": [
      "<one finding per item, ideally with a quantitative claim>"
    ],
    "limitations": ["<known limitation per item>"],
    "citations_referenced": [
      "<author year title>"   // up to 10 most-prominent references
    ]
  }
}

topics[] type vocabulary: same as default.

Rules: only include what\'s in the document. Findings should be
specific (numbers, deltas, percentages where the paper provides
them). Drop boilerplate. Limit topics to {max_topics}. Return ONLY JSON.
"""


class LLMResearchPaperExtractor(LLMDefaultExtractor):
    name = "llm_research_paper"

    async def extract(
        self, text, *, title="", source_type="", max_topics=12, hint=None,
    ) -> ExtractedFields:
        status = {"strategy": self.name, "ok": False}
        if not text or not text.strip():
            status["error"] = "empty_input"
            return ExtractedFields(status=status)
        body = text.strip()
        truncated = False
        if len(body) > self.BODY_BUDGET_CHARS:
            body = body[: self.BODY_BUDGET_CHARS] + "\n\n[…truncated…]"
            truncated = True
        system = _RESEARCH_SYSTEM_PROMPT.replace("{max_topics}", str(max_topics))
        user_lines = [
            f"Document title: {title}" if title else "",
            f"Source type: {source_type}" if source_type else "",
            f"Hint: {hint}" if hint else "",
            "", body,
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
                return ExtractedFields(status=status)
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as je:
                m = re.search(r"\{[\s\S]*\}\s*$", content)
                parsed = None
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        parsed = None
                if parsed is None:
                    status["error"] = f"json_parse_failed: {je}"
                    status["raw_excerpt"] = content[:300]
                    return ExtractedFields(status=status)
        except Exception as ex:
            status["error"] = f"llm_call_failed: {type(ex).__name__}: {ex}"
            status["raw_excerpt"] = (raw_content or "")[:300]
            return ExtractedFields(status=status)

        topics = list(parsed.get("topics") or [])
        claims = list(parsed.get("claims") or [])
        research = parsed.get("research") or {}
        status.update({
            "ok": True,
            "topic_count": len(topics),
            "claim_count": len(claims),
            "findings_count": len(research.get("findings") or []),
        })
        if truncated:
            status["reason"] = "input_truncated_to_60k"
        return ExtractedFields(
            topics=topics, speakers=[], claims=claims,
            status=status,
            frontmatter_additions={"research": research} if research else {},
        )


# ── Built-in: llm_changelog ──────────────────────────────────────

_CHANGELOG_SYSTEM_PROMPT = """\
You are a structured-data extractor for CHANGELOG / RELEASE NOTES
content.

Output STRICT JSON:

{
  "topics": [...],
  "release": {
    "version": "<version string, e.g. v1.4.2 or 2026.05.03>",
    "released_at": "YYYY-MM-DD or null",
    "release_type": "major | minor | patch | preview | other",
    "breaking_changes": ["<short description per item>"],
    "features": ["<short description per item>"],
    "fixes": ["<short description per item>"],
    "deprecations": ["<short description per item>"],
    "security": ["<short description of security-related changes>"]
  }
}

topics[] type vocabulary: same as default.

Rules: be terse — changelog items are inherently short. Group like
items together. If the document has multiple releases, return the
most recent one only and note the others as topics. Limit topics to
{max_topics}. Return ONLY JSON.
"""


class LLMChangelogExtractor(LLMDefaultExtractor):
    name = "llm_changelog"

    async def extract(
        self, text, *, title="", source_type="", max_topics=8, hint=None,
    ) -> ExtractedFields:
        status = {"strategy": self.name, "ok": False}
        if not text or not text.strip():
            status["error"] = "empty_input"
            return ExtractedFields(status=status)
        body = text.strip()
        truncated = False
        if len(body) > self.BODY_BUDGET_CHARS:
            body = body[: self.BODY_BUDGET_CHARS] + "\n\n[…truncated…]"
            truncated = True
        system = _CHANGELOG_SYSTEM_PROMPT.replace("{max_topics}", str(max_topics))
        user_lines = [
            f"Document title: {title}" if title else "",
            f"Source type: {source_type}" if source_type else "",
            f"Hint: {hint}" if hint else "",
            "", body,
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
                return ExtractedFields(status=status)
            try:
                parsed = json.loads(content)
            except json.JSONDecodeError as je:
                m = re.search(r"\{[\s\S]*\}\s*$", content)
                parsed = None
                if m:
                    try:
                        parsed = json.loads(m.group(0))
                    except json.JSONDecodeError:
                        parsed = None
                if parsed is None:
                    status["error"] = f"json_parse_failed: {je}"
                    status["raw_excerpt"] = content[:300]
                    return ExtractedFields(status=status)
        except Exception as ex:
            status["error"] = f"llm_call_failed: {type(ex).__name__}: {ex}"
            status["raw_excerpt"] = (raw_content or "")[:300]
            return ExtractedFields(status=status)

        topics = list(parsed.get("topics") or [])
        release = parsed.get("release") or {}
        status.update({
            "ok": True,
            "topic_count": len(topics),
            "version": release.get("version", ""),
        })
        if truncated:
            status["reason"] = "input_truncated_to_60k"
        return ExtractedFields(
            topics=topics, speakers=[], claims=[],
            status=status,
            frontmatter_additions={"release": release} if release else {},
        )


register_extractor(LLMAnnouncementExtractor())
register_extractor(LLMStructuredSpecsExtractor())
register_extractor(LLMResearchPaperExtractor())
register_extractor(LLMChangelogExtractor())
