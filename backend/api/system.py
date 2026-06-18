"""System diagnostics — sandbox health, runtime info."""
from __future__ import annotations
import logging
from typing import Any

from fastapi import APIRouter

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/system/sandbox")
async def sandbox_health() -> dict[str, Any]:
    """Return sandbox backend health for the Settings page."""
    from sandbox import get_sandbox
    sb = get_sandbox()
    info = await sb.health()
    info.setdefault("backend", sb.name)
    return info


@router.get("/system/update/check")
async def check_update() -> dict[str, Any]:
    """Check for new commits in the upstream git repository."""
    import subprocess
    from pathlib import Path
    from config import get_settings

    settings = get_settings()
    auth_enabled = bool(settings.auth_password)

    repo_root = Path(__file__).resolve().parent.parent.parent
    if not (repo_root / ".git").is_dir():
        return {
            "update_available": False,
            "message": "Not running in a Git repository.",
            "commits": [],
            "auth_enabled": auth_enabled
        }

    try:
        # Fetch latest changes from remote (timeout 15s)
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=str(repo_root),
            check=True,
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        # Check if local branch tracks an upstream branch
        proc_track = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "@{u}"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5
        )
        if proc_track.returncode != 0:
            return {
                "update_available": False,
                "message": "No upstream tracking branch configured.",
                "commits": [],
                "auth_enabled": auth_enabled
            }

        # Get list of commits behind upstream
        proc_log = subprocess.run(
            ["git", "log", "HEAD..@{u}", "--oneline"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5
        )

        if proc_log.returncode != 0:
            return {
                "update_available": False,
                "message": f"Failed to check git logs: {proc_log.stderr}",
                "commits": [],
                "auth_enabled": auth_enabled
            }

        commits = [line.strip() for line in proc_log.stdout.splitlines() if line.strip()]
        return {
            "update_available": len(commits) > 0,
            "message": f"{len(commits)} commits behind upstream." if commits else "Already up to date.",
            "commits": commits,
            "auth_enabled": auth_enabled
        }

    except subprocess.TimeoutExpired:
        return {
            "update_available": False,
            "message": "Git fetch connection timed out.",
            "commits": [],
            "auth_enabled": auth_enabled
        }
    except Exception as e:
        return {
            "update_available": False,
            "message": f"Error checking for updates: {str(e)}",
            "commits": [],
            "auth_enabled": auth_enabled
        }


@router.post("/system/update/execute")
async def execute_update(payload: dict[str, Any]) -> dict[str, Any]:
    """Execute update by pulling git changes and rebuilding the app."""
    import os
    import subprocess
    import hmac
    from pathlib import Path
    from fastapi import HTTPException
    from config import get_settings
    from api.auth import compute_token

    settings = get_settings()

    # 1. Password/Confirmation Gate
    if settings.auth_password:
        password = payload.get("password", "")
        expected = compute_token(settings.auth_password, settings.secret_key)
        given = compute_token(password, settings.secret_key)
        if not hmac.compare_digest(given, expected):
            raise HTTPException(status_code=401, detail="Invalid password.")
    else:
        if not payload.get("confirm", False):
            raise HTTPException(status_code=400, detail="Confirmation required.")

    # 2. Mode detection
    repo_root = Path(__file__).resolve().parent.parent.parent
    is_docker = os.path.exists("/.dockerenv") or not (repo_root / ".git").is_dir()

    if is_docker:
        return {
            "success": False,
            "mode": "docker",
            "message": (
                "Pantheon is running inside a Docker container. In container mode, code updates "
                "cannot be executed inline. Please run the following commands on your host terminal:\n\n"
                "  cd ~/pantheon\n"
                "  git pull\n"
                "  make build\n"
                "  make up"
            )
        }

    # 3. Executing Local update
    update_script = repo_root / "update.sh"
    if not update_script.exists():
        raise HTTPException(status_code=500, detail="Update script update.sh not found.")

    try:
        # Spawn updater script in a detached background session so it outlives parent process shutdown
        subprocess.Popen(
            ["/usr/bin/env", "bash", str(update_script)],
            cwd=str(repo_root),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True
        )
        return {
            "success": True,
            "mode": "local",
            "message": "Update initiated successfully. The application will fetch changes, install dependencies, rebuild the frontend, and restart within 15-30 seconds."
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to start update script: {str(e)}")


@router.get("/system/self-doc")
async def system_self_doc() -> dict[str, Any]:
    """Retrieve self-documentation information."""
    from utils.self_doc import generate_self_doc
    return {"documentation": generate_self_doc()}

