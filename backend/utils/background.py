"""Fire-and-forget asyncio tasks that aren't garbage-collected mid-run.

The event loop only keeps weak references to tasks, so a bare
``asyncio.create_task(coro)`` / ``ensure_future`` whose result is dropped
can be collected before it finishes, and its exception is never seen.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Coroutine

logger = logging.getLogger(__name__)

_TASKS: set[asyncio.Task] = set()


def _done(task: asyncio.Task) -> None:
    _TASKS.discard(task)
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        logger.error("Background task %s failed: %s", task.get_name(), exc, exc_info=exc)


def spawn(coro: Coroutine[Any, Any, Any], *, name: str | None = None) -> asyncio.Task:
    """Schedule ``coro`` on the running loop and keep a strong reference."""
    task = asyncio.get_running_loop().create_task(coro, name=name)
    _TASKS.add(task)
    task.add_done_callback(_done)
    return task
