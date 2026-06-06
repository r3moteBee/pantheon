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
