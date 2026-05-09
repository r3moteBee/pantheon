"""Massachusetts Legislature source adapters.

Mechanism: the malegislature.gov public REST API at ``/api``. No auth.
All endpoints return JSON; bodies are either plain prose (sections,
bills) or HTML (session-law ChapterText) which we run through
``markdownify``.

Genres:
  - malegis/general-law-section   one MGL section, e.g. "M.G.L. c. 23A § 1"
  - malegis/general-law-chapter   whole MGL chapter, e.g. "Chapter 23A"
  - malegis/session-law           one act/resolve, e.g. "2024 Chapter 1"
  - malegis/bill                  one bill/docket, e.g. "H4038" (current court)
  - malegis/hearing               one hearing event, by integer EventId
  - malegis/roll-call             one floor roll call
  - malegis/committee-vote        one committee vote on a document

See ``docs/superpowers/specs/2026-05-08-massachusetts-legislature-adapter-design.md``
for the full design.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from sources.base import (
    FetchedContent,
    IngestRequest,
    SourceAdapter,
)
from sources.registry import register_adapter

logger = logging.getLogger(__name__)

_API_BASE = "https://malegislature.gov/api"
_SITE_BASE = "https://malegislature.gov"
_USER_AGENT = "Pantheon/1.0 (research-harness)"
_HTTP_TIMEOUT = 60
_DEFAULT_COURT_FLOOR = 193  # hard-coded fallback when API + cache both fail

# Process-lifetime cache for the current GeneralCourt number. The MA
# General Court increments roughly every two years; once we've resolved
# it from the API once, we don't need to ask again for the lifetime of
# the FastAPI process.
_CURRENT_COURT_CACHE: dict[str, int] = {}


async def _http_get_json(url: str) -> Any:
    """GET a URL and return parsed JSON. Raises on non-2xx.

    httpx is imported lazily so the module loads cleanly in test
    contexts where networking deps may be stubbed.
    """
    import httpx
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        r = await client.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
        )
        r.raise_for_status()
        return r.json()


async def _current_court() -> int:
    """Return the current GeneralCourt number, with three-level fallback.

    1. Cached value from a prior call (process lifetime).
    2. Live query of /GeneralCourts/Documents — the highest court
       number visible there is the current one.
    3. Hard-coded floor (_DEFAULT_COURT_FLOOR) with a warning logged.
    """
    if "court" in _CURRENT_COURT_CACHE:
        return _CURRENT_COURT_CACHE["court"]
    try:
        payload = await _http_get_json(f"{_API_BASE}/GeneralCourts/Documents")
        nums = [int(c["GeneralCourtNumber"]) for c in payload or []
                if isinstance(c, dict) and c.get("GeneralCourtNumber")]
        if nums:
            n = max(nums)
            _CURRENT_COURT_CACHE["court"] = n
            return n
    except Exception as e:
        logger.warning("malegis: current-court lookup failed (%s); using fallback", e)
    # Fallback to whatever we last cached (if anything), else the floor.
    n = _CURRENT_COURT_CACHE.get("court", _DEFAULT_COURT_FLOOR)
    _CURRENT_COURT_CACHE.setdefault("court", n)
    return n


# ── Identifier parsing ────────────────────────────────────────────

# A "code" is a chapter or section identifier: digits with an optional
# trailing single uppercase letter (e.g. "23A", "3B", "6", "1024").
_CODE = r"(?P<{name}>\d+[A-Z]?)"

_SECTION_FORMAL_RE = re.compile(
    rf"""
    (?:^|\s)
    (?:M\.?\s*G\.?\s*L\.?|MGL|chapter)\s+
    (?:c\.?\s*)?{_CODE.format(name='chapter')}
    \s*(?:§|section|sec\.?|s\.?)\s*
    {_CODE.format(name='section')}
    """,
    re.IGNORECASE | re.VERBOSE,
)

_SECTION_TERSE_RE = re.compile(
    rf"^\s*{_CODE.format(name='chapter')}\s*/\s*{_CODE.format(name='section')}\s*$",
    re.IGNORECASE,
)

_SECTION_URL_RE = re.compile(
    rf"""
    malegislature\.gov/Laws/GeneralLaws/
    (?:[A-Za-z0-9_-]+/)*?       # arbitrary Part/Title segments
    Chapter{_CODE.format(name='chapter')}/
    Section{_CODE.format(name='section')}
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _norm_code(s: str) -> str:
    """Uppercase the trailing letter in a code so '23a' and '23A' compare equal."""
    return s.strip().upper()


