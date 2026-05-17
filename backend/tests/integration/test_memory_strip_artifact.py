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
