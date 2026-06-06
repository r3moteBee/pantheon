"""Harness-computed metrics for autonomous tasks.

Completion notices must report numbers the handler computed from the
event stream and git — never the model's own arithmetic (observed
miscounts: "40 deleted" for 44, "29 branches" for 31, tool_calls=1 on
a 500-call run).
"""
import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")

import subprocess
from pathlib import Path

import pytest
from unittest.mock import patch

from jobs.handlers.autonomous_task import _repo_snapshot, _repo_delta_line

_ROOT = Path(__file__).resolve().parents[2]


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "a.txt").write_text("one\n")
    _git(r, "add", "."); _git(r, "commit", "-m", "base")
    return r


@pytest.mark.asyncio
async def test_repo_snapshot_and_delta(repo):
    with patch("agent.tools._resolve_repo_checkout", return_value=repo):
        before = await _repo_snapshot("t")
        # Simulate agent work: two commits + a dirty file
        (repo / "b.txt").write_text("two\n")
        _git(repo, "add", "."); _git(repo, "commit", "-m", "work 1")
        (repo / "c.txt").write_text("three\n")
        _git(repo, "add", "."); _git(repo, "commit", "-m", "work 2")
        (repo / "a.txt").write_text("changed\n")  # uncommitted
        after = await _repo_snapshot("t")

    assert before["commit_count"] == 1
    assert after["commit_count"] == 3
    assert before["head"] != after["head"]
    assert after["dirty"] is True

    line = _repo_delta_line(before, after)
    assert "+2 commits on main" in line
    assert before["head"][:7] in line and after["head"][:7] in line
    assert "DIRTY" in line


@pytest.mark.asyncio
async def test_repo_delta_no_changes(repo):
    with patch("agent.tools._resolve_repo_checkout", return_value=repo):
        before = await _repo_snapshot("t")
        after = await _repo_snapshot("t")
    line = _repo_delta_line(before, after)
    assert "no new commits" in line
    assert "tree clean" in line


@pytest.mark.asyncio
async def test_repo_snapshot_none_without_checkout():
    with patch("agent.tools._resolve_repo_checkout", return_value=None):
        assert await _repo_snapshot("t") is None
    assert _repo_delta_line(None, None) is None


def test_handler_stamps_harness_metrics():
    src = (_ROOT / "jobs/handlers/autonomous_task.py").read_text(encoding="utf-8")
    # Per-tool usage accumulated in the pump and stamped into the result
    assert "tool_usage[last_tool_name]" in src
    assert '"tool_usage": dict(sorted(tool_usage.items()' in src
    # Repo snapshots bracket the agent loop
    assert "repo_before = await _repo_snapshot" in src
    assert "repo_after = await _repo_snapshot" in src
    assert '"repo_delta": repo_delta' in src
    # Notices carry harness numbers, not model numbers
    assert "Harness-verified" in src
