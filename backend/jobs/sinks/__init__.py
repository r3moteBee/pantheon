"""Output sink registry — where to ship a scheduled_job's result.

Mirror of the handler pattern. Default sink for scheduled_jobs is
'artifact'. The agent prompt produces some content; the sink decides
where that content lands (artifact / telegram / webhook / future SMS).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Awaitable, Callable

from jobs.context import JobContext

logger = logging.getLogger(__name__)


SinkFn = Callable[[JobContext, str, dict], Awaitable[dict]]


@dataclass
class Sink:
    kind: str
    fn: SinkFn
    description: str = ""


SINKS: dict[str, Sink] = {}


def register(kind: str, *, description: str = ""):
    def deco(fn: SinkFn):
        SINKS[kind] = Sink(kind=kind, fn=fn, description=description)
        logger.debug("registered sink: %s", kind)
        return fn
    return deco


def get_sink(kind: str) -> Sink | None:
    return SINKS.get(kind)


def known_kinds() -> list[str]:
    return sorted(SINKS.keys())


def bootstrap_sinks() -> None:
    """Eager-import every sink so it self-registers."""
    for mod in ("artifact_sink", "telegram_sink", "webhook_sink"):
        try:
            __import__(f"jobs.sinks.{mod}")
        except Exception as e:
            logger.warning("sink %s unavailable: %s", mod, e)
    logger.info("Sinks registered: %s", known_kinds())
