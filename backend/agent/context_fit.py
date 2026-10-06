"""Keep one turn's tool loop inside the agent model's context window.

budget_history() (agent/history.py) trims the conversation history when a turn
starts, but inside a turn every tool result is appended to the message list and
nothing takes it out again. A research request that fetches a few pages grows
the prompt past the window: on homely (2026-10-06) research turns reached 105K
tokens against a 32K window, and llama.cpp rejects such a round outright -
"request (40012 tokens) exceeds the available context size (32768 tokens)" - so
the turn ends in an error after all the work.

fit_messages() runs before every model round. When the estimated prompt is over
budget it shrinks OLDER tool results first; the newest ones stay whole, because
they are what the model is working on. It shrinks in stages: a head + tail
excerpt, then a short stub that says what was dropped and how to get it back
(the turn's notes and artifacts, or calling the tool again). It never removes a
message, so every tool call keeps its tool result - providers reject orphaned
calls. If a round still overflows, context_overflow() reads the server's own
token counts so the caller can re-fit with a calibrated estimate and retry once.

Messages are replaced, never edited in place: the same dicts can be shared with
the UI events and the finalize round.
"""
from __future__ import annotations

import json
import re
from typing import Any

# Same ratio as history.py. A cautious 3 chars/token trimmed evidence the model still
# had room for (homely A/B 2026-10-06: answers 7/12 -> 3/12 with no overflows to prevent),
# so the estimate aims at reality and a server-reported overflow is the safety net:
# the round is re-fitted with the server's own token count and retried.
CHARS_PER_TOKEN = 4
MSG_OVERHEAD = 8              # role markers and template tokens per message
REPLY_RESERVE = 4096          # the provider's default max_tokens for a round
SAFETY_MARGIN = 1024          # estimate error and chat-template overhead
KEEP_RECENT = 2               # newest tool results left whole while older ones can shrink
EXCERPT_CHARS = (6000, 2000)  # the excerpt stages before a stub
RECENT_FLOOR_CHARS = 1000     # the newest results never shrink below this
STUB_MARK = "[earlier tool result"

_OVERFLOW_RE = re.compile(r"\((\d+) tokens\) exceeds the available context size \((\d+) tokens\)")


def _text(content: Any) -> str:
    if isinstance(content, list):
        return "".join(p.get("text", "") or "" for p in content if isinstance(p, dict))
    return content or ""


def message_tokens(msg: dict[str, Any]) -> int:
    chars = len(_text(msg.get("content")))
    for tc in msg.get("tool_calls") or []:
        chars += len(json.dumps(tc, default=str))
    return chars // CHARS_PER_TOKEN + MSG_OVERHEAD


def estimate(messages: list[dict[str, Any]], tools: list[dict] | None = None) -> int:
    """Prompt size in tokens, roughly: messages plus the tool schemas the template renders."""
    total = sum(message_tokens(m) for m in messages)
    if tools:
        total += len(json.dumps(tools, default=str)) // CHARS_PER_TOKEN
    return total


def context_window(provider: Any) -> int | None:
    """The agent model's context window from its profile (same lookup as history.resolve_budget)."""
    try:
        cands = getattr(provider, "candidates", None)
        if cands:
            from llm_config.store import get_profile
            endpoint, model = cands[0]
            return get_profile(endpoint, model).context_window or None
    except Exception:
        return None
    return None


def context_overflow(error_message: str | None) -> tuple[int, int] | None:
    """(prompt tokens, context size) when an error says the prompt didn't fit, else None."""
    m = _OVERFLOW_RE.search(error_message or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def _shrink(msg: dict[str, Any], limit: int) -> dict[str, Any]:
    text = _text(msg.get("content"))
    if limit <= 0:
        if text.startswith(STUB_MARK):
            return msg
        new = (f"{STUB_MARK} ({len(text):,} characters) was dropped to keep this turn inside the model's "
               "context window. Rely on what you noted from it (your notes and artifacts), or call the "
               "tool again if you need it.]")
    else:
        if len(text) <= limit or text.startswith(STUB_MARK):
            return msg
        head, tail = int(limit * 0.7), limit - int(limit * 0.7)
        new = (f"{text[:head]}\n\n[... {len(text) - head - tail:,} of {len(text):,} characters of this earlier "
               f"tool result were left out to fit the context window ...]\n\n{text[-tail:]}")
    return {**msg, "content": new}


def fit_messages(messages: list[dict[str, Any]], window: int | None, tools: list[dict] | None = None,
                 reserve: int = REPLY_RESERVE, calibration: float = 1.0) -> dict[str, int]:
    """Shrink tool results in `messages` (in place, by replacing entries) until the estimated
    prompt fits `window` minus `reserve`. calibration scales the estimate (real tokens per
    estimated token) after a server-reported overflow. Returns before/after estimates and how
    many results changed."""
    stats = {"before": 0, "after": 0, "trimmed": 0, "budget": 0}
    if not window:
        return stats
    budget = window - reserve - SAFETY_MARGIN
    stats["budget"] = budget

    def size() -> int:
        return int(estimate(messages, tools) * calibration)

    stats["before"] = stats["after"] = size()
    if stats["before"] <= budget:
        return stats
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    older, recent = tool_idx[:-KEEP_RECENT], tool_idx[-KEEP_RECENT:]
    # Older results go through every stage (down to a stub) before the newest ones are
    # touched; within a stage the oldest go first. The newest results are only ever
    # excerpted - stubbing them would drop exactly what the model just asked for.
    for group, stages in ((older, (*EXCERPT_CHARS, 0)), (recent, (*EXCERPT_CHARS, RECENT_FLOOR_CHARS))):
        for limit in stages:
            for i in group:
                new = _shrink(messages[i], limit)
                if new is not messages[i]:
                    messages[i] = new
                    stats["trimmed"] += 1
                    stats["after"] = size()
                    if stats["after"] <= budget:
                        return stats
    return stats
