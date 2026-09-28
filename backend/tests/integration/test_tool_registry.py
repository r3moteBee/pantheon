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
    assert registry.registered_names() - set(names) == set()
    assert names == tools._ORDER


def test_prefix_handlers_and_unknown_names():
    assert registry.resolve("git_status").__module__ == "agent.tools.git"
    assert registry.resolve("github_list_prs").__module__ == "agent.tools.github"
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
