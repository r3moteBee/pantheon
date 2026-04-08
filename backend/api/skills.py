"""Skills API — list, enable/disable, reload, scan, and inspect skills.

IMPORTANT: Route ordering matters in FastAPI. Literal path routes (like
/skills/reload, /skills/scan/all) MUST be defined before parameterised
routes (like /skills/{skill_name}) to avoid the literal segments being
captured as a skill_name parameter.
"""
from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, UploadFile, File
from pydantic import BaseModel

from config import get_settings
from security_log import sec_log
from skills.registry import get_skill_registry, reload_skill_registry

logger = logging.getLogger(__name__)
router = APIRouter()


class SkillToggleRequest(BaseModel):
    project_id: str
    enabled: bool
    force_enable: bool = False
    override_password: str | None = None


# ═══════════════════════════════════════════════════════════════════════════
# LITERAL ROUTES (must come before {skill_name} parameterised routes)
# ═══════════════════════════════════════════════════════════════════════════

# ── List all skills ──────────────────────────────────────────────────────────

@router.get("/skills")
async def list_skills(
    project_id: str = Query(default=None, description="Project context for enabled-state computation"),
    include_disabled: bool = Query(default=True, description="If false and project_id is set, hide skills disabled for that project"),
) -> dict[str, Any]:
    """List registered skills.

    By default returns ALL skills regardless of per-project disabled state so
    the UI can render enabled/disabled toggles. Pass include_disabled=false
    along with a project_id to filter to only the skills enabled for that
    project (e.g. when listing skills for the resolver).
    """
    registry = get_skill_registry()

    if project_id and not include_disabled:
        skills = registry.list_for_project(project_id)
    else:
        skills = registry.list_all()

    return {
        "skills": [s.to_summary() for s in skills],
        "count": len(skills),
    }


# ── Reload the skill registry ───────────────────────────────────────────────

@router.post("/skills/reload")
async def reload_skills() -> dict[str, Any]:
    """Force-reload all skills from disk."""
    registry = reload_skill_registry()
    return {
        "status": "reloaded",
        "count": len(registry.list_all()),
        "skills": registry.names(),
    }


# ── Scan all / scan summary ─────────────────────────────────────────────────

@router.post("/skills/scan/all")
async def scan_all_skills(
    ai_review: bool = Query(default=False, description="Run AI review on each skill (slow for many skills)"),
) -> dict[str, Any]:
    """Run the security scanner on ALL registered skills.

    Returns a summary of results. AI review is off by default for bulk
    scans to keep it fast — use per-skill scan for deep analysis.
    """
    from skills.scanner import scan_skill

    registry = get_skill_registry()
    all_skills = registry.list_all()
    results: list[dict[str, Any]] = []

    for skill in all_skills:
        skill_dir = Path(skill.skill_dir)
        try:
            result = await scan_skill(
                skill_dir=skill_dir,
                manifest=skill.manifest,
                instructions=skill.instructions,
                run_ai_review=ai_review,
            )
            skill.manifest.security_scan = result
            registry.save_scan_result(skill.name, result)
            results.append({
                "name": skill.name,
                "passed": result.passed,
                "risk_score": result.risk_score,
                "findings_count": len(result.findings),
            })
        except Exception as e:
            logger.error("Scan failed for '%s': %s", skill.name, e)
            results.append({
                "name": skill.name,
                "passed": None,
                "error": str(e),
            })

    passed = sum(1 for r in results if r.get("passed") is True)
    failed = sum(1 for r in results if r.get("passed") is False)
    errored = sum(1 for r in results if r.get("passed") is None)

    sec_log.skill_scan_all(passed=passed, failed=failed, errors=errored)
    return {
        "scanned": len(results),
        "passed": passed,
        "failed": failed,
        "errors": errored,
        "results": results,
    }


@router.get("/skills/scan/summary")
async def scan_summary() -> dict[str, Any]:
    """Get a summary of scan status across all skills (for the dashboard)."""
    registry = get_skill_registry()
    return registry.scan_summary()


# ── Discovery settings ──────────────────────────────────────────────────────

@router.get("/skills/discovery/{project_id}")
async def get_skill_discovery(project_id: str) -> dict[str, str]:
    """Get the skill discovery mode for a project."""
    from secrets.vault import get_vault
    vault = get_vault()
    mode = vault.get_secret(f"skill_discovery_{project_id}") or "off"
    return {"project_id": project_id, "skill_discovery": mode}


