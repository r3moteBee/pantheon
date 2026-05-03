"""Source-adapter base classes and shared types.

A SourceAdapter is the contract every ingest plugin must implement.
The registry calls fetch() then save() then graph_map() for each
identifier; chunking and embedding go through the existing
FileIndexer pipeline so adapters don't have to reimplement that.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional


@dataclass
class IngestRequest:
    """Input to an adapter's ingest() call.

    Skills construct one of these per item (one video, one blog URL,
    one PDF, etc.) and hand it to the registry.
    """
    source_type: str            # e.g. "youtube/interview", "blog/announcement", "pdf/spec-sheet"
    identifier: str             # video_id, URL, file path — adapter decides shape
    project_id: str
    extras: dict[str, Any] = field(default_factory=dict)
    # extras conveys per-call hints: source_types_filter from the
    # skill input, bucket overrides, retrieved_at, search_criteria,
    # speakers (when known up front), etc. Adapter is free to ignore
    # keys it doesn't recognize.


@dataclass
class FetchedContent:
    """What an adapter's fetch() returns.

    Adapters that need to call MCP tools or external HTTP endpoints
    do that internally and produce this normalized record.
    """
    text: str                                   # the document body that will be the artifact body
    title: str = ""
    author_or_publisher: str = ""
    url: str = ""
    published_at: Optional[str] = None          # YYYY-MM-DD if known
    extra_meta: dict[str, Any] = field(default_factory=dict)
    # extra_meta surfaces fields specific to the source type that
    # downstream steps (frontmatter builder, graph mapper) need but
    # that aren't universal — e.g. video_id for YouTube, page_count
    # for PDF, speakers[] when the channel publishes them.


@dataclass
class AdapterResult:
    """Final ingest result returned to the caller.

    Contains the saved artifact id, the path it ended up at, the
    list of graph nodes/edges created, and whatever stats the
    adapter wants to surface (chunks, char count, fetch latency).
    """
    artifact_id: str
    artifact_path: str
    chars_saved: int
    graph_nodes_created: int
    graph_edges_created: int
    skipped: bool = False
    skip_reason: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# Type alias — the registry uses this to validate signatures.
GraphMapFn = Callable[[dict[str, Any], str], Awaitable[tuple[int, int]]]


class SourceAdapter:
    """Contract every source plugin implements.

    Subclass this, set the class attributes, implement fetch() and
    build_frontmatter(), then call register_adapter(YourClass())
    at module import time.

    Example:

        class YouTubeInterviewAdapter(SourceAdapter):
            source_type = "youtube/interview"
            display_name = "YouTube interview"

            async def fetch(self, req): ...
            def build_frontmatter(self, req, fetched): ...

        register_adapter(YouTubeInterviewAdapter())

    The registry handles dedup, save, indexing, and graph extraction.
    Adapters only own the source-specific parts.
    """

    # Required class attributes (set on subclass).
    source_type: str = ""           # canonical type string — must be unique
    display_name: str = ""          # human-readable label for UIs and logs
    artifact_path_template: str = "" # e.g. "youtube-transcripts/{published_at}/{author_or_publisher}/{identifier}-{slug}.md"

    # Optional declarative metadata. The registry uses these to
    # decide whether two adapters can produce the same artifact
    # (collision detection) and to populate UI dropdowns.
    bucket_aliases: tuple[str, ...] = ()  # e.g. ("youtube", "yt") — heuristic shortcuts
    requires_mcp: tuple[str, ...] = ()    # MCP tool names this adapter needs

    # ── Methods adapters override ─────────────────────────────────

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        """Retrieve the raw content. Adapters call MCP tools, HTTP
        fetchers, or local file readers here. Must return a
        normalized FetchedContent. Raise on unrecoverable failure;
        the registry will record the failure and continue with the
        next item."""
        raise NotImplementedError

    def build_frontmatter(
        self, req: IngestRequest, fetched: FetchedContent,
    ) -> dict[str, Any]:
        """Produce the YAML frontmatter dict for this artifact.

        Default implementation builds the canonical typed-topics
        shape (source/topics/speakers). Override only if your source
        needs different fields. ``topics`` is intentionally left
        empty here — topic extraction is a separate concern that
        runs after fetch (typically an LLM step the skill orchestrates).
        """
        return {
            "source": {
                "type": self.source_type,
                "url": fetched.url,
                "author_or_publisher": fetched.author_or_publisher,
                "retrieved_at": fetched.extra_meta.get("retrieved_at"),
            },
            "published_at": fetched.published_at,
            "title": fetched.title,
            "topics": [],
            "speakers": [],
            "searched_by": req.extras.get("searched_by") or {},
            **{k: v for k, v in fetched.extra_meta.items()
               if k not in {"retrieved_at"}},
        }

    def render_artifact_path(
        self, req: IngestRequest, fetched: FetchedContent,
    ) -> str:
        """Produce the artifact path. Default uses
        artifact_path_template with str.format(); adapters override
        for custom slugify rules or fallbacks."""
        from sources.util import slugify
        published = (fetched.published_at or "unknown-date")
        return self.artifact_path_template.format(
            identifier=req.identifier,
            slug=slugify(fetched.title),
            author_or_publisher=slugify(fetched.author_or_publisher) or "unknown",
            published_at=published,
            source_type=self.source_type.replace("/", "-"),
        )

    # ── Methods adapters rarely need to override ──────────────────

    async def post_save_hook(
        self, req: IngestRequest, result: AdapterResult,
    ) -> None:
        """Optional. Runs after the artifact is saved + indexed.
        Useful for adapters that want to trigger downstream work
        (e.g. push to a search index, notify a webhook). Default is
        a no-op."""
        return None
