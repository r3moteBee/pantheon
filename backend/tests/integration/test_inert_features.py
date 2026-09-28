"""Batch 2 of the 2026-09-27 review: features that silently did nothing."""
from __future__ import annotations

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))


@pytest.fixture
def vault(monkeypatch, tmp_path):
    from secrets import vault as _v
    fresh = _v.SecretsVault(db_path=str(tmp_path / "vault.db"), master_key="k")
    monkeypatch.setattr(_v, "_vault_instance", fresh)
    monkeypatch.setattr(_v, "_cache", {})
    return fresh


@pytest.fixture
def data_dir(monkeypatch, tmp_path):
    import config
    real = config.get_settings()
    fake = real.model_copy(update={"data_dir": tmp_path / "data"})
    monkeypatch.setattr(config, "get_settings", lambda: fake)
    from utils import chat_settings
    chat_settings._ready.clear()
    return fake


# ── BUG-1: consolidate reads the session from episodic ──────────────────────

def _manager(session_id="s1"):
    from memory.manager import MemoryManager
    m = MemoryManager.__new__(MemoryManager)
    m.project_id, m.session_id = "p", session_id
    m.episodic = SimpleNamespace(get_recent_messages=AsyncMock(return_value=[
        {"role": "user", "content": "we picked vendor A over B"},
        {"role": "assistant", "content": "noted: A, because of price"},
    ]))
    m.semantic = SimpleNamespace(store=AsyncMock(return_value="doc-1"))
    return m


@pytest.mark.asyncio
async def test_consolidate_uses_episodic_history():
    m = _manager()
    fake_llm = SimpleNamespace(model="x", chat_complete=AsyncMock(return_value={"content": "They chose A."}))
    with patch("models.provider.get_provider_for", return_value=fake_llm), \
         patch("memory.extraction.run_extraction", AsyncMock(return_value={
             "entities": 2, "relationships": 1, "facts": 1, "user_preferences": 0})) as rx:
        out = await m.consolidate_session()
    assert "Summary stored as doc-1" in out and "2 entities" in out
    m.episodic.get_recent_messages.assert_awaited_once_with(project_id="p", session_id="s1", limit=40)
    assert rx.await_args.kwargs["messages"][0]["content"] == "we picked vendor A over B"
    assert "They chose A." in m.semantic.store.await_args.kwargs["content"]


@pytest.mark.asyncio
async def test_consolidate_without_session_says_so():
    assert "No session" in await _manager(session_id="current").consolidate_session()
    assert "No session" in await _manager(session_id=None).consolidate_session()


def test_working_memory_module_is_gone():
    import importlib.util
    assert importlib.util.find_spec("memory.working") is None


# ── BUG-5: remember(tier="graph") really writes the graph ───────────────────

@pytest.mark.asyncio
async def test_remember_graph_runs_extraction():
    m = _manager()
    with patch("memory.extraction.run_extraction", AsyncMock(return_value={
            "entities": 3, "relationships": 2})) as rx:
        ref = await m.remember("Nvidia acquired Run:ai in 2024", tier="graph")
    assert ref == "stored:graph:3 entities, 2 relationships"
    assert rx.await_args.kwargs["min_messages"] == 1


@pytest.mark.asyncio
async def test_remember_working_is_a_session_note():
    m = _manager()
    m.episodic.add_note = AsyncMock(return_value="n1")
    ref = await m.remember("scratch", tier="working", metadata={"tags": ["x"]})
    assert ref == "stored:episodic:n1"
    assert m.episodic.add_note.await_args.kwargs["tags"] == ["x", "working"]


def test_remember_schema_matches_tiers():
    from agent.tools import TOOL_SCHEMAS
    spec = next(t for t in TOOL_SCHEMAS if t["function"]["name"] == "remember")["function"]
    assert spec["parameters"]["properties"]["tier"]["enum"] == ["episodic", "semantic", "graph"]
    assert "'working'" not in spec["description"]


# ── ARCH-1: archival reads and writes one directory ─────────────────────────

def test_archival_uses_configured_data_dir(data_dir):
    from memory.archival import ArchivalMemory
    assert ArchivalMemory(project_id="p").notes_dir == data_dir.data_dir / "projects" / "p" / "notes"


def test_archival_rejects_bad_project_ids(data_dir):
    from memory.archival import ArchivalMemory
    from utils.paths import InvalidProjectId
    with pytest.raises(InvalidProjectId):
        ArchivalMemory(project_id="../..")


def test_stray_notes_are_moved(data_dir, monkeypatch, tmp_path):
    from memory import archival
    cwd = tmp_path / "cwd"
    stray = cwd / "data" / "projects" / "p1" / "notes"
    stray.mkdir(parents=True)
    (stray / "a.md").write_text("A")
    (stray / "b.md").write_text("B-stray")
    canon = data_dir.data_dir / "projects" / "p1" / "notes"
    canon.mkdir(parents=True)
    (canon / "b.md").write_text("B-canonical")
    monkeypatch.chdir(cwd)
    assert archival.migrate_stray_notes() == 1
    assert (canon / "a.md").read_text() == "A"
    assert (canon / "b.md").read_text() == "B-canonical"       # never overwritten
    assert (stray / "b.md").exists()


# ── BUG-7: repo protocol only where git tools exist ────────────────────────

