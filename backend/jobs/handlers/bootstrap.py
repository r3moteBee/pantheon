"""Eager handler import. Called once at FastAPI startup.

Importing each handler module triggers @register() and populates
jobs.handlers.HANDLERS.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


def bootstrap_handlers() -> None:
    """Import every handler module so it self-registers. Called from
    main.py:lifespan startup. Also bootstraps output sinks for
    scheduled_job."""
    try:
        from jobs.sinks import bootstrap_sinks
        bootstrap_sinks()
    except Exception as e:
        logger.warning("sinks bootstrap failed: %s", e)
    # Phase H.3 / H.3.5 / H.4 will populate this list as those handlers
    # land. For H.1 the registry is empty — that's fine; the worker just
    # has nothing to dispatch.
    try:
        from jobs.handlers import autonomous_task   # noqa: F401
    except Exception as e:
        logger.debug("autonomous_task handler unavailable: %s", e)
    try:
        from jobs.handlers import scheduled_job     # noqa: F401
    except Exception as e:
        logger.debug("scheduled_job handler unavailable: %s", e)
    try:
        from jobs.handlers import coding_task       # noqa: F401
    except Exception as e:
        logger.debug("coding_task handler unavailable: %s", e)
    try:
        from jobs.handlers import extraction        # noqa: F401
    except Exception as e:
        logger.debug("extraction handler unavailable: %s", e)
    try:
        from jobs.handlers import file_indexing     # noqa: F401
    except Exception as e:
        logger.debug("file_indexing handler unavailable: %s", e)

    from jobs.handlers import HANDLERS
    logger.info("Handlers registered: %s", sorted(HANDLERS.keys()))
