"""Sources for answers built on web results.

Citation harness (versions / politics / product questions asked naturally): the
agent used web results in 89 of 90 answers but cited a source in 10; told to
cite, in 24 - and then often a plausible "official" page that doesn't state the
fact (Blender 5.2 release notes for "5.2.2", which came from the release
table). So the turn's tool results are kept as url -> text, and an answer that
used the web but cites nothing gets a Sources list of the URLs whose text
actually contains the answer's key facts (version numbers, bolded names/values).
"""
from __future__ import annotations

import re

_URL = re.compile(r"https?://[^\s<>\"'`)\]]+")
_RESULT_BLOCK = re.compile(r"^\s*\d+\.\s+.*?\n\s+(https?://\S+)\s*\n(.*?)(?=^\s*\d+\.\s|\Z)", re.M | re.S)
_RELEASE = re.compile(r"^Release data from (https?://\S+).*?(?=^Release data from |^Search results|\Z)", re.M | re.S)


def evidence_from(tool_name: str, tool_args: dict, result: str) -> dict[str, str]:
    """url -> the text that came with it in one tool result."""
    out: dict[str, str] = {}
    text = str(result or "")
    if tool_name == "web_fetch" and (tool_args or {}).get("url"):
        out[tool_args["url"]] = text[:60000]
    elif tool_name == "web_search":
        for m in _RELEASE.finditer(text):
            out[m.group(1)] = m.group(0)
        for m in _RESULT_BLOCK.finditer(text):
            out.setdefault(m.group(1), m.group(0))
    return out


def key_facts(answer: str) -> list[str]:
    """What an answer asserts that a source must contain: versions/numbers with dots, and short bolded spans."""
    facts = re.findall(r"(?<![\w.])v?(\d+(?:\.\d+){1,3})(?![\d])", answer)
    for b in re.findall(r"\*\*([^*\n]{2,60})\*\*", answer):
        b = b.strip(" .:")
        if not re.fullmatch(r"(sources?|note|short answer|answer|summary|yes|no)", b, re.I):
            facts.append(b)
    seen, out = set(), []
    for f in facts:
        if f.lower() not in seen:
            seen.add(f.lower()); out.append(f)
    return out[:12]


def pick_sources(answer: str, evidence: dict[str, str], limit: int = 2) -> list[str]:
    """URLs whose text contains the answer's HEADLINE fact (its first key fact), ranked by how many of the
    other key facts they also contain (release-data links win ties). Without the headline rule a page about
    a predecessor the answer merely mentions ("Morawiecki" in a "Tusk" answer) or about the release line
    ("Blender 5.2 LTS" for "5.2.2") was picked."""
    facts = key_facts(answer)
    if not facts or not evidence:
        return []
    head = facts[0].lower()
    scored = []
    for url, text in evidence.items():
        low = text.lower()
        if head not in low:
            continue
        n = sum(1 for f in facts if f.lower() in low)
        scored.append((n, "endoflife.date" in url or "/releases" in url, url))
    scored.sort(key=lambda t: (-t[0], not t[1]))
    return [u for _, _, u in scored[:limit]]


def has_url(text: str) -> bool:
    return bool(_URL.search(text or ""))
