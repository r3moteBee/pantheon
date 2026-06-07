"""The conflict-marker pre-commit hook installed by git_sync_repo.

Closes the run_command bypass: the git_commit tool refuses staged
conflict markers, but an agent committing via `run_command 'git add &&
git commit'` skipped that guard entirely. The hook enforces it at the
git layer regardless of entry path.
"""
import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")

import subprocess
from pathlib import Path

import pytest

from agent.tools import _install_precommit_hook, _PRECOMMIT_MARKER

_ROOT = Path(__file__).resolve().parents[2]


def _git(repo, *args, check=True):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=check)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.ts").write_text("const x = 1;\n")
    _git(r, "add", "."); _git(r, "commit", "-m", "base")
    return r


def test_hook_blocks_conflict_markers_via_plain_git(repo):
    assert "installed" in _install_precommit_hook(repo)
    (repo / "a.ts").write_text(
        "<<<<<<< HEAD\nconst x = 1;\n=======\nconst x = 2;\n>>>>>>> branch\n")
    _git(repo, "add", ".")
    res = _git(repo, "commit", "-m", "bad", check=False)
    assert res.returncode != 0
    assert "refusing to commit unresolved merge-conflict markers" in res.stderr
    # Resolve and the same commit goes through
    (repo / "a.ts").write_text("const x = 2;\n")
    _git(repo, "add", ".")
    res2 = _git(repo, "commit", "-m", "good", check=False)
    assert res2.returncode == 0


def test_hook_scoped_to_staged_files(repo):
    """Pre-existing tracked content with literal markers must not block
    commits that don't touch it."""
    (repo / "docs.md").write_text("Conflict markers look like:\n<<<<<<< HEAD\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "docs with literal markers")  # hook not yet installed
    assert "installed" in _install_precommit_hook(repo)
    (repo / "b.ts").write_text("const y = 3;\n")
    _git(repo, "add", "b.ts")
    res = _git(repo, "commit", "-m", "unrelated", check=False)
    assert res.returncode == 0


def test_hook_respects_existing_user_hook(repo):
    hooks = repo / ".git" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    (hooks / "pre-commit").write_text("#!/bin/sh\n# user's own hook\nexit 0\n")
    msg = _install_precommit_hook(repo)
    assert "left untouched" in msg
    assert _PRECOMMIT_MARKER not in (hooks / "pre-commit").read_text()


def test_hook_install_idempotent(repo):
    assert "installed" in _install_precommit_hook(repo)
    assert "installed" in _install_precommit_hook(repo)  # refresh own hook
    hook = repo / ".git" / "hooks" / "pre-commit"
    assert _PRECOMMIT_MARKER in hook.read_text()
    assert os.access(hook, os.X_OK)


def test_git_sync_repo_installs_hook():
    src = (_ROOT / "agent/tools.py").read_text(encoding="utf-8")
    # Called in both the clone and update paths of git_sync_repo
    assert src.count("_install_precommit_hook(dest)") == 2