def test_repo_protocol_follows_host_exec(vault):
    from agent import prompts
    spec = {"owner": "o", "repo": "r", "default_branch": "main"}
    with patch("api.connections.get_project_repo_for_tools", return_value=spec):
        with_exec = prompts.build_system_prompt(project_id="p", host_exec=True)
        without = prompts.build_system_prompt(project_id="p", host_exec=False)
    assert "git_sync_repo" in with_exec and "run_command" in with_exec
    assert "git_sync_repo" not in without and "`run_command`" not in without
    assert "o/r" in without and "start_coding_task" in without


# ── DUP-7: routing outcomes ignore the synthetic recall event ──────────────

@pytest.mark.asyncio
async def test_context_loaded_is_not_a_tool_call(vault, monkeypatch, tmp_path):
    from llm_config import usage
    monkeypatch.setattr(usage, "get_settings", lambda: SimpleNamespace(db_dir=tmp_path))
    from api.chat import _stream_turn

    class Agent:
        provider = None
        def _get_working_messages(self):
            return []
        async def chat(self, message, stream=True):
            yield {"type": "tool_call", "name": "context_loaded", "args": {}}
            yield {"type": "tool_result", "name": "context_loaded", "result": "Error-looking recall text"}
            yield {"type": "tool_call", "name": "read_artifact", "args": {}}
            yield {"type": "tool_result", "name": "read_artifact", "result": "Artifact not found: id=x"}
            yield {"type": "done", "full_response": "ok"}

    async def send(ev):
        pass
    await _stream_turn(Agent(), "hi", "sess-dup7", send)
    row = usage.decision_rows(1)[-1]
    assert (row["tool_calls"], row["tool_errors"]) == (1, 1)


def test_tool_error_patterns():
    from agent.tool_results import is_error
    for bad in ("Error executing x", "Download refused: private", "Access denied: path outside",
                "Unknown tool: foo", "Artifact not found: id=1", "Image generation failed: 500",
                "Refusing to delete the default project"):
        assert is_error(bad), bad
    for ok in ("3 results for nvidia", "Saved artifact demo/x.md", "Stored in semantic memory"):
        assert not is_error(ok), ok


# ── BUG-2/3: one read path for chat settings ───────────────────────────────

def test_chat_settings_project_overrides_global(vault, data_dir):
    from utils import chat_settings as cs
    vault.set_secret("personality_weight", "strong")
    vault.set_secret("memory_recall_enabled", "false")
    eff = cs.effective("p1")
    assert (eff["tone_weight"], eff["memory_recall"], eff["context_focus"]) == ("strong", False, "balanced")
    assert eff["source"]["tone_weight"] == "global"
    cs.set_overrides("p1", {"tone_weight": "minimal", "memory_recall": True, "skill_discovery": "suggest"})
    eff = cs.effective("p1")
    assert (eff["tone_weight"], eff["memory_recall"], eff["skill_discovery"]) == ("minimal", True, "suggest")
    assert eff["source"] == {"tone_weight": "project", "context_focus": "global", "memory_recall": "project"}
    assert vault.get_secret("skill_discovery_p1") == "suggest"    # same key chat reads
    cs.set_overrides("p1", {"tone_weight": None})
    assert cs.effective("p1")["tone_weight"] == "strong"
    assert cs.effective("p2")["tone_weight"] == "strong"
    for bad in ({"tone_weight": "focused"}, {"context_focus": "strong"}, {"nope": 1},
                {"skill_discovery": "always"}):
        with pytest.raises(ValueError):
            cs.set_overrides("p1", bad)


def test_old_rows_with_wrong_scale_are_ignored(vault, data_dir):
    from utils import chat_settings as cs
    with cs._connect() as conn:
        conn.execute("INSERT INTO project_settings (project_id, tone_weight, context_focus, updated_at) "
                     "VALUES ('old', 'focused', 'broad', 'x')")
    assert cs.overrides("old") == {"context_focus": "broad"}


@pytest.mark.asyncio
async def test_project_settings_api(vault, data_dir):
    from fastapi import HTTPException
    from api.projects import get_project_settings, update_project_settings
    # "persona" is no longer a chat setting (presets write soul.md); ignored.
    out = await update_project_settings("p9", {"context_focus": "focused", "persona": "hermes"})
    assert out["overrides"] == {"context_focus": "focused"} and "persona" not in out
    assert out["effective"]["context_focus"] == "focused"
    with pytest.raises(HTTPException):
        await update_project_settings("p9", {"tone_weight": "loud"})
    assert (await get_project_settings("p9"))["effective"]["tone_weight"] == "balanced"


@pytest.mark.asyncio
async def test_agent_reads_project_settings(vault, data_dir):
    """AgentCore takes tone/focus/recall from the project, not just global."""
    from utils import chat_settings as cs
    from agent.core import AgentCore
    cs.set_overrides("p5", {"tone_weight": "minimal", "memory_recall": False})
    captured = {}

    def fake_prompt(**kw):
        captured.update(kw)
        return "sys"

    class Prov:
        model = "m"
        async def chat(self, messages, tools=None, stream=True):
            yield {"type": "text_delta", "content": "hi"}
            yield {"type": "done"}

    mem = SimpleNamespace(recall=AsyncMock(return_value=[]))
    agent = AgentCore(provider=Prov(), memory_manager=mem, project_id="p5", session_id="s")
    with patch("agent.core.build_system_prompt", side_effect=fake_prompt):
        async for _ in agent.chat("hello", stream=True):
            pass
    assert captured["personality_weight"] == "minimal"
    mem.recall.assert_not_awaited()            # recall off for this project
