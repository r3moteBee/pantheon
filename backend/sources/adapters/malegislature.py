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
from sources.util import html_to_markdown

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


def _ordinal(n: int | str) -> str:
    """Return the English ordinal form of n (e.g. 193 → '193rd', 192 → '192nd', 211 → '211th')."""
    n = int(n)
    if 10 <= n % 100 <= 20:
        return f"{n}th"
    return f"{n}{ {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th') }"


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
        out.append(_render_section_body(sec).rstrip())
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

        # Sequential per-section fetch through one client. Each await
        # blocks the next, so we never have more than one in-flight
        # request — already maximally polite without an explicit cap.
        # A transient blip on one section logs a warning and skips that
        # section rather than aborting the whole chapter.
        sections: list[dict[str, Any]] = []
        async with httpx.AsyncClient(
            timeout=_HTTP_TIMEOUT,
            follow_redirects=True,
        ) as client:
            for ref in section_refs:
                code = ref.get("Code")
                if not code:
                    continue
                url = f"{_API_BASE}/Chapters/{chapter}/Sections/{code}"
                try:
                    r = await client.get(
                        url,
                        headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
                    )
                except (httpx.HTTPError, OSError) as e:
                    logger.warning("malegis: section %s/%s fetch failed: %s", chapter, code, e)
                    continue
                if r.status_code != 200:
                    logger.warning("malegis: section %s/%s returned %s", chapter, code, r.status_code)
                    continue
                try:
                    payload = r.json()
                except ValueError as e:
                    logger.warning("malegis: section %s/%s JSON parse failed: %s", chapter, code, e)
                    continue
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


# ── Session law identifier parsing ────────────────────────────────

