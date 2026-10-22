"""Recall ranking + scoping fixes (2026-10-21 recall loop).

Deleted messages/conversations no longer leave recallable vectors, topic-label
vectors and job prompts never take recall slots, graph labels match whole words
only, double-indexed artifacts don't repeat, and the budget packs past a misfit."""
from __future__ import annotations

import asyncio
import hashlib
import math
import re

import pytest


def _embed_sync(text: str) -> list[float]:
    v = [0.0] * 64
    for w in re.findall(r"[a-z0-9]+", text.lower()):
        v[int(hashlib.md5(w.encode()).hexdigest(), 16) % 64] += 1.0
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


async def _embed(text: str) -> list[float]:
    return _embed_sync(text)


async def _embed_many(texts: list[str]) -> list[list[float]]:
    return [_embed_sync(t) for t in texts]


async def _save(ep, *args, **kw):
    """save_message + wait for its vector (one at a time: chromadb races when two
    threads first open the same fresh PersistentClient)."""
    mid = await ep.save_message(*args, **kw)
    await _drain()
    return mid


async def _drain():
    from utils import background
    while [t for t in background._TASKS if not t.done()]:
        await asyncio.gather(*list(background._TASKS), return_exceptions=True)


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    from config import get_settings
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("CHROMA_HOST", "")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _no_reranker(monkeypatch):
    import models.provider
    monkeypatch.setattr(models.provider, "get_provider_for", lambda cls: None)


def _episodic(data_dir, project_id="p1", embedding_fn=_embed):
    from memory.episodic import EpisodicMemory
    return EpisodicMemory(db_path=str(data_dir / "db" / "episodic.db"), project_id=project_id, embedding_fn=embedding_fn)


def _vector_ids(ep) -> set[str]:
    return set(ep._get_vector_collection().get(include=[])["ids"])


# ── Deleted content ──────────────────────────────────────────────────────────

async def test_deleted_conversation_vectors_are_removed(data_dir):
    ep = _episodic(data_dir)
    gone = await _save(ep, "s-del", "p1", "user", "The lab door alarm code is 4471.")
    await _save(ep, "s-keep", "p1", "user", "The ferry back from Gdansk leaves at 19:30.")
    await _drain()
    assert gone in _vector_ids(ep)
    # The API deletes through a plain EpisodicMemory() bound to 'default'.
    pids = await _episodic(data_dir, project_id="default", embedding_fn=None).delete_conversation("s-del")
    assert pids == ["p1"]
    assert gone not in _vector_ids(ep)
    hits = await ep.search_messages("alarm code for the lab door", project_id="p1")
    assert all("4471" not in h["content"] for h in hits)


async def test_deleted_message_vector_is_removed(data_dir):
    ep = _episodic(data_dir)
    mid = await _save(ep, "s1", "p1", "user", "My personal mobile number is +372 5555 0199.")
    await _drain()
    assert await _episodic(data_dir, project_id="default", embedding_fn=None).delete_message(mid)
    assert mid not in _vector_ids(ep)


async def test_orphan_vectors_of_earlier_deletes_are_skipped_and_cleaned(data_dir):
    ep = _episodic(data_dir)
    mid = await _save(ep, "s1", "p1", "user", "The NAS passphrase hint is heron over the dunes.")
    await _save(ep, "s1", "p1", "user", "The NAS is in rack two.")
    await _drain()
    with ep._connect() as conn:                       # deleted before the fix: SQLite only
        conn.execute("DELETE FROM messages WHERE id = ?", (mid,))
        conn.commit()
    hits = await ep.search_messages("NAS passphrase hint", project_id="p1")
    assert hits and all(h["id"] != mid for h in hits)
    assert mid not in _vector_ids(ep)


async def test_conversation_delete_endpoint_drops_extracted_facts(data_dir):
    from api.conversations import delete_conversation
    from memory.semantic import SemanticMemory
    ep = _episodic(data_dir, embedding_fn=None)
    await _save(ep, "s-x", "p1", "user", "hello")
    sem = SemanticMemory(project_id="p1", embedding_fn=_embed)
    await sem.store("Extracted from the chat", metadata={"session_id": "s-x", "source": "auto_extraction"})
    await sem.store("A remembered fact", metadata={"session_id": "s-x"})
    await delete_conversation("s-x")
    assert [m["content"] for m in await sem.list_memories()] == ["A remembered fact"]


# ── Job prompts ──────────────────────────────────────────────────────────────

PROMPT = "You are running an autonomous task: 'Weekly digest'\nJob ID: x\n\nTask:\nSummarize Corvan news."


async def test_job_prompts_are_not_indexed(data_dir):
    ep = _episodic(data_dir)
    pid = await _save(ep, "job", "p1", "user", PROMPT, metadata={"kind": "autonomous_task_prompt"})
    rid = await _save(ep, "job", "p1", "assistant", "Corvan opened a factory in Brno.",
                                metadata={"kind": "autonomous_task_response"})
    await _drain()
    assert _vector_ids(ep) == {rid}
    assert pid  # still stored for the chat drawer
    assert [m["role"] for m in await ep.get_history("job")] == ["user", "assistant"]


async def test_job_prompts_indexed_earlier_are_not_recalled(data_dir):
    ep = _episodic(data_dir)
    mid = await _save(ep, "job", "p1", "assistant", "placeholder")
    with ep._connect() as conn:
        conn.execute("UPDATE messages SET role = 'user', content = ? WHERE id = ?", (PROMPT, mid))
        conn.commit()
    await ep._index_message_vector(mid, PROMPT, {"project_id": "p1", "session_id": "job", "role": "user",
                                                 "timestamp": "2026-10-01T00:00:00+00:00"})
    hits = await ep.search_messages("Corvan news weekly digest", project_id="p1")
    assert all(h["id"] != mid for h in hits)


