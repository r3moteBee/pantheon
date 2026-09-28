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

A `SourceAdapter` subclass (`backend/sources/base.py`) declares these class attributes:

| Attribute | Default | Meaning |
|---|---|---|
| `source_type` | — (required) | Canonical id, e.g. `blog/announcement`. Must be unique. |
| `display_name` | — (required) | Label for UIs and logs. |
| `artifact_path_template` | — (required) | `str.format` template; placeholders `{identifier}`, `{slug}`, `{author_or_publisher}`, `{published_at}`, `{source_type}`. |
| `bucket_aliases` | `()` | Heuristic shortcuts, e.g. `("blog",)`, `("sec", "edgar")`. |
| `requires_mcp` | `()` | MCP tool names the adapter needs; validated when scheduled tasks fire. |
| `extractor_strategy` | `"llm_default"` | Topic extractor the registry runs after `fetch()`. |
| `auto_extract` | `True` | Whether the registry runs that extractor inline. |
| `auto_link_similarity` | `False` | Whether the registry runs the cross-artifact similarity pipeline after indexing. |

and implements:

```python
async def fetch(self, req: IngestRequest) -> FetchedContent: ...       # required
def build_frontmatter(self, req, fetched) -> dict: ...                 # optional override
def render_artifact_path(self, req, fetched) -> str: ...               # optional override
async def post_save_hook(self, req, result: AdapterResult) -> None: ... # optional, default no-op
```

## The pipeline

`registry.ingest(req)` (`backend/sources/registry.py`) runs:

