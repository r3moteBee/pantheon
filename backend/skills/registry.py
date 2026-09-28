"""Skill registry — discovers, loads, and indexes skills from disk.

Skills live in two locations:
  - Bundled:  <repo>/skills/          (shipped with Pantheon)
  - User:     <data>/skills/          (user-installed)

Each skill is a directory containing at minimum a `skill.json` manifest.
An optional `instructions.md` provides the detailed procedure injected
into the system prompt when the skill is active.

Security invariants:
  - Only skills loaded from the bundled directory get is_bundled=True
    (the flag is set by the loader, never read from skill.json)
  - User-installed skills CANNOT override bundled skills of the same name
  - User-installed skills require a passing scan before they can be enabled
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import get_settings
from security_log import sec_log
from skills.models import LoadedSkill, ScanResult, SkillManifest, ProjectSkillSettings

logger = logging.getLogger(__name__)
settings = get_settings()

# Bundled skills live alongside the backend source
_BUNDLED_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"

# User-installed skills live inside the data directory
_USER_SKILLS_DIR = settings.data_dir / "skills"


def _compute_skill_hash(skill_dir: Path) -> str:
    """Compute a content hash of all files in a skill directory.

    Used to detect when skill files change so the scan can be invalidated.
    """
    h = hashlib.sha256()
    for file_path in sorted(skill_dir.rglob("*")):
        if file_path.is_file() and not file_path.name.startswith("."):
            h.update(str(file_path.relative_to(skill_dir)).encode())
            h.update(file_path.read_bytes())
    return h.hexdigest()[:16]


class SkillRegistry:
    """In-memory registry of all available skills."""

    def __init__(self) -> None:
        self._skills: dict[str, LoadedSkill] = {}
        self._bundled_names: set[str] = set()  # Track bundled names for collision protection
        self._loaded = False

    def load(self) -> None:
        """(Re)load all skills from both directories."""
        self._skills.clear()
        self._bundled_names.clear()
        loaded = 0

        # Load bundled skills first
        if _BUNDLED_SKILLS_DIR.is_dir():
            for skill_dir in sorted(_BUNDLED_SKILLS_DIR.iterdir()):
                if skill_dir.is_dir() and (skill_dir / "skill.json").exists():
                    skill = self._load_skill(skill_dir, is_bundled=True)
                    if skill:
                        self._skills[skill.name] = skill
                        self._bundled_names.add(skill.name)
                        loaded += 1

        # Load user-installed skills — CANNOT override bundled skills
        _USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        if _USER_SKILLS_DIR.is_dir():
            for skill_dir in sorted(_USER_SKILLS_DIR.iterdir()):
                if skill_dir.is_dir() and (skill_dir / "skill.json").exists():
                    skill = self._load_skill(skill_dir, is_bundled=False)
                    if skill:
                        if skill.name in self._bundled_names:
                            sec_log.skill_name_collision_blocked(
                                skill=skill.name,
                                reason=f"user skill from {skill_dir} tried to override bundled skill",
                            )
                            logger.warning(
                                "BLOCKED: User skill '%s' from %s cannot override "
                                "bundled skill of the same name — skipping",
                                skill.name, skill_dir,
                            )
                            continue
                        self._skills[skill.name] = skill
                        loaded += 1
                        if skill.manifest.security_scan is None:
                            self._static_scan(skill)

        self._loaded = True
        logger.info("Skill registry loaded: %d skills (%d bundled)", loaded, len(self._bundled_names))

    def _load_skill(self, skill_dir: Path, is_bundled: bool) -> LoadedSkill | None:
        """Load a single skill from a directory.

        The is_bundled flag is determined ONLY by the loader based on which
        directory the skill was loaded from — never from skill.json content.
        This prevents imported skills from claiming to be bundled.
        """
        manifest_path = skill_dir / "skill.json"
        instructions_path = skill_dir / "instructions.md"

        try:
            raw = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest = SkillManifest(**raw)
        except Exception as e:
            logger.warning("Failed to load skill from %s: %s", skill_dir, e)
            return None

        instructions = ""
        if instructions_path.exists():
            try:
                instructions = instructions_path.read_text(encoding="utf-8")
            except Exception as e:
                logger.warning("Failed to read instructions for %s: %s", skill_dir.name, e)

        # Load persisted scan result and check if it's still valid
        content_hash = _compute_skill_hash(skill_dir)
        scan_result = self._load_scan_result(manifest.name, content_hash)
        if scan_result:
            manifest.security_scan = scan_result

        # Load per-project enable/disable state
        disabled_projects = self._load_disabled_projects(manifest.name)

        return LoadedSkill(
            manifest=manifest,
            instructions=instructions,
            skill_dir=str(skill_dir),
            is_bundled=is_bundled,
            disabled_projects=disabled_projects,
            scan_override=self._load_state_entry(manifest.name).get("scan_override", False),
        )

    def _load_state_entry(self, skill_name: str) -> dict[str, Any]:
        state_file = _USER_SKILLS_DIR / ".skill_state.json"
        if state_file.exists():
            try:
                return json.loads(state_file.read_text(encoding="utf-8")).get(skill_name, {}) or {}
            except Exception:
                pass
        return {}

    def _load_disabled_projects(self, skill_name: str) -> list[str]:
        """Load which projects have this skill disabled.

        Skills are enabled for all projects by default.  Only projects
        that explicitly disabled a skill appear in disabled_projects.
        """
        state_file = _USER_SKILLS_DIR / ".skill_state.json"
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
                return state.get(skill_name, {}).get("disabled_projects", [])
            except Exception:
                pass
        return []

    def _save_skill_state(self) -> None:
        """Persist per-skill state (disabled projects) to disk."""
        state: dict[str, Any] = {}
        for name, skill in self._skills.items():
            entry: dict[str, Any] = {}
            if skill.disabled_projects:
                entry["disabled_projects"] = skill.disabled_projects
            if skill.scan_override:
                entry["scan_override"] = True
            if entry:
                state[name] = entry
        state_file = _USER_SKILLS_DIR / ".skill_state.json"
        _USER_SKILLS_DIR.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")

    # ── Scan persistence ─────────────────────────────────────────────────

    def _scan_results_dir(self) -> Path:
        """Directory for persisted scan results."""
        d = _USER_SKILLS_DIR / ".scan_results"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _load_scan_result(self, skill_name: str, content_hash: str) -> ScanResult | None:
        """Load a persisted scan result if it exists and the content hash matches."""
        scan_file = self._scan_results_dir() / f"{skill_name}.json"
        if not scan_file.exists():
            return None
        try:
            raw = json.loads(scan_file.read_text(encoding="utf-8"))
            # Invalidate if content has changed since scan
            if raw.get("content_hash") != content_hash:
                logger.info(
                    "Scan result for '%s' invalidated — content changed (hash %s != %s)",
                    skill_name, raw.get("content_hash", "?"), content_hash,
                )
                return None
            return ScanResult(**raw["result"])
        except Exception as e:
            logger.debug("Failed to load scan result for '%s': %s", skill_name, e)
            return None

    def _static_scan(self, skill: LoadedSkill) -> None:
        """New, edited or hand-written user skills have no valid scan; give
        them the fast deterministic one so nothing reaches the prompt
        unscanned. A failure blocks the skill (LoadedSkill.scan_blocked)."""
        try:
            from skills.scanner import static_scan
            result = static_scan(Path(skill.skill_dir), skill.manifest)
        except Exception as e:
            logger.warning("Static scan failed for '%s': %s", skill.name, e)
            return
        skill.manifest.security_scan = result
        self.save_scan_result(skill.name, result)
        if not result.passed:
            logger.warning("Skill '%s' blocked by its security scan (risk %.2f)", skill.name, result.risk_score)

    def save_scan_result(self, skill_name: str, result: ScanResult) -> None:
        """Persist a scan result to disk, tagged with a content hash."""
        skill = self._skills.get(skill_name)
        if not skill:
            return
        content_hash = _compute_skill_hash(Path(skill.skill_dir))
        scan_file = self._scan_results_dir() / f"{skill_name}.json"
        payload = {
            "content_hash": content_hash,
            "result": json.loads(result.model_dump_json()),
        }
        scan_file.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
        logger.info("Saved scan result for '%s' (hash=%s, passed=%s)", skill_name, content_hash, result.passed)

    # ── Public API ──────────────────────────────────────────────────────

    def ensure_loaded(self) -> None:
        """Load skills if not already loaded."""
        if not self._loaded:
            self.load()

    def get(self, name: str) -> LoadedSkill | None:
        """Get a skill by name."""
        self.ensure_loaded()
        return self._skills.get(name)

    def list_all(self) -> list[LoadedSkill]:
        """List all registered skills."""
        self.ensure_loaded()
        return list(self._skills.values())

    def list_for_project(self, project_id: str) -> list[LoadedSkill]:
        """List skills available for a project.

        A skill is available unless it has been explicitly disabled
        for this project.
        """
        self.ensure_loaded()
        return [
            skill for skill in self._skills.values()
            if skill.is_enabled_for(project_id)
        ]

    def enable_for_project(
        self, skill_name: str, project_id: str, *, force: bool = False
    ) -> dict[str, Any]:
        """Enable a skill for a specific project (remove from disabled list).

        Non-bundled skills require a passing scan before they can be enabled.
        If force=True, the scan gate is bypassed (caller must verify the
        security override password before setting this flag).

        Returns {"enabled": True/False, "reason": str}.
        """
        self.ensure_loaded()
        skill = self._skills.get(skill_name)
        if not skill:
            return {"enabled": False, "reason": "not_found"}

        # Scan gate: non-bundled skills must pass a scan before enabling
        if not skill.is_bundled and not force:
            scan = skill.manifest.security_scan
            if scan is None:
                return {
                    "enabled": False,
                    "reason": "scan_required",
                    "message": "Imported skills must pass a security scan before they can be enabled. Run a scan first.",
                }
            if not scan.passed:
                return {
                    "enabled": False,
                    "reason": "scan_failed",
                    "message": f"This skill failed its security scan (risk score: {scan.risk_score}). Review findings and address issues before enabling.",
                    "overridable": True,
                }

        if force and skill.manifest.security_scan and not skill.manifest.security_scan.passed:
            skill.scan_override = True
        if project_id in skill.disabled_projects:
            skill.disabled_projects.remove(project_id)
        self._save_skill_state()

        result: dict[str, Any] = {"enabled": True, "reason": "ok"}
        if force:
            result["override"] = True
            logger.warning(
                "Security override: skill '%s' force-enabled for project '%s' despite failed scan",
                skill_name,
                project_id,
            )
        return result

    def disable_for_project(self, skill_name: str, project_id: str) -> bool:
        """Disable a skill for a specific project (add to disabled list)."""
        self.ensure_loaded()
        skill = self._skills.get(skill_name)
        if not skill:
            return False
        if project_id not in skill.disabled_projects:
            skill.disabled_projects.append(project_id)
        self._save_skill_state()
        return True

    def delete(self, skill_name: str) -> dict[str, Any]:
        """Delete a skill from the registry and disk.

        Bundled skills cannot be deleted from disk (they live in the repo),
        but they can be removed from the registry. User-installed skills
        are deleted from data/skills/.

        Returns a dict with status info.
        """
        self.ensure_loaded()
        skill = self._skills.get(skill_name)
        if not skill:
            return {"deleted": False, "error": "not_found"}

        is_bundled = skill.is_bundled
        skill_dir = Path(skill.skill_dir)

        # Remove from registry
        del self._skills[skill_name]
        self._save_skill_state()

        # Delete from disk only if user-installed
        if not is_bundled and skill_dir.is_dir():
            import shutil
            shutil.rmtree(skill_dir, ignore_errors=True)
            logger.info("Deleted user skill '%s' from %s", skill_name, skill_dir)
            return {"deleted": True, "was_bundled": False}

        if is_bundled:
            logger.info(
                "Removed bundled skill '%s' from registry (files preserved in %s)",
                skill_name, skill_dir,
            )
            return {"deleted": True, "was_bundled": True, "note": "Bundled skill removed from registry. Files preserved on disk. Reload to restore."}

        return {"deleted": True, "was_bundled": is_bundled}

    def is_bundled_name(self, skill_name: str) -> bool:
        """Check if a name belongs to a bundled skill (collision protection)."""
        self.ensure_loaded()
        return skill_name in self._bundled_names

    def scan_summary(self) -> dict[str, Any]:
        """Return a summary of scan status across all skills."""
        self.ensure_loaded()
        summary: list[dict[str, Any]] = []
        counts = {"passed": 0, "failed": 0, "unscanned": 0, "total": 0}

        for skill in self._skills.values():
            counts["total"] += 1
            scan = skill.manifest.security_scan
            entry: dict[str, Any] = {
                "name": skill.name,
                "is_bundled": skill.is_bundled,
                "version": skill.manifest.version,
                "has_scripts": any(
                    Path(skill.skill_dir).rglob("*.py")
                ) or any(
                    Path(skill.skill_dir).rglob("*.js")
                ) or any(
                    Path(skill.skill_dir).rglob("*.sh")
                ),
            }
            if scan is None:
                entry["scan_status"] = "unscanned"
                entry["risk_score"] = None
                entry["findings_count"] = 0
                entry["scanned_at"] = None
                counts["unscanned"] += 1
            elif scan.passed:
                entry["scan_status"] = "passed"
                entry["risk_score"] = scan.risk_score
                entry["findings_count"] = len(scan.findings)
                entry["scanned_at"] = scan.scanned_at.isoformat() if scan.scanned_at else None
                counts["passed"] += 1
            else:
                entry["scan_status"] = "failed"
                entry["risk_score"] = scan.risk_score
                entry["findings_count"] = len(scan.findings)
                entry["scanned_at"] = scan.scanned_at.isoformat() if scan.scanned_at else None
                counts["failed"] += 1

            summary.append(entry)

        return {"skills": summary, "counts": counts}

    def names(self) -> list[str]:
        """Get all skill names."""
        self.ensure_loaded()
        return list(self._skills.keys())


# ── Singleton ────────────────────────────────────────────────────────────────

_registry: SkillRegistry | None = None


def get_skill_registry() -> SkillRegistry:
    """Get the global skill registry singleton."""
    global _registry
    if _registry is None:
        _registry = SkillRegistry()
        _registry.load()
    return _registry


def reload_skill_registry() -> SkillRegistry:
    """Force-reload the skill registry."""
    global _registry
    _registry = SkillRegistry()
    _registry.load()
    return _registry
