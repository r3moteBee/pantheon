"""Web source adapters.

Mechanism: HTTP fetch + trafilatura extraction, but with table/list
inclusion turned high so structured info (specs, pricing, feature
lists, version tables) survives the article-extraction pass.

Genres:
  - web/product-page    structured product info (uses llm_structured_specs)
  - web/service-page    SaaS service description (uses llm_structured_specs)
  - web/changelog       release notes / version history (uses llm_changelog)

Service vs product is cosmetic at the data level — both produce the
same frontmatter shape. They\'re separate source_types so downstream
filtering (\"show me all NVIDIA product pages\") stays clean.
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


class _WebAdapterBase(SourceAdapter):
    """Shared HTTP + trafilatura fetch logic for every web/* type."""

    artifact_path_template = (
        "web/{source_type}/{author_or_publisher}/{slug}.md"
    )
    bucket_aliases = ("web",)
    requires_mcp = ()

    # Web pages with structured data want every list/table preserved.
    favor_precision: bool = False

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        try:
            import trafilatura
        except ImportError:
            raise RuntimeError(
                "trafilatura not installed. "
                "pip install trafilatura --break-system-packages"
            )

        url = req.identifier
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            raise RuntimeError(f"trafilatura.fetch_url returned None for {url!r}")

        text = trafilatura.extract(
            downloaded,
            url=url,
            output_format="markdown",
            include_tables=True,
            include_lists=True,
            include_images=False,
            include_comments=False,
            favor_precision=self.favor_precision,
            with_metadata=False,
        )
        if not text or len(text.strip()) < 100:
            raise RuntimeError(
                f"trafilatura extracted < 100 chars from {url!r}; "
                f"page may be JS-rendered or behind a login wall"
            )

        meta = trafilatura.extract_metadata(downloaded) or None
        title = ""
        author = ""
        published_at: str | None = None
        if meta is not None:
            title = (getattr(meta, "title", None) or "") or ""
            author = (getattr(meta, "author", None) or getattr(meta, "sitename", None) or "") or ""
            d = getattr(meta, "date", None)
            if d:
                published_at = parse_relative_date(d) or str(d)[:10]

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

    def render_artifact_path(self, req, fetched):
        # Override to substitute source_type slug into the template.
        from sources.util import slugify
        published = fetched.published_at or "unknown-date"
        return self.artifact_path_template.format(
            source_type=self.source_type.replace("/", "-"),
            slug=slugify(fetched.title) or "page",
            author_or_publisher=slugify(fetched.author_or_publisher) or "unknown",
            published_at=published,
            identifier=slugify(req.identifier),
        )


class WebProductPage(_WebAdapterBase):
    source_type = "web/product-page"
    display_name = "Web — product page"
    extractor_strategy = "llm_structured_specs"


class WebServicePage(_WebAdapterBase):
    source_type = "web/service-page"
    display_name = "Web — service / SaaS description page"
    extractor_strategy = "llm_structured_specs"


class WebChangelog(_WebAdapterBase):
    source_type = "web/changelog"
    display_name = "Web — changelog / release notes"
    extractor_strategy = "llm_changelog"


register_adapter(WebProductPage())
register_adapter(WebServicePage())
register_adapter(WebChangelog())
