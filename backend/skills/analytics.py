"""Skill usage analytics — lightweight on-disk counters.

Tracks per-skill: fire count, suggestion count, last fired time, source
breakdown (explicit via /skill-name vs. auto-resolved). Stored as a single
JSON file at `data_dir/skill_analytics.json` — no DB dependency.

Designed to be fire-and-forget from the chat path: `record_fire()` is safe
to call without awaiting and never raises.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

_LOCK = threading.Lock()


def _path() -> Path:
    return settings.data_dir / "skill_analytics.json"


def _load() -> dict[str, Any]:
    p = _path()
    if not p.is_file():
        return {"skills": {}}
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception as e:
        logger.warning("skill_analytics.json unreadable, resetting: %s", e)
        return {"skills": {}}


def _save(data: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2), "utf-8")


def _empty_stats() -> dict[str, Any]:
    return {
        "fires": 0,
        "fires_explicit": 0,
        "fires_auto": 0,
        "suggestions": 0,
        "last_fired": None,
        "last_suggested": None,
    }


def record_fire(skill_name: str, *, source: str = "explicit") -> None:
    """Increment fire counter for a skill. source: 'explicit' | 'auto'."""
    if not skill_name:
        return
    try:
        with _LOCK:
            data = _load()
            stats = data["skills"].setdefault(skill_name, _empty_stats())
            stats["fires"] = stats.get("fires", 0) + 1
            if source == "auto":
                stats["fires_auto"] = stats.get("fires_auto", 0) + 1
            else:
                stats["fires_explicit"] = stats.get("fires_explicit", 0) + 1
            stats["last_fired"] = datetime.now(timezone.utc).isoformat()
            _save(data)
    except Exception as e:
        logger.warning("record_fire failed for %s: %s", skill_name, e)


def record_suggestion(skill_name: str, *, declined: bool = False, accepted: bool = False) -> None:
    if not skill_name:
        return
    try:
        with _LOCK:
            data = _load()
            stats = data["skills"].setdefault(skill_name, _empty_stats())
            if declined:
                stats["suggestions_declined"] = stats.get("suggestions_declined", 0) + 1
            elif accepted:
                stats["suggestions_accepted"] = stats.get("suggestions_accepted", 0) + 1
            else:
                stats["suggestions"] = stats.get("suggestions", 0) + 1
                stats["last_suggested"] = datetime.now(timezone.utc).isoformat()
            _save(data)
    except Exception as e:
        logger.warning("record_suggestion failed for %s: %s", skill_name, e)


def get_all_stats() -> dict[str, dict[str, Any]]:
    with _LOCK:
        return _load().get("skills", {})


def get_stats(skill_name: str) -> dict[str, Any]:
    return get_all_stats().get(skill_name, _empty_stats())


def reset_stats(skill_name: str | None = None) -> None:
    with _LOCK:
        data = _load()
        if skill_name:
            data["skills"].pop(skill_name, None)
        else:
            data["skills"] = {}
        _save(data)
