"""The agent loop must distinguish hitting the iteration cap mid-work
(truncated=True) from the model naturally finishing (truncated=False).

Regression for a real incident: a 100-iteration merge task reported
status=completed with a mid-sentence narration as its "summary".
"""
import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")

import pytest
from unittest.mock import AsyncMock, patch

from agent.core import AgentCore


def _agent():
    provider = AsyncMock()
    return AgentCore(provider=provider, project_id="t", session_id="s"), provider


async def _collect_done(agent, **kwargs):
    done = None
    async for ev in agent.chat("task", stream=False, **kwargs):
        if ev["type"] == "done":
            done = ev
    return done


@pytest.mark.asyncio
@patch("agent.core.build_system_prompt", return_value="sys")
@patch("agent.core.get_all_tool_schemas", return_value=[])
@patch("agent.core.execute_tool", new_callable=AsyncMock, return_value="ok")
async def test_truncated_when_cap_hit_mid_work(_mock_exec, _mock_schemas, _mock_prompt):
    agent, provider = _agent()
    # Model calls a tool every round — never finishes naturally
    provider.chat_complete = AsyncMock(return_value={
        "content": "", "tool_calls": [{"id": "1", "name": "recall", "args": {}}],
    })
    done = await _collect_done(agent, max_iterations=3)
    assert done is not None
    assert done["iterations"] == 3
    assert done["truncated"] is True


@pytest.mark.asyncio
@patch("agent.core.build_system_prompt", return_value="sys")
@patch("agent.core.get_all_tool_schemas", return_value=[])
@patch("agent.core.execute_tool", new_callable=AsyncMock, return_value="ok")
async def test_nonstreaming_yields_tool_call_events(_mock_exec, _mock_schemas, _mock_prompt):
    """Non-streaming mode must emit tool_call events like streaming does —
    job-handler metrics and plan-step matching depend on them. Regression:
    tool_calls_observed reported 1 on a 500-tool-call run."""
    agent, provider = _agent()
    provider.chat_complete = AsyncMock(return_value={
        "content": "", "tool_calls": [{"id": "1", "name": "recall", "args": {"q": "x"}}],
    })
    events = []
    async for ev in agent.chat("task", stream=False, max_iterations=3):
        events.append(ev)
    tool_calls = [e for e in events if e["type"] == "tool_call"]
    tool_results = [e for e in events if e["type"] == "tool_result"]
    assert len(tool_calls) == 3  # one per iteration
    assert tool_calls[0]["name"] == "recall"
    assert tool_calls[0]["args"] == {"q": "x"}
    assert len(tool_results) == 3  # pairing preserved


def test_handler_aborts_on_provider_error():
    """A run ending on a provider error (no done event) must FAIL the job
    with an abort notice, not sail to completed with an empty summary."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[2]
           / "jobs/handlers/autonomous_task.py").read_text(encoding="utf-8")
    assert "ended_on_error" in src
    assert "last_error and not got_done" in src
    assert "Task ABORTED" in src
    assert 'raise RuntimeError(f"LLM provider error ended the run' in src
    # The abort check must come BEFORE the completed-path episodic log
    assert src.index("last_error and not got_done") < src.index('event="completed"')


@pytest.mark.asyncio
@patch("agent.core.build_system_prompt", return_value="sys")
@patch("agent.core.get_all_tool_schemas", return_value=[])
async def test_not_truncated_on_natural_finish(_mock_schemas, _mock_prompt):
    agent, provider = _agent()
    provider.chat_complete = AsyncMock(return_value={
        "content": "All done.", "tool_calls": [],
    })
    done = await _collect_done(agent, max_iterations=3)
    assert done is not None
    assert done["iterations"] == 1
    assert done["truncated"] is False
    assert done["full_response"] == "All done."
