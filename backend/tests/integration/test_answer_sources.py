"""Answers built on web results end with sources that actually contain their facts."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from agent.sources import evidence_from, key_facts, pick_sources  # noqa: E402

SEARCH = """Release data from https://endoflife.date/blender (fetched 2026-10-01; released versions only, newest first):
  newest release: 5.2.2 (cycle 5.2, released 2026-09-20)
  cycle 5.1: latest 5.1.4 (supported)

Search results (snippets may be outdated):
1. Blender 5.2 LTS release notes
   https://developer.blender.org/docs/release_notes/5.2/
   Blender 5.2 LTS was released on July 14, 2026.

2. Download Blender
   https://www.blender.org/download/
   Blender 5.2.2 is the latest version."""


def test_evidence_maps_each_url_to_its_own_text():
    ev = evidence_from("web_search", {"query": "q"}, SEARCH)
    assert "5.2.2" in ev["https://endoflife.date/blender"]
    assert "5.2.2" not in ev["https://developer.blender.org/docs/release_notes/5.2/"]
    assert evidence_from("web_fetch", {"url": "https://x.test/p"}, "page text") == {"https://x.test/p": "page text"}


def test_sources_are_the_pages_that_state_the_facts():
    ev = evidence_from("web_search", {"query": "q"}, SEARCH)
    answer = "The latest stable version of Blender is **5.2.2**."
    assert key_facts(answer) == ["5.2.2"]
    assert pick_sources(answer, ev) == ["https://endoflife.date/blender", "https://www.blender.org/download/"]
    assert pick_sources("Blender is a 3D tool.", ev) == []          # nothing checkable -> nothing appended


class _Prov:
    model, task_class = "m", "agent"

    def __init__(self, final):
        self.final, self.n = final, 0

    async def chat(self, messages, tools=None, stream=True, **kw):
        self.n += 1
        if self.n == 1:
            yield {"type": "tool_call", "id": "s1", "name": "web_search", "args": {"query": "latest blender"}}
        else:
            yield {"type": "text_delta", "content": self.final}
        yield {"type": "done"}


async def _run(final, on=True):
    from agent.core import AgentCore
    from config import get_settings
    agent = AgentCore(provider=_Prov(final), memory_manager=None, project_id="p", session_id="s")
    tools = [{"type": "function", "function": {"name": "web_search", "parameters": {}}}]

    async def fake_exec(**kw):
        return SEARCH
    with patch.object(get_settings(), "answer_sources", on), patch.object(get_settings(), "agent_force_search", False), \
         patch("agent.core.get_all_tool_schemas", return_value=tools), patch("agent.core.build_system_prompt", return_value="sys"), \
         patch("agent.core.execute_tool", fake_exec):
        events = [e async for e in agent.chat("latest blender?")]
    return next(e for e in events if e["type"] == "done")["full_response"]


@pytest.mark.asyncio
async def test_uncited_web_answer_gets_sources_appended():
    out = await _run("The latest stable version of Blender is **5.2.2**.")
    assert out.endswith("Sources:\n- https://endoflife.date/blender\n- https://www.blender.org/download/")


@pytest.mark.asyncio
async def test_answers_that_cite_and_the_setting_are_left_alone():
    cited = "Blender **5.2.2** - https://www.blender.org/download/"
    assert await _run(cited) == cited
    assert "Sources:" not in await _run("The latest stable version of Blender is **5.2.2**.", on=False)
