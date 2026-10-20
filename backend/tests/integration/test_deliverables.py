"""Deliverables end up as artifacts: plans that write them with write_file
are switched to save_to_artifact, ledgers and scratch aren't echoed into
replies, workspace files can be copied into artifacts, and the ios-app skill
ships. From a marble-maze iOS task (2026-10-19) whose plan wrote every Swift
file with write_file, so nothing could be downloaded, built or pushed."""
from __future__ import annotations

import os
import tempfile
import uuid
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

MARBLE_PLAN = """1. Create project directory structure with `run_command`.
2. Write `MarblePhysics.swift` containing `CMMotionManager` logic with `write_file`.
3. Write `MazeView.swift` for the SwiftUI game interface and physics engine with `write_file`.
4. Write `MarbleMazeApp.swift` as the main entry point with `write_file`.
5. Write `README.md` with detailed instructions for opening the project in Xcode with `write_file`.
6. Write intermediate notes to scratch with write_file."""


def test_plan_steps_writing_deliverables_use_save_to_artifact():
    from agent.tools.tasks import fix_deliverable_steps
    plan, changed = fix_deliverable_steps(MARBLE_PLAN)
    assert changed == ["2", "3", "4", "5"]
    lines = plan.splitlines()
    assert "`save_to_artifact`" in lines[1] and "write_file" not in lines[1]
    assert lines[0] == MARBLE_PLAN.splitlines()[0]           # no file in it: untouched
    assert lines[5].endswith("with write_file.")              # scratch without a file name: untouched
    assert fix_deliverable_steps("1. Search with `web_search`.") == ("1. Search with `web_search`.", [])


@pytest.mark.asyncio
async def test_create_task_stores_the_fixed_plan_and_says_so():
    from agent.tools import execute_tool
    sched = AsyncMock(return_value="sched-1")
    with patch("tasks.scheduler.schedule_agent_task", sched):
        out = await execute_tool("create_task", {"name": "Marble", "description": "iOS game", "schedule": "now",
                                                 "plan": MARBLE_PLAN}, None, project_id="p", interactive=True)
    assert "PLAN ADJUSTED: step(s) 2, 3, 4, 5" in out
    stored = sched.call_args.kwargs["plan"]
    assert stored.count("save_to_artifact") == 4 and stored.count("write_file") == 1


def test_saved_echo_skips_ledgers_scratch_and_code():
    from agent.core import _echo_saved
    ledger = "---\ntype: task-ledger\ntask: x\n---\n- [done] a"
    assert not _echo_saved("update_artifact", {"id": "1", "content": ledger})
    assert not _echo_saved("save_to_artifact", {"path": "task-ledger/x.md", "content": "plain"})
    assert not _echo_saved("write_file", {"path": "notes.md", "content": "a brief"})
    assert not _echo_saved("save_to_artifact", {"path": "App/MazeView.swift", "content": "import SwiftUI"})
    assert _echo_saved("save_to_artifact", {"path": "research/brief.md", "content": "# Brief"})


@pytest.mark.asyncio
async def test_workspace_folder_copies_into_artifacts(tmp_path):
    from fastapi import FastAPI
    from api.files import router, _get_workspace
    from artifacts.store import ArtifactStore
    proj = "ws" + uuid.uuid4().hex[:8]
    ws = _get_workspace(proj)
    (ws / "workspace" / "MarbleMaze" / "App").mkdir(parents=True)
    (ws / "workspace" / "MarbleMaze" / "App" / "MazeView.swift").write_text("import SwiftUI\n")
    (ws / "workspace" / "MarbleMaze" / "README.md").write_text("# Marble\n")
    (ws / "workspace" / "MarbleMaze" / "icon.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\x00")
    (ws / "workspace" / "MarbleMaze" / ".DS_Store").write_bytes(b"junk")
    store = ArtifactStore(db_path=str(tmp_path / "a.db"), blobs_dir=tmp_path / "blobs")
    app = FastAPI()
    app.include_router(router, prefix="/api")
    with patch("artifacts.store.get_store", return_value=store), patch("artifacts.embedder.schedule_embed"):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post("/api/files/to-artifacts", json={"project_id": proj, "path": "workspace/MarbleMaze"})
            assert r.status_code == 200, r.text
            out = r.json()
            assert out["copied"] == 3 and out["folder"] == f"{proj}/MarbleMaze"
            view = store.get_by_path(proj, f"{proj}/MarbleMaze/App/MazeView.swift")
            assert view["content"] == "import SwiftUI\n" and view["content_type"] == "text/x-swift"
            assert store.get_by_path(proj, f"{proj}/MarbleMaze/icon.png")["content_type"] == "image/png"
            assert store.get_by_path(proj, f"{proj}/MarbleMaze/.DS_Store") is None
            # again after an edit: the artifact gets a new version, not a copy
            (ws / "workspace" / "MarbleMaze" / "README.md").write_text("# Marble v2\n")
            again = (await c.post("/api/files/to-artifacts", json={"project_id": proj, "path": "workspace/MarbleMaze"})).json()
            assert sorted(i.get("action") or "skipped" for i in again["items"]) == ["skipped", "updated", "updated"]
            assert store.get_by_path(proj, f"{proj}/MarbleMaze/README.md")["content"] == "# Marble v2\n"
            assert (await c.post("/api/files/to-artifacts", json={"project_id": proj, "path": "../etc"})).status_code == 400
            assert (await c.post("/api/files/to-artifacts", json={"project_id": proj, "path": "nope"})).status_code == 404


