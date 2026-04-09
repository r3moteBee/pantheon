"""Pydantic models for the skills system.

Mirrors the two-layer skill.json manifest: standard top-level fields
(compatible with external hubs) plus an optional `pantheon` block for
Pantheon-specific extensions.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ── Enums ────────────────────────────────────────────────────────────────────

class MemoryAccess(str, Enum):
    none = "none"
    read = "r"
    write = "w"
    readwrite = "rw"


class SkillDiscoveryMode(str, Enum):
    off = "off"
    suggest = "suggest"
    auto = "auto"


class EvolutionMode(str, Enum):
    propose = "propose"
    auto_minor = "auto_minor"
    auto = "auto"


class ScanSeverity(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


# ── Skill Parameter ──────────────────────────────────────────────────────────

class SkillParameter(BaseModel):
    name: str
    type: str = "string"
    required: bool = False
    description: str = ""
    default: Any = None


# ── Pantheon Extensions ──────────────────────────────────────────────────────

class MemoryConfig(BaseModel):
    reads: list[str] = Field(default_factory=list)
    writes: list[str] = Field(default_factory=list)
    auto_store: bool = False


class SchedulableConfig(BaseModel):
    enabled: bool = False
    default_cron: str | None = None
    description: str = ""


class MemoryTierPermissions(BaseModel):
    semantic: MemoryAccess = MemoryAccess.none
    episodic: MemoryAccess = MemoryAccess.none
    graph: MemoryAccess = MemoryAccess.none


class PermissionsConfig(BaseModel):
    network_domains: list[str] = Field(default_factory=list)
    file_paths: list[str] = Field(default_factory=list)
    vault_secrets: list[str] = Field(default_factory=list)
    memory_tiers: MemoryTierPermissions = Field(default_factory=MemoryTierPermissions)


class TelemetryConfig(BaseModel):
    track_usage: bool = True
    auto_disable_threshold: int = 5


class EvolutionConfig(BaseModel):
    enabled: bool = False
    locked: bool = False
    mode: EvolutionMode = EvolutionMode.propose


class PantheonExtensions(BaseModel):
    """Pantheon-specific skill extensions (the `pantheon` block in skill.json)."""
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    project_aware: bool = False
    schedulable: SchedulableConfig = Field(default_factory=SchedulableConfig)
    permissions: PermissionsConfig = Field(default_factory=PermissionsConfig)
    telemetry: TelemetryConfig = Field(default_factory=TelemetryConfig)
    evolution: EvolutionConfig = Field(default_factory=EvolutionConfig)


# ── Security Scan Result ─────────────────────────────────────────────────────

class ScanFinding(BaseModel):
    severity: ScanSeverity = ScanSeverity.info
    category: str = ""
    message: str = ""
    line: int | None = None
    file: str | None = None


class ScanResult(BaseModel):
    """Result of a security scan on a skill."""
    passed: bool = True
    scanned_at: datetime | None = None
    scanner_version: str = "1.0"
    findings: list[ScanFinding] = Field(default_factory=list)
    risk_score: float = 0.0  # 0.0 = safe, 1.0 = dangerous


# ── Skill Manifest (top-level) ───────────────────────────────────────────────

class SkillManifest(BaseModel):
    """Full skill manifest — models the skill.json file."""

    # Required
    name: str
    description: str = ""

    # Standard fields (compatible with external hubs)
    version: str = ""
    author: str = ""
    license: str = ""
    triggers: list[str] = Field(default_factory=list)
    chains: list[str] = Field(default_factory=list)  # Skill names to auto-invoke alongside
    parameters: list[SkillParameter] = Field(default_factory=list)
    capabilities_required: list[str] = Field(default_factory=list)
    dependencies: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    source_hub: str | None = None
    security_scan: ScanResult | None = None

    # Pantheon-specific extensions
    pantheon: PantheonExtensions = Field(default_factory=PantheonExtensions)

    class Config:
        extra = "allow"  # Allow unknown fields from external skill formats


# ── Runtime Skill (loaded skill with resolved paths) ─────────────────────────

class LoadedSkill(BaseModel):
    """A skill loaded into the registry with its resolved paths and instructions."""
    manifest: SkillManifest
    instructions: str = ""
    skill_dir: str = ""
    is_bundled: bool = False
    enabled_projects: list[str] = Field(default_factory=list)
    disabled_projects: list[str] = Field(default_factory=list)

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def triggers(self) -> list[str]:
        return self.manifest.triggers

    @property
    def tags(self) -> list[str]:
        return self.manifest.tags

    def is_enabled_for(self, project_id: str) -> bool:
        """Check if this skill is enabled for a given project.

        Logic: enabled by default for all projects unless explicitly disabled.
        """
        return project_id not in self.disabled_projects

    def to_summary(self) -> dict[str, Any]:
        """Return a lightweight summary for API responses."""
        return {
            "name": self.manifest.name,
            "description": self.manifest.description,
            "version": self.manifest.version,
            "author": self.manifest.author,
            "tags": self.manifest.tags,
            "triggers": self.manifest.triggers,
            "is_bundled": self.is_bundled,
            "disabled_projects": self.disabled_projects,
            "schedulable": self.manifest.pantheon.schedulable.enabled,
            "project_aware": self.manifest.pantheon.project_aware,
            "memory_reads": self.manifest.pantheon.memory.reads,
            "memory_writes": self.manifest.pantheon.memory.writes,
            "evolution_enabled": self.manifest.pantheon.evolution.enabled,
            "scan_result": self.manifest.security_scan.model_dump() if self.manifest.security_scan else None,
        }


# ── Project-level skill settings ────────────────────────────────────────────

class ProjectSkillSettings(BaseModel):
    """Per-project skill settings (stored in projects.json)."""
    skill_discovery: SkillDiscoveryMode = SkillDiscoveryMode.off
    enabled_skills: list[str] = Field(default_factory=list)
    disabled_skills: list[str] = Field(default_factory=list)
