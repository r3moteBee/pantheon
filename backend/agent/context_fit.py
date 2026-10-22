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
(the turn's notes and artifacts, or calling the tool again). When stubs are not
enough - a background job of 80+ rounds overflowed 32K with 123 of 125 results
already stubbed, because the calls themselves add up - whole old rounds go: an
assistant tool-call message together with its tool results (never one without
the other - providers reject orphaned calls), replaced by one note that counts
what was removed. If a round still overflows, context_overflow() reads the
server's own token counts so the caller can re-fit with a calibrated estimate
and retry once.

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
KEEP_ROUNDS = 4               # newest tool rounds never dropped whole
DROPPED_MARK = "[Pantheon note] Earlier tool rounds were removed"

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


def _rounds(messages: list[dict[str, Any]]) -> list[tuple[int, int]]:
    """(start, end) spans of tool rounds: an assistant message with tool_calls plus the tool messages after it."""
    spans, i = [], 0
    while i < len(messages):
        m = messages[i]
        if m.get("role") == "assistant" and m.get("tool_calls"):
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                j += 1
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def _drop_round(messages: list[dict[str, Any]], counts: dict[str, int]) -> bool:
    """Remove the oldest droppable tool round, folding it into the single DROPPED_MARK note (created in its
    place the first time). False when only the newest KEEP_ROUNDS are left."""
    spans = _rounds(messages)
    if len(spans) <= KEEP_ROUNDS:
        return False
    a, b = spans[0]
    for tc in messages[a].get("tool_calls") or []:
        name = (tc.get("function") or {}).get("name") or "?"
        counts[name] = counts.get(name, 0) + 1
    note_at = next((i for i, m in enumerate(messages) if m.get("role") == "assistant"
                    and _text(m.get("content")).startswith(DROPPED_MARK)), None)
    del messages[a:b]
    summary = ", ".join(f"{n} x{c}" for n, c in sorted(counts.items(), key=lambda kv: -kv[1]))
    note = {"role": "assistant", "content": (
        f"{DROPPED_MARK} to fit the context window ({sum(counts.values())} calls: {summary}). What they "
        "found is in the notes and artifacts saved so far; check those instead of repeating the calls.")}
    if note_at is None:
        messages.insert(a, note)
    else:
        messages[note_at if note_at < a else note_at - (b - a)] = note
    return True


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
    def shrink(group, stages) -> bool:
        for limit in stages:
            for i in group:
                new = _shrink(messages[i], limit)
                if new is not messages[i]:
                    messages[i] = new
                    stats["trimmed"] += 1
                    stats["after"] = size()
                    if stats["after"] <= budget:
                        return True
        return False

    if shrink(older, (*EXCERPT_CHARS, 0)):
        return stats
    # Still over with every older result stubbed: drop whole old rounds before touching the newest results.
    counts: dict[str, int] = {}
    note = next((m for m in messages if m.get("role") == "assistant"
                 and _text(m.get("content")).startswith(DROPPED_MARK)), None)
    if note:   # rounds dropped by an earlier fit this turn: keep counting from there
        for part in re.findall(r"(\w+) x(\d+)", _text(note["content"])):
            counts[part[0]] = int(part[1])
    while stats["after"] > budget and _drop_round(messages, counts):
        stats["dropped"] = stats.get("dropped", 0) + 1
        stats["after"] = size()
    if stats["after"] <= budget:
        return stats
    tool_idx = [i for i, m in enumerate(messages) if m.get("role") == "tool"]
    shrink(tool_idx[-KEEP_RECENT:], (*EXCERPT_CHARS, RECENT_FLOOR_CHARS))
    return stats
