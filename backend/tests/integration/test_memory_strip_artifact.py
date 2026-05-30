"""Verify graph.strip_artifact removes the artifact's 1:1 nodes but
preserves shared topic nodes that other artifacts reference.

Run: pytest backend/tests/integration/test_memory_strip_artifact.py -v
"""
from __future__ import annotations

import asyncio
import os
import tempfile

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from memory.graph import GraphMemory  # noqa: E402


@pytest.fixture
def graph(tmp_path):
    return GraphMemory(project_id="p1", db_path=str(tmp_path / "graph.db"))


def test_strip_artifact_removes_owned_nodes(graph):
    async def run():
        a_node = await graph.add_node(
            "concept", "doc-A", metadata={"artifact_id": "art-1"}
        )
        topic = await graph.add_node("concept", "shared-topic", metadata={})
        await graph.add_edge_by_label("doc-A", "shared-topic", "DISCUSSES")

        await graph.strip_artifact("art-1")

        # The artifact's own node is gone; the shared topic stays.
        assert await graph.get_node(a_node) is None
        assert await graph.get_node(topic) is not None
    asyncio.run(run())


def test_strip_artifact_leaves_other_artifacts_alone(graph):
    async def run():
        await graph.add_node("concept", "doc-A", metadata={"artifact_id": "art-1"})
        b_node = await graph.add_node("concept", "doc-B", metadata={"artifact_id": "art-2"})
        await graph.strip_artifact("art-1")
        assert await graph.get_node(b_node) is not None
    asyncio.run(run())


def test_strip_artifact_is_idempotent(graph):
    async def run():
        await graph.add_node("concept", "doc-A", metadata={"artifact_id": "art-1"})
        await graph.strip_artifact("art-1")
        await graph.strip_artifact("art-1")  # second call must not raise
    asyncio.run(run())


from memory.semantic import SemanticMemory  # noqa: E402


@pytest.fixture
def semantic(tmp_path, monkeypatch):
    from config import get_settings
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHROMA_HOST", "")
    get_settings.cache_clear()
    get_settings()
    # SemanticMemory uses ChromaDB under DATA_DIR/chroma; using tmp_path
    # gives each test an isolated collection.
    sm = SemanticMemory(project_id="p1")
    get_settings.cache_clear()
    return sm


def test_strip_artifact_deletes_matching_chunks(semantic):
    async def run():
        await semantic.store(
            content="chunk-1 from artifact A",
            metadata={"artifact_id": "art-1", "kind": "artifact_chunk"},
        )
        await semantic.store(
            content="chunk-2 from artifact A",
            metadata={"artifact_id": "art-1", "kind": "artifact_chunk"},
        )
        await semantic.store(
            content="chunk from artifact B",
            metadata={"artifact_id": "art-2", "kind": "artifact_chunk"},
        )
        n = await semantic.strip_artifact("art-1")
        assert n == 2
        # Only the surviving artifact's chunks remain.
        remaining = await semantic.list_memories(limit=10)
        assert len(remaining) == 1
        assert remaining[0]["metadata"]["artifact_id"] == "art-2"
    asyncio.run(run())
