"""Source-adapter plugin registry.

A *source adapter* is a small plugin that knows how to ingest one
kind of content (YouTube video, blog post, PDF, slide deck, podcast,
RSS feed, etc.) into Pantheon's artifact store and graph memory.

Today every ingest skill embeds its own knowledge of how to fetch,
how to structure frontmatter, and how to map fields to graph nodes.
This module replaces that with a registry: each adapter declares
its identity, fetch tool, save tool, frontmatter shape, and graph
mapping. Skills invoke ``ingest(source_type, identifier, ...)`` and
the registry routes to the right adapter.

Adding a new source type = a new file under backend/sources/ that
subclasses SourceAdapter and registers itself. ~30-50 lines.

See SOURCE_ADAPTERS.md in this directory for the design rationale,
schema specification, and worked examples.
"""
from sources.base import SourceAdapter, AdapterResult, IngestRequest
from sources.registry import (
    register_adapter,
    get_adapter,
    list_adapters,
    ingest,
)

__all__ = [
    "SourceAdapter",
    "AdapterResult",
    "IngestRequest",
    "register_adapter",
    "get_adapter",
    "list_adapters",
    "ingest",
]
