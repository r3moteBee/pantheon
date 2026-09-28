"""Workspace and repo-checkout helpers shared by the file, code and git tools."""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from utils.paths import check_project_id, is_within
import logging
from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

def _get_workspace_base(project_id: str | None = None) -> Path:
    if project_id and project_id != "default":
        path = settings.projects_dir / check_project_id(project_id) / "workspace"
    else:
        path = settings.workspace_dir
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _safe_workspace_path(rel_path: str, project_id: str | None = None) -> Path:
    """Resolve path safely within workspace to prevent path traversal."""
    base = _get_workspace_base(project_id)
    target = (base / rel_path).resolve()
    if not is_within(target, base):
        raise ValueError(f"Path traversal denied: {rel_path}")
    return target


_PRECOMMIT_MARKER = "# pantheon-conflict-guard"


_PRECOMMIT_HOOK = f"""#!/bin/sh
{_PRECOMMIT_MARKER}
# Installed by Pantheon's git_sync_repo. Rejects unresolved merge-conflict
# markers in the files being committed, regardless of which tool invokes
# the commit (git_commit tool, run_command, or a human in the checkout).
# Scoped to staged files only so pre-existing content that legitimately
# documents conflict markers doesn't block unrelated commits.
files=$(git diff --cached --name-only --diff-filter=ACM)
[ -z "$files" ] && exit 0
hits=$(printf '%s\\n' "$files" | tr '\\n' '\\0' | xargs -0 git grep --cached -nE '^(<{{7}}|>{{7}})( |$)' -- 2>/dev/null)
if [ -n "$hits" ]; then
  echo "pantheon pre-commit: refusing to commit unresolved merge-conflict markers:" >&2
  echo "$hits" >&2
  echo "Resolve the conflicts first (keep the right code, delete the <<<<<<</=======/>>>>>>> marker lines), then commit again." >&2
  exit 1
fi
exit 0
"""


def _install_precommit_hook(repo_dir: Path) -> str:
    """Install (or refresh) the conflict-marker pre-commit hook.

    Returns a short status string for the tool result. Never clobbers a
    hook Pantheon didn't write."""
    try:
        hooks = repo_dir / ".git" / "hooks"
        hook = hooks / "pre-commit"
        if hook.exists() and _PRECOMMIT_MARKER not in hook.read_text(errors="replace"):
            return "existing non-Pantheon pre-commit hook left untouched"
        hooks.mkdir(parents=True, exist_ok=True)
        hook.write_text(_PRECOMMIT_HOOK)
        hook.chmod(0o755)
        return "conflict-marker pre-commit guard installed"
    except Exception as e:
        logger.debug("pre-commit hook install failed: %s", e)
        return f"pre-commit hook install failed: {e}"


def _repo_checkout_dir(project_id: str | None, owner: str, repo: str) -> Path:
    """Canonical location of a repo's local checkout inside the workspace."""
    return _get_workspace_base(project_id) / "repos" / f"{owner}__{repo}"


def _resolve_repo_checkout(project_id: str | None) -> Path | None:
    """Path of the bound repo's local checkout, or None if the project has
    no repo binding or git_sync_repo hasn't cloned it yet."""
    try:
        from api.connections import get_project_repo_for_tools
        spec = get_project_repo_for_tools(project_id or "default")
    except Exception:
        return None
    if not spec:
        return None
    d = _repo_checkout_dir(project_id, spec["owner"], spec["repo"])
    return d if (d / ".git").exists() else None


def _git_auth_env(token: str | None) -> dict[str, str] | None:
    """Env that authenticates git to github.com without putting the token
    in a URL (which git records in .git/config and exposes via ps)."""
    if not token:
        return None
    import base64
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = dict(os.environ)
    env.update({
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "http.https://github.com/.extraheader",
        "GIT_CONFIG_VALUE_0": f"Authorization: Basic {basic}",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return env


_GIT_TIMEOUT_S = 600


def _scrub_git_config(checkout: Path) -> None:
    """Strip credentials that older versions pushed into .git/config
    (``git push -u https://TOKEN@github.com/...`` records the URL)."""
    import re as _re
    cfg = checkout / ".git" / "config"
    try:
        text = cfg.read_text(encoding="utf-8")
    except OSError:
        return
    cleaned = _re.sub(r"https://[^/@\s]+@github\.com/", "https://github.com/", text)
    if cleaned != text:
        cfg.write_text(cleaned, encoding="utf-8")


async def _run_git_cmd(
    args: list[str], cwd: Path, auto_init: bool = True,
    env: dict[str, str] | None = None,
) -> tuple[int, str, str]:
    """Execute a git command in the specified workspace directory, initializing it first if needed."""
    if auto_init and not (cwd / ".git").exists():
        # Initialize repository
        p_init = await asyncio.create_subprocess_exec(
            "git", "init",
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await p_init.communicate()
        # Set agent config
        p_email = await asyncio.create_subprocess_exec(
            "git", "config", "user.email", "agent@pantheon.local",
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await p_email.communicate()
        p_name = await asyncio.create_subprocess_exec(
            "git", "config", "user.name", "Pantheon Agent",
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await p_name.communicate()

    proc = await asyncio.create_subprocess_exec(
        "git", *args,
        cwd=str(cwd),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=_GIT_TIMEOUT_S)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return 124, "", f"git {args[0]} timed out after {_GIT_TIMEOUT_S}s"
    except BaseException:
        # Job cancelled — don't leave git running.
        if proc.returncode is None:
            proc.kill()
        raise
    return proc.returncode or 0, stdout.decode(errors="replace").strip(), stderr.decode(errors="replace").strip()
