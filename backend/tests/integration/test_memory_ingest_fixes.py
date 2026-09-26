"""Batch-4 regressions: memory tiers + ingest pipeline."""
from __future__ import annotations

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

from memory.graph import GraphMemory  # noqa: E402


@pytest.fixture
def graph(tmp_path):
    return GraphMemory(project_id="p1", db_path=str(tmp_path / "graph.db"))


@pytest.fixture
def semantic(tmp_path, monkeypatch):
    from config import get_settings
    from memory.semantic import SemanticMemory
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHROMA_HOST", "")
    get_settings.cache_clear()
    get_settings()
    sm = SemanticMemory(project_id="p1")
    get_settings.cache_clear()
    return sm


# ── Chunker ──────────────────────────────────────────────────────────────────

def test_single_newline_transcript_is_split():
    from memory.chunker import _chunk_by_paragraphs
    transcript = "\n".join(f"segment {i} some spoken words here." for i in range(3000))
    chunks = _chunk_by_paragraphs(transcript, 1000, 100)
    assert len(chunks) > 50
    assert max(len(c["content"]) for c in chunks) <= 1000 + 100 + 2


def test_unbroken_text_falls_back_to_fixed_windows():
    from memory.chunker import _chunk_by_paragraphs
    chunks = _chunk_by_paragraphs("x" * 5000, 1000, 100)
    assert len(chunks) >= 5


# ── Graph ────────────────────────────────────────────────────────────────────

def test_merge_nodes_moves_all_edges_without_self_loops_or_dupes(graph):
    async def run():
        can = await graph.add_node("concept", "LLM")
        dep = await graph.add_node("concept", "Large Language Model")
        other = await graph.add_node("concept", "GPU")
        doc = await graph.add_node("concept", "doc")
        await graph.add_edge(dep, other, "RELATED")
        await graph.add_edge(doc, dep, "DISCUSSES")
        await graph.add_edge(doc, can, "DISCUSSES")        # would duplicate
        await graph.add_edge(can, dep, "SIMILAR")           # would self-loop
        await graph.add_edge(dep, dep, "SELF")              # would self-loop
        moved = await graph.merge_nodes(can, dep)
        assert moved == 1                                   # only dep->other
        assert await graph.get_node(dep) is None
        with graph._connect() as conn:
            rows = conn.execute(
                "SELECT node_a_id, node_b_id, relationship FROM graph_edges"
            ).fetchall()
        edges = {(r[0], r[1], r[2]) for r in rows}
        assert (can, other, "RELATED") in edges
        assert sum(1 for e in edges if e == (doc, can, "DISCUSSES")) == 1
        assert all(a != b for a, b, _ in edges)
    asyncio.run(run())


def test_merge_nodes_handles_more_than_10k_edges(graph):
    async def run():
        can = await graph.add_node("concept", "can")
        dep = await graph.add_node("concept", "dep")
        hub = await graph.add_node("concept", "hub")
        await graph.add_edge(dep, hub, "OLDEST")
        # Bulk-insert newer unrelated edges that used to push it out of
        # the list_edges(limit=10_000) window.
        import uuid
        from memory.graph import _now_iso
        with graph._connect() as conn:
            conn.executemany(
                "INSERT INTO graph_edges (id, project_id, node_a_id, node_b_id, relationship, weight, created_at) "
                "VALUES (?, 'p1', ?, ?, 'X', 1.0, ?)",
                [(str(uuid.uuid4()), hub, can, _now_iso()) for _ in range(10_050)],
            )
            conn.commit()
        await graph.merge_nodes(can, dep)
        with graph._connect() as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM graph_edges WHERE node_a_id=? AND relationship='OLDEST'", (can,)
            ).fetchone()[0]
        assert n == 1
    asyncio.run(run())


def test_add_node_merges_metadata(graph):
    async def run():
        nid = await graph.add_node("concept", "NVIDIA", metadata={"source_file": "a.md", "url": "u"})
        await graph.add_node("concept", "NVIDIA", metadata={"source_file": "b.md", "url": ""})
        node = await graph.get_node(nid)
        md = node["metadata"]
        assert md["source_file"] == "b.md"
        assert md["url"] == "u"          # empty string didn't clobber
    asyncio.run(run())


def test_k_shortest_paths_are_distinct(graph):
    async def run():
        for lbl in "ABCDE":
            await graph.add_node("concept", lbl)
        for a, b in [("A", "B"), ("B", "E"), ("A", "C"), ("C", "E"), ("A", "D"), ("D", "E"), ("B", "C")]:
            await graph.add_edge_by_label(a, b, "R")
        paths = await graph.get_paths("A", "E", k=5)
        keys = [tuple(n["id"] for n in p) for p in paths]
        assert len(keys) == len(set(keys))
    asyncio.run(run())


# ── Semantic ─────────────────────────────────────────────────────────────────

