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

- **Phase 1 — shipped.** Scaffold + YouTube adapters. `save_transcript_artifact` agent tool kept as a deprecation alias; artifacts written via either path are interoperable.
- **Phase 2 — shipped.** `ingest_source`, `batch_ingest_sources`, `list_source_adapters`, `extract_topics` agent tools wrap `sources.ingest()` and `sources.batch_ingest()`. Skills drive them.
- **Phase 3 — shipped.** Blog, PDF, podcast, web, forum, github, cfr, malegislature adapters all landed. Slide-deck deferred until a real use case emerges. The `content-ingest-graph` skill drives `ingest_source` for any registered source type.
- **Phase 4 — partially shipped.** `list_source_adapters` makes the registry visible to agent prompts. Per-project source-adapter scoping is still global (deferred — see CLAUDE.md "Things explicitly NOT done yet").

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

## Resolved design decisions (H7w)

### 1. Topic extraction — per-adapter strategy, skill override

Each adapter declares `extractor_strategy` (default `"llm_default"`) and `auto_extract` (default `True`). When `ingest()` runs, after `fetch()` and `build_frontmatter()` it calls the named extractor, populates `topics[]` / `speakers[]` / `claims[]` in the frontmatter, then proceeds to save. Skills can override per-call via `IngestRequest.extras["extractor_strategy"]` or skip entirely with `extras["skip_extraction"]=True`.

Built-in extractors (in `backend/sources/extraction.py`):
- `llm_default` — single LLM call with a structured JSON-schema prompt; truncates body to 60k chars as a backstop. Returns typed topics, speakers (only when transcript explicitly attributes utterances), and claims.
- `llm_announcement` — vendor / event announcements (who/what/when/dollars/partners).
- `llm_structured_specs` — datasheets / product pages (specs + pricing + features).
- `llm_research_paper` — academic papers (abstract + methodology + findings).
- `llm_changelog` — release notes.
- `noop` — empty pass-through. For sources whose topics are already in their metadata, or for metadata-only artifacts (hearings, roll calls, committee votes).

Adding a new extractor: subclass `TopicExtractor` (or `LLMDefaultExtractor` to inherit JSON-recovery + diagnostics), set `name`, call `register_extractor(YourClass())`. Same plug-in pattern as adapters.

### 2. Cross-artifact similarity — shipped

Backend pipeline runs post-`index_artifact` with type-gating from the adapter's topic taxonomy and a cosine threshold of 0.86 (matches above 0.92 queue a merge proposal). Each adapter declares `auto_link_similarity` (default `False`); the pipeline reads it on each ingest. Implementation lives in `backend/sources/similarity.py` with topic-label embeddings stored in `backend/memory/topic_embeddings.py` (keyed by `(project_id, topic_type, label)`) and reviewable merges in `backend/memory/merge_proposals.py`. Agents call `list_merge_proposals` / `approve_merge` to curate the graph; a UI panel for this is still TODO (see CLAUDE.md).

### 3. Per-project source registries — deferred to phase 4

Current registry is global. Project scoping (different research domains enabling different source types) is real but YAGNI for the single-user case. Will revisit when the first cross-project use emerges. The `IngestRequest.project_id` field carries through so when scoping arrives, the adapter resolution path becomes `(project_id, source_type)` instead of just `source_type` — the data model is ready.

### 4. Failure semantics — `batch_ingest()` with per-item isolation

`registry.batch_ingest(reqs)` runs each request through `ingest()`, catches all exceptions (including unhandled ones from adapter code), and returns a list of `AdapterResult` — one per request, with `skipped=True` and a `skip_reason` for failures. Default never aborts on a single failure. Pass `stop_on_error=True` for abort-on-first semantics.

The agent-facing tool is `batch_ingest_sources`; it produces a markdown summary with separate "Ingested" and "Skipped" sections so the user can diagnose partial failures without losing successful work.

## Current state (2026-05-09)

Adapters registered (28 across 9 mechanisms):

| Mechanism | Adapters | File |
|---|---|---|
| `youtube` | interview, keynote, other (3) | `adapters/youtube.py` |
| `blog` | announcement, influencer, technical, news (4) | `adapters/blog.py` |
| `pdf` | datasheet, whitepaper, research, marketing (4) | `adapters/pdf.py` |
| `web` | product-page, service-page, changelog (3) | `adapters/web.py` |
| `forum` | reddit, hackernews (2) | `adapters/forum.py` |
| `podcast` | episode (1) | `adapters/podcast.py` |
| `github` | release, changelog (2) | `adapters/github.py` |
| `cfr` | section, part (2) | `adapters/cfr.py` |
| `malegis` | general-law-section, general-law-chapter, session-law, bill, hearing, roll-call, committee-vote (7) | `adapters/malegislature.py` |

Pipeline pieces shipped:
- ✅ `ingest()` / `batch_ingest()` at the registry level
- ✅ `TopicExtractor` base + 6 built-in extractors (`llm_default`, `llm_announcement`, `llm_structured_specs`, `llm_research_paper`, `llm_changelog`, `noop`) with hot-loadable registry
- ✅ Adapter declarative attrs: `extractor_strategy`, `auto_extract`, `auto_link_similarity`
- ✅ Agent tools: `list_source_adapters`, `ingest_source`, `batch_ingest_sources`, `extract_topics`
- ✅ Cross-artifact similarity pipeline + topic-label embeddings + merge proposals
- ✅ Live integration tests for malegislature (gated by `MALEGIS_LIVE=1`)

Still deferred (see CLAUDE.md "Things explicitly NOT done yet" for full list):
- ⏳ Project-scoped registry (currently global)
- ⏳ UI panel for merge-proposal review (agent-tool only)
- ⏳ Playwright fallback for JS-rendered web pages
- ⏳ OCR for image-only PDFs
- ⏳ Reddit OAuth flow (currently uses pasted-payload workaround)
- ⏳ `mgl_citations` → graph edges in file_indexer (frontmatter populated; consumer pending)
