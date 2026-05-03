"""Shared utilities for source adapters."""
from __future__ import annotations

import re


def slugify(s: str, max_len: int = 60) -> str:
    """Lowercase, replace non-alphanumeric runs with '-', trim, cap length.

    Used for both the title slug and the channel/author segment in
    artifact paths so the path is deterministic regardless of which
    adapter constructed it.
    """
    if not s:
        return ""
    out = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    if len(out) > max_len:
        # Trim on a hyphen boundary if possible.
        cut = out[:max_len].rsplit("-", 1)[0]
        out = cut or out[:max_len]
    return out
