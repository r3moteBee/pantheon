"""Instruction-fidelity guards for long agent runs:

1. AgentCore periodically re-injects the original instructions
   (re-anchor) so they don't get buried under accumulated tool output.
2. A budget warning fires when 10 iterations remain.
3. The autonomous_task handler injects the task-ledger protocol and
   resumes from an existing ledger artifact.
"""
import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")

import re
from pathlib import Path

import pytest
from unittest.mock import AsyncMock, patch

from agent.core import AgentCore

_ROOT = Path(__file__).resolve().parents[2]


def _agent():
    provider = AsyncMock()
    # Model that always calls a tool — loop only ends at the cap
    provider.chat_complete = AsyncMock(return_value={
        "content": "", "tool_calls": [{"id": "1", "name": "recall", "args": {}}],
    })
    return AgentCore(provider=provider, project_id="t", session_id="s"), provider


async def _run(agent, **kwargs):
    async for _ in agent.chat("do the task", stream=False, **kwargs):
        pass


def _messages_of_call(provider, idx):
    return provider.chat_complete.await_args_list[idx].kwargs["messages"]


@pytest.mark.asyncio
@patch("agent.core.build_system_prompt", return_value="sys")
@patch("agent.core.get_all_tool_schemas", return_value=[])
@patch("agent.core.execute_tool", new_callable=AsyncMock, return_value="ok")
async def test_reanchor_message_injected(_e, _s, _p):
    agent, provider = _agent()
    await _run(agent, max_iterations=5, reanchor_text="THE PLAN: push every 5",
               reanchor_every=2)
    # Iteration 2 appends the anchor → call #3 (index 2) must contain it
    msgs = _messages_of_call(provider, 2)
    anchors = [m for m in msgs if m["role"] == "user"
               and "harness re-anchor" in str(m.get("content", ""))]
    assert anchors, "re-anchor message not injected"
    assert "THE PLAN: push every 5" in anchors[0]["content"]
    # And it repeats (iteration 4 → present twice in call #5)
    msgs5 = _messages_of_call(provider, 4)
    assert sum("harness re-anchor" in str(m.get("content", ""))
               for m in msgs5 if m["role"] == "user") == 2


@pytest.mark.asyncio
@patch("agent.core.build_system_prompt", return_value="sys")
@patch("agent.core.get_all_tool_schemas", return_value=[])
@patch("agent.core.execute_tool", new_callable=AsyncMock, return_value="ok")
async def test_no_reanchor_without_text(_e, _s, _p):
    agent, provider = _agent()
    await _run(agent, max_iterations=5)
    for i in range(5):
        msgs = _messages_of_call(provider, i)
        assert not any("harness re-anchor" in str(m.get("content", ""))
                       for m in msgs), "re-anchor injected without reanchor_text"


@pytest.mark.asyncio
@patch("agent.core.build_system_prompt", return_value="sys")
@patch("agent.core.get_all_tool_schemas", return_value=[])
@patch("agent.core.execute_tool", new_callable=AsyncMock, return_value="ok")
async def test_budget_warning_at_10_remaining(_e, _s, _p):
    agent, provider = _agent()
    await _run(agent, max_iterations=12)
    # Warning appended at iteration 2 (12-2 == 10) → visible from call #3 on
    msgs = _messages_of_call(provider, 2)
    assert any("Only 10 iterations remain" in str(m.get("content", ""))
               for m in msgs if m["role"] == "user")
    # Fires exactly once across the run
    final = _messages_of_call(provider, 11)
    assert sum("Only 10 iterations remain" in str(m.get("content", ""))
               for m in final if m["role"] == "user") == 1


# ── Structural: ledger protocol wiring in the autonomous handler ───────────

def _handler_src() -> str:
    return (_ROOT / "jobs/handlers/autonomous_task.py").read_text(encoding="utf-8")


def test_handler_injects_ledger_protocol():
    src = _handler_src()
    assert "TASK LEDGER PROTOCOL" in src
    assert "-ledger.md" in src
    # Resume branch keys off artifact existence
    assert "get_by_path" in src
    assert "RESUME" in src


def test_handler_passes_reanchor_to_agent():
    src = _handler_src()
    m = re.search(r"agent\.chat\(([^)]*)\)", src, re.DOTALL)
    assert m, "agent.chat call not found"
    assert "reanchor_text" in m.group(1)
    assert "max_iterations" in m.group(1)
