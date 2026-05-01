"""FirecrackerSandbox — microVM-based execution.

Each execution boots a fresh microVM from a per-runtime base rootfs,
injects the script via debugfs, runs, captures output, and tears down.

Single-user: trimmed from tuatha's multitenant version. No jailer, no
per-tenant data dirs. One install location for everyone (overridable
via FC_DIR / FIRECRACKER_DIR env vars).

Requires Linux + KVM + Firecracker installed (use scripts/setup_firecracker.sh).
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import platform
import shutil
import tempfile
import time
import uuid
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

_ARCH_MAP = {"x86_64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}


class FirecrackerSandbox(SandboxBackend):
    name = "firecracker"

    def __init__(
        self,
        firecracker_bin: Path,
        kernel_path: Path,
        rootfs_dir: Path,
        *,
        vcpu_count: int = 1,
        mem_size_mib: int = 256,
    ):
        self._fc_bin = firecracker_bin
        self._kernel = kernel_path
        self._rootfs_dir = rootfs_dir
        self._vcpu_count = vcpu_count
        self._mem_size_mib = mem_size_mib

    @classmethod
    def from_env(cls) -> "FirecrackerSandbox":
        fc_dir = Path(os.getenv("FIRECRACKER_DIR") or os.getenv("FC_DIR") or "/opt/firecracker")
        arch = platform.machine()
        fc_arch = _ARCH_MAP.get(arch, arch)
        return cls(
            firecracker_bin=fc_dir / "bin" / f"firecracker-{fc_arch}",
            kernel_path=fc_dir / "kernel" / f"vmlinux-{fc_arch}",
            rootfs_dir=fc_dir / "rootfs",
        )

    # ── execute_skill: not yet implemented for Firecracker ──
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
        # Fall back to subprocess for declared skills until we reconcile
        # skill bundle layout with Firecracker rootfs injection. Inline
        # execution is the high-value path for v1.
        from skills.executor import execute_script
        logger.info(
            "FirecrackerSandbox: skill execution falls back to subprocess "
            "(skill=%s). Inline code_execute uses microVM isolation.",
            getattr(skill, "name", "?"),
        )
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
        runtime = self._language_to_runtime(language)
        rootfs_template = self._get_rootfs(runtime)
        if not rootfs_template:
            return SandboxResult(
                exit_code=1,
                stdout="",
                stderr=(
                    f"No rootfs image for runtime '{runtime}'. Expected "
                    f"{self._rootfs_dir / f'{runtime}-base.ext4'}. Run "
                    "scripts/setup_firecracker.sh."
                ),
            )

        vm_id = uuid.uuid4().hex[:12]
        vm_dir = Path(tempfile.mkdtemp(prefix=f"pantheon_fc_{vm_id}_"))
        try:
            vm_rootfs = vm_dir / "rootfs.ext4"
            shutil.copy2(rootfs_template, vm_rootfs)

            inject_dir = vm_dir / "inject"
            inject_dir.mkdir()
            (inject_dir / filename).parent.mkdir(parents=True, exist_ok=True)
            (inject_dir / filename).write_text(code)

            # runner.sh: cd into /inject and run the right interpreter
            interp = language_to_interpreter(language)
            cmd_str = " ".join(interp + [f"/inject/{filename}"])
            env_lines = "\n".join(f'export {k}="{v}"' for k, v in (cfg.extra_env or {}).items())
            runner = inject_dir / "_runner.sh"
            runner.write_text(f"#!/bin/sh\n{env_lines}\ncd /inject\n{cmd_str}\n")
            runner.chmod(0o755)

            # Inject all files into the rootfs ext4 image via debugfs
            for p in inject_dir.rglob("*"):
                if p.is_file():
                    rel = p.relative_to(inject_dir)
                    dest = f"/inject/{rel}"
                    parent = str(Path(dest).parent)
                    await self._debugfs_cmd(vm_rootfs, f"mkdir {parent}")
                    await self._debugfs_cmd(vm_rootfs, f"write {p} {dest}")

            fc_config = {
                "boot-source": {
                    "kernel_image_path": str(self._kernel),
                    "boot_args": "console=ttyS0 reboot=k panic=1 pci=off init=/inject/_runner.sh",
                },
                "drives": [{
                    "drive_id": "rootfs",
                    "path_on_host": str(vm_rootfs),
                    "is_root_device": True,
                    "is_read_only": False,
                }],
                "machine-config": {
                    "vcpu_count": self._vcpu_count,
                    "mem_size_mib": min(cfg.max_memory_mb, self._mem_size_mib),
                },
            }
            cfg_path = vm_dir / "fc_config.json"
            cfg_path.write_text(json.dumps(fc_config))

            start = time.monotonic()
            try:
                proc = await asyncio.create_subprocess_exec(
                    str(self._fc_bin),
                    "--no-api",
                    "--config-file", str(cfg_path),
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(vm_dir),
                )
                try:
                    stdout_b, stderr_b = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=cfg.timeout_seconds + 5,  # boot grace
                    )
                    timed_out = False
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.communicate()
                    stdout_b = b""
                    stderr_b = b"VM execution timed out"
                    timed_out = True
                elapsed = int((time.monotonic() - start) * 1000)
                stdout = stdout_b[: cfg.max_output_bytes].decode("utf-8", errors="replace")
                stderr = stderr_b[: cfg.max_output_bytes].decode("utf-8", errors="replace")
                return SandboxResult(
                    exit_code=proc.returncode or (1 if timed_out else 0),
                    stdout=stdout,
                    stderr=stderr,
                    timed_out=timed_out,
                    duration_ms=elapsed,
                )
            except FileNotFoundError:
                return SandboxResult(
                    exit_code=1,
                    stdout="",
                    stderr=f"Firecracker binary not found at {self._fc_bin}",
                )
            except Exception as e:
                logger.error("Firecracker inline exec error: %s", e, exc_info=True)
                return SandboxResult(exit_code=1, stdout="", stderr=str(e))
        finally:
            shutil.rmtree(vm_dir, ignore_errors=True)

    async def health(self) -> dict[str, Any]:
        arch = platform.machine()
        fc_exists = self._fc_bin.exists()
        kernel_exists = self._kernel.exists()
        kvm_available = Path("/dev/kvm").exists()
        rootfs_images: list[str] = []
        if self._rootfs_dir.exists():
            rootfs_images = [f.name for f in self._rootfs_dir.glob("*.ext4")]
        issues: list[str] = []
        if not fc_exists:
            issues.append(f"firecracker binary missing at {self._fc_bin}")
        if not kernel_exists:
            issues.append(f"kernel image missing at {self._kernel}")
        if not kvm_available:
            issues.append("/dev/kvm unavailable (KVM/nested virtualization required)")
        if not rootfs_images:
            issues.append(f"no rootfs images in {self._rootfs_dir}")
        status = "healthy" if not issues else "degraded"
        return {
            "backend": "firecracker",
            "status": status,
            "arch": arch,
            "firecracker_bin": str(self._fc_bin),
            "kernel": str(self._kernel),
            "rootfs_images": rootfs_images,
            "kvm_available": kvm_available,
            "issues": issues,
        }

    # ── helpers ──

    def _language_to_runtime(self, language: str) -> str:
        # Maps language -> rootfs name. Bash runs in python rootfs (it has /bin/sh).
        l = language.lower()
        if l in {"python", "py"}:
            return "python"
        if l in {"node", "javascript", "js"}:
            return "node"
        if l in {"bash", "sh"}:
            return "python"  # python rootfs also has sh
        return "python"

    def _get_rootfs(self, runtime: str) -> Path | None:
        path = self._rootfs_dir / f"{runtime}-base.ext4"
        return path if path.exists() else None

    async def _debugfs_cmd(self, rootfs: Path, cmd: str) -> None:
        proc = await asyncio.create_subprocess_exec(
            "debugfs", "-w", "-R", cmd, str(rootfs),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
