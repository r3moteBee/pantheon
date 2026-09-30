"""Conversation history budget for one chat turn.

A resumed session replays up to 200 messages into the prompt. With no token
budget, a long chat grows the prompt without bound: slower first calls when the
KV cache is cold, overflow on small-window models, and a small model's
attention spread over hours of stale turns.

budget_history() keeps the newest messages within a token budget and drops the
oldest in whole BLOCKS of BLOCK_MESSAGES, aligned to the message's absolute
position in the session. The cut therefore only moves when another block has
to go (every ~10 turns), so between drops the history is a byte-identical
prompt prefix and the model server's prefix cache keeps working (see
prompts.render_turn_context). Dropped turns are not lost: they are still in
episodic memory, and pre-recall can bring back the relevant ones — it only
skips messages that are actually in the prompt (AgentCore._in_context_texts).
"""
from __future__ import annotations

from typing import Any

BLOCK_MESSAGES = 20        # drop granularity (messages); ~10 user/assistant turns
KEEP_RECENT = 4            # never drop the last few messages, whatever their size
DEFAULT_BUDGET = 24_000    # tokens, when the model's context window is unknown
MAX_AUTO_BUDGET = 24_000   # a 262K window still gets 24K: attention, not capacity, is the limit
MIN_AUTO_BUDGET = 2_000
CHARS_PER_TOKEN = 4


def message_tokens(msg: dict[str, Any]) -> int:
    content = msg.get("content")
    if isinstance(content, list):
        chars = sum(len(p.get("text", "") or "") for p in content if isinstance(p, dict))
    else:
        chars = len(content or "")
    return chars // CHARS_PER_TOKEN + 4


def budget_history(history: list[dict[str, Any]], offset: int, budget: int,
                   block: int = BLOCK_MESSAGES, keep_recent: int = KEEP_RECENT) -> tuple[list[dict[str, Any]], int]:
    """(kept messages, number dropped). ``offset`` is the absolute position of
    history[0] in the session, so block boundaries don't shift as the loaded
    window slides. budget <= 0 keeps everything."""
    if budget <= 0 or not history:
        return history, 0
    sizes = [message_tokens(m) for m in history]
    if sum(sizes) <= budget:
        return history, 0
    last_cut = max(0, len(history) - keep_recent)
    suffix = sum(sizes)
    cut = last_cut
    for i in range(1, last_cut + 1):
        suffix -= sizes[i - 1]
        if (offset + i) % block == 0 and suffix <= budget:
            cut = i
            break
    # Start on a user turn: some chat templates reject a leading assistant message.
    while cut < len(history) - 1 and history[cut].get("role") != "user":
        cut += 1
    return history[cut:], cut


def resolve_budget(provider: Any, configured: int) -> int:
    """HISTORY_TOKEN_BUDGET: >0 fixed, <0 disabled, 0 auto = a quarter of the
    agent model's context window, clamped to [MIN_AUTO_BUDGET, MAX_AUTO_BUDGET]."""
    if configured > 0:
        return configured
    if configured < 0:
        return 0
    window = None
    try:
        cands = getattr(provider, "candidates", None)
        if cands:
            from llm_config.store import get_profile
            endpoint, model = cands[0]
            window = get_profile(endpoint, model).context_window
    except Exception:
        window = None
    if not window:
        return DEFAULT_BUDGET
    return max(MIN_AUTO_BUDGET, min(MAX_AUTO_BUDGET, window // 4))
