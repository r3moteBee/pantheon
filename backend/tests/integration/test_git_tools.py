import pytest
from unittest.mock import patch, AsyncMock
from pathlib import Path
from agent.tools import execute_tool

@pytest.mark.asyncio
@patch("agent.tools._get_workspace_base")
@patch("agent.tools._run_git_cmd")
async def test_git_status_tool(mock_run, mock_get_workspace):
    mock_get_workspace.return_value = Path("/app/data/projects/test/workspace")
    mock_run.return_value = (0, "M  file.txt", "")
    
    res = await execute_tool("git_status", {}, None, project_id="test")
    assert res == "M  file.txt"
    mock_run.assert_called_once_with(["status", "--porcelain"], Path("/app/data/projects/test/workspace"))

@pytest.mark.asyncio
@patch("agent.tools._get_workspace_base")
@patch("agent.tools._run_git_cmd")
async def test_git_create_branch_new(mock_run, mock_get_workspace):
    mock_get_workspace.return_value = Path("/app/data/projects/test/workspace")
    mock_run.return_value = (0, "", "")
    
    res = await execute_tool("git_create_branch", {"branch_name": "feature-test"}, None, project_id="test")
    assert "Created and switched" in res
    mock_run.assert_called_once_with(["checkout", "-b", "feature-test"], Path("/app/data/projects/test/workspace"))

@pytest.mark.asyncio
@patch("agent.tools._get_workspace_base")
@patch("agent.tools._run_git_cmd")
async def test_git_commit_all(mock_run, mock_get_workspace):
    mock_get_workspace.return_value = Path("/app/data/projects/test/workspace")
    mock_run.return_value = (0, "Committed", "")
    
    res = await execute_tool("git_commit", {"message": "feat: test"}, None, project_id="test")
    assert "Committed successfully" in res
    # First stages all (add .) then commits
    mock_run.assert_any_call(["add", "."], Path("/app/data/projects/test/workspace"))
    mock_run.assert_any_call(["commit", "-m", "feat: test"], Path("/app/data/projects/test/workspace"))
