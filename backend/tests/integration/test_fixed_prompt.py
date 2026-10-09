"""The fixed part of every model call - tool schemas + system prompt - stays small, and says the same thing as
the task code (agent/prompts.py, agent/tools/*, data/personality/agent.md).

Every agent round sends this prefix again; the model server caches it, but each cache miss (new chat, eviction,
another user in between) recomputes all of it. On the 9B it was ~16K tokens before the 2026-10 trim, ~9.5K after."""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))


def test_tool_schemas_stay_compact():
    from agent.tools import TOOL_SCHEMAS
    assert len(json.dumps(TOOL_SCHEMAS)) < 27_000      # 40.5K before the trim, 24.8K after


def test_system_prompt_stays_compact():
    from agent.prompts import build_system_prompt
    assert len(build_system_prompt(project_id="default", project_name="Default", host_exec=False)) < 12_000


def test_prompt_matches_the_task_approval_rules():
    """Tasks the user asked for start without approval (tools/tasks.py); the prompt used to say every
    create_task needs approval first, so the model announced tasks instead of creating them."""
    from agent.prompts import build_system_prompt
    text = build_system_prompt(project_id="default", project_name="Default")
    assert "ABSOLUTE RULE" not in text and "DO NOT call `create_task` immediately" not in text
    assert "call create_task for the later part in the same\n  turn" in text


TOOLS = [{"type": "function", "function": {"name": n, "parameters": {}}}
         for n in ("web_search", "github", "git_status", "create_task")]


class _Prov:
    model, task_class = "m", "agent"

    def __init__(self):
        self.tools = None

    async def chat(self, messages, tools=None, stream=True, **kw):
        self.tools = [t["function"]["name"] for t in tools or []]
        yield {"type": "text_delta", "content": "ok"}
        yield {"type": "done"}


async def _offered(bound):
    from agent.core import AgentCore
    from config import get_settings
    prov = _Prov()
    agent = AgentCore(provider=prov, memory_manager=None, project_id="p", session_id="s", interactive=True,
                      host_exec=True)
    spec = {"owner": "o", "repo": "r", "default_branch": "main", "connection_id": "c"} if bound else None
    with patch.object(get_settings(), "agent_force_search", False), \
         patch.object(get_settings(), "agent_thinking", False), \
         patch("agent.core.get_all_tool_schemas", return_value=TOOLS), \
         patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("api.connections.get_project_repo_for_tools", return_value=spec), \
         patch("agent.context_fit.context_window", return_value=None):
        [e async for e in agent.chat("hello")]
    return prov.tools


import pytest  # noqa: E402


@pytest.mark.asyncio
async def test_repo_tools_only_where_a_repo_is_bound():
    assert await _offered(bound=False) == ["web_search", "create_task"]
    assert await _offered(bound=True) == ["web_search", "github", "git_status", "create_task"]


def test_failed_binding_lookup_keeps_the_repo_tools():
    from agent.tools import repo_bound
    with patch("api.connections.get_project_repo_for_tools", side_effect=RuntimeError("db locked")):
        assert repo_bound("p")
