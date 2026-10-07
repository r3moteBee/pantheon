"""Replies that promise more work and then end the turn.

A small model often closes a round with "I'll now fetch the release notes." or
"Next, I'll save this as an artifact." and no tool call - the loop sees a round
without tool calls, treats the reply as the answer, and the promised step never
happens (homely, 9B distill, 2026-10). announced_action() spots such a promise
in the closing sentences of a reply; the loop then runs one more round with
follow_through_nudge(): do it now, queue it with create_task if it belongs later,
or say it is not needed. Offers ("Would you like me to ...?", "I can ... if you
want") and things that depend on the user ("once you send it, I'll ...") are
not promises and end the turn as before.
"""
from __future__ import annotations

import re

MAX_NUDGES = 2          # per turn: a model that keeps announcing without acting is not looped forever
TAIL_SENTENCES = 2      # promises sit at the end of a reply

_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]?")
_PROMISE_RE = re.compile(
    r"^(?:(?:ok(?:ay)?|great|alright|all right|good|perfect|sure|so|now|next|first|then|finally)[,:]?\s+)*"
    r"(?:i(?:'ll| will| am going to|'m going to| shall)|let me|let's)\s+"
    r"(?:(?:now|next|then|also|first|quickly|go ahead and|proceed to|start by|begin by)\s+)*"
    r"(?P<verb>[a-z][a-z-]*)",
    re.I,
)
# verbs that are not an action the agent owes ("let me know", "I'll be happy to", "I'll wait for your reply")
_NOT_ACTIONS = {"know", "be", "wait", "need", "keep", "stay", "leave", "stop", "assume", "note", "say", "admit"}
# the step depends on the user, or is offered rather than promised
_CONDITIONAL_RE = re.compile(r"\b(if you|once you|when you|after you|whenever you|if that|if needed|should you|"
                             r"if you'd like|if you want|unless you|your (?:approval|confirmation|go-ahead))\b", re.I)


def announced_action(text: str | None) -> str | None:
    """The sentence in which the reply promises a step it has not taken, or None."""
    sentences = [s.strip() for s in _SENTENCE_RE.findall((text or "").strip()) if s.strip()]
    for s in reversed(sentences[-TAIL_SENTENCES:]):
        if s.endswith("?") or _CONDITIONAL_RE.search(s):
            continue
        m = _PROMISE_RE.match(s.lstrip("*_-> ").strip())
        if m and m.group("verb").lower() not in _NOT_ACTIONS:
            return s
    return None


def follow_through_nudge(sentence: str) -> str:
    return (f'You ended your reply with "{sentence}" but did not do it. Do it now, using your tools. '
            "If it belongs later or on a schedule, queue it with create_task instead. If it is no longer "
            "needed, say so in one sentence. Do not repeat what you already wrote.")
