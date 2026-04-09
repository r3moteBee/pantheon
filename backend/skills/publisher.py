"""Skill publisher — contribute a local skill back to a configured registry hub.

The contribution flow:

1. Validate the skill (scanner must have passed).
2. Package the skill as a portable tar.gz (via exporter).
3. POST the archive to the registry's `/skills/submit` endpoint (if the
   registry supports submission — many are read-only mirrors).
4. If the registry does not support submission, stage the archive under
   `data_dir/skill_submissions/{registry_id}/{skill}-{ts}.tar.gz` and
   return the local path so the user can upload manually.

This keeps the publishing surface honest: we try the API first, fall back
to a staged file with clear next steps, and never silently drop a contribution.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from config import get_settings
from skills.exporter import export_skill_targz
from skills.registries_config import list_registries

logger = logging.getLogger(__name__)
settings = get_settings()


def _staging_dir(registry_id: str) -> Path:
    return settings.data_dir / "skill_submissions" / registry_id


def _stage_archive(registry_id: str, skill_name: str, archive: bytes) -> Path:
    d = _staging_dir(registry_id)
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = d / f"{skill_name}-{ts}.tar.gz"
    path.write_bytes(archive)
    return path


def _registry_submit_url(registry_url: str) -> str:
    return registry_url.rstrip("/") + "/skills/submit"


async def publish_skill(
    skill_name: str,
    registry_id: str,
    *,
    note: str = "",
) -> dict[str, Any]:
    """Publish a user skill to a configured registry hub.

    Returns {"status": "submitted"|"staged", ...details}.
    """
    # Validation: scanner must have passed, if there's a scan result
    from skills.registry import get_skill_registry
    registry = get_skill_registry()
    skill = registry.get(skill_name)
    if not skill:
        raise FileNotFoundError(f"Skill not found: {skill_name}")
    if skill.is_bundled:
        raise PermissionError("Cannot publish bundled skills")
    scan = skill.manifest.security_scan
    if scan and not scan.passed:
        raise PermissionError(
            "Skill has not passed the security scanner — resolve findings before publishing"
        )

    # Look up registry config (may be redacted — we'll load the token separately)
    all_regs = list_registries()
    reg_cfg = next((r for r in all_regs if r.get("id") == registry_id), None)
    if not reg_cfg:
        raise ValueError(f"Registry '{registry_id}' not configured")

    # Package
    archive = export_skill_targz(skill_name)
    filename = f"{skill_name}.tar.gz"

    # Resolve auth token (vault-backed if present)
    headers: dict[str, str] = {"User-Agent": "pantheon-publisher/1.0"}
    try:
        from secrets.vault import get_vault
        token = get_vault().get_secret(f"skill_registry:{registry_id}")
        if token:
            headers["Authorization"] = f"Bearer {token}"
    except Exception as e:
        logger.warning("Could not load publish token for %s: %s", registry_id, e)

    url = _registry_submit_url(reg_cfg.get("url", ""))

    # Attempt live upload; fall back to staging
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            files = {"archive": (filename, archive, "application/gzip")}
            data = {"skill": skill_name, "note": note}
            resp = await client.post(url, headers=headers, files=files, data=data)
        if resp.status_code in (200, 201, 202):
            return {
                "status": "submitted",
                "registry": registry_id,
                "skill": skill_name,
                "response": resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {"text": resp.text[:500]},
                "archive_size": len(archive),
            }
        logger.info(
            "Registry %s rejected live submission (%s); staging archive locally",
            registry_id, resp.status_code,
        )
        staged = _stage_archive(registry_id, skill_name, archive)
        return {
            "status": "staged",
            "reason": f"registry returned {resp.status_code}",
            "registry": registry_id,
            "skill": skill_name,
            "staged_path": str(staged),
            "archive_size": len(archive),
            "next_steps": "Registry does not accept direct submissions. Upload the staged archive via the registry's web UI or share the file directly.",
        }
    except Exception as e:
        logger.info("Live submission to %s failed (%s); staging archive", registry_id, e)
        staged = _stage_archive(registry_id, skill_name, archive)
        return {
            "status": "staged",
            "reason": f"submission error: {e}",
            "registry": registry_id,
            "skill": skill_name,
            "staged_path": str(staged),
            "archive_size": len(archive),
            "next_steps": "Could not reach registry; archive saved locally. Retry when the registry is reachable, or upload manually.",
        }
