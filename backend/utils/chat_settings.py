"""Per-project chat settings with global fallback — the one read path.

Knobs:
  tone_weight    — how strongly soul.md colours replies (minimal|balanced|strong)
  context_focus  — how tightly recall sticks to the current message (broad|balanced|focused)
  memory_recall  — pre-recall memories before each turn (bool)
  skill_discovery — off|suggest|auto

A project override lives in phase_g.db ``project_settings`` (NULL = inherit);
the global value is the vault key Settings writes (``personality_weight``,
``context_focus``, ``memory_recall_enabled``). skill_discovery has always
been per project in the vault (``skill_discovery_<project>``) and stays
there, so the chat header, Project Settings and the skills API agree.

Chat (AgentCore) reads ``effective(project_id)``; the chat header and
Project Settings write ``set_overrides``.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

KNOBS: dict[str, dict[str, Any]] = {
    "tone_weight": {"global_key": "personality_weight", "default": "balanced",
                    "choices": ("minimal", "balanced", "strong")},
    "context_focus": {"global_key": "context_focus", "default": "balanced",
                      "choices": ("broad", "balanced", "focused")},
    "memory_recall": {"global_key": "memory_recall_enabled", "default": True, "choices": (True, False)},
}
SKILL_DISCOVERY_CHOICES = ("off", "suggest", "auto")

_ready: set[str] = set()


def _db_path() -> str:
    from config import get_settings
    s = get_settings()
    s.db_dir.mkdir(parents=True, exist_ok=True)
    return str(s.db_dir / "phase_g.db")


def _connect() -> sqlite3.Connection:
    from db_utils import apply_sqlite_pragmas
    path = _db_path()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    apply_sqlite_pragmas(conn)
    if path not in _ready:
        sql = Path(__file__).resolve().parent.parent / "data" / "migrations" / "002_phase_g.sql"
        if sql.exists():
            conn.executescript(sql.read_text())
        cols = {r[1] for r in conn.execute("PRAGMA table_info(project_settings)")}
        if "memory_recall" not in cols:
            conn.execute("ALTER TABLE project_settings ADD COLUMN memory_recall INTEGER")
        conn.commit()
        _ready.add(path)
    return conn


def _valid(knob: str, value: Any) -> Any:
    """Normalised value, or None when unset/invalid (older rows stored
    tone values from the wrong scale)."""
    if value is None or value == "":
        return None
    if knob == "memory_recall":
        if isinstance(value, str):
            return value.lower() in ("1", "true", "on", "yes")
        return bool(value)
    return value if value in KNOBS[knob]["choices"] else None


def overrides(project_id: str) -> dict[str, Any]:
    """Project-level values that are set (others inherit)."""
    with _connect() as conn:
        row = conn.execute("SELECT * FROM project_settings WHERE project_id = ?",
                           (project_id,)).fetchone()
    if not row:
        return {}
    out = {}
    for knob in KNOBS:
        v = _valid(knob, row[knob] if knob in row.keys() else None)
        if v is not None:
            out[knob] = v
    return out


def global_values() -> dict[str, Any]:
    from secrets.vault import get_vault
    vault = get_vault()
    out: dict[str, Any] = {}
    for knob, spec in KNOBS.items():
        v = _valid(knob, vault.get_secret(spec["global_key"]))
        out[knob] = spec["default"] if v is None else v
    return out


def skill_discovery(project_id: str) -> str:
    from secrets.vault import get_vault
    mode = get_vault().get_secret(f"skill_discovery_{project_id}") or "off"
    return mode if mode in SKILL_DISCOVERY_CHOICES else "off"


def effective(project_id: str | None) -> dict[str, Any]:
    g = global_values()
    o = overrides(project_id) if project_id else {}
    eff = {**g, **o}
    eff["skill_discovery"] = skill_discovery(project_id or "default")
    eff["source"] = {k: ("project" if k in o else "global") for k in KNOBS}
    return eff


def set_overrides(project_id: str, values: dict[str, Any]) -> None:
    """Set/clear project overrides. ``None``/"" clears (inherit global).
    Raises ValueError on an unknown knob or value."""
    from datetime import datetime, timezone
    from secrets.vault import get_vault
    row_updates: dict[str, Any] = {}
    for knob, value in values.items():
        if knob == "skill_discovery":
            if value in (None, ""):
                get_vault().delete_secret(f"skill_discovery_{project_id}")
            elif value in SKILL_DISCOVERY_CHOICES:
                get_vault().set_secret(f"skill_discovery_{project_id}", value)
            else:
                raise ValueError(f"skill_discovery must be one of {', '.join(SKILL_DISCOVERY_CHOICES)}")
            continue
        if knob not in KNOBS:
            raise ValueError(f"unknown chat setting {knob!r}")
        if value in (None, ""):
            row_updates[knob] = None
            continue
        v = _valid(knob, value)
        if v is None:
            raise ValueError(f"{knob} must be one of {', '.join(map(str, KNOBS[knob]['choices']))}")
        row_updates[knob] = int(v) if knob == "memory_recall" else v
    if not row_updates:
        return
    now = datetime.now(timezone.utc).isoformat()
    with _connect() as conn:
        conn.execute("INSERT OR IGNORE INTO project_settings (project_id, tone_weight, context_focus, "
                     "updated_at) VALUES (?, NULL, NULL, ?)", (project_id, now))
        for knob, v in row_updates.items():
            conn.execute(f"UPDATE project_settings SET {knob} = ?, updated_at = ? WHERE project_id = ?",
                         (v, now, project_id))
        conn.commit()
