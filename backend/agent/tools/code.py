"""Host execution: code_execute and run_command (HOST_EXEC_TOOLS, gated)."""
from __future__ import annotations

from typing import Any
from agent.tools.registry import ToolContext, tool
from agent.tools import workspace as _ws


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "code_execute",
            "description": (
                "Run a Python, Node or Bash snippet in the sandbox; returns stdout, stderr and exit code. "
                "Default timeout 30 s, 256 MB."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["python", "node", "javascript", "bash"]
                    },
                    "code": {
                        "type": "string"
                    },
                    "filename": {
                        "type": "string"
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "1-300, default 30."
                    }
                },
                "required": ["language", "code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run a bash command in the project's repo checkout (git_sync_repo) or workspace: installs, "
                "tests, linters, builds. Runs on the host - trusted repos only. Default timeout 120 s (max "
                "1800)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string"
                    },
                    "workdir": {
                        "type": "string",
                        "description": "Subdirectory to run in."
                    },
                    "timeout_seconds": {
                        "type": "integer"
                    }
                },
                "required": ["command"]
            }
        }
    },
]


@tool('code_execute')
async def _tool_code_execute(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    from sandbox import get_sandbox
    from sandbox.backend import SandboxConfig
    language = (tool_args.get("language") or "python").lower()
    code = tool_args.get("code") or ""
    if not code.strip():
        return "code_execute: empty code argument"
    timeout = int(tool_args.get("timeout_seconds") or 30)
    timeout = max(1, min(timeout, 300))
    cfg = SandboxConfig(timeout_seconds=timeout)
    result = await get_sandbox().execute_inline(
        language=language,
        code=code,
        filename=tool_args.get("filename"),
        config=cfg,
    )
    # Render result for the model in a stable, parseable form.
    parts = [f"exit_code: {result.exit_code}",
             f"duration_ms: {result.duration_ms}"]
    if result.timed_out:
        parts.append("timed_out: true")
    if result.stdout:
        parts.append("stdout:\n" + result.stdout.rstrip())
    if result.stderr:
        parts.append("stderr:\n" + result.stderr.rstrip())
    return "\n\n".join(parts) or "(no output)"



@tool('run_command')
async def _tool_run_command(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    effective_project = ctx.effective_project
    from sandbox import get_sandbox
    from sandbox.backend import SandboxConfig
    command = (tool_args.get("command") or "").strip()
    if not command:
        return "run_command: empty command argument"
    timeout = int(tool_args.get("timeout_seconds") or 120)
    timeout = max(1, min(timeout, 1800))
    base = _ws._resolve_repo_checkout(effective_project) or _ws._get_workspace_base(effective_project)
    workdir = (tool_args.get("workdir") or "").strip().lstrip("/")
    if workdir:
        cand = (base / workdir).resolve()
        if not cand.is_relative_to(base):
            return f"run_command: workdir escapes the workspace: {workdir}"
        if not cand.is_dir():
            return f"run_command: workdir not found: {workdir}"
        base = cand
    # Generous memory cap — package managers and test runners need
    # far more virtual address space than inline snippets.
    cfg = SandboxConfig(timeout_seconds=timeout, max_memory_mb=4096,
                        workspace_dir=base)
    result = await get_sandbox().execute_inline(
        language="bash", code=command, config=cfg,
    )
    parts = [f"cwd: {base}",
             f"exit_code: {result.exit_code}",
             f"duration_ms: {result.duration_ms}"]
    if result.timed_out:
        parts.append(f"timed_out: true (limit {timeout}s — pass a "
                     f"higher timeout_seconds if the command needs more)")
    if result.stdout:
        parts.append("stdout:\n" + result.stdout.rstrip())
    if result.stderr:
        parts.append("stderr:\n" + result.stderr.rstrip())
    return "\n\n".join(parts) or "(no output)"

