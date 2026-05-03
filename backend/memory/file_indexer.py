"""File ingestion pipeline — extract, chunk, embed, and index workspace files.

Turns passive file storage into searchable semantic memory.  Supports
Markdown (with YAML frontmatter), plain text, CSV, PDF, and images.

Images are described via a vision-capable LLM and the description is
stored as a single semantic chunk, making image content retrievable
alongside text during inference.

For Markdown files with YAML frontmatter, structured metadata
(source, topics, speakers, etc.) is extracted and routed to the
graph as well — see _index_typed_topics_to_graph for the canonical
shape used by ingest skills.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Supported file types ─────────────────────────────────────────────────────

from utils.vision import IMAGE_EXTENSIONS, describe_image

SUPPORTED_EXTENSIONS = {
    ".md", ".markdown", ".txt", ".text", ".csv", ".tsv",
    ".json", ".yaml", ".yml", ".py", ".js", ".ts",
    ".html", ".htm", ".xml", ".log",
    ".pdf",
} | IMAGE_EXTENSIONS

# ── Chunking defaults ────────────────────────────────────────────────────────

DEFAULT_CHUNK_SIZE = 500      # tokens (~2000 chars)
DEFAULT_CHUNK_OVERLAP = 50    # tokens (~200 chars)
CHARS_PER_TOKEN = 4           # rough estimate


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _content_hash(content: str) -> str:
    """SHA-256 hash of content for dedup/change detection."""
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


# ── YAML frontmatter parsing ────────────────────────────────────────────────

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Split YAML frontmatter from body text.

    Returns (metadata_dict, body_text).  If no frontmatter, returns ({}, text).
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text
    try:
        import yaml
        meta = yaml.safe_load(match.group(1)) or {}
        body = text[match.end():]
        return meta, body
    except Exception as e:
        logger.debug("YAML frontmatter parse failed: %s", e)
        return {}, text


# ── Text extraction ──────────────────────────────────────────────────────────

async def extract_text(file_path: Path) -> str:
    """Extract text content from a file based on its extension."""
    ext = file_path.suffix.lower()

    if ext == ".pdf":
        return await _extract_pdf(file_path)
    elif ext == ".csv" or ext == ".tsv":
        return _extract_csv(file_path)
    else:
        # Default: read as text
        try:
            return file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as e:
            logger.warning("Failed to read %s: %s", file_path, e)
            return ""


async def _extract_pdf(file_path: Path) -> str:
    """Extract text from PDF, falling back to vision OCR for scanned pages.

    Pipeline:
    1. Extract text via pdfplumber (or pymupdf).
    2. Any page that yields no text is rendered to an image and sent through
       the vision model for OCR-style description.
    3. Results are stitched together page-by-page.
    """
    page_texts: list[tuple[int, str | None]] = []  # (page_num, text_or_None)

    # ── Phase 1: text extraction ────────────────────────────────────────
    extracted = False
    try:
        import pdfplumber
        with pdfplumber.open(str(file_path)) as pdf:
            for i, page in enumerate(pdf.pages):
                text = (page.extract_text() or "").strip()
                page_texts.append((i, text if text else None))
        extracted = True
    except ImportError:
        pass

    if not extracted:
        try:
            import fitz  # pymupdf
            doc = fitz.open(str(file_path))
            for i, page in enumerate(doc):
                text = (page.get_text() or "").strip()
                page_texts.append((i, text if text else None))
            doc.close()
            extracted = True
        except ImportError:
            pass

    if not extracted:
        logger.warning("No PDF library available (install pdfplumber or pymupdf)")
        return ""

    # ── Phase 2: vision OCR for pages with no text ──────────────────────
    blank_pages = [i for i, t in page_texts if t is None]
    if blank_pages:
        vision_results = await _ocr_pdf_pages(file_path, blank_pages)
        for page_num, desc in vision_results.items():
            # Replace the None entry
            for idx, (pn, _) in enumerate(page_texts):
                if pn == page_num:
                    page_texts[idx] = (pn, desc)
                    break

    # ── Assemble ────────────────────────────────────────────────────────
    parts = []
    for page_num, text in page_texts:
        if text:
            parts.append(text)
    return "\n\n".join(parts)


async def _ocr_pdf_pages(file_path: Path, page_numbers: list[int]) -> dict[int, str]:
    """Render specific PDF pages to images and describe via vision model.

    Returns {page_number: description} for pages that got a description.
    """
    results: dict[int, str] = {}

    # Try pymupdf first (built-in rendering, no external deps)
    page_images = _render_pages_pymupdf(file_path, page_numbers)

    if page_images is None:
        # Fallback: pdf2image (requires poppler)
        page_images = _render_pages_pdf2image(file_path, page_numbers)

    if not page_images:
        logger.warning("Cannot render PDF pages to images (install pymupdf or pdf2image+poppler)")
        return results

    for page_num, img_bytes in page_images.items():
        try:
            fake_path = Path(f"{file_path.stem}_page{page_num + 1}.png")
            desc = await describe_image(
                fake_path,
                content=img_bytes,
                detail_prompt=(
                    f"This is page {page_num + 1} of a PDF document '{file_path.name}'. "
                    "Extract and transcribe ALL text visible on this page. "
                    "Include headings, paragraphs, table contents, form fields, "
                    "captions, and any other text. Preserve the logical reading order. "
                    "If there are diagrams or images, briefly describe them."
                ),
            )
            if desc and len(desc) > 10:
                results[page_num] = f"[Page {page_num + 1} — OCR]\n{desc}"
                logger.info("Vision OCR page %d of %s: %s", page_num + 1, file_path.name, desc[:80])
        except Exception as e:
            logger.warning("Vision OCR failed for page %d of %s: %s", page_num + 1, file_path.name, e)

    return results


def _render_pages_pymupdf(file_path: Path, page_numbers: list[int]) -> dict[int, bytes] | None:
    """Render PDF pages to PNG bytes using pymupdf (fitz)."""
    try:
        import fitz
    except ImportError:
        return None

    images: dict[int, bytes] = {}
    try:
        doc = fitz.open(str(file_path))
        for page_num in page_numbers:
            if page_num >= len(doc):
                continue
            page = doc[page_num]
            # Render at 2x for better OCR quality
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
            images[page_num] = pix.tobytes("png")
        doc.close()
    except Exception as e:
        logger.warning("pymupdf rendering failed: %s", e)
    return images if images else None


def _render_pages_pdf2image(file_path: Path, page_numbers: list[int]) -> dict[int, bytes] | None:
    """Render PDF pages to PNG bytes using pdf2image (requires poppler)."""
    try:
        from pdf2image import convert_from_path
        import io
    except ImportError:
        return None

    images: dict[int, bytes] = {}
    try:
        for page_num in page_numbers:
            # pdf2image uses 1-based page numbers
            pil_images = convert_from_path(
                str(file_path),
                first_page=page_num + 1,
                last_page=page_num + 1,
                dpi=200,
            )
            if pil_images:
                buf = io.BytesIO()
                pil_images[0].save(buf, format="PNG")
                images[page_num] = buf.getvalue()
    except Exception as e:
        logger.warning("pdf2image rendering failed: %s", e)
    return images if images else None


def _extract_csv(file_path: Path) -> str:
    """Extract CSV as readable text (headers + sample rows)."""
    import csv
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            rows = []
            for i, row in enumerate(reader):
                rows.append(" | ".join(row))
                if i > 100:  # Cap at 100 rows for indexing
                    rows.append(f"... ({i}+ rows total)")
                    break
        return "\n".join(rows)
    except Exception as e:
        logger.warning("CSV extraction failed for %s: %s", file_path, e)
        return ""


# ── Chunking ─────────────────────────────────────────────────────────────────

def chunk_text(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    respect_headings: bool = True,
) -> list[dict[str, Any]]:
    """Split text into overlapping chunks for embedding.

    Returns list of dicts: {content, chunk_index, heading, char_start, char_end}

    When respect_headings=True (default for Markdown), heading boundaries
    are preferred split points.
    """
    if not text.strip():
        return []

    max_chars = chunk_size * CHARS_PER_TOKEN
    overlap_chars = chunk_overlap * CHARS_PER_TOKEN

    if respect_headings:
        chunks = _chunk_by_headings(text, max_chars, overlap_chars)
    else:
        chunks = _chunk_by_paragraphs(text, max_chars, overlap_chars)

    return chunks


def _chunk_by_headings(text: str, max_chars: int, overlap_chars: int) -> list[dict]:
    """Split on Markdown headings first, then by paragraphs within sections."""
    # Split on heading lines (## Heading)
    heading_pattern = re.compile(r"^(#{1,4})\s+(.+)$", re.MULTILINE)
    sections: list[tuple[str, str]] = []  # (heading, content)

    last_end = 0
    current_heading = ""
    for match in heading_pattern.finditer(text):
        if last_end > 0 or match.start() > 0:
            section_text = text[last_end:match.start()].strip()
            if section_text:
                sections.append((current_heading, section_text))
        current_heading = match.group(2).strip()
        last_end = match.end()

    # Remaining text after last heading
    remaining = text[last_end:].strip()
    if remaining:
        sections.append((current_heading, remaining))

    # If no headings found, fall back to paragraph chunking
    if not sections:
        return _chunk_by_paragraphs(text, max_chars, overlap_chars)

    # Now chunk each section
    chunks = []
    idx = 0
    for heading, section_text in sections:
        if len(section_text) <= max_chars:
            chunks.append({
                "content": section_text,
                "chunk_index": idx,
                "heading": heading,
            })
            idx += 1
        else:
            # Sub-chunk by paragraphs
            sub_chunks = _chunk_by_paragraphs(section_text, max_chars, overlap_chars)
            for sc in sub_chunks:
                sc["heading"] = heading
                sc["chunk_index"] = idx
                chunks.append(sc)
                idx += 1

    return chunks


def _chunk_by_paragraphs(text: str, max_chars: int, overlap_chars: int) -> list[dict]:
    """Split text into chunks by paragraph boundaries with overlap."""
    paragraphs = re.split(r"\n\s*\n", text)
    chunks = []
    current = ""
    idx = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        if len(current) + len(para) + 2 > max_chars and current:
            chunks.append({
                "content": current.strip(),
                "chunk_index": idx,
                "heading": "",
            })
            idx += 1
            # Keep overlap from end of previous chunk
            if overlap_chars > 0 and len(current) > overlap_chars:
                current = current[-overlap_chars:] + "\n\n" + para
            else:
                current = para
        else:
            current = current + "\n\n" + para if current else para

    if current.strip():
        chunks.append({
            "content": current.strip(),
            "chunk_index": idx,
            "heading": "",
        })

    return chunks


# ── Index tracking (SQLite) ──────────────────────────────────────────────────

class FileIndex:
    """Tracks which files have been indexed and their content hashes.

    Prevents re-indexing unchanged files and enables incremental updates.
    """

    def __init__(self, db_path: str = "data/file_index.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS indexed_files (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    chunk_count INTEGER DEFAULT 0,
                    indexed_at TEXT NOT NULL,
                    file_size INTEGER DEFAULT 0,
                    metadata TEXT DEFAULT '{}',
                    UNIQUE(project_id, file_path)
                )
            """)
            conn.commit()

    def is_indexed(self, project_id: str, file_path: str, content_hash: str) -> bool:
        """Check if a file with this hash is already indexed."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT content_hash FROM indexed_files WHERE project_id = ? AND file_path = ?",
                (project_id, file_path)
            ).fetchone()
        return row is not None and row[0] == content_hash

    def mark_indexed(
        self,
        project_id: str,
        file_path: str,
        content_hash: str,
        chunk_count: int,
        file_size: int = 0,
        metadata: dict | None = None,
    ) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                INSERT INTO indexed_files (id, project_id, file_path, content_hash, chunk_count, indexed_at, file_size, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(project_id, file_path) DO UPDATE SET
                    content_hash = excluded.content_hash,
                    chunk_count = excluded.chunk_count,
                    indexed_at = excluded.indexed_at,
                    file_size = excluded.file_size,
                    metadata = excluded.metadata
            """, (
                str(uuid.uuid4()), project_id, file_path, content_hash,
                chunk_count, _now_iso(), file_size,
                json.dumps(metadata or {}),
            ))
            conn.commit()

    def remove_indexed(self, project_id: str, file_path: str) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "DELETE FROM indexed_files WHERE project_id = ? AND file_path = ?",
                (project_id, file_path)
            )
            conn.commit()

    def list_indexed(self, project_id: str) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM indexed_files WHERE project_id = ? ORDER BY indexed_at DESC",
                (project_id,)
            ).fetchall()
        return [dict(r) for r in rows]


