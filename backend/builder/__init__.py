"""Builds: run a project folder's build and tests in a throwaway container.

The agent's run_build tool (agent/tools/build.py) hands a folder of artifacts
to a builder; the builder runs a toolchain image on it with no network and
returns the exit code and log. Pantheon itself never runs the compiler.

One backend ships today:
  * KubernetesBuilder - a pod per build in BUILDER_NAMESPACE, created through
    the in-cluster API (deploy/kubernetes/builder.yaml sets up the namespace).

Selected with PANTHEON_BUILDER ("" = off, "kubernetes").
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from config import get_settings

if TYPE_CHECKING:
    from builder.backend import BuildBackend

logger = logging.getLogger(__name__)

_INSTANCE: "BuildBackend | None" = None


def builder_enabled() -> bool:
    return bool((get_settings().pantheon_builder or "").strip())


def get_builder() -> "BuildBackend":
    """Process-global builder backend. Raises BuildError when none is configured."""
    global _INSTANCE
    from builder.backend import BuildError
    kind = (get_settings().pantheon_builder or "").strip().lower()
    if _INSTANCE is not None and _INSTANCE.name == kind:
        return _INSTANCE
    if kind == "kubernetes":
        from builder.kubernetes import KubernetesBuilder
        _INSTANCE = KubernetesBuilder.from_settings()
        logger.info("Builder backend: kubernetes (namespace %s)", _INSTANCE.namespace)
        return _INSTANCE
    if not kind:
        raise BuildError("No builder is configured (PANTHEON_BUILDER is empty).")
    raise BuildError(f"Unknown builder {kind!r} (PANTHEON_BUILDER): the only one is 'kubernetes'.")
