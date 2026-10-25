"""Durable record of MCP background tasks a tool call is waiting on.

The tasks extension recommends persisting task ids so polling can resume
after a client restart. ``MCPClient._run_task`` records each task here with
the chat context it belongs to (set by ``agent.tools.execute_tool`` through
``CALL_CONTEXT``), forgets it when the task ends, and
``MCPManager.resume_tasks`` picks up whatever is left at startup: it polls
the task to its end and drops the result into the originating conversation.

One small JSON file under DATA_DIR; writes are whole-file and rare.
"""
from __future__ import annotations

import json
import logging
import os
import time
from contextvars import ContextVar
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# {"session_id", "project_id", "tool"} for the tool call in progress, or None
# outside a chat turn (jobs, adapters) where there is no place to resume to.
CALL_CONTEXT: ContextVar[dict[str, Any] | None] = ContextVar("mcp_call_context", default=None)

FILENAME = "mcp_tasks.json"


def _path() -> Path:
    from config import get_settings
    return Path(get_settings().data_dir) / FILENAME


def _load() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(_path().read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("mcp task store unreadable; starting empty", exc_info=True)
        return {}


def _save(data: dict[str, dict[str, Any]]) -> None:
    path = _path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        logger.warning("mcp task store not saved", exc_info=True)


def remember(connection: str, tool: str, task_id: str, context: dict[str, Any] | None) -> None:
    data = _load()
    data[task_id] = {"connection": connection, "tool": tool, "task_id": task_id,
                     "context": dict(context or {}), "created_at": time.time()}
    _save(data)


def forget(task_id: str) -> None:
    data = _load()
    if data.pop(task_id, None) is not None:
        _save(data)


def pending() -> list[dict[str, Any]]:
    return list(_load().values())
