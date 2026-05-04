"""PDF source adapters.

Mechanism: HTTP download (or local file path) + PDF text extraction.
Default extractor is pdfplumber (better with complex tables — useful
for datasheets and research papers); pypdf fallback for prose-heavy
PDFs where pdfplumber stalls.

Genres:
  - pdf/datasheet     product specs (uses llm_structured_specs)
  - pdf/whitepaper    long-form thought leadership (uses llm_default)
  - pdf/research      academic papers (uses llm_research_paper)
  - pdf/marketing     case studies, brochures (uses llm_default)

Identifier semantics: a URL (https://...) or a local file path
(/path/to/file.pdf or file:///path/to/file.pdf).
"""
from __future__ import annotations

import io
import logging
from pathlib import Path
from typing import Any

from sources.base import (
    FetchedContent,
    IngestRequest,
    SourceAdapter,
)
from sources.registry import register_adapter
from sources.util import parse_relative_date

logger = logging.getLogger(__name__)


def _looks_like_url(s: str) -> bool:
    return s.startswith(("http://", "https://"))


def _read_local(path: str) -> bytes:
    p = Path(path.removeprefix("file://"))
    if not p.is_absolute():
        raise RuntimeError(f"PDF path must be absolute: {path!r}")
    if not p.is_file():
        raise RuntimeError(f"PDF not found at {p}")
    return p.read_bytes()


async def _download(url: str) -> bytes:
    try:
        import httpx
    except ImportError:
        raise RuntimeError(
            "httpx not installed. pip install httpx --break-system-packages"
        )
    async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
        r = await client.get(url, headers={"User-Agent": "Pantheon/1.0"})
        r.raise_for_status()
        return r.content


def _extract_with_pdfplumber(blob: bytes) -> tuple[str, dict]:
    """Return (text, metadata). Best for tables / structured layouts.

    Stitches per-page text with a ``\\n\\n--- page N ---\\n\\n`` divider
    so the LLM has a sense of the page boundaries (helps with
    research papers / datasheets where structure matters).
    """
    import pdfplumber
    parts: list[str] = []
    metadata: dict[str, Any] = {}
    with pdfplumber.open(io.BytesIO(blob)) as pdf:
        if pdf.metadata:
            metadata = {k: str(v) for k, v in pdf.metadata.items()}
        for i, page in enumerate(pdf.pages, start=1):
            try:
                t = page.extract_text() or ""
            except Exception as e:
                t = f"[page {i} extraction failed: {e}]"
            parts.append(f"--- page {i} ---\n{t}")
    return "\n\n".join(parts), metadata


def _extract_with_pypdf(blob: bytes) -> tuple[str, dict]:
    """Faster fallback for prose-heavy PDFs."""
    import pypdf
    reader = pypdf.PdfReader(io.BytesIO(blob))
    metadata = {}
    if reader.metadata:
        metadata = {str(k): str(v) for k, v in reader.metadata.items()}
    parts = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            t = page.extract_text() or ""
        except Exception as e:
            t = f"[page {i} extraction failed: {e}]"
        parts.append(f"--- page {i} ---\n{t}")
    return "\n\n".join(parts), metadata


class _PDFAdapterBase(SourceAdapter):
    """Shared download + extraction logic for every pdf/* type."""

    artifact_path_template = "pdfs/{published_at}/{author_or_publisher}/{slug}.md"
    bucket_aliases = ("pdf",)
    requires_mcp = ()

    # Default to pdfplumber. Subclasses can flip to pypdf for speed
    # when complex tables aren't expected.
    pdf_engine: str = "pdfplumber"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        if _looks_like_url(req.identifier):
            blob = await _download(req.identifier)
        else:
            blob = _read_local(req.identifier)

        # Primary engine.
        try:
            if self.pdf_engine == "pypdf":
                text, meta = _extract_with_pypdf(blob)
            else:
                text, meta = _extract_with_pdfplumber(blob)
        except Exception as e:
            logger.warning(
                "primary engine %s failed for %s: %s; trying fallback",
                self.pdf_engine, req.identifier, e,
            )
            try:
                text, meta = _extract_with_pypdf(blob)
            except Exception as e2:
                raise RuntimeError(
                    f"both pdfplumber and pypdf failed for {req.identifier!r}: "
                    f"primary={e}; fallback={e2}"
                )

        if not text or len(text.strip()) < 100:
            raise RuntimeError(
                f"PDF extracted < 100 chars from {req.identifier!r}; "
                f"may be image-only / scanned (would need OCR)"
            )

        # Title / author / date from metadata where available.
        title = (
            meta.get("/Title") or meta.get("Title")
            or req.extras.get("title") or Path(req.identifier).stem
        )
        author = (
            meta.get("/Author") or meta.get("Author")
            or req.extras.get("author_or_publisher") or ""
        )
        date_str = (
            meta.get("/CreationDate") or meta.get("CreationDate")
            or meta.get("/ModDate") or meta.get("ModDate") or ""
        )
        # PDF dates often come as "D:20250403120000Z00'00'" — pull the YYYYMMDD.
        published_at: str | None = None
        if isinstance(date_str, str) and date_str.startswith("D:") and len(date_str) >= 10:
            d = date_str[2:10]
            published_at = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        elif isinstance(date_str, str) and len(date_str) >= 10:
            published_at = parse_relative_date(date_str[:10]) or date_str[:10]
        if not published_at:
            published_at = (
                req.extras.get("published_at")
                or parse_relative_date(req.extras.get("published"))
            )

        return FetchedContent(
            text=text,
            title=str(title),
            author_or_publisher=str(author),
            url=req.identifier if _looks_like_url(req.identifier) else "",
            published_at=published_at,
            extra_meta={
                "retrieved_at": req.extras.get("retrieved_at"),
                "fetch_method": f"pdf:{self.pdf_engine}",
                "page_count_hint": (text.count("--- page ") or 1),
            },
        )


class PDFDatasheet(_PDFAdapterBase):
    source_type = "pdf/datasheet"
    display_name = "PDF — product datasheet / spec sheet"
    extractor_strategy = "llm_structured_specs"
    pdf_engine = "pdfplumber"  # tables matter here


class PDFWhitepaper(_PDFAdapterBase):
    source_type = "pdf/whitepaper"
    display_name = "PDF — whitepaper / thought leadership"
    extractor_strategy = "llm_default"
    pdf_engine = "pypdf"  # prose, faster


class PDFResearch(_PDFAdapterBase):
    source_type = "pdf/research"
    display_name = "PDF — research / academic paper"
    extractor_strategy = "llm_research_paper"
    pdf_engine = "pdfplumber"  # tables + figures matter


class PDFMarketing(_PDFAdapterBase):
    source_type = "pdf/marketing"
    display_name = "PDF — marketing / case study / brochure"
    extractor_strategy = "llm_default"
    pdf_engine = "pypdf"


register_adapter(PDFDatasheet())
register_adapter(PDFWhitepaper())
register_adapter(PDFResearch())
register_adapter(PDFMarketing())
