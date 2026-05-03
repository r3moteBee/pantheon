"""YouTube source adapters.

Three concrete source_types share one fetcher (mcp_SubDownload_*)
and one frontmatter builder, but report different display names and
different bucket aliases so the heuristic+bucket type resolver can
route per-video correctly.

This is the canonical example of how to add a source — once you've
got the fetch working, you typically only override fetch() and let
build_frontmatter() do the default typed-topics shape.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sources.base import (
    FetchedContent,
    IngestRequest,
    SourceAdapter,
)
from sources.registry import register_adapter
from sources.util import parse_relative_date

logger = logging.getLogger(__name__)


def _resolve_published_at(extras: dict) -> str | None:
    """Pick the best published_at signal from a request's extras.

    Skills calling search_youtube get back a 'published' field with
    a relative string ("4 months ago"). Forwarding that through
    extras['published'] lets us derive an ISO date in the adapter.
    Skills that already have an absolute date can pass
    extras['published_at'] directly. Absolute wins if both are set.
    """
    if not isinstance(extras, dict):
        return None
    abs_v = extras.get("published_at")
    if isinstance(abs_v, str) and abs_v.strip():
        # Trust it's already ISO-ish; parse_relative_date passes
        # ISO inputs through.
        parsed = parse_relative_date(abs_v)
        if parsed:
            return parsed
    rel_v = extras.get("published") or extras.get("published_relative")
    if isinstance(rel_v, str) and rel_v.strip():
        parsed = parse_relative_date(rel_v)
        if parsed:
            return parsed
    return None


class _YouTubeAdapterBase(SourceAdapter):
    """Shared fetch logic for every youtube/* source_type."""

    requires_mcp = ("mcp_SubDownload_fetch_transcript",)
    artifact_path_template = (
        "youtube-transcripts/{published_at}/{author_or_publisher}/"
        "{identifier}-{slug}.md"
    )
    bucket_aliases = ("youtube",)

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        from mcp_client.manager import get_mcp_manager
        mgr = get_mcp_manager()
        raw = await mgr.execute_tool(
            "mcp_SubDownload_fetch_transcript",
            {"video_id": req.identifier, "save": False},
        )
        payload = raw
        if isinstance(payload, str):
            payload = json.loads(payload)
        inner = payload.get("result") if isinstance(payload, dict) and isinstance(payload.get("result"), dict) else payload
        text = (inner or {}).get("text") or ""
        if not text and isinstance((inner or {}).get("segments"), list):
            text = "\n".join(
                (seg.get("text") or "").strip() for seg in inner["segments"]
            )
        meta = (inner or {}).get("meta") or {}
        return FetchedContent(
            text=text,
            title=meta.get("title", "") or req.identifier,
            author_or_publisher=meta.get("author", ""),
            url=(inner or {}).get("video_url") or f"https://www.youtube.com/watch?v={req.identifier}",
            published_at=_resolve_published_at(req.extras),
            extra_meta={
                "video_id": req.identifier,
                "channel_id": meta.get("channel_id", ""),
                "thumbnail": meta.get("thumbnail", ""),
                "language": (inner or {}).get("language", ""),
                "retrieved_at": req.extras.get("retrieved_at"),
            },
        )

    def build_frontmatter(
        self, req: IngestRequest, fetched: FetchedContent,
    ) -> dict[str, Any]:
        """Override default to add YouTube-specific fields next to
        the canonical typed-topics shape."""
        fm = super().build_frontmatter(req, fetched)
        fm["video_id"] = fetched.extra_meta.get("video_id", "")
        fm["video_title"] = fetched.title
        fm["channel_name"] = fetched.author_or_publisher
        # topics[] and speakers[] are intentionally left as []. They
        # get populated by an LLM extraction step that the skill
        # orchestrates after ingest, then update_artifact rewrites
        # the frontmatter. The graph extractor then re-runs on
        # update and builds the proper edges.
        return fm


class YouTubeInterview(_YouTubeAdapterBase):
    source_type = "youtube/interview"
    display_name = "YouTube interview / Q&A"


class YouTubeKeynote(_YouTubeAdapterBase):
    source_type = "youtube/keynote"
    display_name = "YouTube keynote / main session"


class YouTubeOther(_YouTubeAdapterBase):
    source_type = "youtube/other"
    display_name = "YouTube (uncategorized)"


# Register all three at import time.
register_adapter(YouTubeInterview())
register_adapter(YouTubeKeynote())
register_adapter(YouTubeOther())
