"""run_build: build and test a folder of project artifacts in a throwaway
container (builder/), and hand back the result and a download link."""
from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import quote

from agent.tools.registry import ToolContext, tool

logger = logging.getLogger(__name__)

LOG_SHOWN = 10_000        # chars of build log the model sees (head + tail)
ERROR_LINES = 40
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")   # colour codes (swiftc colours even without a tty)
_ERR = re.compile(r"(\berror\b|\bfailed\b|\bFAIL\b|panicked|Traceback|undefined reference)", re.I)


def _schema(toolchains: dict | None = None) -> dict[str, Any]:
    if toolchains is None:
        from builder.backend import DEFAULT_TOOLCHAINS as toolchains
    listing = "; ".join(f"{t.name}: {t.about} (default command: {t.command})" for t in toolchains.values())
    return {
        "type": "function",
        "function": {
            "name": "run_build",
            "description": (
                "Build and test a folder of this project's artifacts in a clean, throwaway container "
                "with no network, and get back PASSED/FAILED, the compiler and test output, and (when it "
                "passes) a link to download the folder as a zip. Use it after writing code to check that "
                "it compiles and its tests pass; fix what it reports and run it again. Toolchains - "
                f"{listing}."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Artifact folder to build, bare like list_artifacts' path_prefix, e.g. 'SpiderSolitaire/'. Its files are the build's working tree."},
                    "toolchain": {"type": "string", "enum": list(toolchains), "description": "Which toolchain image to build with."},
                    "command": {"type": "string", "description": "Shell command to run in the folder. Defaults to the toolchain's command."},
                    "workdir": {"type": "string", "description": "Subfolder (inside path) to run the command in, e.g. the folder holding Package.swift."},
                    "timeout_seconds": {"type": "integer", "description": "Build time limit (default and maximum BUILDER_TIMEOUT_SECONDS)."},
                },
                "required": ["path", "toolchain"],
            },
        },
    }


SCHEMAS: list[dict[str, Any]] = [_schema()]


def current_schema() -> dict[str, Any]:
    """The schema with the configured toolchains (BUILDER_TOOLCHAINS)."""
    from builder.backend import toolchains
    return _schema(toolchains())


def _excerpt(log: str, passed: bool) -> str:
    log = _ANSI.sub("", log).replace("\r", "")
    errors: list[str] = []
    if not passed:
        for line in log.splitlines():
            s = line.strip()
            if s and _ERR.search(s) and s not in errors:
                errors.append(s[:300])
                if len(errors) >= ERROR_LINES:
                    break
    if len(log) > LOG_SHOWN:
        head = LOG_SHOWN // 4
        log = (log[:head] + f"\n... ({len(log) - LOG_SHOWN} chars cut) ...\n" + log[-(LOG_SHOWN - head):])
    out = ""
    if errors:
        out += "Error lines:\n" + "\n".join("  " + e for e in errors) + "\n\n"
    return out + "--- build log ---\n" + log.strip()


def _clean_rel(p: str | None) -> str:
    p = (p or "").strip().strip("/")
    if any(part in ("..", "") for part in p.split("/")) and p:
        raise ValueError(f"workdir {p!r} must be a plain relative folder")
    return p


@tool('run_build')
async def _tool_run_build(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    from artifacts.store import get_store, project_slug
    from builder import builder_enabled, get_builder
    from builder.backend import BuildError, toolchains
    from config import get_settings

    if not builder_enabled():
        return "run_build is not available: no builder is configured on this Pantheon (PANTHEON_BUILDER)."
    s = get_settings()
    chains = toolchains()
    tc = chains.get((tool_args.get("toolchain") or "").strip().lower())
    if tc is None:
        return f"Unknown toolchain {tool_args.get('toolchain')!r}. Available: {', '.join(chains)}."
    project = ctx.effective_project
    slug = project_slug(project)
    folder = (tool_args.get("path") or "").strip().strip("/")
    if folder == slug:
        folder = ""
    elif folder.startswith(slug + "/"):
        folder = folder[len(slug) + 1:]
    prefix = f"{slug}/{folder}/" if folder else f"{slug}/"
    try:
        workdir = _clean_rel(tool_args.get("workdir"))
    except ValueError as e:
        return f"run_build: {e}"
    files = get_store().files_under(project, prefix)
    if not files:
        return (f"run_build: no artifacts under {prefix!r}. Save the source files there first "
                "(list_artifacts with path_prefix shows what is in a folder).")
    command = (tool_args.get("command") or "").strip() or tc.command
    timeout = min(int(tool_args.get("timeout_seconds") or s.builder_timeout_seconds), s.builder_timeout_seconds)
    try:
        res = await get_builder().run(tc, files, command=command, workdir=workdir, timeout_s=timeout,
                                      label=f"{project}:{prefix}")
    except BuildError as e:
        return f"run_build could not run the build: {e}"

    status = ("TIMED OUT" if res.timed_out else "PASSED" if res.passed else "FAILED")
    lines = [f"Build {status} - toolchain {tc.name} ({tc.image}), "
             + (f"exit code {res.exit_code}" if res.exit_code is not None else "no exit code")
             + (f" ({res.reason})" if res.reason and res.reason not in ("Completed", "Error") else "")
             + f", {res.duration_s:.0f} s, {len(files)} files from {prefix}",
             f"Command: {command}" + (f"  (in {workdir}/)" if workdir else "")]
    if res.passed:
        name = (folder.rsplit("/", 1)[-1] or slug) + ".zip"
        url = f"/api/artifacts/export-all?project_id={quote(project)}&path_prefix={quote(prefix)}"
        lines.append(f"Download the folder: [{name}]({url})")
    elif res.timed_out:
        lines.append(f"The build ran past {timeout} s and was stopped.")
    else:
        lines.append("Fix the errors below in the artifacts (update_artifact) and run run_build again.")
    return "\n".join(lines) + "\n\n" + _excerpt(res.log, res.passed)
