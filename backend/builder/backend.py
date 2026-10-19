"""BuildBackend ABC, toolchains and the source bundle shared by builders."""
from __future__ import annotations

import abc
import io
import json
import logging
import tarfile
from dataclasses import dataclass
from typing import Any

from config import get_settings

logger = logging.getLogger(__name__)


class BuildError(Exception):
    """A build could not be run (not: the build ran and failed)."""


@dataclass
class Toolchain:
    name: str
    image: str
    command: str          # default build command, run with sh -c in the folder
    about: str = ""       # one line for the tool description


# Official images, minor-version tags (patch updates come with a pull). All of
# these work offline for code without third-party dependencies; builds have no
# network unless BUILDER_ALLOW_NETWORK is set. Override or add with
# BUILDER_TOOLCHAINS (JSON, same fields).
DEFAULT_TOOLCHAINS: dict[str, Toolchain] = {t.name: t for t in (
    Toolchain("swift", "docker.io/library/swift:6.4", "swift build && swift test",
              "Swift 6.4 on Linux: Swift packages (Package.swift), Foundation, XCTest and Swift Testing. "
              "No SwiftUI/UIKit/AppKit - for an Apple-platform app, build the platform-independent "
              "code (model, rules) as a package with tests and guard UI code with #if canImport(SwiftUI)"),
    Toolchain("c", "docker.io/library/gcc:16", "make",
              "GCC 16 / G++ with make (C and C++)"),
    Toolchain("go", "docker.io/library/golang:1.27", "go build ./... && go test ./...",
              "Go 1.27, standard library only offline"),
    Toolchain("rust", "docker.io/library/rust:1.99", "cargo build --offline && cargo test --offline",
              "Rust 1.99 with cargo, no crates.io offline"),
    Toolchain("python", "docker.io/library/python:3.15-slim", "python -m unittest discover -v",
              "Python 3.15 standard library (unittest)"),
    Toolchain("node", "docker.io/library/node:24-slim", "node --test",
              "Node.js 24 LTS (node --test), no npm install offline"),
    Toolchain("java", "docker.io/library/eclipse-temurin:25-jdk",
              "mkdir -p /tmp/classes && javac -d /tmp/classes $(find . -name '*.java')",
              "Java 25 JDK (javac/java), no Maven/Gradle downloads offline"),
)}


def toolchains() -> dict[str, Toolchain]:
    """Defaults merged with BUILDER_TOOLCHAINS, e.g.
    {"swift": {"image": "registry.local/swift:6.4"}, "zig": {"image": "...", "command": "zig build test"}}.
    An entry with "image": "" removes that toolchain."""
    out = dict(DEFAULT_TOOLCHAINS)
    raw = (get_settings().builder_toolchains or "").strip()
    if not raw:
        return out
    try:
        extra = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("BUILDER_TOOLCHAINS is not valid JSON (%s); using the defaults", e)
        return out
    for name, spec in (extra or {}).items():
        if not isinstance(spec, dict):
            continue
        base = out.get(name)
        image = spec.get("image", base.image if base else "")
        if not image:
            out.pop(name, None)
            continue
        out[name] = Toolchain(name, image, spec.get("command", base.command if base else "make"),
                              spec.get("about", base.about if base else ""))
    return out


def bundle(files: dict[str, bytes]) -> bytes:
    """files (relative path -> bytes) as a gzipped tar with fixed metadata."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=9) as tar:
        for path in sorted(files):
            data = files[path]
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.mtime = len(data), 0o644, 0
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


@dataclass
class BuildResult:
    exit_code: int | None
    log: str
    duration_s: float
    timed_out: bool = False
    reason: str = ""      # e.g. OOMKilled, DeadlineExceeded

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out


class BuildBackend(abc.ABC):
    name: str = "abstract"

    @abc.abstractmethod
    async def run(self, toolchain: Toolchain, files: dict[str, bytes], *, command: str,
                  workdir: str = "", timeout_s: int = 900, label: str = "") -> BuildResult:
        """Unpack files into an empty directory, run command there (sh -c, in
        workdir if given) in toolchain.image with no network; return its result."""

    @abc.abstractmethod
    async def health(self) -> dict[str, Any]:
        """Diagnostics: is the backend reachable and safe to use."""
