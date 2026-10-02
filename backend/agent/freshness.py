"""Questions whose answer may have changed since the model was trained.

Measured on politics / current events (officeholders, latest race results,
rates): when the agent searched it was right 54/60 times; when it answered
from its own knowledge it was right 8/15 - "As of October 2026, the President
is Joe Biden", "the current Pope is Pope Francis". So for these questions the
agent's first round must call a tool (tool_choice "required"); the model still
picks which one, and every later round is unconstrained.
"""
from __future__ import annotations
# (also: unknown_entities - names the model may not know; see below)

import re

_FRESH_RE = re.compile(
    # explicit time / recency ("current" only next to something that changes in the world - see below)
    r"\b(latest|newest|today|tonight|tomorrow|yesterday|right now|this (week|month|year|season)"
    r"|last (night|week|month|weekend)|recent(ly)?|most recent|news|breaking|so far|still|anymore|nowadays)\b"
    r"|\bcurrent(ly)?\s+(?:\w+\s+){0,2}?(price|prices|rate|rates|version|release|status|weather|forecast|score|standings"
    r"|leader|president|prime minister|pm|chancellor|pope|ceo|cfo|cto|mp|mayor|governor|champion|record|population|events?"
    r"|news|government|holder)\b"
    # values that move
    r"|\b(price|prices|stock price|exchange rate|weather|forecast|score|standings|polls?|election|won|winner)\b"
    r"|\b(interest|exchange|deposit|mortgage|inflation|unemployment|tax|policy|refinancing|facility|base)\s+rates?\b"
    r"|\b(election|race|match|game|vote|poll)\s+results?\b"
    # who holds an office or title now ("Who is the prime minister of Japan?")
    r"|\bwho(?:'s| is| are)\s+(?:the\s+)?(?:current\s+)?(president|prime minister|premier|chancellor|pope|king|queen"
    r"|monarch|ceo|chair(man|woman|person)?|leader|head of|governor|mayor|speaker|secretary[- ]general|minister"
    r"|champion|coach|manager|owner)\b",
    re.I,
)

# Questions about Pantheon itself or the user's own things are never "look it up on
# the web": "describe the current configuration (of this agent harness)" was answered
# from Windows and Microsoft Agent Framework pages because "current" forced a search
# (2026-10-02). The agent has its own tools for these (get_self_documentation,
# recall, list_artifacts, ...).
_SELF_RE = re.compile(
    r"\b(?:your|yourself|yourselves)\s+(?:own\s+)?(?:config(?:uration)?|settings?|setup|set-up|tools?|capabilit(?:y|ies)|memor(?:y|ies)"
    r"|instructions?|system prompt|prompt|model|models|version|skills?|name|personality|persona|limits?|features?|architecture)\b"
    r"|\babout (?:you|yourself)\b"
    r"|\b(?:this|the) (?:agent|assistant|harness|agent harness|system|bot|chat|conversation|session|project|workspace|server"
    r"|setup|deployment|instance|app|tool)\b"
    r"|\bpantheon\b"
    r"|\bmy (?:own )?(?:projects?|files?|notes?|memor(?:y|ies)|config(?:uration)?|settings?|setup|tasks?|jobs?|artifacts?"
    r"|workspace|account|data|conversations?|chats?|documents?|uploads?)\b",
    re.I,
)


def about_self(message: str) -> bool:
    return bool(_SELF_RE.search(message or ""))


def needs_fresh_facts(message: str) -> bool:
    return bool(_FRESH_RE.search(message or "")) and not about_self(message)


# ── Names the model may not know ─────────────────────────────────────────────
#
# Products, models and companies announced after training. Measured: asked
# "What is Jev?" (TypeSafe AI's decision model, Sept 2026) the agent searched
# 1 time in 5 and otherwise made something up from whatever was nearby -
# "Jevons paradox", "the user's Proton Mail project", and for "use Jev to
# summarize my meeting notes", "an artifact-based summarization skill". Jev
# can't generate text at all. So a capitalised name the agent is asked about,
# or asked to use, counts as time-sensitive unless it is one of Pantheon's own
# tools/skills.

# Capitalised ("Jev", "Steam Frame") or camel-case ("iPhone Duo", "macOS"), up to 4 words incl. versions ("v2.0")
_NAME = r"(?P<name>(?:[A-Z]|[a-z]+[A-Z])[\w.+\-]*(?:\s+(?:[A-Z0-9][\w.+\-]*|v?\d[\w.]*)){0,3})"
_ENTITY_RES = [
    re.compile(r"\b(?i:what|who)(?:'s|\s+(?i:is|are|was|were))\s+(?:(?i:the|a|an)\s+)?" + _NAME),
    re.compile(r"\b(?i:tell me about|explain|describe|have you heard (?:of|about)|do you know(?: about)?|how does|"
               r"how do i use|what can|capabilities of|features of|pricing (?:of|for)|reviews? of|specs? (?:of|for))\s+"
               r"(?:(?i:the|a|an)\s+)?" + _NAME),
    re.compile(r"\b(?i:use|using|try|trying|set up|setting up|integrate|integrating|install|installing|switch(?:ing)? to|"
               r"buy|buying|adopt|migrate to|upgrade to|deploy|deploying)\s+(?:(?i:the|a|an)\s+)?" + _NAME),
]
# capitalised words that aren't names of things to look up
_COMMON = set("""I I'm I've Me My We Our You Your It Its This That These Those The A An Here There Hi Hello Thanks
    Please OK Okay Yes No Monday Tuesday Wednesday Thursday Friday Saturday Sunday January February March April May
    June July August September October November December""".split())


def unknown_entities(message: str, known: set[str] | None = None) -> list[str]:
    """Names in an entity question / "use X" request, minus Pantheon's own tools/skills (``known``, lower-case)."""
    if about_self(message):
        return []
    known = {k.lower() for k in (known or set())}
    out = []
    for rx in _ENTITY_RES:
        for m in rx.finditer(message or ""):
            name = m.group("name").strip(" .?!,")
            first = name.split()[0]
            if first in _COMMON or name.lower() in known or name.lower().replace(" ", "_") in known:
                continue
            if name not in out:
                out.append(name)
    return out