def _parse_section_identifier(identifier: str) -> dict[str, str]:
    """Return {'chapter', 'section'} for any accepted shape.

    Raises RuntimeError when the identifier is unparseable or names
    only a chapter (use _parse_chapter_identifier for that).
    """
    s = (identifier or "").strip()
    if not s:
        raise RuntimeError("malegis: empty identifier")
    for rx in (_SECTION_URL_RE, _SECTION_FORMAL_RE, _SECTION_TERSE_RE):
        m = rx.search(s)
        if m:
            return {
                "chapter": _norm_code(m.group("chapter")),
                "section": _norm_code(m.group("section")),
            }
    # If the input parses as a chapter-only citation, give a targeted error.
    if _CHAPTER_ONLY_RE.search(s):
        raise RuntimeError(
            f"malegis/general-law-section: section number missing in {identifier!r} "
            f"(use general-law-chapter for whole-chapter ingest)"
        )
    raise RuntimeError(
        f"malegis/general-law-section: cannot parse identifier {identifier!r}; "
        f"expected 'M.G.L. c. <chapter> § <section>', '<chapter>/<section>', "
        f"or a malegislature.gov URL"
    )


# Sentinel used by the section parser to give a better error when the
# user passed a chapter-only citation. The chapter parser uses the same
# regex.
_CHAPTER_ONLY_RE = re.compile(
    rf"""
    ^\s*
    (?:M\.?\s*G\.?\s*L\.?|MGL|chapter)\s+
    (?:c\.?\s*)?{_CODE.format(name='chapter')}
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


# ── Body rendering ────────────────────────────────────────────────

_WS_RE = re.compile(r"[ \t]+")


def _normalize_prose(s: str) -> str:
    """Normalize line endings to \\n, collapse intra-line whitespace, preserve blank lines."""
    if not s:
        return ""
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse runs of spaces/tabs but preserve newlines.
    lines = [_WS_RE.sub(" ", ln).strip() for ln in s.split("\n")]
    return "\n".join(lines).strip()


def _render_section_body(section: dict[str, Any]) -> str:
    """Render one MGL section as markdown.

    Input is the raw JSON dict from /Chapters/{c}/Sections/{s}.
    """
    code = section.get("Code", "?")
    name = (section.get("Name") or "").strip()
    text = _normalize_prose(section.get("Text") or "")
    is_repealed = bool(section.get("IsRepealed"))

    out: list[str] = []
    out.append(f"## Section {code}. {name}".rstrip())
    out.append("")
    if is_repealed:
        out.append(f"_Repealed_ — {name or 'see chapter notes'}")
        out.append("")
    if text:
        out.append(text)
    return "\n".join(out).rstrip() + "\n"


# ── Adapter base ──────────────────────────────────────────────────

class _MALegisBaseAdapter(SourceAdapter):
    """Shared behavior: bucket aliases, common frontmatter additions."""
    bucket_aliases = ("malegis", "mass", "malaw")
    requires_mcp = ()
    extractor_strategy = "llm_default"
    auto_link_similarity = True

    def build_frontmatter(self, req, fetched) -> dict[str, Any]:
        fm = super().build_frontmatter(req, fetched)
        meta = fetched.extra_meta or {}
        for key in ("citation", "jurisdiction", "as_of_date", "mgl_citations"):
            if key in meta:
                fm[key] = meta[key]
        return fm


# ── General Law section adapter ───────────────────────────────────

class GeneralLawSection(_MALegisBaseAdapter):
    source_type = "malegis/general-law-section"
    display_name = "MGL — single section"
    artifact_path_template = "mass-laws/chapter-{chapter_code}/section-{section_code}.md"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        parts = _parse_section_identifier(req.identifier)
        chapter = parts["chapter"]
        section = parts["section"]
        url = f"{_API_BASE}/Chapters/{chapter}/Sections/{section}"
        payload = await _http_get_json(url)
        # The endpoint returns a single dict (not an array) despite the
        # swagger schema. Defend against either shape.
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if not payload:
            raise RuntimeError(f"malegis: empty payload from {url}")

        body = _render_section_body(payload)
        if len(body.strip()) < 10:
            raise RuntimeError(
                f"malegis/general-law-section: rendered body too short for "
                f"chapter {chapter} section {section}"
            )

        chapter_code = (payload.get("Chapter") or {}).get("Code") or chapter
        part_code = (payload.get("Part") or {}).get("Code") or ""
        name = (payload.get("Name") or "").strip()
        as_of = datetime.now(timezone.utc).date().isoformat()
        citation = f"M.G.L. c. {chapter_code} § {section}"
        site_url = (
            f"{_SITE_BASE}/Laws/GeneralLaws/Part{part_code}/Chapter{chapter_code}/Section{section}"
            if part_code else
            f"{_SITE_BASE}/Laws/GeneralLaws/Chapter{chapter_code}/Section{section}"
        )
        return FetchedContent(
            text=body,
            title=f"Section {section}. {name}".strip(),
            author_or_publisher="Massachusetts General Court",
            url=site_url,
            published_at=as_of,
            extra_meta={
                "chapter_code": chapter_code,
                "section_code": section,
                "part_code": part_code,
                "is_repealed": bool(payload.get("IsRepealed")),
                "citation": citation,
                "jurisdiction": "MA",
                "as_of_date": as_of,
                "hierarchy": {
                    "part": part_code,
                    "chapter": chapter_code,
                    "section": section,
                },
                "mgl_citations": [],
            },
        )

    def render_artifact_path(self, req, fetched):
        return self.artifact_path_template.format(
            chapter_code=fetched.extra_meta.get("chapter_code", "?"),
            section_code=fetched.extra_meta.get("section_code", "?"),
        )

    def build_frontmatter(self, req, fetched) -> dict[str, Any]:
        fm = super().build_frontmatter(req, fetched)
        fm["hierarchy"] = fetched.extra_meta.get("hierarchy", {})
        fm["is_repealed"] = fetched.extra_meta.get("is_repealed", False)
        return fm


register_adapter(GeneralLawSection())


# ── General Law chapter identifier parsing ────────────────────────

_CHAPTER_BARE_RE = re.compile(
    rf"^\s*{_CODE.format(name='chapter')}\s*$",
)

_CHAPTER_URL_RE = re.compile(
    rf"""
    malegislature\.gov/Laws/GeneralLaws/
    (?:[A-Za-z0-9_-]+/)*?
    Chapter{_CODE.format(name='chapter')}
    (?:/?$|/?[?#])
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _parse_chapter_identifier(identifier: str) -> dict[str, str]:
    """Return {'chapter'} for any accepted shape."""
    s = (identifier or "").strip()
    if not s:
        raise RuntimeError("malegis: empty identifier")
    for rx in (_CHAPTER_URL_RE, _CHAPTER_ONLY_RE, _CHAPTER_BARE_RE):
        m = rx.search(s)
        if m:
            return {"chapter": _norm_code(m.group("chapter"))}
    raise RuntimeError(
        f"malegis/general-law-chapter: cannot parse identifier {identifier!r}; "
        f"expected 'M.G.L. c. <chapter>', 'Chapter <chapter>', '<chapter>', "
        f"or a malegislature.gov URL"
    )


# ── General Law chapter body rendering ────────────────────────────

def _render_chapter_body(chapter: dict[str, Any], sections: list[dict[str, Any]]) -> str:
    code = chapter.get("Code", "?")
    name = (chapter.get("Name") or "").strip()
    part_code = (chapter.get("Part") or {}).get("Code", "")
    out: list[str] = []
    out.append(f"# Chapter {code} — {name}".rstrip())
    if part_code:
        out.append("")
        out.append(f"_(Part {part_code})_")
    out.append("")
    for sec in sections:
        out.append(_render_section_body(sec))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


# ── General Law chapter adapter ───────────────────────────────────

class GeneralLawChapter(_MALegisBaseAdapter):
    source_type = "malegis/general-law-chapter"
    display_name = "MGL — entire chapter"
    artifact_path_template = "mass-laws/chapter-{chapter_code}/index.md"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        import httpx
        parts = _parse_chapter_identifier(req.identifier)
        chapter = parts["chapter"]
        max_sections = int((req.extras or {}).get("max_sections") or 200)

        # First call: chapter detail, includes the section index.
        chapter_payload = await _http_get_json(f"{_API_BASE}/Chapters/{chapter}")
        if isinstance(chapter_payload, list):
            chapter_payload = chapter_payload[0] if chapter_payload else {}
        if not chapter_payload:
            raise RuntimeError(f"malegis: empty chapter payload for {chapter}")

        section_refs = chapter_payload.get("Sections") or []
        if len(section_refs) > max_sections:
            logger.warning(
                "malegis: chapter %s has %d sections; capped at %d. "
                "Pass extras['max_sections'] to raise the cap.",
                chapter, len(section_refs), max_sections,
            )
            section_refs = section_refs[:max_sections]

        # Politeness: cap concurrent connections to 4.
        sections: list[dict[str, Any]] = []
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=4),
        ) as client:
            for ref in section_refs:
                code = ref.get("Code")
                if not code:
                    continue
                url = f"{_API_BASE}/Chapters/{chapter}/Sections/{code}"
                r = await client.get(
                    url,
                    headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                )
                if r.status_code != 200:
                    logger.warning("malegis: section %s/%s returned %s", chapter, code, r.status_code)
                    continue
                payload = r.json()
                if isinstance(payload, list):
                    payload = payload[0] if payload else {}
                if payload:
                    sections.append(payload)

        body = _render_chapter_body(chapter_payload, sections)
        if len(body.strip()) < 50:
            raise RuntimeError(f"malegis: chapter {chapter} rendered too small")

        chapter_code = chapter_payload.get("Code", chapter)
        part_code = (chapter_payload.get("Part") or {}).get("Code", "")
        name = (chapter_payload.get("Name") or "").strip()
        as_of = datetime.now(timezone.utc).date().isoformat()
        citation = f"M.G.L. c. {chapter_code}"
        site_url = (
            f"{_SITE_BASE}/Laws/GeneralLaws/Part{part_code}/Chapter{chapter_code}"
            if part_code else
            f"{_SITE_BASE}/Laws/GeneralLaws/Chapter{chapter_code}"
        )
        return FetchedContent(
            text=body,
            title=f"Chapter {chapter_code} — {name}".rstrip(),
            author_or_publisher="Massachusetts General Court",
            url=site_url,
            published_at=as_of,
            extra_meta={
                "chapter_code": chapter_code,
                "part_code": part_code,
                "is_repealed": bool(chapter_payload.get("IsRepealed")),
                "section_count": len(sections),
                "citation": citation,
                "jurisdiction": "MA",
                "as_of_date": as_of,
                "hierarchy": {"part": part_code, "chapter": chapter_code},
                "mgl_citations": [],
            },
        )

    def render_artifact_path(self, req, fetched):
        return self.artifact_path_template.format(
            chapter_code=fetched.extra_meta.get("chapter_code", "?"),
        )

    def build_frontmatter(self, req, fetched) -> dict[str, Any]:
        fm = super().build_frontmatter(req, fetched)
        fm["hierarchy"] = fetched.extra_meta.get("hierarchy", {})
        fm["is_repealed"] = fetched.extra_meta.get("is_repealed", False)
        fm["section_count"] = fetched.extra_meta.get("section_count", 0)
        return fm


register_adapter(GeneralLawChapter())