@router.put("/skills/discovery/{project_id}")
async def set_skill_discovery(project_id: str, mode: str = Query(...)) -> dict[str, str]:
    """Set the skill discovery mode for a project (off / suggest / auto)."""
    if mode not in ("off", "suggest", "auto"):
        raise HTTPException(status_code=400, detail="Mode must be 'off', 'suggest', or 'auto'")
    from secrets.vault import get_vault
    vault = get_vault()
    vault.set_secret(f"skill_discovery_{project_id}", mode)
    logger.info("Skill discovery for project '%s' set to '%s'", project_id, mode)
    return {"project_id": project_id, "skill_discovery": mode}


# ── Quarantine list ──────────────────────────────────────────────────────────

@router.get("/skills/quarantine/list")
async def list_quarantined() -> dict[str, Any]:
    """List skills in the quarantine directory."""
    settings = get_settings()
    quarantine_dir = settings.data_dir / "skills" / ".quarantine"
    if not quarantine_dir.is_dir():
        return {"quarantined": [], "count": 0}

    quarantined = []
    for d in sorted(quarantine_dir.iterdir()):
        if d.is_dir():
            manifest_path = d / "skill.json"
            info: dict[str, Any] = {"name": d.name, "path": str(d)}
            if manifest_path.exists():
                try:
                    import json
                    raw = json.loads(manifest_path.read_text())
                    info["description"] = raw.get("description", "")
                    info["version"] = raw.get("version", "")
                except Exception:
                    pass
            quarantined.append(info)

    return {"quarantined": quarantined, "count": len(quarantined)}


# ── Security override status ────────────────────────────────────────────────

@router.get("/skills/security/override-status")
async def override_status() -> dict[str, Any]:
    """Check whether a security override password is configured."""
    from secrets.vault import get_vault
    vault = get_vault()
    pw = vault.get_secret("skill_security_override_password")
    return {"configured": bool(pw)}


# ── Hub import endpoints ────────────────────────────────────────────────────

@router.get("/skills/hubs")
async def list_hubs() -> dict[str, Any]:
    """List available import hub sources."""
    from skills.importer import list_hubs as _list_hubs
    return {"hubs": _list_hubs()}


@router.post("/skills/search-hub")
async def search_hub(
    query: str = Query(..., description="Search query"),
    hub: str | None = Query(default=None, description="Specific hub to search (or all)"),
) -> dict[str, Any]:
    """Search hubs for importable skills."""
    from skills.importer import search_hubs
    results = await search_hubs(query, hub=hub)
    return {
        "results": [r.model_dump() for r in results],
        "count": len(results),
        "query": query,
        "hub": hub or "all",
    }


class SkillImportRequest(BaseModel):
    source: str
    hub: str = "local"
    ai_review: bool = True


@router.post("/skills/import")
async def import_skill_endpoint(req: SkillImportRequest) -> dict[str, Any]:
    """Import a skill from a hub URL, GitHub repo, or local path.

    Body:
        source: URL, repo identifier, or local file path
        hub: "github" | "local" | "skill_md"
        ai_review: Whether to run AI review during scan (default true)
    """
    from skills.importer import import_skill
    result = await import_skill(
        source=req.source,
        hub=req.hub,
        run_scan=True,
        ai_review=req.ai_review,
    )

    # Log the import
    if result.success:
        sec_log.skill_scan_passed(
            skill=result.skill_name,
            risk=result.scan_risk or 0.0,
            findings=result.scan_findings,
        ) if result.scan_passed else None
        logger.info(
            "Skill imported: %s from %s (hub=%s, quarantined=%s)",
            result.skill_name, result.source, req.hub, result.quarantined,
        )
    else:
        logger.warning("Skill import failed: %s from %s", result.message, result.source)

    return result.model_dump()


@router.post("/skills/import/upload")
async def import_upload(
    file: UploadFile = File(...),
    ai_review: bool = Query(default=True),
) -> dict[str, Any]:
    """Import a skill from an uploaded archive file (.tar.gz, .zip)."""
    import tempfile

    if file is None:
        raise HTTPException(status_code=400, detail="No file uploaded")

    # Save uploaded file to temp location
    suffix = Path(file.filename).suffix if file.filename else ".zip"
    name = file.filename or "upload"
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        suffix = ".tar.gz"

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix, prefix="skill_upload_") as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = tmp.name

    try:
        from skills.importer import import_skill
        result = await import_skill(
            source=tmp_path,
            hub="local",
            run_scan=True,
            ai_review=ai_review,
        )
        if result.success:
            logger.info("Skill uploaded and imported: %s", result.skill_name)
        return result.model_dump()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ═══════════════════════════════════════════════════════════════════════════
