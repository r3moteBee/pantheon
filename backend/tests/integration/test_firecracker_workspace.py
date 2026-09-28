"""Firecracker run_command sees the repo: workspace disk round trip, runner
script and console parsing. The VM itself needs KVM, so it isn't booted
here; the image build and read-back use the same mkfs/debugfs calls."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess

import pytest

from sandbox import firecracker_backend as fc

needs_e2fs = pytest.mark.skipif(not (shutil.which("mkfs.ext4") and shutil.which("debugfs")),
                                reason="e2fsprogs not installed")


def test_parse_console_drops_boot_log_and_reads_exit_code():
    console = "[    0.1] Linux version...\r\nboot noise\r\n__PANTHEON_START__\r\nhello\r\nworld\r\n__PANTHEON_EXIT__=3\r\n[ 0.9] Kernel panic"
    out, code = fc._parse_console(console)
    assert out == "hello\nworld\n" and code == 3
    assert fc._parse_console("no markers at all") == ("no markers at all", None)


def test_runner_script_mounts_and_unmounts_workspace():
    script = fc._runner_script("bash /inject/main.sh", {"A": "it's"}, workspace=True)
    assert "mount -t ext4 /dev/vdb /workspace" in script
    assert script.index("cd /workspace") < script.index("__PANTHEON_START__") < script.index("bash /inject/main.sh")
    assert script.index("cd /\n") < script.index("umount /workspace") < script.index("__PANTHEON_EXIT__=$rc")
    assert "export A='it'\\''s'" in script
    plain = fc._runner_script("python3 /inject/main.py", {}, workspace=False)
    assert "/dev/vdb" not in plain and "cd /inject" in plain


@needs_e2fs
def test_workspace_disk_round_trip(tmp_path):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "app.py").write_text("print(1)\n")
    (repo / "README.md").write_text("old\n")
    (repo / "stale.txt").write_text("delete me\n")
    os.symlink("README.md", repo / "link")
    before = fc._tree(repo)
    vm_dir = tmp_path / "vm"
    vm_dir.mkdir()
    img = vm_dir / "ws.ext4"
    assert asyncio.run(fc._build_workspace_image(repo, img)) is None

    # What the command does inside the VM.
    new = tmp_path / "built.txt"
    new.write_text("artifact\n")
    readme = tmp_path / "README.md"
    readme.write_text("new contents\n")
    for cmd in ("rm stale.txt", "mkdir build", f"write {new} build/out.txt",
                "rm README.md", f"write {readme} README.md"):
        subprocess.run(["debugfs", "-w", "-R", cmd, str(img)], check=True, capture_output=True)

    # Created on the host while the VM ran: must survive the sync.
    (repo / "host-only.txt").write_text("keep\n")

    assert asyncio.run(fc._sync_back(img, repo, before, vm_dir)) is None
    assert (repo / "build" / "out.txt").read_text() == "artifact\n"
    assert (repo / "README.md").read_text() == "new contents\n"
    assert not (repo / "stale.txt").exists()
    assert (repo / "host-only.txt").exists()
    assert (repo / "src" / "app.py").read_text() == "print(1)\n"
    assert os.readlink(repo / "link") == "README.md"
    assert not (repo / "lost+found").exists()
