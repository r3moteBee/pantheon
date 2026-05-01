"""Pluggable sandbox layer.

Two backends ship today:
  * SubprocessSandbox — local process with timeout/memory limits. Default.
  * FirecrackerSandbox — microVM isolation (opt-in, Linux+KVM only).

Selected via PANTHEON_SANDBOX env var ("subprocess" | "firecracker").
"""
from __future__ import annotations
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sandbox.backend import SandboxBackend

logger = logging.getLogger(__name__)

_INSTANCE: "SandboxBackend | None" = None


def get_sandbox() -> "SandboxBackend":
    """Process-global singleton sandbox backend."""
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE

    backend = os.getenv("PANTHEON_SANDBOX", "subprocess").lower().strip()
    if backend == "firecracker":
        try:
            from sandbox.firecracker_backend import FirecrackerSandbox
            _INSTANCE = FirecrackerSandbox.from_env()
            logger.info("Sandbox backend: firecracker")
            return _INSTANCE
        except Exception as e:
            logger.error(
                "Firecracker backend requested but failed to initialize "
                "(%s). Falling back to subprocess. Run "
                "scripts/setup_firecracker.sh and verify /dev/kvm.",
                e,
            )

    from sandbox.subprocess_backend import SubprocessSandbox
    _INSTANCE = SubprocessSandbox()
    logger.info("Sandbox backend: subprocess")
    return _INSTANCE


def reset_sandbox_for_tests() -> None:
    global _INSTANCE
    _INSTANCE = None
