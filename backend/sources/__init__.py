"""Source-adapter plugin registry — public API.

See SOURCE_ADAPTERS.md for the design.
"""
from sources.base import SourceAdapter, AdapterResult, IngestRequest, FetchedContent
from sources.registry import (
    register_adapter,
    get_adapter,
    list_adapters,
    resolve_by_bucket,
    ingest,
    batch_ingest,
)
from sources.extraction import (
    TopicExtractor,
    ExtractedFields,
    register_extractor,
    get_extractor,
    list_extractors,
)

# Importing this package side-effect-registers all built-in adapters.
from sources import adapters  # noqa: F401

__all__ = [
    # Types
    "SourceAdapter", "AdapterResult", "IngestRequest", "FetchedContent",
    "TopicExtractor", "ExtractedFields",
    # Registry
    "register_adapter", "get_adapter", "list_adapters", "resolve_by_bucket",
    "ingest", "batch_ingest",
    # Extractor registry
    "register_extractor", "get_extractor", "list_extractors",
]
