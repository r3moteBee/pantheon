"""research_batch: a list researched one item at a time, a sourced note per item, then a summary from the notes."""
from __future__ import annotations

import os
import tempfile
from unittest.mock import patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))


class _Ctx:
    def __init__(self, payload):
        self.job_id, self.project_id, self.title = "job-12345678", "p", "x"
        self.payload, self.progress, self.result = payload, [], {}
        self.cancel_after = None

    async def heartbeat(self, progress=None):
        self.progress.append(progress)

    def touch(self):
        pass

    def cancel_requested(self):
        return self.cancel_after is not None and len(self.progress) > self.cancel_after

    def update_result(self, d):
        self.result.update(d)


class _Store:
    def __init__(self):
        self.rows = {}

    def get_by_path(self, project_id, path):
        return self.rows.get(path)

    def create(self, **kw):
        self.rows[kw["path"]] = {"id": f"a{len(self.rows)}", "content": kw["content"], **kw}
        return self.rows[kw["path"]]


@pytest.mark.asyncio
async def test_each_item_gets_its_own_turn_note_and_the_summary_reads_only_notes():
    from jobs.handlers.research_batch import handle_research_batch
    store, seen = _Store(), []

    async def fake_run(self, prompt):
        seen.append((self.only_tools, self.web_budget, self.memory_manager, prompt))
        if self.only_tools == set():
            return "Summary: Ohio has a race; Utah not found."
        return "not found" if "Utah" in prompt else "- Candidates: A vs B (https://x.test/ohio)"
    ctx = _Ctx({"topic": "2026 Senate", "items": ["Ohio", "Utah"], "item_question": "Who runs in {item}?",
                "lookups_per_item": 4})
    with patch("artifacts.store.get_store", return_value=store), patch("artifacts.store.project_slug", return_value="proj"), \
         patch("models.provider.get_provider_for", return_value=object()), \
         patch("agent.core.AgentCore.run_autonomous", fake_run):
        res = await handle_research_batch(ctx)
    assert res["notes_written"] == 2 and res["nothing_found"] == 1 and res["summary_artifact_id"]
    assert set(store.rows) == {"proj/research/2026-senate/ohio.md", "proj/research/2026-senate/utah.md",
                               "proj/research/2026-senate/summary.md"}
    item_turns = [s for s in seen if s[0] != set()]
    assert all(s[0] == {"web_search", "web_fetch"} and s[1] == 4 and s[2] is None for s in item_turns)  # no memory
    assert "Who runs in Ohio?" in item_turns[0][3] and "Utah" not in item_turns[0][3]
    summary_prompt = seen[-1][3]
    assert "A vs B" in summary_prompt and "not found" in summary_prompt
    assert ctx.progress[0] == "Item 1/2: Ohio"


@pytest.mark.asyncio
async def test_rerun_reuses_existing_notes_and_cancel_stops_between_items():
    from jobs.handlers.research_batch import handle_research_batch
    store, runs = _Store(), []
    store.rows["proj/research/t/a.md"] = {"id": "old", "content": "# a\n- fact (https://x)"}

    async def fake_run(self, prompt):
        runs.append(prompt)
        return "- fact (https://y)"
    ctx = _Ctx({"topic": "t", "items": ["a", "b", "c"], "item_question": "{item}?"})
    ctx.cancel_after = 0     # cancel requested once item "b" has started (checked before "c")
    with patch("artifacts.store.get_store", return_value=store), patch("artifacts.store.project_slug", return_value="proj"), \
         patch("models.provider.get_provider_for", return_value=object()), \
         patch("agent.core.AgentCore.run_autonomous", fake_run):
        res = await handle_research_batch(ctx)
    assert res["notes_reused"] == 1 and res["notes_written"] == 1          # a reused, b written, c never started
    assert "proj/research/t/c.md" not in store.rows


@pytest.mark.asyncio
async def test_create_task_research_batch_payload():
    from agent import tools
    captured = {}

    async def fake_schedule(**kw):
        captured.update(kw); return "sched-9"
    with patch("tasks.scheduler.schedule_agent_task", fake_schedule):
        out = await tools.execute_tool("create_task", {
            "name": "2026 Senate", "description": "Senate survey", "plan": "1. each state 2. summary",
            "job_type": "research_batch", "items": "Ohio, Utah", "item_question": "Who runs in {item}?"}, None,
            interactive=True)
        bad = await tools.execute_tool("create_task", {"name": "x", "description": "x", "plan": "1.",
                                                        "job_type": "research_batch"}, None, interactive=True)
    assert "PROPOSED" in out and captured["job_type"] == "research_batch"
    assert captured["extras"] == {"topic": "2026 Senate", "items": ["Ohio", "Utah"],
                                  "item_question": "Who runs in {item}?", "lookups_per_item": 6}
    assert "needs items" in bad


def test_only_tools_restricts_what_the_agent_is_offered():
    from agent.core import AgentCore
    a = AgentCore(provider=object(), only_tools={"web_search"}, web_budget=3)
    assert a.only_tools == {"web_search"} and a.web_budget == 3
