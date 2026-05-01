"""SubprocessSandbox — local subprocess with timeout/memory limits.

Wraps the existing skills/executor.execute_script logic for declared
skills, and adds an inline path for ad-hoc agent code execution.
"""
from __future__ import annotations
import asyncio
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

from sandbox.backend import (
    SandboxBackend,
    SandboxConfig,
    SandboxResult,
    language_to_default_filename,
    language_to_interpreter,
)

logger = logging.getLogger(__name__)


class SubprocessSandbox(SandboxBackend):
    name = "subprocess"

    async def execute_skill(
        self,
        skill: Any,
        script_name: str,
        *,
        args: list[str] | None = None,
        input_data: str | None = None,
        timeout: int = 30,
        workspace_dir: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        # Delegate to the legacy executor — preserves all existing
        # permission checks, env filtering, security logging.
        from skills.executor import execute_script
        return await execute_script(
            skill=skill,
            script_name=script_name,
            args=args,
            input_data=input_data,
            timeout=timeout,
            workspace_dir=workspace_dir,
            extra_env=extra_env,
        )

    async def execute_inline(
        self,
        language: str,
        code: str,
        *,
        filename: str | None = None,
        config: SandboxConfig | None = None,
    ) -> SandboxResult:
        cfg = config or SandboxConfig()
        filename = filename or language_to_default_filename(language)
        interp = language_to_interpreter(language)

        tmpdir = tempfile.mkdtemp(prefix="pantheon_inline_")
        try:
            script_path = Path(tmpdir) / filename
            script_path.parent.mkdir(parents=True, exist_ok=True)
            script_path.write_text(code)

            cmd = interp + [str(script_path)]

            # Filtered env
            allowed_keys = {"PATH", "HOME", "USER", "LANG", "LC_ALL",
                            "PYTHONPATH", "NODE_PATH", "TERM"}
            env: dict[str, str] = {k: v for k, v in os.environ.items()
                                   if k in allowed_keys}
            if cfg.workspace_dir:
                env["WORKSPACE_DIR"] = str(cfg.workspace_dir)
            env.update(cfg.extra_env or {})

            # Resource limits via ulimit prefix (Linux only; harmless on macOS)
            mem_kb = cfg.max_memory_mb * 1024
            quoted_cmd = " ".join(_shquote(c) for c in cmd)
            shell_cmd = f"ulimit -v {mem_kb} 2>/dev/null; exec {quoted_cmd}"

            cwd = str(cfg.workspace_dir) if cfg.workspace_dir else tmpdir

            start = time.monotonic()
            try:
                proc = await asyncio.create_subprocess_shell(
                    shell_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=cwd,
                    env=env,
                )
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=cfg.timeout_seconds,
                    )
                    timed_out = False
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.communicate()
                    stdout_b = b""
                    stderr_b = b"Inline execution timed out"
                    timed_out = True
                elapsed_ms = int((time.monotonic() - start) * 1000)
                stdout = stdout_b[: cfg.max_output_bytes].decode(
                    "utf-8", errors="replace"
                )
                stderr = stderr_b[: cfg.max_output_bytes].decode(
                    "utf-8", errors="replace"
                )
                return SandboxResult(
                    exit_code=proc.returncode or (1 if timed_out else 0),
                    stdout=stdout,
                    stderr=stderr,
                    timed_out=timed_out,
                    duration_ms=elapsed_ms,
                )
            except FileNotFoundError as e:
                return SandboxResult(
                    exit_code=127,
                    stdout="",
                    stderr=f"Interpreter not found: {e}",
                )
            except Exception as e:
                logger.error("Inline execution error: %s", e, exc_info=True)
                return SandboxResult(
                    exit_code=1,
                    stdout="",
                    stderr=str(e),
                )
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    async def health(self) -> dict[str, Any]:
        return {
            "backend": "subprocess",
            "status": "healthy",
            "platform": sys.platform,
            "note": (
                "Development sandbox — no isolation guarantees. Run "
                "scripts/setup_firecracker.sh and set "
                "PANTHEON_SANDBOX=firecracker for real isolation."
            ),
            "issues": [],
        }


def _shquote(s: str) -> str:
    """Minimal POSIX shell quoting for command arguments."""
    if not s or any(c in s for c in " \t\n\"'\\$`!*?[]{}|&;<>()"):
        return "'" + s.replace("'", "'\\''") + "'"
    return s
