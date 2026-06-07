"""End-to-end tests for git_merge + hardened git_commit against real
local git repos (no network, no mocks of git itself).

These guard the failure modes observed in a real merge session: an agent
"merging" by rewriting whole files from memory, committing unresolved
conflict markers, and writing commit messages claiming merges that never
ran (caught now by branch-name reporting).
"""
import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")

import subprocess

import pytest
from unittest.mock import patch

from agent.tools import execute_tool


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


@pytest.fixture
def repo(tmp_path):
    """A repo with main + two branches: 'clean' (no conflict) and
    'conflicting' (edits the same line as main)."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-b", "main")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "app.ts").write_text("const port = 3000;\n")
    _git(r, "add", "."); _git(r, "commit", "-m", "base")

    _git(r, "checkout", "-b", "clean")
    (r / "other.ts").write_text("export const x = 1;\n")
    _git(r, "add", "."); _git(r, "commit", "-m", "clean feature")

    _git(r, "checkout", "main", "--")
    _git(r, "checkout", "-b", "conflicting")
    (r / "app.ts").write_text("const port = 4000;\n")
    _git(r, "add", "."); _git(r, "commit", "-m", "port 4000")

    _git(r, "checkout", "main", "--")
    (r / "app.ts").write_text("const port = 5000;\n")
    _git(r, "add", "."); _git(r, "commit", "-m", "port 5000")
    return r


@pytest.mark.asyncio
async def test_git_merge_clean(repo):
    with patch("agent.tools._resolve_repo_checkout", return_value=repo):
        res = await execute_tool("git_merge", {"branch": "clean"}, None, project_id="t")
    assert "Merged 'clean' into 'main'" in res
    assert (repo / "other.ts").exists()


@pytest.mark.asyncio
async def test_git_merge_conflict_reports_hunks(repo):
    with patch("agent.tools._resolve_repo_checkout", return_value=repo):
        res = await execute_tool("git_merge", {"branch": "conflicting"}, None, project_id="t")
    assert "CONFLICTS in 1 file(s)" in res
    assert "app.ts" in res
    assert "<<<<<<< " in res and ">>>>>>> " in res
    assert "edit ONLY the conflicted regions" in res


@pytest.mark.asyncio
async def test_git_commit_refuses_conflict_markers(repo):
    with patch("agent.tools._resolve_repo_checkout", return_value=repo):
        await execute_tool("git_merge", {"branch": "conflicting"}, None, project_id="t")
        # Try to commit with the markers still in the file
        res = await execute_tool("git_commit", {"message": "resolve"}, None, project_id="t")
    assert "Refusing to commit" in res
    assert "app.ts" in res


@pytest.mark.asyncio
async def test_resolve_then_commit_concludes_merge(repo):
    with patch("agent.tools._resolve_repo_checkout", return_value=repo):
        await execute_tool("git_merge", {"branch": "conflicting"}, None, project_id="t")
        (repo / "app.ts").write_text("const port = 4000;\n")  # resolved
        res = await execute_tool("git_commit", {"message": "resolve conflict"}, None, project_id="t")
    assert "Committed successfully on branch 'main'" in res
    assert not (repo / ".git" / "MERGE_HEAD").exists()  # merge concluded
    log = _git(repo, "log", "--oneline", "-1").stdout
    assert "resolve conflict" in log


@pytest.mark.asyncio
async def test_git_merge_abort(repo):
    with patch("agent.tools._resolve_repo_checkout", return_value=repo):
        await execute_tool("git_merge", {"branch": "conflicting"}, None, project_id="t")
        res = await execute_tool("git_merge", {"abort": True}, None, project_id="t")
    assert "Merge aborted" in res
    assert (repo / "app.ts").read_text() == "const port = 5000;\n"
    assert not (repo / ".git" / "MERGE_HEAD").exists()


@pytest.mark.asyncio
async def test_git_merge_unknown_ref(repo):
    with patch("agent.tools._resolve_repo_checkout", return_value=repo):
        res = await execute_tool("git_merge", {"branch": "nope"}, None, project_id="t")
    assert "not found" in res
    assert "git_sync_repo" in res


def test_repo_protocol_in_prompt_when_bound():
    from agent.prompts import build_system_prompt
    spec = {"connection_id": "c", "owner": "octo", "repo": "demo",
            "default_branch": "main"}
    with patch("api.connections.get_project_repo_for_tools", return_value=spec):
        prompt = build_system_prompt(project_id="p1")
    assert "Repository work protocol" in prompt
    assert "octo/demo" in prompt
    assert "git_merge" in prompt


def test_repo_protocol_absent_without_binding():
    from agent.prompts import build_system_prompt
    with patch("api.connections.get_project_repo_for_tools", return_value=None):
        prompt = build_system_prompt(project_id="p1")
    assert "Repository work protocol" not in prompt