# PARAMETERISED ROUTES (/skills/{skill_name}/...)
# ═══════════════════════════════════════════════════════════════════════════

# ── Get a single skill ──────────────────────────────────────────────────────

@router.get("/skills/{skill_name}")
async def get_skill(skill_name: str) -> dict[str, Any]:
    """Get full details for a skill including instructions."""
    registry = get_skill_registry()
    skill = registry.get(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    summary = skill.to_summary()
    summary["instructions"] = skill.instructions
    summary["skill_dir"] = skill.skill_dir
    summary["parameters"] = [p.model_dump() for p in skill.manifest.parameters]
    summary["capabilities_required"] = skill.manifest.capabilities_required
    summary["pantheon"] = skill.manifest.pantheon.model_dump()
    return summary


# ── Enable / disable a skill for a project ───────────────────────────────────

@router.put("/skills/{skill_name}/toggle")
async def toggle_skill(skill_name: str, req: SkillToggleRequest) -> dict[str, Any]:
    """Enable or disable a skill for a specific project.

    Non-bundled skills require a passing security scan before they can be
    enabled. If the scan gate blocks enabling, the response includes the
    reason and a 403 status.
    """
    registry = get_skill_registry()
    skill = registry.get(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    if req.enabled:
        force = False
        if req.force_enable:
            # Verify security override password from vault
            from secrets.vault import get_vault
            vault = get_vault()
            stored_pw = vault.get_secret("skill_security_override_password")
            if not stored_pw:
                raise HTTPException(
                    status_code=403,
                    detail="No security override password has been configured. Set one in Settings before using force enable.",
                )
            if not req.override_password or req.override_password != stored_pw:
                sec_log.skill_override_failed(skill=skill_name, reason="bad_password")
                raise HTTPException(
                    status_code=403,
                    detail="Incorrect security override password.",
                )
            force = True

        result = registry.enable_for_project(skill_name, req.project_id, force=force)
        if not result["enabled"]:
            raise HTTPException(
                status_code=403,
                detail=result.get("message", result.get("reason", "Cannot enable")),
            )
        if force:
            sec_log.skill_override_used(skill=skill_name, project=req.project_id)
        else:
            sec_log.skill_enabled(skill=skill_name, project=req.project_id)
    else:
        registry.disable_for_project(skill_name, req.project_id)
        sec_log.skill_disabled(skill=skill_name, project=req.project_id)

    return {
        "skill": skill_name,
        "project_id": req.project_id,
        "enabled": req.enabled,
    }


# ── Delete a skill ──────────────────────────────────────────────────────────

@router.delete("/skills/{skill_name}")
async def delete_skill(skill_name: str) -> dict[str, Any]:
    """Delete a skill from the registry and (for user-installed) from disk.

    Bundled skills are removed from the registry but preserved on disk.
    Reloading the registry will restore them.
    """
    registry = get_skill_registry()
    skill = registry.get(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    is_bundled = skill.is_bundled
    result = registry.delete(skill_name)
    if not result.get("deleted"):
        raise HTTPException(status_code=500, detail=result.get("error", "Delete failed"))

    sec_log.skill_deleted(skill=skill_name, is_bundled=is_bundled)
    logger.info("Deleted skill '%s': %s", skill_name, result)
    return {"skill": skill_name, **result}


# ── Per-skill scanning ──────────────────────────────────────────────────────

@router.post("/skills/{skill_name}/scan")
async def scan_skill_endpoint(
    skill_name: str,
    ai_review: bool = Query(default=True, description="Run AI review (Layer 3) — slower but deeper"),
) -> dict[str, Any]:
    """Run the security scanner on a skill and store the result."""
    from skills.scanner import scan_skill

    registry = get_skill_registry()
    skill = registry.get(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    skill_dir = Path(skill.skill_dir)
    result = await scan_skill(
        skill_dir=skill_dir,
        manifest=skill.manifest,
        instructions=skill.instructions,
        run_ai_review=ai_review,
    )

    # Persist the scan result in memory and on disk
    skill.manifest.security_scan = result
    registry.save_scan_result(skill_name, result)

    # Log scan result
    n_findings = len(result.findings)
    if result.passed:
        sec_log.skill_scan_passed(skill=skill_name, risk=result.risk_score, findings=n_findings)
    else:
        sec_log.skill_scan_failed(skill=skill_name, risk=result.risk_score, findings=n_findings)

    # If the scan failed, move to quarantine (non-bundled only)
    if not result.passed and not skill.is_bundled:
        quarantine_result = _quarantine_skill(skill_name, reason="scan_failed")
        sec_log.skill_quarantined(skill=skill_name, reason="scan_failed")
        return {
            "skill": skill_name,
            "scan": result.model_dump(),
            "quarantined": quarantine_result.get("quarantined", False),
        }

    return {
        "skill": skill_name,
        "scan": result.model_dump(),
        "quarantined": False,
    }


@router.get("/skills/{skill_name}/scan")
async def get_scan_result(skill_name: str) -> dict[str, Any]:
    """Get the most recent scan result for a skill."""
    registry = get_skill_registry()
    skill = registry.get(skill_name)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

    if not skill.manifest.security_scan:
        return {"skill": skill_name, "scan": None, "message": "No scan has been run yet"}

    return {
        "skill": skill_name,
        "scan": skill.manifest.security_scan.model_dump(),
    }


# ── Per-skill quarantine ────────────────────────────────────────────────────

def _quarantine_skill(skill_name: str, reason: str = "") -> dict[str, Any]:
    """Move a user-installed skill to the quarantine directory.

    Bundled skills cannot be quarantined (they live in the repo); instead
    they are flagged only.
    """
    settings = get_settings()
    registry = get_skill_registry()
    skill = registry.get(skill_name)
    if not skill:
        return {"quarantined": False, "error": "not_found"}

    if skill.is_bundled:
        logger.warning(
            "Bundled skill '%s' failed scan (reason: %s) — cannot quarantine, flagging only",
            skill_name, reason,
        )
        return {
            "quarantined": False,
            "bundled": True,
            "message": "Bundled skill flagged but not quarantined (files are in the repo)",
        }

    # Move user-installed skill to quarantine
    skill_dir = Path(skill.skill_dir)
    quarantine_dir = settings.data_dir / "skills" / ".quarantine"
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    dest = quarantine_dir / skill_name

    try:
        if dest.exists():
            shutil.rmtree(dest)
        shutil.move(str(skill_dir), str(dest))
        registry.delete(skill_name)
        logger.info("Quarantined skill '%s' → %s (reason: %s)", skill_name, dest, reason)
        return {"quarantined": True, "path": str(dest), "reason": reason}
    except Exception as e:
        logger.error("Failed to quarantine '%s': %s", skill_name, e)
        return {"quarantined": False, "error": str(e)}


@router.post("/skills/{skill_name}/quarantine")
async def quarantine_skill_endpoint(skill_name: str) -> dict[str, Any]:
    """Manually quarantine a skill."""
    result = _quarantine_skill(skill_name, reason="manual")
    if result.get("error") == "not_found":
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")
    if result.get("quarantined"):
        sec_log.skill_quarantined(skill=skill_name, reason="manual")
    return {"skill": skill_name, **result}


@router.post("/skills/{skill_name}/unquarantine")
async def unquarantine_skill(skill_name: str) -> dict[str, Any]:
    """Restore a skill from quarantine back to user-installed skills."""
    settings = get_settings()
    quarantine_dir = settings.data_dir / "skills" / ".quarantine"
    quarantined_path = quarantine_dir / skill_name

    if not quarantined_path.is_dir():
        raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not in quarantine")

    # Block restoration if name collides with a bundled skill
    registry = get_skill_registry()
    if registry.is_bundled_name(skill_name):
        raise HTTPException(
            status_code=409,
            detail=f"Cannot restore '{skill_name}' — a bundled skill with that name exists",
        )

    user_skills_dir = settings.data_dir / "skills"
    dest = user_skills_dir / skill_name
    if dest.exists():
        raise HTTPException(
            status_code=409,
            detail=f"A skill named '{skill_name}' already exists — remove it first",
        )

    try:
        shutil.move(str(quarantined_path), str(dest))
        reload_skill_registry()
        sec_log.skill_unquarantined(skill=skill_name)
        logger.info("Restored skill '%s' from quarantine", skill_name)
        return {"skill": skill_name, "restored": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to restore: {e}")
