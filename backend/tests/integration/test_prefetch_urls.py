"""A URL in the user's message is read before the first model round."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from agent.core import _user_urls  # noqa: E402


def test_urls_are_extracted_cleanly():
    assert _user_urls("https://notes.test/team/offsite-update - what's this about?") == ["https://notes.test/team/offsite-update"]
    assert _user_urls("see (https://en.wikipedia.org/wiki/Jev_(AI_model)), thanks.") == ["https://en.wikipedia.org/wiki/Jev_(AI_model)"]
    assert _user_urls("compare https://a.test/x and https://b.test/y, and https://a.test/x again") == ["https://a.test/x", "https://b.test/y"]
    assert _user_urls("```\ncurl https://api.test/v1\n```\nwhy does this fail?") == []
    assert _user_urls("no links here") == []


class _Prov:
    model, task_class = "m", "agent"

    def __init__(self):
        self.seen = []

    async def chat(self, messages, tools=None, stream=True, **kw):
        self.seen.append(messages)
        yield {"type": "text_delta", "content": "It's about the offsite moving to Porto."}
        yield {"type": "done"}


async def _run(msg, on=True):
    from agent.core import AgentCore
    from config import get_settings
    prov = _Prov(); calls = []
    agent = AgentCore(provider=prov, memory_manager=None, project_id="p", session_id="s")
    tools = [{"type": "function", "function": {"name": n, "parameters": {}}} for n in ("web_fetch", "web_search")]

    async def fake_exec(**kw):
        calls.append((kw["tool_name"], kw["tool_args"])); return "# Offsite update\nMoved to Porto."
    with patch.object(get_settings(), "agent_prefetch_urls", on), patch.object(get_settings(), "agent_thinking", False), \
         patch("agent.core.get_all_tool_schemas", return_value=tools), patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.execute_tool", fake_exec):
        [e async for e in agent.chat(msg)]
    return calls, prov


@pytest.mark.asyncio
async def test_url_is_fetched_before_round_one():
    calls, prov = await _run("https://notes.test/team/offsite-update - what's this about?")
    assert calls == [("web_fetch", {"url": "https://notes.test/team/offsite-update"})]
    assert prov.seen[0][-1]["role"] == "tool"


@pytest.mark.asyncio
async def test_a_url_question_skips_the_web_pre_search_and_the_setting_turns_it_off():
    calls, _ = await _run("What's the latest news at https://news.test/today?")
    assert [c[0] for c in calls] == ["web_fetch"]
    calls, _ = await _run("https://notes.test/x - what's this about?", on=False)
    assert calls == []
