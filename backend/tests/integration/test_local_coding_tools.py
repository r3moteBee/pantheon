"""Tests for the local coding loop tools: git_sync_repo + run_command.

git_sync_repo network calls are mocked at the _run_git_cmd layer (same
convention as test_git_tools.py); run_command executes for real through
the subprocess sandbox.
"""
import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")

import pytest
from unittest.mock import patch, AsyncMock
from pathlib import Path

from agent.tools import execute_tool, _repo_checkout_dir


# ── run_command ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
@patch("agent.tools._resolve_repo_checkout", return_value=None)
@patch("agent.tools._get_workspace_base")
async def test_run_command_executes_in_workspace(mock_base, _mock_checkout, tmp_path):
    mock_base.return_value = tmp_path
    res = await execute_tool(
        "run_command", {"command": "echo hello && pwd"}, None, project_id="test")
    assert "exit_code: 0" in res
    assert "hello" in res
    assert str(tmp_path) in res  # ran with cwd inside the workspace


@pytest.mark.asyncio
@patch("agent.tools._resolve_repo_checkout")
@patch("agent.tools._get_workspace_base")
async def test_run_command_prefers_repo_checkout(mock_base, mock_checkout, tmp_path):
    checkout = tmp_path / "repos" / "o__r"
    checkout.mkdir(parents=True)
    mock_base.return_value = tmp_path
    mock_checkout.return_value = checkout
    res = await execute_tool("run_command", {"command": "pwd"}, None, project_id="test")
    assert "exit_code: 0" in res
    assert str(checkout) in res


@pytest.mark.asyncio
@patch("agent.tools._resolve_repo_checkout", return_value=None)
@patch("agent.tools._get_workspace_base")
async def test_run_command_workdir_traversal_blocked(mock_base, _mock_checkout, tmp_path):
    base = tmp_path / "ws"
    base.mkdir()
    mock_base.return_value = base
    res = await execute_tool(
        "run_command", {"command": "pwd", "workdir": "../"}, None, project_id="test")
    assert "escapes the workspace" in res


@pytest.mark.asyncio
@patch("agent.tools._resolve_repo_checkout", return_value=None)
@patch("agent.tools._get_workspace_base")
async def test_run_command_missing_workdir(mock_base, _mock_checkout, tmp_path):
    mock_base.return_value = tmp_path
    res = await execute_tool(
        "run_command", {"command": "pwd", "workdir": "nope"}, None, project_id="test")
    assert "workdir not found" in res


@pytest.mark.asyncio
async def test_run_command_empty():
    res = await execute_tool("run_command", {"command": "  "}, None, project_id="test")
    assert "empty command" in res


@pytest.mark.asyncio
@patch("agent.tools._resolve_repo_checkout", return_value=None)
@patch("agent.tools._get_workspace_base")
async def test_run_command_timeout(mock_base, _mock_checkout, tmp_path):
    mock_base.return_value = tmp_path
    res = await execute_tool(
        "run_command",
        {"command": "sleep 5", "timeout_seconds": 1},
        None, project_id="test")
    assert "timed_out: true" in res


# ── git_sync_repo ───────────────────────────────────────────────────────────

_SPEC = {"connection_id": "c1", "owner": "octo", "repo": "demo",
         "default_branch": "main"}


@pytest.mark.asyncio
@patch("api.connections.get_project_repo_for_tools", return_value=None)
async def test_git_sync_repo_no_binding(_mock_spec):
    res = await execute_tool("git_sync_repo", {}, None, project_id="test")
    assert "No repo bound" in res


@pytest.mark.asyncio
@patch("api.connections.get_token", return_value="tok-secret")
@patch("api.connections.get_project_repo_for_tools", return_value=_SPEC)
@patch("agent.tools._run_git_cmd", new_callable=AsyncMock)
@patch("agent.tools._get_workspace_base")
async def test_git_sync_repo_clone_flow(mock_base, mock_run, _mock_spec, _mock_token, tmp_path):
    mock_base.return_value = tmp_path
    mock_run.return_value = (0, "abc123 init", "")

    res = await execute_tool("git_sync_repo", {}, None, project_id="test")

    dest = tmp_path / "repos" / "octo__demo"
    calls = [c.args for c in mock_run.await_args_list]
    # Clone uses the token URL, from the parent dir, no auto-init
    assert calls[0][0][:2] == ["clone", "https://tok-secret@github.com/octo/demo.git"]
    assert calls[0][1] == dest.parent
    # Token must not persist in the remote URL
    assert ["remote", "set-url", "origin", "https://github.com/octo/demo.git"] in [c[0] for c in calls]
    assert "Cloned octo/demo" in res
    assert "repos/octo__demo" in res
    assert "tok-secret" not in res


@pytest.mark.asyncio
@patch("api.connections.get_token", return_value="tok-secret")
@patch("api.connections.get_project_repo_for_tools", return_value=_SPEC)
@patch("agent.tools._run_git_cmd", new_callable=AsyncMock)
@patch("agent.tools._get_workspace_base")
async def test_git_sync_repo_update_flow(mock_base, mock_run, _mock_spec, _mock_token, tmp_path):
    mock_base.return_value = tmp_path
    dest = tmp_path / "repos" / "octo__demo"
    (dest / ".git").mkdir(parents=True)  # simulate existing checkout
    mock_run.return_value = (0, "ok", "")

    res = await execute_tool("git_sync_repo", {"branch": "dev"}, None, project_id="test")

    calls = [c.args[0] for c in mock_run.await_args_list]
    assert calls[0][0] == "fetch"
    assert ["checkout", "dev"] in calls
    assert "Updated existing checkout" in res
    assert "tok-secret" not in res


@pytest.mark.asyncio
@patch("api.connections.get_token", return_value="tok-secret")
@patch("api.connections.get_project_repo_for_tools", return_value=_SPEC)
@patch("agent.tools._run_git_cmd", new_callable=AsyncMock)
@patch("agent.tools._get_workspace_base")
async def test_git_sync_repo_clone_failure_redacts_token(mock_base, mock_run, _mock_spec, _mock_token, tmp_path):
    mock_base.return_value = tmp_path
    mock_run.return_value = (128, "", "fatal: could not read from https://tok-secret@github.com/octo/demo.git")

    res = await execute_tool("git_sync_repo", {}, None, project_id="test")
    assert "Clone failed" in res
    assert "tok-secret" not in res
    assert "********" in res


# ── checkout resolution shared by git_* tools ───────────────────────────────

def test_repo_checkout_dir_layout(tmp_path):
    with patch("agent.tools._get_workspace_base", return_value=tmp_path):
        d = _repo_checkout_dir("p1", "octo", "demo")
    assert d == tmp_path / "repos" / "octo__demo"


@pytest.mark.asyncio
@patch("api.connections.get_project_repo_for_tools", return_value=_SPEC)
@patch("agent.tools._run_git_cmd", new_callable=AsyncMock)
@patch("agent.tools._get_workspace_base")
async def test_git_status_uses_checkout_when_present(mock_base, mock_run, _mock_spec, tmp_path):
    mock_base.return_value = tmp_path
    dest = tmp_path / "repos" / "octo__demo"
    (dest / ".git").mkdir(parents=True)
    mock_run.return_value = (0, "M  app.py", "")

    res = await execute_tool("git_status", {}, None, project_id="test")
    assert res == "M  app.py"
    mock_run.assert_called_once_with(["status", "--porcelain"], dest)