1. **Resolve adapter** by `req.source_type`. Unknown type → skipped result.
2. **`fetch()`** — the adapter calls MCP / HTTP / file IO and returns a normalized `FetchedContent` (text, title, url, author_or_publisher, published_at, free-form `extra_meta` such as `video_id`). An exception or empty text → skipped result (`fetch_failed: …` / `empty_content`).
3. **`build_frontmatter()`** — default builds the canonical typed-topics shape (`source` block, `published_at`, `title`, empty `topics: []` / `speakers: []`, `searched_by`, plus every `extra_meta` key). `None`/empty values are dropped.
4. **Topic extraction (registry-owned).** If `adapter.auto_extract` is true and `extras["skip_extraction"]` is not set, the registry runs the extractor named by `extras["extractor_strategy"]` or else `adapter.extractor_strategy`, and fills `topics` / `speakers` / `claims` plus any extractor-specific fields (`frontmatter_additions`, which can't overwrite the canonical keys). The result, success or failure, is recorded in the `extraction_status` frontmatter block.
5. **`render_artifact_path()`** — formats `artifact_path_template` (title and author are slugified; missing date → `unknown-date`), then the registry prefixes the project slug.
6. **Save (dedup on canonical path).** If an artifact already exists at that path, it is **updated in place** (a new version in `artifact_versions`, `update_mode=True` in the result). Pass `extras={"force_new": True}` to create a separate artifact instead; creates retry `-1`, `-2`, … suffixes on a UNIQUE-path collision (up to 50). A concurrent ingest that creates the canonical path first is also turned into an update rather than a `-1` duplicate.
7. **Index + graph.** The registry calls `MemoryManager.index_artifact(id)`, which chunks and embeds the body and runs the FileIndexer's typed-topics graph branch (source/content/topic/speaker nodes and edges). Only if that fails (or no memory manager is passed) does it fall back to the generic embedder, `artifacts.embedder.schedule_embed`.
8. **Similarity (opt-in).** If `auto_link_similarity` is true, `sources.similarity.link_artifact_topics` runs; counts come back in `AdapterResult.extra`.
9. **`post_save_hook()`** — optional adapter callback for downstream side effects.

`batch_ingest(reqs)` runs many requests with per-item failure isolation (see below).

## What adapters do NOT own

- **Topic extraction.** Adapters leave `topics: []` empty; the registry runs the declared `extractor_strategy` (step 4). An adapter only chooses the strategy, or sets `auto_extract = False` to opt out (callers can also opt out per call with `extras["skip_extraction"]=True`). To inspect or try a different strategy without saving, use the `extract_topics` agent tool; to commit it, re-run `ingest_source` with `extras.extractor_strategy` (the re-ingest updates the same artifact).
- **Chunking and embedding.** `index_artifact` / FileIndexer handles that.
- **Graph extraction.** The `_index_typed_topics_to_graph` branch reads the frontmatter and builds nodes/edges. Adapters can add *new* frontmatter fields, but surfacing them as graph nodes means extending the graph branch, not the adapter.
- **Save, dedup, versioning.** The registry.
- **Authorization.** The agent layer enforces who can call ingest; adapters trust the caller.

## Fetching rules

- **HTTP:** use `utils.net.safe_http_get(url, *, timeout=60.0, headers=None, client=None)`. It blocks non-public addresses on every redirect hop and pins the validated IP. Never use `httpx.AsyncClient().get(url, follow_redirects=True)` or `trafilatura.fetch_url` on a caller-supplied URL. It returns an `httpx.Response`; call `raise_for_status()` yourself.
- **MCP tools:** use `get_mcp_manager().call_tool_raw(tool_name, args)`, which returns `{"text", "structured", "is_error"}`. Never `execute_tool()` (that's LLM-formatted prose). List the tool in `requires_mcp`.
- **Blocking parsers** (trafilatura, pdfplumber): wrap in `asyncio.to_thread`.

## Migration path

- **Phase 1 — shipped.** Scaffold + YouTube adapters. `save_transcript_artifact` is now a hidden legacy alias (not shown to the model); artifacts written via either path are interoperable.
- **Phase 2 — shipped.** `ingest_source`, `batch_ingest_sources`, `list_source_adapters`, `extract_topics` agent tools wrap `sources.ingest()` and `sources.batch_ingest()`. Skills drive them.
- **Phase 3 — shipped.** Blog, PDF, podcast, web, forum, github, cfr, malegislature and SEC EDGAR adapters all landed. Slide-deck deferred until a real use case emerges. The `content-ingest-graph` skill drives `ingest_source` for any registered source type.
- **Phase 4 — partially shipped.** `list_source_adapters` makes the registry visible to agent prompts. Per-project source-adapter scoping is still global (deferred — see CLAUDE.md "Things explicitly NOT done yet").

## Worked example: adding a blog adapter

Condensed from the real `backend/sources/adapters/blog.py`:

```python
# backend/sources/adapters/blog.py
import asyncio
from sources.base import SourceAdapter, FetchedContent, IngestRequest
from sources.registry import register_adapter
from sources.util import html_to_markdown, parse_relative_date


class _BlogAdapterBase(SourceAdapter):
    artifact_path_template = "blogs/{published_at}/{author_or_publisher}/{slug}.md"
    bucket_aliases = ("blog",)
    requires_mcp = ()

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        import trafilatura
        from utils.net import safe_http_get   # SSRF-guarded, checks every redirect hop

        url = req.identifier
        resp = await safe_http_get(url)
        resp.raise_for_status()
        html = resp.text

        cleaned = await asyncio.to_thread(
            trafilatura.extract, html, url=url, output_format="html",
            include_tables=True, include_comments=False,
        )
        text = html_to_markdown(cleaned or "")
        if len(text.strip()) < 100:
            raise RuntimeError(f"extracted < 100 chars from {url!r}; JS-rendered or paywalled?")

        meta = await asyncio.to_thread(trafilatura.extract_metadata, html)
        return FetchedContent(
            text=text,
            title=getattr(meta, "title", "") or req.extras.get("title") or url,
            author_or_publisher=getattr(meta, "author", "") or "",
            url=url,
            published_at=(getattr(meta, "date", None)
                          or req.extras.get("published_at")
                          or parse_relative_date(req.extras.get("published"))),
            extra_meta={"retrieved_at": req.extras.get("retrieved_at"),
                        "fetch_method": "trafilatura"},
        )


class BlogAnnouncement(_BlogAdapterBase):
    source_type = "blog/announcement"
    display_name = "Blog — vendor / event announcement"
    extractor_strategy = "llm_announcement"


register_adapter(BlogAnnouncement())
```

Then add `from sources.adapters import blog` to `backend/sources/adapters/__init__.py`. The default `build_frontmatter` produces the typed-topics shape; the registry handles extraction, save/dedup, index and graph. Skills call `ingest_source(source_type="blog/announcement", identifier=url)` per item.

An MCP-backed adapter does the same with `call_tool_raw` (see `adapters/youtube.py`):

```python
from mcp_client.manager import get_mcp_manager

raw = await get_mcp_manager().call_tool_raw(
    "mcp_<server>_fetch_transcript", {"video_id": req.identifier, "save": False},
)
payload = raw.get("structured") or json.loads(raw.get("text") or "{}")
```

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

Backend pipeline runs post-`index_artifact` with type-gating from the adapter's topic taxonomy and a cosine threshold of 0.86 (matches above 0.92 queue a merge proposal). Each adapter declares `auto_link_similarity` (default `False`); the pipeline reads it on each ingest. Implementation lives in `backend/sources/similarity.py` with topic-label embeddings stored in `backend/memory/topic_embeddings.py` (keyed by `(project_id, topic_type, label)`) and reviewable merges in `backend/memory/merge_proposals.py`. Agents call `merge_topics` (list / approve / reject / force) to curate the graph; a UI panel for this is still TODO (see CLAUDE.md).

### 3. Per-project source registries — deferred to phase 4

Current registry is global. Project scoping (different research domains enabling different source types) is real but YAGNI for the single-user case. Will revisit when the first cross-project use emerges. The `IngestRequest.project_id` field carries through so when scoping arrives, the adapter resolution path becomes `(project_id, source_type)` instead of just `source_type` — the data model is ready.

### 4. Failure semantics — `batch_ingest()` with per-item isolation

`registry.batch_ingest(reqs)` runs each request through `ingest()`, catches all exceptions (including unhandled ones from adapter code), and returns a list of `AdapterResult` — one per request, with `skipped=True` and a `skip_reason` for failures. Default never aborts on a single failure. Pass `stop_on_error=True` for abort-on-first semantics.

The agent-facing tool is `batch_ingest_sources`; it produces a markdown summary with separate "Ingested" and "Skipped" sections so the user can diagnose partial failures without losing successful work.

## Current state

Adapters registered (29 across 10 mechanisms — count `register_adapter(` calls under `adapters/` to re-check):

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
| `sec` | edgar (1) — SEC URL or `TICKER/FORM` (e.g. `AAPL/10-K`); aliases `sec`, `edgar` | `adapters/sec_edgar.py` |

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