# ── Main indexer ─────────────────────────────────────────────────────────────

class FileIndexer:
    """Indexes workspace files into semantic memory and graph.

    Usage:
        indexer = FileIndexer(memory_manager, project_id="my-project")
        stats = await indexer.index_file(Path("/workspace/notes/2026-q1-summary.md"))
        stats = await indexer.index_directory(Path("/workspace/"))
    """

    def __init__(
        self,
        memory_manager: Any,
        project_id: str = "default",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    ):
        self.memory_manager = memory_manager
        self.project_id = project_id
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.file_index = FileIndex()

    async def index_file(
        self,
        file_path: Path,
        force: bool = False,
    ) -> dict[str, Any]:
        """Index a single file: extract, chunk, embed, store.

        Returns stats: {file, chunks_stored, entities_extracted, skipped}
        """
        stats = {
            "file": str(file_path),
            "chunks_stored": 0,
            "entities_extracted": 0,
            "skipped": False,
        }

        ext = file_path.suffix.lower()
        if ext not in SUPPORTED_EXTENSIONS:
            stats["skipped"] = True
            stats["reason"] = f"unsupported extension: {file_path.suffix}"
            return stats

        rel_path = str(file_path)

        # ── Image files: describe via vision model ──────────────────────
        if ext in IMAGE_EXTENSIONS:
            return await self._index_image(file_path, rel_path, stats, force)

        # ── Text-based files ────────────────────────────────────────────
        # Extract text
        text = await extract_text(file_path)
        if not text.strip():
            stats["skipped"] = True
            stats["reason"] = "empty content"
            return stats

        # Check if already indexed with same content
        content_hash = _content_hash(text)
        if not force and self.file_index.is_indexed(self.project_id, rel_path, content_hash):
            stats["skipped"] = True
            stats["reason"] = "unchanged"
            return stats

        # Parse frontmatter if Markdown
        frontmatter = {}
        body = text
        if ext in (".md", ".markdown"):
            frontmatter, body = parse_frontmatter(text)

        # Chunk
        chunks = chunk_text(
            body,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            respect_headings=ext in (".md", ".markdown"),
        )

        # Store each chunk in semantic memory
        for chunk in chunks:
            try:
                chunk_meta = {
                    "type": "file_chunk",
                    "source_file": file_path.name,
                    "source_path": rel_path,
                    "chunk_index": str(chunk["chunk_index"]),
                    "heading": chunk.get("heading", ""),
                    "content_hash": content_hash,
                    "project_id": self.project_id,
                    "indexed_at": _now_iso(),
                }
                # Merge frontmatter fields as metadata (flattened)
                for k, v in frontmatter.items():
                    if isinstance(v, (str, int, float, bool)):
                        chunk_meta[f"fm_{k}"] = str(v)
                    elif isinstance(v, list):
                        chunk_meta[f"fm_{k}"] = ",".join(str(x) for x in v)

                await self.memory_manager.semantic.store(
                    content=chunk["content"],
                    metadata=chunk_meta,
                )
                stats["chunks_stored"] += 1
            except Exception as e:
                logger.warning("Failed to store chunk %d of %s: %s", chunk["chunk_index"], file_path.name, e)

        # Extract frontmatter entities to graph (for structured files)
        if frontmatter:
            stats["entities_extracted"] = await self._index_frontmatter_to_graph(
                frontmatter, file_path.name
            )

        # Mark as indexed
        self.file_index.mark_indexed(
            project_id=self.project_id,
            file_path=rel_path,
            content_hash=content_hash,
            chunk_count=stats["chunks_stored"],
            file_size=file_path.stat().st_size if file_path.exists() else 0,
            metadata={"frontmatter_keys": list(frontmatter.keys())} if frontmatter else {},
        )

        logger.info(
            "Indexed %s: %d chunks, %d entities",
            file_path.name, stats["chunks_stored"], stats["entities_extracted"],
        )
        return stats

    async def index_text(
        self,
        text: str,
        *,
        virtual_path: str,
        is_markdown: bool = True,
        frontmatter_extras: dict[str, Any] | None = None,
        force: bool = False,
        source_label: str | None = None,
    ) -> dict[str, Any]:
        """Index in-memory text content (e.g., an artifact transcript).

        Mirrors index_file but skips disk extraction so callers can pass
        content directly. ``virtual_path`` is the logical identity used
        for dedup and as the displayed source path. ``frontmatter_extras``
        merges extra metadata (e.g., artifact tags, artifact_id) into
        each chunk's metadata under ``fm_*`` keys, mirroring how
        index_file handles real frontmatter.
        """
        stats = {
            "file": virtual_path,
            "chunks_stored": 0,
            "entities_extracted": 0,
            "skipped": False,
        }

        if not text or not text.strip():
            stats["skipped"] = True
            stats["reason"] = "empty content"
            return stats

        rel_path = virtual_path
        content_hash = _content_hash(text)
        if not force and self.file_index.is_indexed(self.project_id, rel_path, content_hash):
            stats["skipped"] = True
            stats["reason"] = "unchanged"
            return stats

        # Parse YAML frontmatter if the body looks like Markdown.
        frontmatter: dict[str, Any] = {}
        body = text
        if is_markdown:
            frontmatter, body = parse_frontmatter(text)
        if frontmatter_extras:
            for k, v in frontmatter_extras.items():
                frontmatter.setdefault(k, v)

        chunks = chunk_text(
            body,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            respect_headings=is_markdown,
        )

        display_name = source_label or Path(virtual_path).name or virtual_path
        for chunk in chunks:
            try:
                chunk_meta = {
                    "type": "artifact_chunk" if frontmatter_extras and "artifact_id" in frontmatter_extras else "file_chunk",
                    "source_file": display_name,
                    "source_path": rel_path,
                    "chunk_index": str(chunk["chunk_index"]),
                    "heading": chunk.get("heading", ""),
                    "content_hash": content_hash,
                    "project_id": self.project_id,
                    "indexed_at": _now_iso(),
                }
                for k, v in frontmatter.items():
                    if isinstance(v, (str, int, float, bool)):
                        chunk_meta[f"fm_{k}"] = str(v)
                    elif isinstance(v, list):
                        chunk_meta[f"fm_{k}"] = ",".join(str(x) for x in v)
                await self.memory_manager.semantic.store(
                    content=chunk["content"],
                    metadata=chunk_meta,
                )
                stats["chunks_stored"] += 1
            except Exception as e:
                logger.warning(
                    "Failed to store chunk %d of %s: %s",
                    chunk["chunk_index"], display_name, e,
                )

        if frontmatter:
            stats["entities_extracted"] = await self._index_frontmatter_to_graph(
                frontmatter, display_name,
            )

        self.file_index.mark_indexed(
            project_id=self.project_id,
            file_path=rel_path,
            content_hash=content_hash,
            chunk_count=stats["chunks_stored"],
            file_size=len(text.encode("utf-8")),
            metadata={"frontmatter_keys": list(frontmatter.keys())} if frontmatter else {},
        )

        logger.info(
            "Indexed text %s: %d chunks, %d entities",
            display_name, stats["chunks_stored"], stats["entities_extracted"],
        )
        return stats

    async def index_directory(
        self,
        directory: Path,
        recursive: bool = True,
        force: bool = False,
    ) -> dict[str, Any]:
        """Index all supported files in a directory.

        Returns aggregate stats.
        """
        total_stats = {
            "files_processed": 0,
            "files_skipped": 0,
            "total_chunks": 0,
            "total_entities": 0,
            "errors": 0,
        }

        if not directory.exists() or not directory.is_dir():
            logger.warning("Directory does not exist: %s", directory)
            return total_stats

        pattern = "**/*" if recursive else "*"
        for file_path in sorted(directory.glob(pattern)):
            if not file_path.is_file():
                continue
            if file_path.name.startswith("."):
                continue
            if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue

            try:
                result = await self.index_file(file_path, force=force)
                if result.get("skipped"):
                    total_stats["files_skipped"] += 1
                else:
                    total_stats["files_processed"] += 1
                    total_stats["total_chunks"] += result.get("chunks_stored", 0)
                    total_stats["total_entities"] += result.get("entities_extracted", 0)
            except Exception as e:
                logger.error("Failed to index %s: %s", file_path, e)
                total_stats["errors"] += 1

        logger.info(
            "Directory indexing complete: %d files, %d chunks, %d entities, %d skipped, %d errors",
            total_stats["files_processed"],
            total_stats["total_chunks"],
            total_stats["total_entities"],
            total_stats["files_skipped"],
            total_stats["errors"],
        )
        return total_stats


    async def _index_typed_topics_to_graph(
        self,
        frontmatter: dict[str, Any],
        filename: str,
        graph: Any,
    ) -> int:
        """Handle the typed-topics frontmatter shape used by
        content-ingest-graph and other ingest skills.

        Creates source / content / topic / person nodes and the
        produces / discusses / features_speaker edges per the spec.
        """
        entities_created = 0

        # 1. Source node (label = source.type, e.g. 'source:youtube/keynote')
        src = frontmatter.get("source") or {}
        source_label = ""
        if isinstance(src, dict):
            stype = (src.get("type") or "").strip()
            if stype:
                source_label = f"source:{stype}"
                try:
                    await graph.add_node("concept", source_label, metadata={
                        "entity_type": "source",
                        "source_file": filename,
                        "url": str(src.get("url") or ""),
                        "author_or_publisher": str(src.get("author_or_publisher") or ""),
                    })
                    entities_created += 1
                except Exception as e:
                    logger.debug("source node add failed: %s", e)

        # 2. Content (video/document) node — label = video_title or
        #    document_title or filename fallback.
        content_label = (
            (frontmatter.get("video_title")
             or frontmatter.get("document_title")
             or frontmatter.get("title")
             or filename)
            or ""
        ).strip()
        if content_label:
            try:
                await graph.add_node("concept", content_label, metadata={
                    "entity_type": "video" if frontmatter.get("video_id") else "document",
                    "source_file": filename,
                    "video_id": str(frontmatter.get("video_id") or ""),
                    "channel_name": str(frontmatter.get("channel_name") or ""),
                    "published_at": str(frontmatter.get("published_at") or ""),
                })
                entities_created += 1
            except Exception as e:
                logger.debug("content node add failed: %s", e)
            # source --produces--> video/document
            if source_label:
                try:
                    await graph.add_edge_by_label(source_label, content_label, "PRODUCES")
                except Exception as e:
                    logger.debug("source->content edge failed: %s", e)

        # 3. Topic nodes + discusses edges
        TYPE_MAP = {
            "concept":         ("concept", "concept"),
            "market":          ("concept", "market"),
            "market_segment":  ("concept", "market_segment"),
            "metric":          ("concept", "metric"),
            "event":           ("concept", "event"),
            "other":           ("concept", "other"),
            "technology":      ("concept", "technology"),
            "framework":       ("concept", "technology"),
            "vendor":          ("concept", "organization"),
            "organization":    ("concept", "organization"),
            "person":          ("person",  "person"),
        }
        topics = frontmatter.get("topics") or []
        if isinstance(topics, list):
            for t in topics:
                if not isinstance(t, dict):
                    continue
                label = (t.get("label") or "").strip()
                if not label:
                    continue
                ttype = (t.get("type") or "concept").strip().lower()
                node_type, entity_type = TYPE_MAP.get(ttype, ("concept", ttype or "concept"))
                meta = {
                    "entity_type": entity_type,
                    "source_file": filename,
                    "topic_type": ttype,
                }
                conf = t.get("confidence")
                if isinstance(conf, (int, float)):
                    meta["confidence"] = float(conf)
                try:
                    await graph.add_node(node_type, label, metadata=meta)
                    entities_created += 1
                except Exception as e:
                    logger.debug("topic node add failed for %r: %s", label, e)
                # video --discusses--> topic
                if content_label:
                    try:
                        await graph.add_edge_by_label(content_label, label, "DISCUSSES")
                    except Exception as e:
                        logger.debug("video->topic edge failed for %r: %s", label, e)

        # 4. Speaker nodes + features_speaker edges
        speakers = frontmatter.get("speakers") or []
        if isinstance(speakers, list):
            for sp in speakers:
                if not isinstance(sp, dict):
                    continue
                name = (sp.get("name") or "").strip()
                if not name:
                    continue
                role = (sp.get("role") or "speaker").strip().lower()
                try:
                    await graph.add_node("person", name, metadata={
                        "entity_type": "person",
                        "source_file": filename,
                        "role": role,
                    })
                    entities_created += 1
                except Exception as e:
                    logger.debug("speaker node add failed for %r: %s", name, e)
                if content_label:
                    try:
                        await graph.add_edge_by_label(content_label, name, "FEATURES_SPEAKER")
                    except Exception as e:
                        logger.debug("video->speaker edge failed for %r: %s", name, e)

        return entities_created

    async def _index_image(
        self,
        file_path: Path,
        rel_path: str,
        stats: dict[str, Any],
        force: bool,
    ) -> dict[str, Any]:
        """Index an image file by generating a vision description.

        The description is stored as a single semantic chunk with
        type='image_description', making it searchable alongside text.
        """
        # Use file size as a stable content hash for images
        file_size = file_path.stat().st_size if file_path.exists() else 0
        content_hash = _content_hash(f"{file_path.name}:{file_size}")

        if not force and self.file_index.is_indexed(self.project_id, rel_path, content_hash):
            stats["skipped"] = True
            stats["reason"] = "unchanged"
            return stats

        # Generate description via vision model
        try:
            description = await describe_image(file_path)
        except Exception as e:
            logger.warning("Vision description failed for %s: %s", file_path.name, e)
            description = None

        if not description or len(description) < 10:
            # Store basic metadata even if vision fails
            description = f"Image file: {file_path.name} ({file_size / 1024:.0f}KB)"

        # Store as semantic memory
        try:
            await self.memory_manager.semantic.store(
                content=f"Image '{file_path.name}': {description}",
                metadata={
                    "type": "image_description",
                    "source_file": file_path.name,
                    "source_path": rel_path,
                    "content_hash": content_hash,
                    "project_id": self.project_id,
                    "indexed_at": _now_iso(),
                    "file_size": str(file_size),
                },
            )
            stats["chunks_stored"] = 1
        except Exception as e:
            logger.warning("Failed to store image description for %s: %s", file_path.name, e)

        # Mark as indexed
        self.file_index.mark_indexed(
            project_id=self.project_id,
            file_path=rel_path,
            content_hash=content_hash,
            chunk_count=stats["chunks_stored"],
            file_size=file_size,
            metadata={"image_description": description[:200]},
        )

        logger.info("Indexed image %s: %s", file_path.name, description[:80])
        return stats

    async def _index_frontmatter_to_graph(
        self,
        frontmatter: dict[str, Any],
        filename: str,
    ) -> int:
        """Extract graph nodes/edges from YAML frontmatter.

        Handles two shapes:
          1. **typed-topics ingest** (the canonical shape, used by
             content-ingest-graph and any future ingest skill):
                source / video_id / topics[] / speakers[] →
                source --produces--> video --discusses--> topic
                video --features_speaker--> person
          2. **legacy vendor/product** (kept for backward compat with
             early research projects): vendor / product /
             market_segments / technologies / competitors. Will be
             migrated to a plugin adapter — see SOURCE-ADAPTER design
             doc.
        """
        entities_created = 0
        graph = self.memory_manager.graph

        # ── Shape 1: typed-topics ingest (transcripts, blogs, PDFs) ──
        source_block = frontmatter.get("source")
        topics = frontmatter.get("topics")
        if isinstance(source_block, dict) or isinstance(topics, list):
            entities_created += await self._index_typed_topics_to_graph(
                frontmatter, filename, graph,
            )
            # Don't fall through — typed-topics shape is mutually
            # exclusive with the legacy vendor/product shape.
            return entities_created

        # ── Shape 2: legacy vendor/product (backward compat) ──
        fm_type = frontmatter.get("type", "")
        vendor = frontmatter.get("vendor", "")
        product = frontmatter.get("product", "")

        # Create vendor node
        if vendor:
            await graph.add_node("concept", vendor, metadata={
                "entity_type": "vendor",
                "source_file": filename,
                "status": str(frontmatter.get("status", "")),
            })
            entities_created += 1

        # Create product node and link to vendor
        if product:
            await graph.add_node("concept", product, metadata={
                "entity_type": "product",
                "source_file": filename,
                "vendor": vendor,
            })
            entities_created += 1
            if vendor:
                await graph.add_edge_by_label(vendor, product, "OFFERS")

        # Market segments
        segments = frontmatter.get("market_segments", [])
        if isinstance(segments, list):
            for seg in segments:
                if isinstance(seg, str) and seg.strip():
                    await graph.add_node("concept", seg.strip(), metadata={"entity_type": "market"})
                    entities_created += 1
                    target = vendor or product
                    if target:
                        await graph.add_edge_by_label(target, seg.strip(), "IN_SEGMENT")

        # Technologies / tags
        for field_name in ("technologies", "tags", "key_technologies"):
            tags = frontmatter.get(field_name, [])
            if isinstance(tags, list):
                for tag in tags:
                    if isinstance(tag, str) and tag.strip():
                        await graph.add_node("concept", tag.strip(), metadata={"entity_type": "technology"})
                        entities_created += 1
                        target = vendor or product
                        if target:
                            await graph.add_edge_by_label(target, tag.strip(), "USES_TECHNOLOGY")

        # Competitors
        competitors = frontmatter.get("competitors", [])
        if isinstance(competitors, list) and vendor:
            for comp in competitors:
                if isinstance(comp, str) and comp.strip():
                    await graph.add_node("concept", comp.strip(), metadata={"entity_type": "vendor"})
                    entities_created += 1
                    await graph.add_edge_by_label(vendor, comp.strip(), "COMPETES_WITH")

        return entities_created
