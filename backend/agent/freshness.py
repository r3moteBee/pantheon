"""Questions whose answer may have changed since the model was trained.

Measured on politics / current events (officeholders, latest race results,
rates): when the agent searched it was right 54/60 times; when it answered
from its own knowledge it was right 8/15 - "As of October 2026, the President
is Joe Biden", "the current Pope is Pope Francis". So for these questions the
agent's first round must call a tool (tool_choice "required"); the model still
picks which one, and every later round is unconstrained.
"""
from __future__ import annotations

import re

_FRESH_RE = re.compile(
    # explicit time / recency
    r"\b(latest|newest|current(ly)?|today|tonight|tomorrow|yesterday|right now|this (week|month|year|season)"
    r"|last (night|week|month|weekend)|recent(ly)?|most recent|news|breaking|so far|still|anymore|nowadays)\b"
    # values that move
    r"|\b(price|prices|rate|rates|exchange rate|stock|weather|forecast|score|standings|polls?|results?|won|winner)\b"
    # who holds an office or title now ("Who is the prime minister of Japan?")
    r"|\bwho(?:'s| is| are)\s+(?:the\s+)?(?:current\s+)?(president|prime minister|premier|chancellor|pope|king|queen"
    r"|monarch|ceo|chair(man|woman|person)?|leader|head of|governor|mayor|speaker|secretary[- ]general|minister"
    r"|champion|coach|manager|owner)\b",
    re.I,
)


def needs_fresh_facts(message: str) -> bool:
    return bool(_FRESH_RE.search(message or ""))
