"""The tool registry: every schema has a handler and vice versa, dispatch
goes through it, and the tool order the model sees is stable."""
from __future__ import annotations

import os
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from agent import tools  # noqa: E402
from agent.tools import registry  # noqa: E402


def test_every_schema_has_a_handler_and_every_handler_a_schema():
    names = [s["function"]["name"] for s in tools.TOOL_SCHEMAS]
    assert len(names) == len(set(names))
    assert [n for n in names if registry.resolve(n) is None] == []
    # Everything registered is either advertised or a hidden legacy alias.
    assert registry.registered_names() - set(names) <= tools.LEGACY_TOOLS
    assert not (set(names) & tools.LEGACY_TOOLS)
    assert all(registry.resolve(n) for n in tools.LEGACY_TOOLS)
    assert names == tools._ORDER


def test_prefix_handlers_and_unknown_names():
    assert registry.resolve("git_status").__module__ == "agent.tools.git"
    assert registry.resolve("github_list_pulls").__module__ == "agent.tools.github"
    assert registry.resolve("github").__module__ == "agent.tools.github"
    assert registry.resolve("browser_navigate").__module__ == "agent.tools.web"
    assert registry.resolve("no_such_tool") is None


def test_duplicate_registration_is_an_error():
    with pytest.raises(ValueError):
        registry.tool("recall")(lambda *a: None)


def test_context_effective_project():
    assert registry.ToolContext().effective_project == "default"
    assert registry.ToolContext(project_id="p").effective_project == "p"


@pytest.mark.asyncio
async def test_execute_tool_dispatches_and_contains_errors(monkeypatch):
    seen = {}

    async def fake(ctx, tool_name, tool_args):
        seen.update(ctx=ctx, name=tool_name, args=tool_args)
        if tool_args.get("boom"):
            raise RuntimeError("kaboom")
        return "ok"

    monkeypatch.setitem(registry._EXACT, "t_probe", fake)
    assert await tools.execute_tool("t_probe", {"a": 1}, None, project_id="p", session_id="s",
                                    interactive=True) == "ok"
    assert (seen["name"], seen["args"], seen["ctx"].project_id, seen["ctx"].session_id,
            seen["ctx"].interactive) == ("t_probe", {"a": 1}, "p", "s", True)
    assert await tools.execute_tool("t_probe", {"boom": 1}, None) == "Error executing t_probe: kaboom"
    assert await tools.execute_tool("nope", {}, None) == "Unknown tool: nope"


@pytest.mark.asyncio
async def test_consolidated_tools_route_to_the_old_handlers(monkeypatch):
    calls = []

    def fake(name):
        async def h(ctx, tool_name, tool_args):
            calls.append((name, tool_name, tool_args))
            return name
        return h

    for n in ("list_merge_proposals", "approve_merge", "index_artifact", "index_workspace"):
        monkeypatch.setitem(registry._EXACT, n, fake(n))
    assert await tools.execute_tool("merge_topics", {"action": "approve", "proposal_id": "p1",
                                                     "canonical_label": "Dell"}, None) == "approve_merge"
    assert calls[-1] == ("approve_merge", "approve_merge", {"proposal_id": "p1", "canonical_label": "Dell"})
    assert await tools.execute_tool("merge_topics", {"action": "list"}, None) == "list_merge_proposals"
    assert await tools.execute_tool("index", {"target": "artifact", "path_prefix": "NBJ/"}, None) == "index_artifact"
    assert calls[-1][2] == {"path_prefix": "NBJ/"}
    assert await tools.execute_tool("index", {"target": "workspace"}, None) == "index_workspace"
    assert "must be one of" in await tools.execute_tool("merge_topics", {"action": "nuke"}, None)
    assert "must be one of" in await tools.execute_tool("github", {"action": "rm_rf"}, None)


@pytest.mark.asyncio
async def test_github_action_maps_to_github_handler(monkeypatch):
    from agent.tools import github as gh
    seen = {}

    async def fake(ctx, tool_name, tool_args):
        seen.update(name=tool_name, args=tool_args)
        return "ok"

    monkeypatch.setattr(gh, "_tool_github_tools", fake)
    assert await tools.execute_tool("github", {"action": "read_file", "path": "a.py", "ref": "dev"}, None) == "ok"
    assert seen == {"name": "github_read_file", "args": {"path": "a.py", "ref": "dev"}}


@pytest.mark.asyncio
async def test_create_task_coding_job_type_queues_a_coding_task(monkeypatch):
    from agent.tools import tasks as t
    seen = {}

    async def fake(ctx, tool_name, tool_args):
        seen.update(tool_args)
        return "queued"

    monkeypatch.setattr(t, "_tool_start_coding_task", fake)
    out = await tools.execute_tool("create_task", {
        "name": "Fix login", "description": "Fix the login bug", "schedule": "now",
        "plan": "1. read auth.py", "job_type": "coding_task", "coding_context": "FastAPI"}, None)
    assert out == "queued"
    assert seen["title"] == "Fix login" and seen["coding_context"] == "FastAPI"
    assert seen["task_description"].startswith("Fix the login bug") and "1. read auth.py" in seen["task_description"]


def test_retired_names_are_not_shown_to_the_model():
    import json
    import re
    from pathlib import Path
    from agent.prompts import build_system_prompt
    text = (json.dumps(tools.TOOL_SCHEMAS)
            + build_system_prompt(project_id="default", project_name="Default")
            + build_system_prompt(project_id="default", host_exec=False)
            + (Path(__file__).resolve().parents[2] / "data" / "personality" / "agent.md").read_text())
    assert {n for n in tools.LEGACY_TOOLS if re.search(rf"\b{n}\b", text)} == set()
