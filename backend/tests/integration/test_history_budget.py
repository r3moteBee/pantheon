"""Long conversations: history is kept within a token budget, dropped in
cache-stable blocks, and dropped turns stay reachable through recall."""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from agent.history import budget_history, message_tokens, resolve_budget  # noqa: E402


def _conv(n, chars=400):
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i} " + "x" * chars} for i in range(n)]


def test_under_budget_keeps_everything():
    h = _conv(10)
    assert budget_history(h, 0, 10_000) == (h, 0)
    assert budget_history(h, 0, 0) == (h, 0)             # disabled


def test_drops_whole_blocks_from_the_start_and_starts_on_a_user_turn():
    h = _conv(60)                                         # ~104 tokens each
    kept, dropped = budget_history(h, 0, 3_000, block=20)
    assert dropped % 20 == 0 and dropped > 0
    assert sum(message_tokens(m) for m in kept) <= 3_000
    assert kept[0]["role"] == "user" and kept[-1] is h[-1]


def test_cut_is_stable_between_block_drops():
    """Growing the conversation by a turn does not move the cut until another
    block must go: the kept history is a stable prefix for the KV cache."""
    h = _conv(60)
    k1, d1 = budget_history(h, 0, 4_000, block=20)
    k2, d2 = budget_history(h + _conv(2), 0, 4_000, block=20)
    assert d1 == d2 and k2[:len(k1)] == k1


def test_blocks_align_to_absolute_positions():
    """from_session loads only the newest N; the offset keeps boundaries fixed."""
    h = _conv(60)
    kept_full, dropped_full = budget_history(h, 0, 3_000, block=20)
    kept_tail, dropped_tail = budget_history(h[10:], 10, 3_000, block=20)
    assert kept_tail == kept_full and dropped_tail == dropped_full - 10


def test_huge_recent_message_still_keeps_the_last_few():
    h = _conv(10) + [{"role": "user", "content": "y" * 200_000}, {"role": "assistant", "content": "ok"}]
    kept, _ = budget_history(h, 0, 1_000, keep_recent=4)
    assert kept[-1]["content"] == "ok" and len(kept) >= 2


def test_auto_budget_follows_the_model_window():
    prov = SimpleNamespace(candidates=[("homely", "agent")])
    with patch("llm_config.store.get_profile", return_value=SimpleNamespace(context_window=262_144)):
        assert resolve_budget(prov, 0) == 24_000
    with patch("llm_config.store.get_profile", return_value=SimpleNamespace(context_window=8_192)):
        assert resolve_budget(prov, 0) == 2_048
    assert resolve_budget(prov, 5_000) == 5_000 and resolve_budget(prov, -1) == 0


class _Prov:
    model, task_class = "m", "agent"
    candidates = [("homely", "agent")]

    def __init__(self):
        self.seen = []

    async def chat(self, messages, tools=None, stream=True, **kw):
        self.seen.append(messages)
        yield {"type": "text_delta", "content": "ok"}
        yield {"type": "done"}


@pytest.mark.asyncio
async def test_agent_sends_the_budgeted_history_and_lets_recall_reach_the_rest():
    from agent.core import AgentCore
    from config import get_settings
    recall_kw = {}

    async def recall(**kw):
        recall_kw.update(kw)
        return []
    prov = _Prov()
    agent = AgentCore(provider=prov, memory_manager=SimpleNamespace(recall=recall), project_id="p", session_id="s")
    agent.working_memory = _conv(60)
    with patch.object(get_settings(), "history_token_budget", 3_000), \
         patch("agent.core.get_all_tool_schemas", return_value=[]), \
         patch("agent.core.build_system_prompt", return_value="sys"):
        [e async for e in agent.chat("what did I say first?")]
    sent = prov.seen[0]
    history = sent[1:-1]
    assert len(history) < 60 and history[-1] == agent.working_memory[59]
    assert "m0 " not in str(history)
    assert (agent.working_memory[0]["content"].strip() not in recall_kw["in_context"])   # dropped -> recallable
    assert agent.working_memory[59]["content"].strip() in recall_kw["in_context"]        # sent -> not repeated
    assert "older messages are not shown" in sent[-1]["content"]
