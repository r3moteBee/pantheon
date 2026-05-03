"""Job handler registry. Handlers register at import time.

A handler is just an async function that takes a JobContext and returns
a result dict. The worker is responsible for atomicity / timeouts /
status writes — the handler focuses on the work itself.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from jobs.context import JobContext

logger = logging.getLogger(__name__)


HandlerFn = Callable[[JobContext], Awaitable[dict]]


@dataclass
class Handler:
    job_type: str
    fn: HandlerFn
    default_timeout_seconds: int = 600
    description: str = ""


HANDLERS: dict[str, Handler] = {}


def register(
    job_type: str,
    *,
    default_timeout_seconds: int = 600,
    description: str = "",
):
    """Decorator: @register('my_job_type'). Wraps an async function and
    adds it to the global registry."""
    def deco(fn: HandlerFn):
        HANDLERS[job_type] = Handler(
            job_type=job_type, fn=fn,
            default_timeout_seconds=default_timeout_seconds,
            description=description,
        )
        logger.debug("registered handler: %s", job_type)
        return fn
    return deco


def get_handler(job_type: str) -> Handler | None:
    return HANDLERS.get(job_type)


def known_types() -> list[str]:
    return sorted(HANDLERS.keys())


# Eager-import handler modules so they self-register.
# Each module is responsible for calling register() at import time.
def _load_handlers() -> None:
    # The actual imports happen in jobs.handlers.bootstrap (below) to
    # keep this module's import graph minimal. main.py calls bootstrap()
    # during the FastAPI lifespan startup.
    pass
