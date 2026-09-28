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
                "Execute a code snippet in an isolated sandbox and return its "
                "stdout, stderr, and exit code. Use this to test code, run "
                "computations, validate logic, generate data, or prototype "
                "before committing. Supports Python, Node, and Bash. The "
                "sandbox has a default 30-second timeout and 256 MB memory "
                "limit. Output is truncated at 1 MB. In subprocess mode the "
                "snippet runs on the host with no filesystem isolation; use "
                "Firecracker mode (PANTHEON_SANDBOX=firecracker) for real "
                "isolation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["python", "node", "javascript", "bash"],
                        "description": "Runtime for the snippet."
                    },
                    "code": {
                        "type": "string",
                        "description": "The code to execute."
                    },
                    "filename": {
                        "type": "string",
                        "description": "Optional filename for the script (e.g. 'analysis.py'). Defaults are language-appropriate."
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout override (1-300 seconds, default 30)."
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
                "Run a shell (bash) command inside the project's local repo "
                "checkout (created by git_sync_repo), or the project "
                "workspace if no checkout exists. Use this to install "
                "dependencies, run test suites (pytest, npm test), linters, "
                "or build steps. Multi-line scripts are allowed; paths are "
                "relative to the checkout root. Runs on the host in "
                "subprocess sandbox mode — only use against trusted repos. "
                "Default timeout 120s (max 1800)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Bash command or multi-line script to run."
                    },
                    "workdir": {
                        "type": "string",
                        "description": "Optional subdirectory (relative to the repo checkout / workspace) to run in."
                    },
                    "timeout_seconds": {
                        "type": "integer",
                        "description": "Optional timeout override (1-1800 seconds, default 120). Raise it for dependency installs or slow test suites."
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

