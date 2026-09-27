"""Store methods that replaced ad-hoc sqlite3.connect sites in the API /
job layers (conversation title/metadata/delete, project stats, graph path
edges, node counts), plus the read-only export connection and the
runtime-settings reader that moved out of the api.settings router.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from memory.episodic import EpisodicMemory  # noqa: E402
from memory.graph import GraphMemory  # noqa: E402


@pytest.fixture
def ep(tmp_path):
    return EpisodicMemory(db_path=str(tmp_path / "episodic.db"))


def _journal_mode(path: str) -> str:
    conn = sqlite3.connect(path)
    try:
        return conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()


async def test_episodic_init_uses_wal(ep):
    assert _journal_mode(ep.db_path) == "wal"


async def test_set_conversation_title_only_fills_empty(ep):
    await ep.save_message(session_id="s1", project_id="p", role="user", content="hi")
    assert await ep.set_conversation_title("s1", "Task A") is True
    assert (await ep.get_conversation("s1"))["title"] == "Task A"
    # Already titled → untouched by default
    assert await ep.set_conversation_title("s1", "Task B") is False
    assert (await ep.get_conversation("s1"))["title"] == "Task A"
    # Explicit overwrite
    assert await ep.set_conversation_title("s1", "Task C", only_if_empty=False) is True
    assert (await ep.get_conversation("s1"))["title"] == "Task C"
    # Unknown session is a no-op
    assert await ep.set_conversation_title("nope", "X") is False


async def test_merge_conversation_metadata_creates_then_merges(ep):
    import json
    await ep.merge_conversation_metadata("s2", {"active_personas": ["a"]})
    conv = await ep.get_conversation("s2")
    assert conv["project_id"] == "default" and conv["title"] == "New Chat"
    assert json.loads(conv["metadata"]) == {"active_personas": ["a"]}
    await ep.merge_conversation_metadata("s2", {"pinned": True})
    conv = await ep.get_conversation("s2")
    assert json.loads(conv["metadata"]) == {"active_personas": ["a"], "pinned": True}


async def test_delete_conversation_removes_messages(ep):
    await ep.save_message(session_id="s3", project_id="p", role="user", content="x")
    await ep.save_message(session_id="keep", project_id="p", role="user", content="y")
    await ep.delete_conversation("s3")
    assert await ep.get_conversation("s3") is None
    assert await ep.get_history(session_id="s3") == []
    assert len(await ep.get_history(session_id="keep")) == 1


async def test_project_stats(ep):
    await ep.save_message(session_id="a", project_id="p1", role="user", content="1")
    await ep.save_message(session_id="a", project_id="p1", role="assistant", content="2")
    await ep.save_message(session_id="b", project_id="p2", role="user", content="3")
    stats = await ep.project_stats("p1")
    assert stats["messages"] == 2 and stats["conversations"] == 1
    assert set(stats["project_ids_in_db"]) == {"p1", "p2"}


async def test_graph_edges_along_path_and_count(tmp_path):
    g = GraphMemory(project_id="p1", db_path=str(tmp_path / "graph.db"))
    await g.add_edge_by_label("A", "B", "RELATES_TO")
    await g.add_edge_by_label("C", "B", "MENTIONS")  # reverse direction
    other = GraphMemory(project_id="p2", db_path=g.db_path)
    await other.add_node("concept", "Z")
    a = await g.get_node_by_label("A")
    b = await g.get_node_by_label("B")
    c = await g.get_node_by_label("C")
    edges = await g.edges_along_path([a, b, c])
    assert [e["relationship"] for e in edges] == ["RELATES_TO", "MENTIONS"]
    assert edges[0]["source"] == a["id"] and edges[0]["target"] == b["id"]
    assert edges[1]["source"] == c["id"] and edges[1]["target"] == b["id"]
    # Pair with no edge is skipped; other projects' edges never match
    assert await g.edges_along_path([a, c]) == []
    assert await other.edges_along_path([a, b]) == []
    assert await g.count_nodes() == 3
    assert await other.count_nodes() == 1


def test_export_connection_is_read_only(tmp_path):
    from api.project_export import _connect_ro
    path = tmp_path / "x.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (a)")
    conn.commit()
    conn.close()
    ro = _connect_ro(str(path))
    try:
        assert ro.execute("SELECT count(*) FROM t").fetchone()[0] == 0
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("INSERT INTO t VALUES (1)")
    finally:
        ro.close()
    # Missing file is an error, not a silently created empty DB
    with pytest.raises(sqlite3.OperationalError):
        _connect_ro(str(tmp_path / "missing.db")).execute("SELECT 1")
    assert not (tmp_path / "missing.db").exists()


def test_runtime_chunk_settings_vault_over_config(monkeypatch):
    from utils import runtime_settings
    import secrets.vault as vault_mod
    from config import get_settings
    cfg = get_settings()
    vals = {"file_chunk_size": "1234", "file_chunk_overlap": "bogus"}
    monkeypatch.setattr(vault_mod, "get_vault",
                        lambda: SimpleNamespace(get_secret=lambda k: vals.get(k)))
    size, overlap, strategy = runtime_settings.get_active_chunk_settings()
    assert size == 1234
    assert overlap == cfg.file_chunk_overlap  # unparsable → config default
    assert strategy == cfg.file_chunk_strategy
    # The router re-exports the same function
    from api import settings as api_settings
    assert api_settings.get_active_chunk_settings is runtime_settings.get_active_chunk_settings
