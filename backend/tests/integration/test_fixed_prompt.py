"""The fixed part of every model call - tool schemas + system prompt - stays small, and says the same thing as
the task code (agent/prompts.py, agent/tools/*, data/personality/agent.md).

Every agent round sends this prefix again; the model server caches it, but each cache miss (new chat, eviction,
another user in between) recomputes all of it. Main 45bfa4f: ~16.3K est. tokens (chars/4) without MCP tools; ~10.6K after the trim (rebased #46)."""
from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import patch

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))


def test_tool_schemas_stay_compact():
    from agent.tools import TOOL_SCHEMAS
    assert len(json.dumps(TOOL_SCHEMAS)) < 30_000      # 42.5K chars before the trim, 27.7K after


def test_system_prompt_stays_compact():
    from agent.prompts import build_system_prompt
    assert len(build_system_prompt(project_id="default", project_name="Default", host_exec=False)) < 15_000


def test_prompt_keeps_the_single_task_approval_rule():
    """One approval rule (#57): requested schedules are shown as a plan and approved in chat; wording alone
    never approves. The trim must not lose it."""
    from agent.prompts import build_system_prompt
    text = build_system_prompt(project_id="default", project_name="Default")
    assert "## Scheduled task approval flow" in text and "skip_review: true" in text
    assert "imperative wording" in text and "Outside the web chat every task is a proposal" in text


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