def test_ios_app_skill_ships():
    from skills.registry import get_skill_registry
    sk = get_skill_registry().get("ios-app")
    assert sk is not None and sk.is_bundled
    assert "import into xcode" in sk.triggers
    text = sk.instructions
    assert "save_to_artifact" in text and "project.yml" in text and "run_build" in text
    assert "Do NOT write a `.xcodeproj`" in text


@pytest.mark.asyncio
async def test_plan_naming_a_skill_attaches_it():
    from agent.tools import execute_tool
    sched = AsyncMock(return_value="sched-2")
    plan = "1. Invoke the `/ios-app` skill with the requirements.\n2. Save the project under `MarbleMaze/`."
    with patch("tasks.scheduler.schedule_agent_task", sched):
        out = await execute_tool("create_task", {"name": "Marble", "description": "see https://x.test/a/b and /nope",
                                                 "schedule": "now", "plan": plan}, None, project_id="p", interactive=True)
    assert sched.call_args.kwargs["skill_name"] == "ios-app" and "SKILL ATTACHED" in out
    with patch("tasks.scheduler.schedule_agent_task", sched):
        await execute_tool("create_task", {"name": "x", "description": "read /etc/hosts", "schedule": "now",
                                           "plan": "1. Search with `web_search`."}, None, project_id="p", interactive=True)
    assert sched.call_args.kwargs["skill_name"] is None


@pytest.mark.asyncio
async def test_write_file_deliverables_are_also_artifacts(tmp_path):
    from agent.tools import execute_tool
    from artifacts.store import ArtifactStore
    proj = "wf" + uuid.uuid4().hex[:8]
    store = ArtifactStore(db_path=str(tmp_path / "a.db"), blobs_dir=tmp_path / "blobs")
    run = lambda path, content="x": execute_tool("write_file", {"path": path, "content": content}, None, project_id=proj)
    with patch("artifacts.store.get_store", return_value=store), patch("artifacts.embedder.schedule_embed"):
        out = await run("workspace/MarbleMaze/Core/MotionManager.swift", "import CoreMotion")
        assert f"Also saved as artifact {proj}/MarbleMaze/Core/MotionManager.swift" in out
        a = store.get_by_path(proj, f"{proj}/MarbleMaze/Core/MotionManager.swift")
        assert a["content"] == "import CoreMotion" and a["content_type"] == "text/x-swift"
        await run("MarbleMaze/README.md", "# Marble")
        assert store.get_by_path(proj, f"{proj}/MarbleMaze/README.md") is not None
        for scratch in ("tmp/check.py", "MarbleMaze/.build/x.swift", "repos/o__r/main.py", "out.log", "data.bin"):
            assert "Also saved" not in await run(scratch), scratch
        # rewrite: a new version; the same content again: none
        await run("workspace/MarbleMaze/Core/MotionManager.swift", "import CoreMotion // v2")
        await run("workspace/MarbleMaze/Core/MotionManager.swift", "import CoreMotion // v2")
        assert len(store.list_versions(a["id"])) == 2
        assert store.get(a["id"])["content"] == "import CoreMotion // v2"
