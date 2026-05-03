"""Skill resolver — matches user messages to relevant skills.

Two resolution strategies:
  1. Explicit:  User prefixes message with `/skill-name`
  2. Auto:      Keyword + embedding matching against triggers and tags

Phase 1 uses keyword matching. Embedding-based matching will be added
in Phase 2 when the resolver gets access to the embedding provider.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from skills.models import LoadedSkill, SkillDiscoveryMode
from skills.registry import get_skill_registry

logger = logging.getLogger(__name__)

# Common English stopwords. Excluded from overlap scoring so a skill
# whose trigger contains 'what', 'the', or 'about' doesn't match
# every question. Keep this list conservative — only words that
# carry essentially zero topical signal.
_STOPWORDS = frozenset({
    "the", "and", "for", "are", "but", "not", "you", "all", "can",
    "had", "her", "was", "one", "our", "out", "day", "get", "has",
    "him", "his", "how", "man", "new", "now", "old", "see", "two",
    "who", "boy", "did", "its", "let", "put", "say", "she", "too",
    "use", "any", "what", "this", "with", "have", "from", "they",
    "would", "there", "their", "could", "should", "also", "into",
    "than", "then", "them", "these", "those", "your", "about",
    "tell", "ask", "want", "need", "like", "just", "such", "very",
    "more", "most", "some", "much", "many", "other", "where",
    "when", "which", "while", "been", "were", "will", "shall",
    "may", "might", "make", "take", "give", "find", "look",
    "going", "doing", "having", "being", "does", "yes",
})


def resolve_explicit(message: str) -> tuple[str | None, str]:
    """Check if the message starts with /skill-name and return (skill_name, rest_of_message).

    Returns (None, original_message) if no skill prefix is found.
    """
    stripped = message.strip()
    match = re.match(r"^/([a-zA-Z0-9_-]+)\s*(.*)", stripped, re.DOTALL)
    if not match:
        return None, message

    candidate = match.group(1).lower()
    rest = match.group(2).strip()

    registry = get_skill_registry()
    # Try the literal candidate, then underscore<->hyphen variants,
    # so /content_ingest_graph and /content-ingest-graph both match
    # content-ingest-graph.
    for variant in (candidate, candidate.replace("_", "-"), candidate.replace("-", "_")):
        skill = registry.get(variant)
        if skill:
            return skill.name, rest

    # Not a known skill — return original message unchanged
    return None, message


def resolve_auto(
    message: str,
    project_id: str = "default",
    mode: SkillDiscoveryMode = SkillDiscoveryMode.off,
    top_k: int = 3,
) -> list[dict[str, Any]]:
    """Find skills that match the user message via keyword scoring.

    Returns a ranked list of matches: [{"skill": LoadedSkill, "score": float, "reason": str}, ...]

    Only returns results when mode is 'suggest' or 'auto'.
    """
    if mode == SkillDiscoveryMode.off:
        return []

    registry = get_skill_registry()
    available = registry.list_for_project(project_id)

    if not available:
        return []

    # Normalise message for matching
    msg_lower = message.lower()
    msg_words_raw = set(re.findall(r"\b[a-z]{3,}\b", msg_lower))
    msg_words = msg_words_raw - _STOPWORDS

    scored: list[tuple[LoadedSkill, float, str]] = []

    for skill in available:
        score = 0.0
        reasons: list[str] = []

        # Check triggers (highest weight). Full-phrase containment is
        # the strongest signal — keep that exactly. Partial overlap
        # ignores stopwords so triggers like "what is the weather"
        # don't match every question via {what, the}.
        for trigger in skill.triggers:
            trigger_lower = trigger.lower()
            if trigger_lower in msg_lower:
                score += 3.0
                reasons.append(f"trigger match: \'{trigger}\'")
                break  # One trigger match is enough
            trigger_words_raw = set(re.findall(r"\b[a-z]{3,}\b", trigger_lower))
            trigger_words = trigger_words_raw - _STOPWORDS
            overlap = msg_words & trigger_words
            # Require at least 2 NON-STOPWORD overlap terms.
            if len(overlap) >= 2:
                score += 1.5
                reasons.append(f"partial trigger: {overlap}")

        # Check tags (medium weight). Tags are usually one or two
        # words, no need for stopword filtering — but ignore generic
        # one-letter or stopword tags if any author shipped them.
        for tag in skill.tags:
            tag_lower = tag.lower()
            if tag_lower in _STOPWORDS:
                continue
            if tag_lower in msg_lower:
                score += 1.0
                reasons.append(f"tag match: \'{tag}\'")

        # Check skill name (low weight, catches /skill-name references)
        if skill.name.replace("-", " ") in msg_lower or skill.name in msg_lower:
            score += 2.0
            reasons.append("name match")

        # Check description keywords (low weight, stopword-filtered).
        desc_words_raw = set(re.findall(r"\b[a-z]{3,}\b", skill.manifest.description.lower()))
        desc_words = desc_words_raw - _STOPWORDS
        desc_overlap = msg_words & desc_words
        if len(desc_overlap) >= 3:
            score += 0.5 * min(len(desc_overlap), 5)
            reasons.append(f"description overlap: {desc_overlap}")

        if score > 0:
            scored.append((skill, score, "; ".join(reasons)))

    # Sort by score descending, return top_k
    scored.sort(key=lambda x: x[1], reverse=True)
    results = []
    for skill, score, reason in scored[:top_k]:
        results.append({
            "skill": skill,
            "score": round(score, 2),
            "reason": reason,
        })

    return results


_MAX_CHAIN_DEPTH = 2
_MAX_CHAINED_SKILLS = 4


def build_skill_context(
    skill: LoadedSkill,
    project_id: str | None = None,
    *,
    _visited: set[str] | None = None,
    _depth: int = 0,
) -> str:
    """Build the skill instruction block to inject into the system prompt.

    If the skill declares `chains: [other-skill, ...]`, the chained skills'
    contexts are appended (1-hop by default, depth-capped at 2, deduped).
    """
    _visited = _visited if _visited is not None else set()
    if skill.manifest.name in _visited:
        return ""
    _visited.add(skill.manifest.name)

    header_prefix = "Active Skill" if _depth == 0 else f"Chained Skill (depth {_depth})"
    lines = [
        f"## {header_prefix}: {skill.manifest.name}",
        f"**Description:** {skill.manifest.description}",
        "",
    ]

    # Parameters
    if skill.manifest.parameters:
        lines.append("**Parameters:**")
        for p in skill.manifest.parameters:
            req = " (required)" if p.required else ""
            lines.append(f"  - `{p.name}` ({p.type}){req}: {p.description}")
        lines.append("")

    # Memory access
    mem = skill.manifest.pantheon.memory
    if mem.reads or mem.writes:
        access_parts = []
        if mem.reads:
            access_parts.append(f"reads: {', '.join(mem.reads)}")
        if mem.writes:
            access_parts.append(f"writes: {', '.join(mem.writes)}")
        lines.append(f"**Memory access:** {'; '.join(access_parts)}")
        lines.append("")

    # Project context
    if skill.manifest.pantheon.project_aware and project_id:
        lines.append(f"**Project context:** This skill is project-aware. Current project: {project_id}")
        lines.append("")

    # Instructions
    if skill.instructions:
        lines.append("---")
        lines.append("")
        lines.append(skill.instructions)

    # Chained skills — append their contexts (deduped, depth-capped)
    chains = getattr(skill.manifest, "chains", None) or []
    if chains and _depth < _MAX_CHAIN_DEPTH:
        registry = get_skill_registry()
        appended = 0
        for chained_name in chains:
            if appended >= _MAX_CHAINED_SKILLS:
                break
            chained = registry.get(chained_name)
            if not chained:
                logger.debug("Chained skill not found: %s", chained_name)
                continue
            sub = build_skill_context(
                chained, project_id,
                _visited=_visited, _depth=_depth + 1,
            )
            if sub:
                lines.append("")
                lines.append("")
                lines.append(sub)
                appended += 1

    return "\n".join(lines)
