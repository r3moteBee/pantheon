"""FirecrackerSandbox — microVM-based execution.

Each execution boots a fresh microVM from a per-runtime base rootfs,
injects the script via debugfs, runs, captures output, and tears down.

When the caller passes ``SandboxConfig.workspace_dir`` (run_command in a
repo checkout), that directory is packed into a second ext4 disk
(``mkfs.ext4 -d``), mounted at /workspace in the VM as the working
directory, and afterwards unpacked (``debugfs rdump``) and mirrored back,
so the command sees the repo and its changes persist. The VM has no
network interface, so commands that download packages fail.

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
            vcpu_count=int(os.getenv("FC_VCPUS", "1")),
            mem_size_mib=int(os.getenv("FC_MEM_MIB", "1024")),
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

            interp = language_to_interpreter(language)
            cmd_str = " ".join(interp + [f"/inject/{filename}"])
            workspace = Path(cfg.workspace_dir) if cfg.workspace_dir else None
            runner = inject_dir / "_runner.sh"
            runner.write_text(_runner_script(cmd_str, cfg.extra_env or {}, bool(workspace)))
            runner.chmod(0o755)

            ws_img = None
            before: set[str] = set()
            if workspace:
                if not workspace.is_dir():
                    return SandboxResult(exit_code=1, stdout="",
                                         stderr=f"workspace not found: {workspace}")
                before = _tree(workspace)
                ws_img = vm_dir / "workspace.ext4"
                err = await _build_workspace_image(workspace, ws_img)
                if err:
                    return SandboxResult(exit_code=1, stdout="", stderr=err)

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
                }] + ([{
                    "drive_id": "workspace",          # /dev/vdb in the guest
                    "path_on_host": str(ws_img),
                    "is_root_device": False,
                    "is_read_only": False,
                }] if ws_img else []),
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
                console = stdout_b.decode("utf-8", errors="replace")
                stdout, exit_code = _parse_console(console)
                stdout = stdout[: cfg.max_output_bytes]
                stderr = stderr_b[: cfg.max_output_bytes].decode("utf-8", errors="replace")
                if exit_code is None:
                    exit_code = 1 if (timed_out or proc.returncode) else 0
                if ws_img and not timed_out:
                    sync_err = await _sync_back(ws_img, workspace, before, vm_dir)
                    if sync_err:
                        stderr = (stderr + "\n" if stderr else "") + sync_err
                        exit_code = exit_code or 1
                return SandboxResult(
                    exit_code=exit_code,
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
        for tool in ("debugfs", "mkfs.ext4", "e2fsck"):
            if not shutil.which(tool):
                issues.append(f"{tool} not found (install e2fsprogs)")
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


# ── Guest runner, console parsing, workspace disk ──────────────────────────

_START = "__PANTHEON_START__"
_EXIT = "__PANTHEON_EXIT__="


def _runner_script(cmd: str, env: dict[str, str], workspace: bool) -> str:
    """PID 1 in the guest. Mounts the workspace disk when there is one,
    runs the command there, reports its exit code on the console, then
    syncs and unmounts so the host can read the disk back."""
    exports = "\n".join(f"export {k}={_sh_quote(v)}" for k, v in env.items())
    ws_up = ("mkdir -p /workspace\nmount -t ext4 /dev/vdb /workspace || "
             "{ echo 'workspace mount failed' >&2; echo " + _EXIT + "97; exit 97; }\n"
             "cd /workspace") if workspace else "cd /inject"
    ws_down = "cd /\nsync\numount /workspace 2>/dev/null" if workspace else "sync"
    return (
        "#!/bin/sh\n"
        "mount -t proc proc /proc 2>/dev/null\n"
        "mount -t devtmpfs dev /dev 2>/dev/null\n"
        f"{exports}\n{ws_up}\n"
        f"echo {_START}\n"
        f"{cmd}\n"
        "rc=$?\n"
        f"{ws_down}\n"
        f"echo {_EXIT}$rc\n"
    )


def _sh_quote(v: str) -> str:
    return "'" + str(v).replace("'", "'\\''") + "'"


def _parse_console(console: str) -> tuple[str, int | None]:
    """The command's output (boot log before the start marker dropped) and
    its real exit code — Firecracker's own exit code says nothing about it."""
    console = console.replace("\r\n", "\n")
    out = console.split(_START, 1)[1].lstrip("\n") if _START in console else console
    code = None
    if _EXIT in out:
        out, tail = out.rsplit(_EXIT, 1)
        digits = "".join(ch for ch in tail[:4] if ch.isdigit())
        code = int(digits) if digits else None
    return out, code


