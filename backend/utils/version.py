"""The app version: frontend/package.json is the single source of truth.

/api/health, the FastAPI app and get_self_documentation all read it here. In
the Docker image the backend runs from /app with package.json copied next to it
(no frontend/ sibling); in a checkout it is ../frontend/package.json.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent


@lru_cache(maxsize=1)
def app_version() -> str:
    for pkg in (_BACKEND.parent / "frontend" / "package.json",   # checkout / dev
                _BACKEND / "package.json"):                      # Docker image
        if pkg.exists():
            try:
                return json.loads(pkg.read_text(encoding="utf-8"))["version"]
            except Exception:
                pass
    return "0.0.0-dev"
