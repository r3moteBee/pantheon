"""Fixes for issues found in the 2026-10-02 repo review: failed imports return 422, one
chat WebSocket route, one app-version resolver, retired phase_g tables, and autoresearch
as a job (the skill's code_execute path could never work)."""
from __future__ import annotations

import os
import sqlite3
import sys
import tempfile
from unittest.mock import patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))


@pytest.mark.asyncio
async def test_blocked_import_returns_422_with_the_scan_findings():
    from api.project_import import ImportResult, ScanResult
    from api.projects import import_project_endpoint

    class F:
        async def read(self):
            return b"not a zip"
    failed = ImportResult(success=False, message="Security scan failed - import blocked",
                          scan=ScanResult(passed=False, findings=[]))
    with patch("api.project_import.import_project", return_value=failed):
        resp = await import_project_endpoint(file=F(), target_id=None, target_name=None, components=None, overwrite=False)
    assert resp.status_code == 422
    import json
    body = json.loads(resp.body)
    assert body["success"] is False and "scan" in body


def test_chat_websocket_is_served_only_at_ws_chat():
    from main import app
    paths = [getattr(r, "path", "") for r in app.routes]
    assert "/ws/chat" in paths and "/api/ws/chat" not in paths


def test_one_app_version_for_health_and_self_doc():
    import main
    from utils.version import app_version
    assert main._APP_VERSION == app_version() != "unknown"
    from utils import self_doc
    assert not hasattr(self_doc, "_resolve_app_version")


def test_retired_phase_g_tables_are_dropped_only_when_empty(tmp_path):
    from config import get_settings
    import utils.chat_settings as cs
    db = tmp_path / "db"
    db.mkdir()
    conn = sqlite3.connect(db / "phase_g.db")
    conn.executescript("CREATE TABLE task_runs (id TEXT); CREATE TABLE project_repo_bindings (project_id TEXT);"
                       "CREATE TABLE project_mcp_enablement (project_id TEXT);"
                       "INSERT INTO project_mcp_enablement VALUES ('p');")
    conn.commit(); conn.close()
    with patch.object(get_settings(), "data_dir", tmp_path), patch.object(cs, "_ready", set()):
        cs._connect().close()
    tables = {r[0] for r in sqlite3.connect(db / "phase_g.db").execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "task_runs" not in tables and "project_repo_bindings" not in tables
    assert "project_mcp_enablement" in tables            # not empty: kept
    assert "project_settings" in tables


@pytest.mark.asyncio
async def test_autoresearch_job_keeps_only_improvements_and_saves_a_report(tmp_path):
    from jobs.handlers.autoresearch import handle_autoresearch
    from utils.autoresearch import AutoresearchRunner
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "target.py").write_text("DELAY = 5\n")
    (ws / "bench.py").write_text("import target\nprint(f'time: {target.DELAY}')\n")
    mutations = iter(["DELAY = 3\n", "DELAY = 4\n", "DELAY = 1\n"])

    async def fake_mutation(self, code, lang):
        return next(mutations)
    saved = {}

    class Store:
        def create(self, **kw):
            saved.update(kw)
            return {"id": "art-1"}

    class Ctx:
        project_id, job_id = "p", "job-123456789"
        payload = {"target_file": "target.py", "eval_cmd": f"{sys.executable} bench.py", "metric": "time",
                   "direction": "min", "iterations": 3}
        progress = []

        async def heartbeat(self, progress=None):
            self.progress.append(progress)

        def cancel_requested(self):
            return False
    with patch.object(AutoresearchRunner, "get_mutation", fake_mutation), \
         patch("agent.tools.workspace._get_workspace_base", return_value=ws.resolve()), \
         patch("artifacts.store.get_store", return_value=Store()):
        res = await handle_autoresearch(Ctx())
    assert res["initial_metric"] == 5 and res["best_metric"] == 1 and res["kept_rounds"] == [1, 3]
    assert (ws / "target.py").read_text() == "DELAY = 1\n"           # round 2's regression was reverted
    assert res["report_artifact_id"] == "art-1" and saved["path"].startswith("autoresearch/")
    assert any("Round 2/3: REGRESSION" in (p or "") for p in Ctx.progress)


@pytest.mark.asyncio
async def test_autoresearch_job_rejects_paths_outside_the_workspace(tmp_path):
    from jobs.handlers.autoresearch import handle_autoresearch

    class Ctx:
        project_id, job_id = "p", "j"
        payload = {"target_file": "../../etc/passwd", "eval_cmd": "true", "metric": "x"}
    with patch("agent.tools.workspace._get_workspace_base", return_value=tmp_path.resolve()):
        res = await handle_autoresearch(Ctx())
    assert res["status"] == "failed" and "not found in the project workspace" in res["error"]


@pytest.mark.asyncio
async def test_start_autoresearch_only_from_the_web_chat(tmp_path, monkeypatch):
    from agent import tools
    from jobs import store as job_store
    (tmp_path / "t.py").write_text("x = 1\n")
    created = []

    class Store:
        def create(self, **kw):
            created.append(kw)
            return {"id": "job-abcdefgh"}
    monkeypatch.setattr(job_store, "get_store", lambda: Store())
    args = {"target_file": "t.py", "eval_cmd": "python b.py", "metric": "time", "direction": "min"}
    with patch("agent.tools.workspace._get_workspace_base", return_value=tmp_path.resolve()):
        out = await tools.execute_tool("start_autoresearch", dict(args), None, interactive=False, host_exec=True)
        assert "refused" in out and created == []
        out = await tools.execute_tool("start_autoresearch", dict(args), None, interactive=True, host_exec=True)
    assert "Autoresearch queued" in out and created[0]["job_type"] == "autoresearch"
    assert "start_autoresearch" in tools.HOST_EXEC_TOOLS            # never offered to bots / background jobs