def test_strip_artifact_matches_indexer_fm_prefix(semantic):
    async def run():
        await semantic.store(content="embedder chunk", metadata={"artifact_id": "art-1"})
        await semantic.store(content="indexer chunk", metadata={"fm_artifact_id": "art-1", "source_path": "x.md"})
        await semantic.store(content="other", metadata={"artifact_id": "art-2"})
        assert await semantic.strip_artifact("art-1") == 2
        remaining = await semantic.list_memories(limit=10)
        assert [r["content"] for r in remaining] == ["other"]
    asyncio.run(run())


def test_topic_embedding_keeps_first_seen(semantic):
    from memory.topic_embeddings import topic_doc_id, upsert_topic_embedding

    async def run():
        await upsert_topic_embedding(semantic, label="GPU", topic_type="Technology",
                                     project_id="p1", artifact_id="first", when="2026-01-01")
        await upsert_topic_embedding(semantic, label="GPU", topic_type="technology",
                                     project_id="p1", artifact_id="second", when="2026-02-01")
        md = await semantic.get_metadata(topic_doc_id("p1", "technology", "GPU"))
        assert md["first_seen_artifact_id"] == "first"
        assert md["last_seen_artifact_id"] == "second"
        assert md["topic_type"] == "technology"
    asyncio.run(run())


# ── Embedding provider ───────────────────────────────────────────────────────

def test_embed_failure_raises_instead_of_zero_vector():
    import httpx
    from models.provider import ModelProvider

    p = ModelProvider.__new__(ModelProvider)
    p.base_url = "http://embed.invalid"
    p.embedding_model = "m"
    p._headers = lambda: {}

    class _Boom:
        async def post(self, *a, **k): raise httpx.ConnectError("down")

    with patch("utils.http.shared_client", return_value=_Boom()):
        with pytest.raises(httpx.ConnectError):
            asyncio.run(p.embed("hello-unique-uncached"))


# ── YouTube adapter ──────────────────────────────────────────────────────────

def test_youtube_fetch_uses_structured_output():
    from sources.adapters.youtube import YouTubeOther
    from sources.base import IngestRequest

    mgr = MagicMock()
    mgr.call_tool_raw = AsyncMock(return_value={
        "text": "Transcript fetched.\n\n<structured-output>...</structured-output>",
        "structured": {"text": "hello world " * 20, "meta": {"title": "T"}},
        "is_error": False,
    })
    adapter = YouTubeOther()
    with patch("mcp_client.manager.get_mcp_manager", return_value=mgr):
        fetched = asyncio.run(adapter.fetch(IngestRequest(
            source_type=adapter.source_type, identifier="vid123", project_id="p1",
        )))
    assert "hello world" in fetched.text


# ── Artifact store tag filter ────────────────────────────────────────────────

def test_artifact_list_tag_filter_applies_before_limit(tmp_path, monkeypatch):
    from artifacts.store import ArtifactStore
    store = ArtifactStore(db_path=str(tmp_path / "artifacts.db"))
    for i in range(10):
        store.create(project_id="p1", path=f"notes/n{i}.md", content="x",
                     content_type="text/markdown", title=f"n{i}", tags=["other"])
    store.create(project_id="p1", path="notes/target.md", content="x",
                 content_type="text/markdown", title="target", tags=["wanted"])
    got = store.list(project_id="p1", tag="wanted", limit=5)
    assert [a["path"] for a in got] == ["notes/target.md"]


def test_embed_many_batches_and_orders_by_index():
    from models.provider import ModelProvider
    import httpx
    p = ModelProvider.__new__(ModelProvider)
    p.base_url = "http://embed.test"
    p.embedding_model = "m"
    p._headers = lambda: {}
    calls = []

    def handler(req):
        import json as _j
        inputs = _j.loads(req.content)["input"]
        calls.append(len(inputs))
        data = [{"index": i, "embedding": [float(len(t))]} for i, t in enumerate(inputs)]
        return httpx.Response(200, json={"data": list(reversed(data))})

    async def run():
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        with patch("utils.http.shared_client", return_value=client):
            out = await p.embed_many(["a", "bb", "ccc"], batch_size=2)
        await client.aclose()
        return out
    out = asyncio.run(run())
    assert out == [[1.0], [2.0], [3.0]]
    assert calls == [2, 1]


def test_embed_cache_dedupes_repeat_queries():
    from models.provider import ModelProvider
    p = ModelProvider.__new__(ModelProvider)
    p.base_url = "http://embed.test"
    p.embedding_model = "m-cache"
    n = {"calls": 0}

    async def fake(text):
        n["calls"] += 1
        return [0.5]
    p._embed_uncached = fake

    async def run():
        await p.embed("same query")
        await p.embed("same query")
    asyncio.run(run())
    assert n["calls"] == 1


def test_recall_graph_helpers(graph):
    async def run():
        a = await graph.add_node("concept", "NVIDIA")
        b = await graph.add_node("concept", "Blackwell")
        await graph.add_edge(a, b, "PRODUCES")
        hits = await graph.nodes_mentioned_in("Today nvidia announced something")
        assert [h["label"] for h in hits] == ["NVIDIA"]
        edges = await graph.edges_for_nodes([a])
        assert len(edges) == 1 and edges[0]["node_b_label"] == "Blackwell"
    asyncio.run(run())
