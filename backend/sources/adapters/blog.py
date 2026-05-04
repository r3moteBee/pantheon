"""Blog source adapters.

Mechanism: HTTP fetch + trafilatura article extraction. Outputs
clean markdown to feed the topic extractor. Same fetch logic for
all genres; subclasses just declare display_name + extractor_strategy
and let registry.ingest() do the rest.

Genres:
  - blog/announcement   product launches, partnerships, milestones
                        (uses llm_announcement extractor — captures
                         dates, dollar amounts, partners, products)
  - blog/influencer     opinion / industry analysis from named
                        figures (uses llm_default; speakers
                        captured when transcript-style)
  - blog/technical      engineering deep-dives, feature breakdowns
                        (uses llm_default)
  - blog/news           press / media coverage (uses llm_default)

If trafilatura fails to extract a clean body — usually because the
page is JavaScript-rendered or behind a paywall — the adapter
records the failure on FetchedContent.extra_meta["fetch_error"]
and lets the registry fail-fast with a useful message.
"""
from __future__ import annotations

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


class _BlogAdapterBase(SourceAdapter):
    """Shared HTTP + trafilatura fetch logic for every blog/* type."""

    artifact_path_template = (
        "blogs/{published_at}/{author_or_publisher}/{slug}.md"
    )
    bucket_aliases = ("blog",)
    requires_mcp = ()  # no MCP needed; uses trafilatura directly

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        try:
            import trafilatura
        except ImportError:
            raise RuntimeError(
                "trafilatura not installed. "
                "pip install trafilatura --break-system-packages"
            )

        url = req.identifier
        # trafilatura.fetch_url returns the raw HTML.
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            raise RuntimeError(f"trafilatura.fetch_url returned None for {url!r}")

        # extract returns clean markdown by default; include_tables
        # and include_lists make sure structured info isn't dropped.
        text = trafilatura.extract(
            downloaded,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_lists=True,
            include_images=False,
            include_comments=False,
            favor_precision=False,  # we want full body; LLM handles boilerplate
            with_metadata=False,
        )
        if not text or len(text.strip()) < 100:
            raise RuntimeError(
                f"trafilatura extracted < 100 chars from {url!r}; "
                f"page may be JS-rendered or behind a paywall"
            )

        # Pull metadata separately — title, author, date.
        meta = trafilatura.extract_metadata(downloaded) or None
        title = ""
        author = ""
        published_at: str | None = None
        if meta is not None:
            title = (getattr(meta, "title", None) or "") or ""
            author = (getattr(meta, "author", None) or "") or ""
            d = getattr(meta, "date", None)
            if d:
                # trafilatura returns YYYY-MM-DD strings.
                published_at = parse_relative_date(d) or str(d)[:10]

        # Allow caller to override metadata via extras.
        if not title:
            title = req.extras.get("title") or url
        if not author:
            author = req.extras.get("author_or_publisher") or ""
        if not published_at:
            published_at = (
                req.extras.get("published_at")
                or parse_relative_date(req.extras.get("published"))
            )

        return FetchedContent(
            text=text,
            title=title,
            author_or_publisher=author,
            url=url,
            published_at=published_at,
            extra_meta={
                "retrieved_at": req.extras.get("retrieved_at"),
                "fetch_method": "trafilatura",
            },
        )


class BlogAnnouncement(_BlogAdapterBase):
    source_type = "blog/announcement"
    display_name = "Blog — vendor / event announcement"
    extractor_strategy = "llm_announcement"


class BlogInfluencer(_BlogAdapterBase):
    source_type = "blog/influencer"
    display_name = "Blog — influencer / industry analyst"
    extractor_strategy = "llm_default"


class BlogTechnical(_BlogAdapterBase):
    source_type = "blog/technical"
    display_name = "Blog — technical deep-dive"
    extractor_strategy = "llm_default"


class BlogNews(_BlogAdapterBase):
    source_type = "blog/news"
    display_name = "Blog — news / press coverage"
    extractor_strategy = "llm_default"


register_adapter(BlogAnnouncement())
register_adapter(BlogInfluencer())
register_adapter(BlogTechnical())
register_adapter(BlogNews())