# ── Topic-label vectors ──────────────────────────────────────────────────────

async def test_topic_node_vectors_are_never_recalled(data_dir):
    from memory.manager import MemoryManager
    from memory.topic_embeddings import find_similar_topics, upsert_topic_embedding
    m = MemoryManager(project_id="p1", embedding_fn=_embed, embedding_batch_fn=_embed_many)
    for label in ("Data Retention", "Data retention policy", "Retention"):
        await upsert_topic_embedding(m.semantic, label=label, topic_type="concept", project_id="p1")
    await m.semantic.store("Raw sensor telemetry is kept for 18 months (data retention policy).")
    out = await m.recall("Data Retention", tiers=["semantic"], limit_per_tier=3)
    assert out and all((r.get("metadata") or {}).get("kind") != "topic_node" for r in out)
    # The similarity pipeline still finds them.
    assert await find_similar_topics(m.semantic, label="Data Retention", topic_type="concept", project_id="p1")


# ── Graph matching ───────────────────────────────────────────────────────────

@pytest.fixture
def graph(tmp_path):
    from memory.graph import GraphMemory
    return GraphMemory(project_id="p1", db_path=str(tmp_path / "graph.db"))


async def test_short_labels_match_whole_words_only(graph):
    await graph.add_node("concept", "AI")
    await graph.add_node("concept", "Data Retention")
    assert await graph.nodes_mentioned_in("Priya said the harbour master wants berth 7") == []
    hits = await graph.nodes_mentioned_in("AI features and data retention rules")
    assert [h["label"] for h in hits] == ["Data Retention", "AI"]


async def test_search_nodes_finds_labels_inside_a_question(graph):
    await graph.add_node("person", "Jonas Varga")
    await graph.add_node("person", "Mira Okafor")
    await graph.add_node("concept", "Jonas")           # partial-name node ranks lower... but whole label in query
    await graph.add_node("concept", "Varga Hydraulics")
    hits = await graph.search_nodes("Who does Jonas Varga report to?")
    assert hits[0]["label"] == "Jonas Varga" and hits[0]["match"] == 1.0
    assert {h["label"] for h in hits} == {"Jonas Varga", "Jonas", "Varga Hydraulics"}
    assert next(h for h in hits if h["label"] == "Varga Hydraulics")["match"] == 0.5
    assert await graph.search_nodes("what about the weather?") == []


# ── Duplicates + budget ──────────────────────────────────────────────────────

def test_same_artifact_chunks_from_both_indexers_are_deduped():
    from memory.manager import _dedupe_chunks
    hits = [
        {"id": "1", "content": "# Release notes\n\nCurrent release: 2.4.1 fixes CAN dropout.", "metadata": {"artifact_id": "a"}},
        {"id": "2", "content": "Release notes\n\nCurrent release: 2.4.1 fixes CAN dropout.", "metadata": {"fm_artifact_id": "a"}},
        {"id": "3", "content": "Current release: 2.4.1 fixes CAN dropout.", "metadata": {"artifact_id": "b"}},
        {"id": "4", "content": "A different section of the same artifact about pumps.", "metadata": {"artifact_id": "a"}},
    ]
    assert [h["id"] for h in _dedupe_chunks(hits)] == ["1", "3", "4"]


async def test_embedder_does_not_duplicate_index_artifact_chunks(data_dir):
    from artifacts.embedder import _indexed_by_index_artifact
    from memory.file_indexer import _content_hash
    from memory.semantic import SemanticMemory
    sem = SemanticMemory(project_id="p1", embedding_fn=_embed)
    art = {"id": "a1", "content": "# T\n\nv2 text"}
    assert not await _indexed_by_index_artifact(sem, art)
    await sem.store("v2 text", metadata={"fm_artifact_id": "a1", "content_hash": _content_hash(art["content"])})
    assert await _indexed_by_index_artifact(sem, art)
    assert not await _indexed_by_index_artifact(sem, {**art, "content": "# T\n\nv3 text"})


def test_index_text_chunks_carry_title_and_heading():
    from memory.file_indexer import _with_header
    assert _with_header("Current release: 2.4.1", "Release notes", "") == "Release notes\n\nCurrent release: 2.4.1"
    assert _with_header("The watchdog is 750 ms.", "Kestrel spec", "7. Safety") == \
        "Kestrel spec › 7. Safety\n\nThe watchdog is 750 ms."
    assert _with_header("# Release notes\n\nx", "Release notes", "Release notes") == "# Release notes\n\nx"


def test_budget_skips_a_misfit_and_keeps_packing():
    from memory.manager import ContextBudget, MemoryManager
    m = MemoryManager.__new__(MemoryManager)
    m.context_budget = ContextBudget(recall_budget=100)
    results = [{"content": "a" * 40}, {"content": "b" * 4000}, {"content": "c" * 40}]
    out = m._apply_budget(results)
    assert [r["content"][0] for r in out] == ["a", "b", "c"]
    assert out[1].get("truncated") and len(out[1]["content"]) <= 80 * 4 + 3
    m.context_budget = ContextBudget(recall_budget=30)
    out = m._apply_budget(results)
    assert [r["content"][0] for r in out] == ["a", "c"]   # 10 left: not worth a truncated block
