# Source-adapter API

A *source adapter* is a plugin that knows how to ingest one kind of content into Pantheon's artifact store and graph memory. Adapters replace the per-skill fetch/save/graph code that used to live inside each ingest workflow.

## Why

Before adapters, every ingest skill carried its own knowledge of:
- which MCP tool to call for fetching
- how to lay out the artifact path
- what frontmatter shape to write
- which graph nodes/edges to create

That was OK for one source (YouTube transcripts) but as soon as a second source (PDF spec sheets, blog posts, podcasts, slide decks, RSS) shows up, the skill instructions duplicate and the graph extractor forks. Adapters consolidate.

## Contract

A `SourceAdapter` subclass declares:

```python
class MyAdapter(SourceAdapter):
    source_type = "blog/announcement"           # canonical id; must be unique
    display_name = "Blog announcement"
    bucket_aliases = ("blog",)
    requires_mcp = ()                           # MCP tools needed (for capability checks)
    artifact_path_template = "blogs/{published_at}/{author_or_publisher}/{slug}.md"

    async def fetch(self, req: IngestRequest) -> FetchedContent: ...
    # build_frontmatter() and render_artifact_path() have working
    # defaults — override only when the source needs custom shape.
```

## The pipeline

`sources.ingest(req)` runs:

1. **resolve adapter** by `req.source_type` (or via bucket alias).
2. **fetch()** — adapter calls MCP / HTTP / file IO. Returns a normalized `FetchedContent` with text, title, url, author, published_at, and a free-form `extra_meta` dict for source-specific fields (e.g. `video_id`).
3. **build_frontmatter()** — produces the YAML dict. Default builds the canonical typed-topics shape (`source` block + `topics: []` + `speakers: []`); override to add source-specific fields next to it.
4. **render_artifact_path()** — string-format `artifact_path_template`. Default uses `slugify` over title and author for safety.
5. **save** — registry serializes frontmatter + body to markdown, normalizes the project slug, retries up to 50 numbered variants on UNIQUE-path collisions.
6. **embed + index** — registry calls `MemoryManager.index_artifact(id)`. The FileIndexer's typed-topics graph branch runs, producing source/content/topic/speaker nodes and edges per the existing spec.
7. **post_save_hook()** — optional adapter callback for downstream side effects (notify, push, etc.).

## What adapters do NOT own

- Chunking and embedding. The `FileIndexer` pipeline handles that.
- Graph extraction. The `_index_typed_topics_to_graph` branch reads the frontmatter the adapter produced and builds nodes/edges. Adapters can introduce *new* frontmatter fields, but if they want those fields surfaced as graph nodes they extend the graph branch — not the adapter.
- Topic extraction. Topic labeling is an LLM step (the skill orchestrates it). Adapter writes empty `topics: []` initially; skill updates the artifact frontmatter once topics are extracted; graph extractor re-runs on update.
- Authorization. The agent layer enforces who can call ingest; adapters trust the caller.

## Migration path

Phase 1 (this ship): scaffold + YouTube adapters. Existing `save_transcript_artifact` agent tool still works; it writes the same shape adapters produce, so artifacts saved via either path are interoperable.

Phase 2: add a thin `ingest_source` agent tool that wraps `sources.ingest()`. Skills migrate to it. `save_transcript_artifact` stays as a deprecation alias.

Phase 3: add adapters for blog, PDF, slide deck, podcast (each ~30-50 lines following `youtube.py`). Update `content-ingest-graph` skill to drive `ingest_source` for any registered source type.

Phase 4: registry-aware skill creation — when a user creates a research skill, the system prompt and skill template enumerate the registered source types so the agent knows what's actually supported.

## Worked example: adding a blog adapter

```python
# backend/sources/adapters/blog.py
import httpx
from sources.base import SourceAdapter, FetchedContent, IngestRequest
from sources.registry import register_adapter

class BlogAnnouncement(SourceAdapter):
    source_type = "blog/announcement"
    display_name = "Blog post — announcement"
    bucket_aliases = ("blog",)
    artifact_path_template = "blogs/{published_at}/{author_or_publisher}/{slug}.md"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        async with httpx.AsyncClient() as client:
            r = await client.get(req.identifier, timeout=30)
            r.raise_for_status()
        # ... HTML -> markdown conversion (use trafilatura or readability)
        text = strip_to_article(r.text)
        meta = extract_meta(r.text)
        return FetchedContent(
            text=text,
            title=meta.title,
            author_or_publisher=meta.author,
            url=req.identifier,
            published_at=meta.published_at,
            extra_meta={"original_html_url": req.identifier},
        )

register_adapter(BlogAnnouncement())
```

That's the whole adapter. The default `build_frontmatter` produces the typed-topics shape, the registry handles save/index/graph. Skill instructions then call `ingest_source(source_type="blog/announcement", identifier=url)` per item.

## Open design questions

1. **Topic-extraction step**: should it be an adapter responsibility (with adapters declaring which extraction strategy to use), a skill responsibility (current), or a backend pipeline that runs after every save? Argues for: each source type has known topic-extraction patterns; argues against: extraction is LLM-driven and skill-aware. Recommendation: backend pipeline driven by per-source extractor configs; skills can override.

2. **Cross-artifact similarity**: same question. Currently skill-driven; should be a backend pipeline that runs after each `index_artifact` and adds `semantically_similar_to` edges where cosine ≥ threshold and types are compatible.

3. **Per-project source registries**: should some adapters be project-scoped (so different projects can have different research domains)? Likely yes — defer to phase 4.

4. **Failure semantics**: today `ingest()` returns a single `AdapterResult` with `skipped=True` on failure. For batch ingests (5 videos at once), the caller wants per-item failure isolation. Recommend a `batch_ingest(reqs)` helper at the registry level that runs each through `ingest()` and returns a list, never raising on individual failures.
