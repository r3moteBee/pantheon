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
    r"|news|government|holder|senator|representative|congress(?:wo)?m[ae]n|justice|cabinet|minister)s?\b"
    # values that move
    r"|\b(price|prices|stock price|exchange rate|weather|forecast|score|standings|polls?|polling|election|elections|midterms?"
    r"|primaries|primary (?:election|race|results?)|won|winner)\b"
    r"|\bupcoming (?:\w+\s+){0,2}?(?:elections?|races?|votes?|launch(?:es)?|releases?|match(?:es)?|games?|fights?|events?)\b"
    r"|\b(interest|exchange|deposit|mortgage|inflation|unemployment|tax|policy|refinancing|facility|base)\s+rates?\b"
    r"|\b(election|race|match|game|vote|poll)\s+results?\b"
    # who holds an office or title now ("Who is the prime minister of Japan?")
    r"|\bwho(?:'s| is| are)\s+(?:the\s+)?(?:current\s+)?(president|prime minister|premier|chancellor|pope|king|queen"
    r"|monarch|ceo|chair(man|woman|person)?|leader|head of|governor|mayor|speaker|secretary[- ]general|minister"
    r"|champion|coach|manager|owner)s?\b",
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
    r"|\bthe current (?:config(?:uration)?|setup|set-up|settings)\b(?!\s+(?:of|for|in|on)\b)"
    r"|\b(?:what|which) (?:tools?|models?|llms?|skills?|capabilit(?:y|ies)|version|memory|settings?) (?:do|are|can|did|have) you\b"
    r"|\b(?:are|is) you running\b|\byou(?:'re| are)? running on\b|\bwhat are you running\b"
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


# Self-description questions get Pantheon's real state first: with only the web
# search suppressed, "describe the current configuration of this agent harness"
# was still answered from a recalled earlier (wrong) reply, without any tool.
_SELF_DESCRIBE_RE = re.compile(
    r"\b(config(?:uration|ured)?|settings?|set ?up|architecture|tools?|capabilit(?:y|ies)|version|models?|"
    r"memory (?:system|tiers?|setup)|how (?:are|is) (?:you|it|this|pantheon) (?:set up|configured|built|running))\b",
    re.I,
)


def wants_self_description(message: str) -> bool:
    return about_self(message) and bool(_SELF_DESCRIBE_RE.search(message or ""))


# ── Follow-ups inherit the lookup ────────────────────────────────────────────
#
# "can you do a similar breakdown for the house?" right after two searched
# questions about the Senate races has no time words of its own, so it went to
# the quick route unsearched, filled a 435-seat table from memory until
# max_tokens and repeated "Greg Landsman" across Ohio districts (2026-10-02).
# "and Docker Engine?" after "latest Kubernetes version" answered v27.1.1
# (2024). A short follow-up in a conversation about current facts is itself
# about current facts - unless it's an acknowledgement or asks to reshape the
# answer already given.
_ACK_RE = re.compile(
    r"^\W*(?:(?:ok(?:ay)?|k|thanks?|thank you|thx|ty|cheers|cool|great|nice|perfect|got it|awesome|sounds good|"
    r"makes sense|good|wow|lol|haha|interesting|i see|understood|noted|sure|yes|yep|yeah|no|nope|bye|so|very|much"
    r"|that'?s|it|helpful|all|for now)\W*)+$",
    re.I,
)
# reshaping what was already said needs no new facts
_RESHAPE_RE = re.compile(
    r"\b(summari[sz]e|rewrite|rephrase|reword|translate|shorter|longer|shorten|simplif\w*|format|bullet|table of that"
    r"|tl;?dr|poem|joke|in (?:french|spanish|german|english)|explain (?:that|this|it)|eli5|why did you)\b",
    re.I,
)
FOLLOWUP_MAX_CHARS = 300
FOLLOWUP_LOOKBACK = 3   # user messages


def is_ack(message: str) -> bool:
    return bool(_ACK_RE.match((message or "").strip()))


# an answer that ended with a Sources list (ours - core.py - or the model's own) was looked up
_SOURCED_RE = re.compile(r"(?im)^\W*sources?\W*$[\s\S]*?https?://")


def looked_up(reply: str) -> bool:
    return bool(_SOURCED_RE.search(reply or ""))


def followup_needs_fresh(message: str, history: list[dict]) -> bool:
    """True when ``message`` continues a conversation about current facts and so
    needs a lookup too. ``history`` = this session's earlier messages
    ({"role", "content"}, oldest first). A user question that needed fresh facts,
    or an answer built on web sources, makes the conversation "current"; it stays
    so through short follow-ups and acknowledgements, and a long or self-directed
    message ends it."""
    m = (message or "").strip()
    if (not m or len(m) > FOLLOWUP_MAX_CHARS or is_ack(m) or about_self(m) or _RESHAPE_RE.search(m)
            or needs_fresh_facts(m)):
        return False
    users = [i for i, h in enumerate(history) if h.get("role") == "user"][-FOLLOWUP_LOOKBACK:]
    live = False
    for n, i in enumerate(users):
        p = str(history[i].get("content") or "")
        end = users[n + 1] if n + 1 < len(users) else len(history)
        replies = " ".join(str(h.get("content") or "") for h in history[i + 1:end] if h.get("role") == "assistant")
        if needs_fresh_facts(p) or looked_up(replies):
            live = True
        elif about_self(p) or len(p) > FOLLOWUP_MAX_CHARS:
            live = False
    return live
