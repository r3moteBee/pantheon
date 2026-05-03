"""Active memory extraction — LLM-powered post-conversation analysis.

Runs after conversations (or on a timer) to extract entities, facts,
relationships, and user preferences from chat messages, then routes
them to semantic and graph memory tiers automatically.

Uses the prefill/curation model (Nemotron Nano or similar) to keep
inference costs near zero while running on local hardware.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


# ── Extraction prompt templates ──────────────────────────────────────────────

EXTRACTION_SYSTEM_PROMPT = """\
You are a memory extraction engine. Analyze the conversation and extract
structured knowledge that should be remembered for future interactions.

Output valid JSON with these keys:

{
  "entities": [
    {"label": "...", "type": "person|concept|project|event|fact|organization|technology|product", "description": "one-line summary"}
  ],
  "relationships": [
    {"source": "entity_label", "target": "entity_label", "relationship": "verb phrase", "detail": "optional context"}
  ],
  "facts": [
    {"content": "declarative fact worth remembering", "confidence": 0.0-1.0, "tags": ["topic1"]}
  ],
  "user_preferences": [
    {"content": "preference or correction the user stated", "tags": ["preference"]}
  ]
}

Rules:
- Only extract information from the USER's messages and factual content discussed.
- DO NOT extract the AI assistant's persona, identity, personality, or
  role-play character as entities. The assistant may adopt a character
  or persona (Zeus, Athena, etc.) — ignore it entirely.
- DO NOT create entities for "Assistant", "AI", the persona name, or any
  character the assistant is playing.
- DO NOT include facts about how the assistant responded or its
  personality traits.
- Focus on real-world knowledge, user statements, and substantive topics.
- Do NOT extract pleasantries, filler, or meta-conversation about the chat itself.
- Entity labels should be canonical (e.g., "Anthropic" not "the Anthropic company"; "PostgreSQL" not "the database we use").
- Confidence: 1.0 = user explicitly stated it, 0.7 = strongly implied, 0.4 = inferred.
- Keep descriptions and facts concise — one sentence each.
- If there is nothing worth extracting, return empty arrays for all keys.
- Return ONLY the JSON object, no markdown fences or explanation."""

EXTRACTION_USER_TEMPLATE = """\
Extract structured knowledge from this conversation:

{transcript}"""


# ── Node type mapping ────────────────────────────────────────────────────────

# Map extraction entity types to graph node types.
# Graph memory currently supports: concept, person, project, event, fact
ENTITY_TYPE_MAP = {
    "person": "person",
    "organization": "concept",
    "technology": "concept",
    "product": "concept",
    "concept": "concept",
    "project": "project",
    "event": "event",
    "fact": "fact",
}


class MemoryExtractor:
    """Extracts structured knowledge from conversations using a curation LLM.

    Designed to run asynchronously after a conversation ends or after
    a configurable number of messages.

    Usage:
        extractor = MemoryExtractor(memory_manager)
        stats = await extractor.extract_from_messages(messages, project_id)
    """

    def __init__(
        self,
        memory_manager: Any,
        provider: Any | None = None,
        min_messages: int = 4,
        max_transcript_chars: int = 12000,
    ):
        self.memory_manager = memory_manager
        self._provider = provider
        self.min_messages = min_messages
        self.max_transcript_chars = max_transcript_chars

    def _get_provider(self):
        """Get the curation model provider (prefill model preferred)."""
        if self._provider:
            return self._provider
        try:
            from models.provider import get_prefill_provider
            return get_prefill_provider()
        except Exception:
            from models.provider import get_provider
            return get_provider()

    def _build_transcript(self, messages: list[dict[str, Any]]) -> str:
        """Build a transcript string from message dicts, respecting char limit."""
        lines = []
        total = 0
        # Process most recent messages first to prioritize recent context
        for msg in reversed(messages):
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            if role not in ("user", "assistant"):
                continue
            line = f"{role}: {content}"
            if total + len(line) > self.max_transcript_chars:
                break
            lines.append(line)
            total += len(line)
        lines.reverse()
        return "\n\n".join(lines)

    async def extract_from_messages(
        self,
        messages: list[dict[str, Any]],
        project_id: str = "default",
        session_id: str | None = None,
    ) -> dict[str, int]:
        """Run extraction on a list of messages and store results.

        Returns a stats dict: {entities, relationships, facts, user_preferences}
        """
        stats = {"entities": 0, "relationships": 0, "facts": 0, "user_preferences": 0}

        # Skip if too few messages
        user_assistant_msgs = [
            m for m in messages if m.get("role") in ("user", "assistant")
        ]
        if len(user_assistant_msgs) < self.min_messages:
            logger.debug(
                "Skipping extraction: only %d messages (min %d)",
                len(user_assistant_msgs),
                self.min_messages,
            )
            return stats

        transcript = self._build_transcript(messages)
        if not transcript.strip():
            return stats

        # Call the curation model
        provider = self._get_provider()
        try:
            result = await provider.chat_complete([
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": EXTRACTION_USER_TEMPLATE.format(transcript=transcript)},
            ])
            raw = (result.get("content") or "").strip()
            if not raw:
                logger.warning("Extraction returned empty response")
                return stats
            extracted = self._parse_extraction(raw)
        except Exception as e:
            logger.error("Extraction LLM call failed: %s", e)
            return stats

        # Route extracted data to memory tiers
        now_iso = datetime.now(timezone.utc).isoformat()

        # 1. Entities → graph nodes
        for entity in extracted.get("entities", []):
            try:
                label = entity.get("label", "").strip()
                if not label:
                    continue
                etype = entity.get("type", "concept")
                node_type = ENTITY_TYPE_MAP.get(etype, "concept")
                metadata = {
                    "description": entity.get("description", ""),
                    "extracted_at": now_iso,
                    "source": "auto_extraction",
                    "original_type": etype,
                }
                await self.memory_manager.graph.add_node(
                    node_type=node_type,
                    label=label,
                    metadata=metadata,
                )
                stats["entities"] += 1
            except Exception as e:
                logger.warning("Failed to store entity '%s': %s", entity.get("label"), e)

        # 2. Relationships → graph edges
        for rel in extracted.get("relationships", []):
            try:
                source = rel.get("source", "").strip()
                target = rel.get("target", "").strip()
                relationship = rel.get("relationship", "").strip()
                if not (source and target and relationship):
                    continue
                await self.memory_manager.graph.add_edge_by_label(
                    label_a=source,
                    label_b=target,
                    relationship=relationship,
                )
                stats["relationships"] += 1
            except Exception as e:
                logger.warning("Failed to store relationship '%s'->'%s': %s", rel.get("source"), rel.get("target"), e)

        # 3. Facts → semantic memory
        for fact in extracted.get("facts", []):
            try:
                content = fact.get("content", "").strip()
                if not content:
                    continue
                confidence = float(fact.get("confidence", 0.7))
                tags = fact.get("tags", [])
                await self.memory_manager.semantic.store(
                    content=content,
                    metadata={
                        "type": "extracted_fact",
                        "confidence": str(confidence),
                        "tags": ",".join(tags) if tags else "",
                        "source": "auto_extraction",
                        "session_id": session_id or "",
                        "extracted_at": now_iso,
                    },
                )
                stats["facts"] += 1
            except Exception as e:
                logger.warning("Failed to store fact: %s", e)

        # 4. User preferences → semantic memory (high priority)
        for pref in extracted.get("user_preferences", []):
            try:
                content = pref.get("content", "").strip()
                if not content:
                    continue
                await self.memory_manager.semantic.store(
                    content=content,
                    metadata={
                        "type": "user_preference",
                        "confidence": "1.0",
                        "tags": ",".join(pref.get("tags", ["preference"])),
                        "source": "auto_extraction",
                        "session_id": session_id or "",
                        "extracted_at": now_iso,
                    },
                )
                stats["user_preferences"] += 1
            except Exception as e:
                logger.warning("Failed to store user preference: %s", e)

        logger.info(
            "Extraction complete — entities=%d, relationships=%d, facts=%d, preferences=%d",
            stats["entities"],
            stats["relationships"],
            stats["facts"],
            stats["user_preferences"],
        )
        return stats

    def _parse_extraction(self, raw: str) -> dict[str, list]:
        """Parse the LLM's JSON output, handling common formatting issues."""
        # Strip markdown fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            # Remove ```json ... ``` wrapper
            lines = cleaned.split("\n")
            start = 1 if lines[0].startswith("```") else 0
            end = -1 if lines[-1].strip() == "```" else len(lines)
            cleaned = "\n".join(lines[start:end])

        try:
            parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError as e:
            logger.warning("Extraction JSON parse failed: %s — raw: %s", e, cleaned[:200])

        # Return empty structure on parse failure
        return {"entities": [], "relationships": [], "facts": [], "user_preferences": []}


async def run_extraction(
    messages: list[dict[str, Any]],
    memory_manager: Any,
    project_id: str = "default",
    session_id: str | None = None,
    provider: Any | None = None,
    min_messages: int = 4,
) -> dict[str, int]:
    """Convenience function for one-shot extraction.

    Can be called from chat API, session consolidation, or background tasks.
    """
    extractor = MemoryExtractor(
        memory_manager=memory_manager,
        provider=provider,
        min_messages=min_messages,
    )
    return await extractor.extract_from_messages(
        messages=messages,
        project_id=project_id,
        session_id=session_id,
    )