_SESSION_LAW_TERSE_RE = re.compile(r"^\s*(?P<year>\d{4})\s*/\s*(?P<chapter>\d+[A-Z]?)\s*$")
_SESSION_LAW_NATURAL_RE = re.compile(
    r"""
    (?:
      (?P<year1>\d{4})\s+chapter\s+(?P<chapter1>\d+[A-Z]?)
    )|(?:
      (?:acts|resolves)\s+of\s+(?P<year2>\d{4})\s*,?\s*chapter\s+(?P<chapter2>\d+[A-Z]?)
    )|(?:
      chapter\s+(?P<chapter3>\d+[A-Z]?)\s+of\s+(?P<year3>\d{4})
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)
_SESSION_LAW_URL_RE = re.compile(
    r"malegislature\.gov/Laws/SessionLaws/(?:Acts|Resolves)/(?P<year>\d{4})/Chapter(?P<chapter>\d+[A-Z]?)",
    re.IGNORECASE,
)


def _parse_session_law_identifier(identifier: str) -> dict[str, str]:
    s = (identifier or "").strip()
    if not s:
        raise RuntimeError("malegis: empty identifier")
    m = _SESSION_LAW_URL_RE.search(s)
    if m:
        return {"year": m.group("year"), "chapter": _norm_code(m.group("chapter"))}
    m = _SESSION_LAW_TERSE_RE.match(s)
    if m:
        return {"year": m.group("year"), "chapter": _norm_code(m.group("chapter"))}
    m = _SESSION_LAW_NATURAL_RE.search(s)
    if m:
        year = m.group("year1") or m.group("year2") or m.group("year3")
        chapter = m.group("chapter1") or m.group("chapter2") or m.group("chapter3")
        return {"year": year, "chapter": _norm_code(chapter)}
    raise RuntimeError(
        f"malegis/session-law: cannot parse identifier {identifier!r}; "
        f"expected '<year>/<chapter>', '<year> Chapter <chapter>', "
        f"'Chapter <chapter> of <year>', or a SessionLaws URL"
    )


# ── Cross-reference extraction (used by bills + session laws) ─────

_MGL_CITATION_PATTERNS = [
    # "section <s> of chapter <c> [of the General Laws]"
    re.compile(
        r"section\s+(?P<section>\d+[A-Z]?)\s+of\s+chapter\s+(?P<chapter>\d+[A-Z]?)",
        re.IGNORECASE,
    ),
    # "M.G.L. c. <c> § <s>"
    re.compile(
        r"M\.?\s*G\.?\s*L\.?\s+c\.?\s*(?P<chapter>\d+[A-Z]?)\s*§\s*(?P<section>\d+[A-Z]?)",
        re.IGNORECASE,
    ),
    # "chapter <c> of the General Laws" (no section)
    re.compile(
        r"chapter\s+(?P<chapter>\d+[A-Z]?)\s+of\s+the\s+General\s+Laws",
        re.IGNORECASE,
    ),
    # "M.G.L. c. <c>" (no section)
    re.compile(
        r"M\.?\s*G\.?\s*L\.?\s+c\.?\s*(?P<chapter>\d+[A-Z]?)\b",
        re.IGNORECASE,
    ),
]


def _extract_mgl_citations(text: str) -> list[dict[str, "str | None"]]:
    """Pull MGL citations out of free text. Returns a deduped list of
    {'chapter', 'section'} dicts (section may be None when only the
    chapter is mentioned).

    Order: section-bearing patterns first so '...section 9 of chapter
    40A...' captures the pair before the chapter-only pattern matches
    'chapter 40A' alone.
    """
    if not text:
        return []
    seen: set[tuple[str, "str | None"]] = set()
    out: list[dict[str, "str | None"]] = []

    # First pass: section + chapter
    for rx in _MGL_CITATION_PATTERNS[:2]:
        for m in rx.finditer(text):
            key = (_norm_code(m.group("chapter")), _norm_code(m.group("section")))
            if key not in seen:
                seen.add(key)
                out.append({"chapter": key[0], "section": key[1]})

    # Second pass: chapter only — skip if we already have a section
    # citation for that chapter.
    chapters_with_section = {c for (c, s) in seen if s is not None}
    for rx in _MGL_CITATION_PATTERNS[2:]:
        for m in rx.finditer(text):
            chapter = _norm_code(m.group("chapter"))
            key = (chapter, None)
            if key in seen:
                continue
            if chapter in chapters_with_section:
                continue
            seen.add(key)
            out.append({"chapter": chapter, "section": None})
    return out


# ── Session law body rendering ────────────────────────────────────

_RELATIVE_ANCHOR_RE = re.compile(r'\((/(?:Laws|Bills|RollCall)/[^)\s]+)\)')


def _rewrite_relative_anchors(md: str) -> str:
    """Rewrite markdown links whose targets start with /Laws/, /Bills/,
    or /RollCall/ to absolute malegislature.gov URLs."""
    return _RELATIVE_ANCHOR_RE.sub(lambda m: f"({_SITE_BASE}{m.group(1)})", md)


def _render_session_law_body(law: dict[str, Any]) -> tuple[str, list[dict]]:
    """Render a session law as markdown. Returns (markdown, mgl_citations)."""
    title = (law.get("Title") or "").strip()
    year = law.get("Year")
    chapter_number = law.get("ChapterNumber") or "?"
    law_type = (law.get("Type") or "Acts").strip()
    status = (law.get("Status") or "").strip()
    chapter_html = law.get("ChapterText") or ""

    md_body = html_to_markdown(chapter_html)
    md_body = _rewrite_relative_anchors(md_body)

    out: list[str] = []
    out.append(f"# {title}".rstrip())
    out.append("")
    header_bits = [f"*{law_type} of {year}, Chapter {chapter_number}*"]
    if status:
        header_bits.append(f"— {status}")
    out.append(" ".join(header_bits))
    out.append("")
    if md_body.strip():
        out.append(md_body.rstrip())
        out.append("")

    origin = law.get("OriginBill") or {}
    if origin:
        out.append("---")
        out.append("")
        out.append("**Origin bill:** "
                   f"{origin.get('BillNumber', '?')}"
                   f" — {(origin.get('Title') or '').strip()}")
        sponsor = (origin.get("PrimarySponsor") or {}).get("Name")
        if sponsor:
            out.append(f"**Primary sponsor:** {sponsor}")

    body_md = "\n".join(out).rstrip() + "\n"
    cites = _extract_mgl_citations(body_md)
    return body_md, cites


# ── Session law adapter ───────────────────────────────────────────

class SessionLaw(_MALegisBaseAdapter):
    source_type = "malegis/session-law"
    display_name = "MA session law (Acts/Resolves)"
    artifact_path_template = "mass-session-laws/{year}/chapter-{chapter_number}.md"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        parts = _parse_session_law_identifier(req.identifier)
        year = parts["year"]
        chapter = parts["chapter"]
        url = f"{_API_BASE}/SessionLaws/{year}/{chapter}"
        payload = await _http_get_json(url)
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if not payload:
            raise RuntimeError(f"malegis/session-law: empty payload for {year}/{chapter}")

        body, cites = _render_session_law_body(payload)
        as_of = datetime.now(timezone.utc).date().isoformat()
        title = (payload.get("Title") or f"{payload.get('Type', 'Acts')} {year} c. {chapter}").strip()
        origin = payload.get("OriginBill") or {}
        approved = (payload.get("ApprovedDate") or "").strip()
        published_at = _parse_approved_date(approved) or as_of
        site_url = f"{_SITE_BASE}/Laws/SessionLaws/{payload.get('Type', 'Acts')}/{year}/Chapter{chapter}"
        return FetchedContent(
            text=body,
            title=title,
            author_or_publisher="Massachusetts General Court",
            url=site_url,
            published_at=published_at,
            extra_meta={
                "year": year,
                "chapter_number": chapter,
                "act_type": payload.get("Type") or "Acts",
                "approval_type": payload.get("ApprovalType") or "",
                "approved_date": approved,
                "origin_bill": {
                    "number": origin.get("BillNumber"),
                    "court": origin.get("GeneralCourtNumber"),
                    "primary_sponsor": (origin.get("PrimarySponsor") or {}).get("Name"),
                },
                "citation": f"{payload.get('Type', 'Acts')} of {year}, Chapter {chapter}",
                "jurisdiction": "MA",
                "as_of_date": as_of,
                "mgl_citations": cites,
            },
        )

    def render_artifact_path(self, req, fetched):
        return self.artifact_path_template.format(
            year=fetched.extra_meta.get("year", "0000"),
            chapter_number=fetched.extra_meta.get("chapter_number", "?"),
        )

    def build_frontmatter(self, req, fetched) -> dict[str, Any]:
        fm = super().build_frontmatter(req, fetched)
        for key in ("year", "chapter_number", "act_type", "approval_type",
                    "approved_date", "origin_bill"):
            if key in fetched.extra_meta:
                fm[key] = fetched.extra_meta[key]
        return fm


_APPROVED_DATE_RE = re.compile(r"^([A-Za-z]{3})\s+(\d{1,2})\s+(\d{4})$")
_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_approved_date(s: str) -> "str | None":
    """Convert 'Jan 08 2024' → '2024-01-08'. Returns None if unparseable."""
    if not s:
        return None
    m = _APPROVED_DATE_RE.match(s.strip())
    if not m:
        return None
    mon_name, day, year = m.group(1), int(m.group(2)), int(m.group(3))
    mon = _MONTHS.get(mon_name)
    if not mon:
        return None
    return f"{year:04d}-{mon:02d}-{day:02d}"


register_adapter(SessionLaw())


# ── Bill identifier parsing ───────────────────────────────────────

_BILL_TERSE_RE = re.compile(
    r"^\s*(?P<prefix>[HS]D?)\.?(?P<num>\d+)(?:@(?P<court>\d+))?\s*$",
    re.IGNORECASE,
)
_BILL_URL_RE = re.compile(
    r"malegislature\.gov/Bills/(?P<court>\d+)/(?P<prefix>[HS]D?)\.?(?P<num>\d+)",
    re.IGNORECASE,
)


def _parse_bill_identifier(identifier: str) -> dict[str, Any]:
    """Return {'bill_number', 'general_court'}.

    'general_court' is None when the caller used the bare-bill shorthand
    (e.g. 'H4038'); the adapter resolves it via _current_court() before
    fetching.
    """
    s = (identifier or "").strip()
    if not s:
        raise RuntimeError("malegis: empty identifier")
    m = _BILL_URL_RE.search(s)
    if m:
        return {
            "bill_number": f"{m.group('prefix').upper()}{m.group('num')}",
            "general_court": int(m.group("court")),
        }
    m = _BILL_TERSE_RE.match(s)
    if m:
        court = int(m.group("court")) if m.group("court") else None
        return {
            "bill_number": f"{m.group('prefix').upper()}{m.group('num')}",
            "general_court": court,
        }
    raise RuntimeError(
        f"malegis/bill: cannot parse identifier {identifier!r}; "
        f"expected 'H4038', 'H.4038', 'H4038@193', or a malegislature.gov URL"
    )


# ── Bill body rendering ───────────────────────────────────────────

def _render_bill_body(
    bill: dict[str, Any],
    *,
    history,
) -> tuple[str, list[dict]]:
    """Render a bill payload as markdown. Returns (markdown, mgl_citations).

    `history` is an optional list of DocumentHistoryAction dicts. Pass
    None to omit the History section.
    """
    title = (bill.get("Title") or "").strip()
    bill_number = bill.get("BillNumber") or "?"
    court = bill.get("GeneralCourtNumber") or "?"
    legtype = (bill.get("LegislationTypeName") or "Bill").strip()
    pinslip = (bill.get("Pinslip") or "").strip()
    doc_text = _normalize_prose(bill.get("DocumentText") or "")

    primary = (bill.get("PrimarySponsor") or {}).get("Name")
    cosponsors = [c.get("Name") for c in (bill.get("Cosponsors") or []) if c.get("Name")]
    joint = (bill.get("JointSponsor") or {}).get("Name")

    out: list[str] = []
    out.append(f"# {title}".rstrip())
    out.append("")
    out.append(f"**{bill_number}** · {legtype} · {_ordinal(court)} General Court")
    out.append("")
    if pinslip:
        for line in pinslip.splitlines():
            out.append(f"> {line.strip()}")
        out.append("")

    if doc_text:
        out.append("## Bill text")
        out.append("")
        out.append(doc_text)
        out.append("")

    out.append("## Sponsors")
    out.append("")
    if primary:
        out.append(f"- Primary: {primary}")
    if cosponsors:
        out.append(f"- Cosponsors: {', '.join(cosponsors)}")
    if joint:
        out.append(f"- Joint sponsor: {joint}")
    out.append("")

    recs = bill.get("CommitteeRecommendations") or []
    if recs:
        out.append("## Committee recommendations")
        out.append("")
        for rec in recs:
            code = (rec.get("Committee") or {}).get("CommitteeCode", "?")
            action = rec.get("Action") or "?"
            out.append(f"- {code}: {action}")
        out.append("")

    amendments = bill.get("Amendments") or []
    if amendments:
        out.append("## Amendments")
        out.append("")
        for am in amendments:
            num = am.get("AmendmentNumber") or am.get("Number") or "?"
            atitle = (am.get("Title") or "").strip()
            out.append(f"- {num}: {atitle}".rstrip(": "))
        out.append("")

    rollcalls = bill.get("RollCalls") or []
    if rollcalls:
        out.append("## Roll calls")
        out.append("")
        for rc in rollcalls:
            branch = rc.get("Branch") or "?"
            num = rc.get("RollCallNumber") or rc.get("Number") or "?"
            out.append(f"- {branch} #{num}")
        out.append("")

    if history:
        out.append("## History")
        out.append("")
        for h in history:
            date_ = (h.get("Date") or "").strip()
            branch = (h.get("Branch") or "").strip()
            action = (h.get("Action") or "").strip()
            desc = (h.get("Description") or "").strip()
            line = f"- {date_} · {branch} · {action}".rstrip(" ·")
            if desc:
                line += f" — {desc}"
            out.append(line)
        out.append("")

    body = "\n".join(out).rstrip() + "\n"
    cites = _extract_mgl_citations(body)
    return body, cites


# ── Bill adapter ──────────────────────────────────────────────────

class Bill(_MALegisBaseAdapter):
    source_type = "malegis/bill"
    display_name = "MA bill / docket"
    artifact_path_template = "mass-bills/court-{general_court}/{bill_number}.md"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        parts = _parse_bill_identifier(req.identifier)
        bill_number = parts["bill_number"]
        court = (req.extras or {}).get("general_court") or parts["general_court"]
        if court is None:
            court = await _current_court()
        court = int(court)

        url = f"{_API_BASE}/GeneralCourts/{court}/Documents/{bill_number}"
        payload = await _http_get_json(url)
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if not payload:
            raise RuntimeError(f"malegis/bill: empty payload for {court}/{bill_number}")

        history = None
        if (req.extras or {}).get("include_history"):
            import httpx
            try:
                history = await _http_get_json(
                    f"{_API_BASE}/GeneralCourts/{court}/Documents/{bill_number}/DocumentHistoryActions"
                )
            except (httpx.HTTPError, OSError, ValueError) as e:
                logger.warning("malegis/bill: history fetch failed for %s/%s: %s",
                               court, bill_number, e)

        body, cites = _render_bill_body(payload, history=history)

        as_of = datetime.now(timezone.utc).date().isoformat()
        title = (payload.get("Title") or bill_number).strip()
        primary = (payload.get("PrimarySponsor") or {}).get("Name", "")
        return FetchedContent(
            text=body,
            title=title,
            author_or_publisher=primary or "Massachusetts General Court",
            url=f"{_SITE_BASE}/Bills/{court}/{bill_number}",
            published_at=as_of,
            extra_meta={
                "bill_number": bill_number,
                "docket_number": payload.get("DocketNumber"),
                "general_court": court,
                "legislation_type": payload.get("LegislationTypeName"),
                "primary_sponsor": primary,
                "cosponsors": [c.get("Name") for c in (payload.get("Cosponsors") or []) if c.get("Name")],
                "committee_recommendations": [
                    {
                        "committee_code": (rec.get("Committee") or {}).get("CommitteeCode"),
                        "action": rec.get("Action"),
                    }
                    for rec in (payload.get("CommitteeRecommendations") or [])
                ],
                "pinslip": (payload.get("Pinslip") or "").strip(),
                "citation": f"{bill_number} ({_ordinal(court)} General Court)",
                "jurisdiction": "MA",
                "as_of_date": as_of,
                "mgl_citations": cites,
            },
        )

    def render_artifact_path(self, req, fetched):
        return self.artifact_path_template.format(
            general_court=fetched.extra_meta.get("general_court", "0"),
            bill_number=fetched.extra_meta.get("bill_number", "?"),
        )

    def build_frontmatter(self, req, fetched) -> dict[str, Any]:
        fm = super().build_frontmatter(req, fetched)
        for key in ("bill_number", "docket_number", "general_court",
                    "legislation_type", "primary_sponsor", "cosponsors",
                    "committee_recommendations", "pinslip"):
            if key in fetched.extra_meta:
                fm[key] = fetched.extra_meta[key]
        return fm


register_adapter(Bill())