def _tree(root: Path) -> set[str]:
    out: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        rel = os.path.relpath(dirpath, root)
        for name in dirnames + filenames:
            out.add(os.path.normpath(os.path.join(rel, name)))
    return out


async def _run(*args: str) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    out, _ = await proc.communicate()
    return proc.returncode or 0, out.decode("utf-8", errors="replace")


async def _build_workspace_image(src: Path, img: Path) -> str | None:
    """Pack ``src`` into an ext4 image with room for the command's output
    (FC_WORKSPACE_HEADROOM_MB, default 1024). Returns an error or None."""
    used = sum(f.stat().st_size for f in src.rglob("*") if f.is_file() and not f.is_symlink())
    headroom = int(os.getenv("FC_WORKSPACE_HEADROOM_MB", "1024"))
    size_mb = int(used * 1.3 / 1_048_576) + headroom
    rc, out = await _run("mkfs.ext4", "-q", "-F", "-L", "workspace", "-d", str(src), str(img), f"{size_mb}M")
    return None if rc == 0 else f"could not build the workspace disk (mkfs.ext4 -d): {out.strip()[:300]}"


async def _sync_back(img: Path, dest: Path, before: set[str], vm_dir: Path) -> str | None:
    """Unpack the workspace disk and mirror it onto ``dest``. Deletes only
    paths that existed before the run and are gone from the disk, so
    nothing created on the host meanwhile is lost."""
    await _run("e2fsck", "-fy", str(img))  # unclean unmount after a crash
    out_dir = vm_dir / "workspace-out"
    out_dir.mkdir()
    rc, out = await _run("debugfs", "-R", f"rdump / {out_dir}", str(img))
    if rc != 0:
        return f"could not read the workspace back from the VM: {out.strip()[:300]}"
    shutil.rmtree(out_dir / "lost+found", ignore_errors=True)
    return await asyncio.to_thread(_mirror, out_dir, dest, before)


def _mirror(src: Path, dest: Path, before: set[str]) -> str | None:
    try:
        after = _tree(src)
        for rel in sorted(after):
            s, d = src / rel, dest / rel
            if s.is_symlink():
                target = os.readlink(s)
                if d.is_symlink() and os.readlink(d) == target:
                    continue
                if d.exists() or d.is_symlink():
                    shutil.rmtree(d) if d.is_dir() and not d.is_symlink() else d.unlink()
                d.parent.mkdir(parents=True, exist_ok=True)
                os.symlink(target, d)
            elif s.is_dir():
                if d.is_symlink() or (d.exists() and not d.is_dir()):
                    d.unlink()
                d.mkdir(parents=True, exist_ok=True)
            else:
                st = s.stat()
                if d.is_file() and not d.is_symlink():
                    dt = d.stat()
                    if dt.st_size == st.st_size and int(dt.st_mtime) == int(st.st_mtime):
                        continue
                elif d.is_dir() and not d.is_symlink():
                    shutil.rmtree(d)
                elif d.is_symlink():
                    d.unlink()
                d.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(s, d)
        # Deepest first so directories are empty when their turn comes.
        for rel in sorted(before - after, key=lambda r: r.count(os.sep), reverse=True):
            d = dest / rel
            if d.is_symlink() or d.is_file():
                d.unlink(missing_ok=True)
            elif d.is_dir():
                shutil.rmtree(d, ignore_errors=True)
        return None
    except Exception as e:  # report, don't lose the command's output
        logger.error("Firecracker workspace sync-back failed: %s", e, exc_info=True)
        return f"workspace sync-back failed: {e}"
