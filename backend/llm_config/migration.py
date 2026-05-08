"""One-shot migration from legacy flat vault keys → saved endpoints + role mapping."""
from __future__ import annotations
import logging
from secrets.vault import get_vault

logger = logging.getLogger(__name__)


def migrate_from_legacy() -> None:
    """Stub — real implementation in Task 3. Sets the flag so
    resolve_role doesn't loop."""
    get_vault().set_secret("llm_config_migrated_v1", "true")
