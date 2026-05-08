"""Podcast source adapter.

Mechanism: HTTP fetch + trafilatura article extraction over an
episode page URL. Most podcast hosts (Buzzsprout, Transistor,
Substack-podcasts, Apple show pages, podcast websites) publish a
public episode page with title, description, and — increasingly —
a transcript. trafilatura handles those cleanly.

For audio-only podcasts (no transcript on the page) the caller can
provide a transcript directly via ``extras['transcript']`` — the
adapter uses it verbatim and skips the HTTP fetch. This is how
external transcription pipelines (whisper, otter.ai, etc.) plug in
without requiring this adapter to ship its own STT engine.

Genres:
  - podcast/episode    one episode page (uses ``llm_default``;
                       speakers populated when the transcript
                       attributes utterances)

If trafilatura returns < 100 chars of body and no transcript was
supplied, the adapter raises so the registry surfaces a clean
``fetch_failed`` skip rather than producing a noise artifact.
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
from sources.util import parse_relative_date, slugify

logger = logging.getLogger(__name__)


class PodcastEpisode(SourceAdapter):
    source_type = "podcast/episode"
    display_name = "Podcast — episode page or transcript"
    bucket_aliases = ("podcast",)
    requires_mcp = ()
    artifact_path_template = (
        "podcasts/{published_at}/{author_or_publisher}/{slug}.md"
    )
    extractor_strategy = "llm_default"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        extras = req.extras or {}
        # Caller-supplied transcript path: skip HTTP, use it verbatim.
        provided_transcript = extras.get("transcript")
        if isinstance(provided_transcript, str) and provided_transcript.strip():
            text = provided_transcript.strip() + "\n"
            title = extras.get("title") or req.identifier
            author = extras.get("author_or_publisher") or extras.get("show") or ""
            url = extras.get("url") or (req.identifier if req.identifier.startswith("http") else "")
            published_at = (
                extras.get("published_at")
                or parse_relative_date(extras.get("published"))
            )
            return FetchedContent(
                text=text, title=title, author_or_publisher=author,
                url=url, published_at=published_at,
                extra_meta={
                    "retrieved_at": extras.get("retrieved_at"),
                    "fetch_method": "caller_supplied_transcript",
                    "episode_guid": extras.get("episode_guid"),
                    "duration_seconds": extras.get("duration_seconds"),
                    "show": extras.get("show"),
                },
            )

        # Otherwise: trafilatura on the URL (same shape as blog adapter).
        try:
            import trafilatura
        except ImportError:
            raise RuntimeError(
                "trafilatura not installed. "
                "pip install trafilatura --break-system-packages"
            )
        url = req.identifier
        if not url.startswith(("http://", "https://")):
            raise RuntimeError(
                f"podcast/episode: identifier must be a URL or "
                f"extras['transcript'] must be supplied; got {url!r}"
            )

        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            raise RuntimeError(f"trafilatura.fetch_url returned None for {url!r}")

        from sources.util import html_to_markdown as _h2m
        cleaned_html = trafilatura.extract(
            downloaded, url=url,
            output_format="html",
            include_tables=True,
            include_images=False,
            include_comments=False,
            favor_precision=False,
            with_metadata=False,
        )
        text = _h2m(cleaned_html or "")
        if not text or len(text.strip()) < 100:
            text = trafilatura.extract(
                downloaded, url=url,
                output_format="markdown",
                include_tables=True,
                include_images=False,
                include_comments=False,
                favor_precision=False,
                with_metadata=False,
            )
        if not text or len(text.strip()) < 100:
            raise RuntimeError(
                f"podcast/episode: trafilatura got < 100 chars from {url!r}; "
                f"page may lack a transcript / show notes — pass extras['transcript']"
            )

        meta = trafilatura.extract_metadata(downloaded) or None
        title = ""
        author = ""
        published_at: str | None = None
        if meta is not None:
            title = (getattr(meta, "title", None) or "") or ""
            author = (
                getattr(meta, "author", None)
                or getattr(meta, "sitename", None)
                or ""
            ) or ""
            d = getattr(meta, "date", None)
            if d:
                published_at = parse_relative_date(d) or str(d)[:10]

        if not title:
            title = extras.get("title") or url
        if not author:
            author = extras.get("author_or_publisher") or extras.get("show") or ""
        if not published_at:
            published_at = (
                extras.get("published_at")
                or parse_relative_date(extras.get("published"))
            )

        return FetchedContent(
            text=text,
            title=title,
            author_or_publisher=author,
            url=url,
            published_at=published_at,
            extra_meta={
                "retrieved_at": extras.get("retrieved_at"),
                "fetch_method": "trafilatura",
                "episode_guid": extras.get("episode_guid"),
                "duration_seconds": extras.get("duration_seconds"),
                "show": extras.get("show") or author,
            },
        )

    def build_frontmatter(self, req, fetched) -> dict[str, Any]:
        fm = super().build_frontmatter(req, fetched)
        # Surface show as a top-level frontmatter field so graph
        # extraction can attach show -> episode edges later if desired.
        show = fetched.extra_meta.get("show") or fetched.author_or_publisher
        if show:
            fm["show"] = show
        return fm


register_adapter(PodcastEpisode())
