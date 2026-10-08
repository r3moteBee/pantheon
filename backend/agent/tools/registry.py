"""Tool registry: each tool module declares its schemas (``SCHEMAS``) and
registers a handler per tool with ``@tool``. ``execute_tool`` (in
``agent.tools``) resolves the name here — exact names first, then
prefixes (``browser_``, ``github_``, ``git_``) — and calls the handler as
``handler(ctx, tool_name, tool_args)``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

Handler = Callable[["ToolContext", str, dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class ToolContext:
    """What a tool call knows about its caller."""
    memory_manager: Any = None
    project_id: str | None = None
    session_id: str | None = None
    last_assistant_text: str = ""
    # True only for turns a person is driving in the web UI.
    interactive: bool = False
    # True when that person's message itself asked for scheduled or background
    # work (agent/task_intent.py): create_task then skips the review step.
    user_requested_task: bool = False
    host_exec: bool = False

    @property
    def effective_project(self) -> str:
        return self.project_id or "default"


_EXACT: dict[str, Handler] = {}
_PREFIX: list[tuple[str, Handler]] = []


def tool(*names: str, prefix: str | None = None) -> Callable[[Handler], Handler]:
    """Register ``fn`` for the given tool names and/or a name prefix."""
    def deco(fn: Handler) -> Handler:
        for n in names:
            if n in _EXACT:
                raise ValueError(f"tool {n!r} registered twice")
            _EXACT[n] = fn
        if prefix:
            _PREFIX.append((prefix, fn))
        return fn
    return deco


def resolve(name: str) -> Handler | None:
    if name in _EXACT:
        return _EXACT[name]
    for p, fn in _PREFIX:
        if name.startswith(p):
            return fn
    return None


def registered_names() -> set[str]:
    return set(_EXACT)
