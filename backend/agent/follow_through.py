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

A promise that waits on something else ("Once the extraction is complete, I'll
edit it ...") counts: on 2026-10-07 the agent said that, ended the turn, and the
edit never happened although the extraction had already finished - that is the
case create_task exists for (or the thing is already done and the step can run
now). Such promises are often followed by a numbered plan, so the last
TAIL_CHARS of the reply are searched, not just its last sentence.
"""
from __future__ import annotations

import re

MAX_NUDGES = 2          # per turn: a model that keeps announcing without acting is not looped forever
TAIL_CHARS = 900        # promises sit near the end of a reply, often followed by a numbered plan

_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?:]?")
_PROMISE_RE = re.compile(
    # an optional lead-in clause that names what the step waits for ("Once the upload finishes, ...")
    r"^(?:(?:once|when|after|as soon as|after that|as soon as that)\b[^,]{0,80},\s*)?"
    r"(?:(?:ok(?:ay)?|great|alright|all right|good|perfect|sure|so|now|next|first|then|finally)[,:]?\s+)*"
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
    text = (text or "").strip()
    if text.rstrip().endswith("?"):
        return None                     # the reply hands the turn back with a question
    sentences = [s.strip() for s in _SENTENCE_RE.findall(text[-TAIL_CHARS:]) if s.strip()]
    for s in reversed(sentences):
        if s.endswith("?") or _CONDITIONAL_RE.search(s):
            continue
        m = _PROMISE_RE.match(s.lstrip("*_-> ").strip())
        if m and m.group("verb").lower() not in _NOT_ACTIONS:
            return s
    return None


def follow_through_nudge(sentence: str) -> str:
    return (f'You said "{sentence}" but ended your turn without doing it. Do it now, using your tools - '
            "if it was waiting for something (an extraction, a download), check: it is often finished already. "
            "If it really has to happen later or on a schedule, queue it with create_task, which is what tasks "
            "are for. If it is no longer needed, say so in one sentence. Do not repeat what you already wrote.")
