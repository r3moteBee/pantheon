"""Runtime-setting readers shared by non-API modules.

Values here are user-adjustable at runtime (Settings writes them to the
vault) and fall back to the static ``config.Settings`` defaults. Keep
readers here, not in an ``api/`` router module, so memory/agent code can
import them without pulling in FastAPI routers.
"""
from __future__ import annotations


def _int_or(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def get_active_chunk_settings() -> tuple[int, int, str]:
    """Current file-chunking config: ``(size, overlap, strategy)`` — vault
    overrides (``file_chunk_size`` / ``file_chunk_overlap`` /
    ``file_chunk_strategy``) over the config defaults."""
    from config import get_settings
    from secrets.vault import get_vault

    cfg = get_settings()
    vault = get_vault()
    size = _int_or(vault.get_secret("file_chunk_size"), cfg.file_chunk_size)
    overlap = _int_or(vault.get_secret("file_chunk_overlap"), cfg.file_chunk_overlap)
    strategy = vault.get_secret("file_chunk_strategy") or cfg.file_chunk_strategy
    return size, overlap, strategy
