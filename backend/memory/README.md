# backend/memory

Pantheon's memory tiers and the `MemoryManager` that fronts them. All
SQLite stores live under `settings.db_dir` (`data/db/`) and go through
`db_utils.apply_sqlite_pragmas`; vectors live in ChromaDB under
`<data_dir>/chroma/` (or a remote Chroma when `chroma_host` is set).

## Tiers and storage

| Tier | Class | Storage |
|---|---|---|
| Episodic | `EpisodicMemory` | `data/db/episodic.db` (`conversations`, `messages`, `task_logs`, `memory_notes`); optional message vectors in `data/chroma/episodic-<project>` |
| Semantic | `SemanticMemory` | ChromaDB, one collection per project (`proj-<id>`) at `data/chroma/<project>`; each vector tagged with its embedding model |
| Graph | `GraphMemory` | `data/db/graph.db` (`graph_nodes`, `graph_edges`); `add_node` / `add_edge` idempotent |
| File index | `FileIndex` / `FileIndexer` | `data/db/file_index.db` (`indexed_files`: content hash per path); chunks go to semantic, frontmatter topics to graph |
| Archival | `ArchivalMemory` | Markdown under `<data_dir>/projects/<id>/notes/` plus `project_summary.md`; managed in Memory → Archival, not searched by `recall` |
| Topic embeddings | `topic_embeddings.py` | Semantic collection, `metadata.kind=topic_node`, deterministic id per `(project_id, topic_type, label)` |
| Merge proposals | `merge_proposals.py` | `data/db/merge_proposals.db` (`topic_merge_proposals`); never auto-applied |

**Working memory** is `AgentCore.working_memory` (in-process list, not
persisted). There is no `memory/working.py`; `remember(tier="working")`
from old callers becomes a session-tagged episodic note.

## MemoryManager

Build one with `create_memory_manager(project_id, session_id)`, which
wires the `embed` route's `embed` / `embed_many`.

- `recall(query, tiers=None, project_id=None, limit_per_tier=3, context_focus=None,
  in_context=None, min_relevance=0.0, session_fallback=None, …)`:
  searches semantic (never `kind=topic_node` label vectors; near-duplicate
  chunks of one artifact collapsed), episodic (similarity only; vectors of
  deleted messages and job prompts skipped) and graph (`search_nodes`: labels
  named in the query as whole words, then word overlap; scored by embedding
  similarity when no reranker is configured) - archival and working are never
  searched. Sorts by score, appends 1-hop graph context for entities named
  (whole words) in the top hits, reranks everything when a `rerank` route
  exists, applies context-focus recency weighting once, then dedups and packs
  the `ContextBudget` recall budget (an item that doesn't fit is skipped, not
  the end of the list). `in_context` drops hits the prompt already carries,
  `min_relevance` drops reranked items below the floor, and `session_fallback`
  adds similar earlier turns of that chat (see "Memory, recall and history" in
  `/CLAUDE.md`).
- `remember(content, tier="semantic", metadata=None, session_id=None)`:
  `semantic` stores a vector; `episodic` / `working` add an episodic note;
  `graph` runs the conversation extractor (`run_extraction`, `min_messages=1`)
  on the text; `archival` appends a note file. The agent's `remember` tool
  only offers `episodic`, `semantic` and `graph`.
- `consolidate_session(message_count=40)`: reads the session's recent
  messages from episodic, summarises them with the summarize-class model into
  a semantic `session_summary`, then runs extraction. Needs a real `session_id`.
- `run_extraction_on_recent(message_count=20)`: extraction only.
- `index_artifact(artifact_id, force=False)`: runs `FileIndexer.index_text`
  on a text artifact and upserts its frontmatter topics as topic embeddings.
- `index_workspace_file(path)` / `index_workspace_directory(dir)`.
- `audit_memory(tier)`: dump a tier for the Memory UI.
- `set_active_project(project_id)`: rebuild every tier for another project.

## Recall provenance tags

`recall` returns dicts (`content`, `tier`, `source`, `score`, `metadata`).
Graph hits have content `[graph:<node_type>] <label>` plus `→` / `←` edge
lines; augmentation blocks read `[graph context for '<label>']`. The
`recall` agent tool (`agent/tools/memory.py`) renders them as:

```
[semantic/artifact] <chunk>
  ↳ source: <artifact path>  id=<artifact_id> tags=[...]
[semantic/file:<path>] <chunk>
[episodic session=<first 8 chars> <timestamp>] [<role>] <message>
[graph] [graph:<node_type>] <label>
```

## Modules

| File | Purpose |
|---|---|
| `__init__.py` | Package docstring only |
| `manager.py` | `MemoryManager`, `ContextBudget`, `create_memory_manager` |
| `episodic.py` | Chat history, task logs and notes; vector search with LIKE fallback |
| `semantic.py` | ChromaDB wrapper: `store` / `store_many`, `search`, `delete_where`, `strip_artifact`, `reembed_stale` |
| `graph.py` | Nodes and edges: traversal (`find_related`, `get_path(s)`), `search_nodes`, `merge_nodes` (one transaction), `strip_artifact` |
| `file_indexer.py` | Text extraction (md, txt, csv, pdf with OCR fallback, images via vision), chunk and embed, frontmatter to graph (`_index_typed_topics_to_graph`) |
| `chunker.py` | `chunk_text`: `headings`, `paragraphs` or `fixed` strategies |
| `extraction.py` | `MemoryExtractor` / `run_extraction`: LLM extraction of entities, relationships, facts and preferences from messages |
| `archival.py` | `ArchivalMemory` notes and project summary; `migrate_stray_notes()` runs at startup |
| `topic_embeddings.py` | `upsert_topic_embedding`, `find_similar_topics` (type-gated), `delete_topic_embeddings_for_label` |
| `merge_proposals.py` | `propose`, `list_proposals`, `get_proposal`, `set_status` |

The similarity pipeline and merge execution that use the last two live in
`backend/sources/similarity.py`.
