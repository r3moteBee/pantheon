"""SandboxBackend ABC and shared types."""
from __future__ import annotations
import abc
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_ms: int = 0

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict[str, Any]:
        return {
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "duration_ms": self.duration_ms,
            "success": self.success,
        }


@dataclass
class SandboxConfig:
    timeout_seconds: int = 30
    max_memory_mb: int = 256
    max_output_bytes: int = 1_048_576  # 1 MB
    network_enabled: bool = True
    workspace_dir: Path | None = None
    extra_env: dict[str, str] = field(default_factory=dict)


# Inline-execution interpreters
_INTERPRETERS = {
    "python": ["python3"],
    "py": ["python3"],
    "node": ["node"],
    "javascript": ["node"],
    "js": ["node"],
    "bash": ["bash"],
    "sh": ["sh"],
}

_DEFAULT_FILENAMES = {
    "python": "main.py",
    "py": "main.py",
    "node": "main.js",
    "javascript": "main.js",
    "js": "main.js",
    "bash": "main.sh",
    "sh": "main.sh",
}


def language_to_interpreter(language: str) -> list[str]:
    return _INTERPRETERS.get(language.lower(), ["python3"])


def language_to_default_filename(language: str) -> str:
    return _DEFAULT_FILENAMES.get(language.lower(), "main.py")


class SandboxBackend(abc.ABC):
    """Pluggable execution environment.

    `execute_skill` runs a permission-checked skill script (the existing
    Pantheon path). `execute_inline` runs an ad-hoc snippet supplied by
    the agent (the `code_execute` tool path).
    """

    name: str = "abstract"

    @abc.abstractmethod
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
        """Execute a declared skill script. Returns a dict in the same
        shape skills.executor.execute_script has historically produced
        (stdout, stderr, exit_code, timed_out, duration_ms, script, skill).
        """

    @abc.abstractmethod
    async def execute_inline(
        self,
        language: str,
        code: str,
        *,
        filename: str | None = None,
        config: SandboxConfig | None = None,
    ) -> SandboxResult:
        """Execute a snippet supplied by the agent."""

    @abc.abstractmethod
    async def health(self) -> dict[str, Any]:
        """Return health/diagnostic info for the Settings page."""
