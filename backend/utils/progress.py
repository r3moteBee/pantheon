"""Ambient "still making progress" signal for the job stall watchdog.

pinger_for(ctx, max_quiet=...) installs ctx.touch as the reporter for the
code it wraps; anything below it (agent loop, tool calls, ingest items, LLM
and MCP responses) calls report_progress() without needing a ctx handle.
A genuinely hung await reports nothing, so the pinger stops vouching for
it and the watchdog can stall the job.
"""
from __future__ import annotations

from contextvars import ContextVar
from typing import Callable

_reporter: ContextVar[Callable[[], None] | None] = ContextVar("progress_reporter", default=None)


def report_progress() -> None:
    cb = _reporter.get()
    if cb is not None:
        try:
            cb()
        except Exception:
            pass


def set_reporter(cb: Callable[[], None] | None):
    """Install ``cb``; returns a token for reset_reporter()."""
    return _reporter.set(cb)


def reset_reporter(token) -> None:
    _reporter.reset(token)
