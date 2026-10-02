"""Centralized security audit log.

All security-relevant events are written to a dedicated log file at
``data/logs/security.log`` as well as to stdout via the standard logger.
Each entry is a structured JSON line for easy parsing and dashboarding.

Usage::

    from security_log import sec_log

    sec_log.auth_login_failure(ip="1.2.3.4", reason="bad password")
    sec_log.skill_scan_failed(skill="shady", risk=0.85, findings=12)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from config import get_settings

# ── Logger setup ─────────────────────────────────────────────────────────────

_LOGGER_NAME = "pantheon.security"
_logger = logging.getLogger(_LOGGER_NAME)
_initialised = False


def _ensure_init() -> None:
    """Lazily attach a file handler the first time an event is recorded."""
    global _initialised
    if _initialised:
        return
    _initialised = True

    settings = get_settings()
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "security.log"

    handler = logging.FileHandler(str(log_file), encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(message)s"))  # raw JSON lines
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = True  # still show in console via root


def _emit(event: str, level: str = "INFO", **kwargs: Any) -> None:
    """Write a structured JSON log line."""
    _ensure_init()
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "level": level,
        **{k: v for k, v in kwargs.items() if v is not None},
    }
    line = json.dumps(entry, default=str)
    log_level = getattr(logging, level.upper(), logging.INFO)
    _logger.log(log_level, line)


# ── Authentication events ────────────────────────────────────────────────────

class _SecurityLog:
    """Typed methods for each security event category."""

    # ── Auth ──────────────────────────────────────────────────────────────

    def auth_login_success(self, *, ip: str | None = None) -> None:
        _emit("auth.login_success", ip=ip)

    def auth_login_failure(self, *, ip: str | None = None, reason: str = "bad_password") -> None:
        _emit("auth.login_failure", level="WARNING", ip=ip, reason=reason)

    # ── Skills: scanning ─────────────────────────────────────────────────

    def skill_scan_passed(self, *, skill: str, risk: float, findings: int) -> None:
        _emit("skill.scan_passed", skill=skill, risk=risk, findings=findings)

    def skill_scan_failed(self, *, skill: str, risk: float, findings: int) -> None:
        _emit("skill.scan_failed", level="WARNING", skill=skill, risk=risk, findings=findings)

    def skill_scan_all(self, *, passed: int, failed: int, errors: int) -> None:
        _emit("skill.scan_all", passed=passed, failed=failed, errors=errors)

    # ── Skills: enable / disable ─────────────────────────────────────────

    def skill_enabled(self, *, skill: str, project: str) -> None:
        _emit("skill.enabled", skill=skill, project=project)

    def skill_disabled(self, *, skill: str, project: str) -> None:
        _emit("skill.disabled", skill=skill, project=project)

    def skill_override_used(self, *, skill: str, project: str) -> None:
        _emit("skill.override_used", level="WARNING", skill=skill, project=project)

    def skill_override_failed(self, *, skill: str, reason: str) -> None:
        _emit("skill.override_failed", level="WARNING", skill=skill, reason=reason)

    # ── Skills: quarantine ───────────────────────────────────────────────

    def skill_quarantined(self, *, skill: str, reason: str = "scan_failed") -> None:
        _emit("skill.quarantined", level="WARNING", skill=skill, reason=reason)

    def skill_unquarantined(self, *, skill: str) -> None:
        _emit("skill.unquarantined", skill=skill)

    # ── Skills: delete ───────────────────────────────────────────────────

    def skill_deleted(self, *, skill: str, is_bundled: bool) -> None:
        _emit("skill.deleted", level="WARNING" if is_bundled else "INFO",
              skill=skill, is_bundled=is_bundled)

    # ── Skills: name masquerade / spoofing ───────────────────────────────

    def skill_name_collision_blocked(self, *, skill: str, reason: str) -> None:
        _emit("skill.name_collision_blocked", level="WARNING", skill=skill, reason=reason)

    # ── Vault / secrets ──────────────────────────────────────────────────

    def secret_set(self, *, key: str) -> None:
        _emit("vault.secret_set", key=key)

    def secret_deleted(self, *, key: str) -> None:
        _emit("vault.secret_deleted", key=key)

    # ── Settings changes ─────────────────────────────────────────────────

    def settings_updated(self, *, changed_keys: list[str]) -> None:
        _emit("settings.updated", changed_keys=changed_keys)


# Singleton
sec_log = _SecurityLog()
