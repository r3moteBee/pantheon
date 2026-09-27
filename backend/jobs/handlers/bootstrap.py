"""Eager handler import. Called once at FastAPI startup.

Importing each handler module triggers @register() and populates
jobs.handlers.HANDLERS.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)


def bootstrap_handlers() -> None:
    """Import every handler module so it self-registers. Called from
    main.py:lifespan startup."""
    try:
        from jobs.handlers import autonomous_task   # noqa: F401
    except Exception as e:
        logger.debug("autonomous_task handler unavailable: %s", e)
    try:
        from jobs.handlers import coding_task       # noqa: F401
    except Exception as e:
        logger.debug("coding_task handler unavailable: %s", e)
    try:
        from jobs.handlers import image_extraction   # noqa: F401
    except Exception as e:
        logger.debug("image_extraction handler unavailable: %s", e)
    try:
        from jobs.handlers import iteration_loop    # noqa: F401
    except Exception as e:
        logger.debug("iteration_loop handler unavailable: %s", e)

    from jobs.handlers import HANDLERS
    logger.info("Handlers registered: %s", sorted(HANDLERS.keys()))
