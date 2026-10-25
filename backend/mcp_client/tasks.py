"""MCP tasks extension (``io.modelcontextprotocol/tasks``, client side).

A server that negotiated the extension may answer ``tools/call`` with
``resultType: "task"`` - a durable handle - instead of the result. The
client then polls ``tasks/get`` at the server's ``pollIntervalMs`` until the
task is terminal: ``completed`` carries the CallToolResult in ``result``,
``failed`` a JSON-RPC error, ``cancelled`` nothing. A task that needs the
user (``input_required``) lists its ``inputRequests``; the client answers
them through the same elicitor the MRTR flow uses and sends ``tasks/update``.
``tasks/cancel`` is sent when the user cancels, when nobody can answer an
input request, and when the client gives up (TTL backstop, max wait).

Pure helpers live here; the polling loop is ``MCPClient._run_task``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

TASKS_EXTENSION = "io.modelcontextprotocol/tasks"

STATUS_WORKING = "working"
STATUS_INPUT_REQUIRED = "input_required"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"
STATUSES = frozenset({STATUS_WORKING, STATUS_INPUT_REQUIRED, STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED})
TERMINAL = frozenset({STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED})

# Polling cadence: the server's pollIntervalMs, clamped. Tests lower MIN_POLL_MS.
DEFAULT_POLL_MS = 2000
MIN_POLL_MS = 250
MAX_POLL_MS = 30_000
# Longest a single tool call waits on a task before cancelling it (seconds);
# the agent turn is blocked meanwhile. Settings.mcp_task_max_wait_seconds overrides.
DEFAULT_MAX_WAIT_S = 1800.0


class InvalidTask(Exception):
    """A task object the server sent is not usable."""


def parse_task(obj: Any) -> dict[str, Any]:
    """Validate a Task / DetailedTask from the wire. Returns it unchanged."""
    if not isinstance(obj, dict):
        raise InvalidTask("task is not an object")
    task_id = obj.get("taskId")
    if not isinstance(task_id, str) or not task_id:
        raise InvalidTask("task has no taskId")
    status = obj.get("status")
    if status not in STATUSES:
        raise InvalidTask(f"task {task_id} has unknown status {status!r}")
    if status == STATUS_COMPLETED and not isinstance(obj.get("result"), dict):
        raise InvalidTask(f"task {task_id} is completed without a result")
    if status == STATUS_FAILED and not isinstance(obj.get("error"), dict):
        raise InvalidTask(f"task {task_id} failed without an error")
    if status == STATUS_INPUT_REQUIRED and not isinstance(obj.get("inputRequests"), dict):
        raise InvalidTask(f"task {task_id} needs input but lists no inputRequests")
    return obj


def poll_delay(task: dict[str, Any]) -> float:
    """Seconds to wait before the next tasks/get."""
    ms = task.get("pollIntervalMs")
    if not isinstance(ms, (int, float)) or isinstance(ms, bool) or ms <= 0:
        ms = DEFAULT_POLL_MS
    return max(MIN_POLL_MS, min(MAX_POLL_MS, float(ms))) / 1000.0


def _parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def expired(task: dict[str, Any], now: datetime | None = None) -> bool:
    """TTL backstop: createdAt + ttlMs has passed (a null ttlMs never expires)."""
    ttl = task.get("ttlMs")
    created = _parse_time(task.get("createdAt"))
    if ttl is None or isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or created is None:
        return False
    now = now or datetime.now(timezone.utc)
    return (now - created).total_seconds() * 1000.0 > ttl


def public_status(task: dict[str, Any], *, elapsed_s: float) -> dict[str, Any]:
    """What the UI gets about a running task."""
    out: dict[str, Any] = {"task_id": task.get("taskId"), "state": task.get("status"), "elapsed_s": int(elapsed_s)}
    msg = task.get("statusMessage")
    if isinstance(msg, str) and msg.strip():
        out["message"] = msg.strip()[:500]
    return out
