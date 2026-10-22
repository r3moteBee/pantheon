"""SandboxBackend ABC and shared types."""
from __future__ import annotations
import abc
import re
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


_SAFE_SCRIPT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def safe_script_filename(filename: str | None, language: str) -> str:
    """The script name a backend writes the snippet to: a bare basename of
    letters, digits, '.', '_', '-' (it is joined to a temp/inject dir and
    pasted into the guest's runner script). Anything else — directories,
    '..', absolute paths, shell metacharacters, '' — gets the language's
    default name."""
    default = language_to_default_filename(language)
    if not filename or not isinstance(filename, str):
        return default
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        return default
    return filename if _SAFE_SCRIPT_NAME.match(filename) and ".." not in filename else default


class SandboxBackend(abc.ABC):
    """Pluggable execution environment.

    `execute_inline` runs an ad-hoc snippet supplied by the agent (the
    `code_execute` tool path). Skills are markdown recipes and are never
    executed.
    """

    name: str = "abstract"

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
