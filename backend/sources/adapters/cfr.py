"""Code of Federal Regulations (CFR) source adapters.

Mechanism: the eCFR Versioner API at ``ecfr.gov/api/versioner/v1``.
No auth required. The API exposes the regulation text as structured
XML keyed by (date, title, part, section). We fetch the slice the
caller asked for, convert the XML tree to markdown, and enrich
frontmatter with the hierarchy chain (Title → Chapter → Part →
Subpart → Section) via the ancestry endpoint.

Genres:
  - cfr/section   one section, e.g. "12 CFR 1024.20"
                  (smallest research unit; ~5-50 KB typical)
  - cfr/part      one whole part, e.g. "12 CFR Part 1024"
                  (e.g. RESPA / Regulation X is ~500 KB; fits the
                   default extractor budget after markdownification
                   trims it, but for very large parts the LLM
                   extractor will truncate to 60 KB)

Identifier formats accepted (cfr/section):
  - "12 CFR 1024.20"          citation form, with or without "§"
  - "Title 12 Section 1024.20"
  - https://www.ecfr.gov/current/title-12/.../section-1024.20
  - "title-12/section-1024.20" or "title-12/part-1024/section-1024.20"

Identifier formats accepted (cfr/part):
  - "12 CFR Part 1024"
  - "12 CFR 1024"             (when no decimal — interpret as part)
  - https://www.ecfr.gov/current/title-12/.../part-1024
  - "title-12/part-1024"

Date semantics:
  - Default: today (UTC). If the API returns 404 (the title may not
    have an issuance for today), the adapter falls back to the
    title's ``up_to_date_as_of`` from the titles index.
  - Override: pass ``extras['date']`` as YYYY-MM-DD.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from typing import Any

from sources.base import (
    FetchedContent,
    IngestRequest,
    SourceAdapter,
)
from sources.registry import register_adapter

logger = logging.getLogger(__name__)

_API_BASE = "https://www.ecfr.gov/api/versioner/v1"
_USER_AGENT = "Pantheon/1.0 (research-harness)"
_HTTP_TIMEOUT = 60  # eCFR Part-sized XML can be slow


# ── Identifier parsing ────────────────────────────────────────────

# "12 CFR 1024.20", "12 CFR § 1024.20", "12 CFR Part 1024 § 1024.20"
_CITATION_RE = re.compile(
    r"""
    (?:^|\s)
    (?:title\s+)?
    (?P<title>\d{1,2})        # title number 1..50
    \s+CFR\s+
    (?:part\s+)?              # optional "Part" keyword
    (?:§\s*)?                 # optional § sign
    (?P<thing>\d{1,4}(?:\.\d+[a-z]?)?)  # 1024 or 1024.20 or 1024.20a
    """,
    re.IGNORECASE | re.VERBOSE,
)

# eCFR URL: /current/title-12/.../section-1024.20 or /part-1024 or /on/2025-01-01/title-12/...
# Use a negative lookahead so prefix segments (chapter-, subchapter-,
# subtitle-, etc.) don't accidentally consume the part-/section-
# segments we want to capture.
_URL_RE = re.compile(
    r"""
    ecfr\.gov/
    (?:current|on/(?P<url_date>\d{4}-\d{2}-\d{2}))
    /title-(?P<title>\d+)
    (?:/(?!part-|section-)[a-z]+-[^/]+)*
    (?:/part-(?P<part>[\w.-]+))?
    (?:/section-(?P<section>[\d.]+[a-z]?))?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# "title-12/part-1024/section-1024.20"
_SLUG_RE = re.compile(
    r"""
    ^title-(?P<title>\d+)
    (?:/(?!part-|section-)[a-z]+-[^/]+)*
    (?:/part-(?P<part>[\w.-]+))?
    (?:/section-(?P<section>[\d.]+[a-z]?))?
    \s*$
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _parse_identifier(identifier: str, *, want_section: bool) -> dict[str, str | None]:
    """Return {'title', 'part', 'section', 'date'} from any accepted shape.

    'part' is required for both adapters. 'section' is required when
    want_section=True. 'date' is None unless the URL form supplied one.
    """
    s = (identifier or "").strip()
    if not s:
        raise RuntimeError("cfr: empty identifier")

    title: str | None = None
    part: str | None = None
    section: str | None = None
    url_date: str | None = None

    m = _URL_RE.search(s)
    if m:
        title = m.group("title")
        part = m.group("part")
        section = m.group("section")
        url_date = m.group("url_date")
    else:
        m = _SLUG_RE.match(s)
        if m:
            title = m.group("title")
            part = m.group("part")
            section = m.group("section")
        else:
            m = _CITATION_RE.search(s)
            if not m:
                raise RuntimeError(
                    f"cfr: cannot parse identifier {identifier!r}; "
                    f"expected '<N> CFR <part>[.<section>]', a slug like "
                    f"'title-N/part-X/section-X.Y', or an ecfr.gov URL"
                )
            title = m.group("title")
            thing = m.group("thing")
            if "." in thing:
                # "1024.20" → section, part is the integer prefix
                section = thing
                part = thing.split(".", 1)[0]
            else:
                # "1024" → part only
                part = thing
                section = None

    if not title:
        raise RuntimeError(f"cfr: title number missing in {identifier!r}")
    if not part:
        raise RuntimeError(f"cfr: part number missing in {identifier!r}")
    if want_section and not section:
        raise RuntimeError(
            f"cfr/section: section number missing in {identifier!r} "
            f"(use cfr/part if you want the whole part)"
        )
    return {"title": title, "part": part, "section": section, "date": url_date}


# ── Network helpers ───────────────────────────────────────────────

# Cache title metadata for the process lifetime — titles list rarely
# changes and the index is ~8 KB, so refetching per-request would be
# wasteful. If a title gets amended after the cache populates, the
# user can pass extras['date'] to bypass the fallback.
_TITLES_INDEX: dict[int, str] = {}


async def _http_get(url: str, *, accept: str = "application/json"):
    import httpx
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=True) as client:
        return await client.get(
            url,
            headers={"User-Agent": _USER_AGENT, "Accept": accept},
        )


async def _ensure_titles_index() -> None:
    if _TITLES_INDEX:
        return
    r = await _http_get(f"{_API_BASE}/titles.json")
    r.raise_for_status()
    payload = r.json() or {}
    for t in payload.get("titles", []):
        n = t.get("number")
        d = t.get("up_to_date_as_of")
        if n and d:
            _TITLES_INDEX[int(n)] = d


async def _resolve_date(title: int, requested: str | None) -> str:
    """Pick the as-of date for an API call.

    Priority: caller-supplied > today > title's up_to_date_as_of.
    """
    if requested:
        return requested
    return datetime.now(timezone.utc).date().isoformat()


async def _fetch_full_xml(date_: str, title: int, *, part: str, section: str | None) -> bytes:
    """Hit /full/{date}/title-{n}.xml with optional part/section filters."""
    qs = [f"part={part}"]
    if section:
        qs.append(f"section={section}")
    url = f"{_API_BASE}/full/{date_}/title-{title}.xml?{'&'.join(qs)}"
    r = await _http_get(url, accept="application/xml")
    if r.status_code == 404:
        # Likely a stale "today" — fall back to the title's up_to_date.
        await _ensure_titles_index()
        fallback = _TITLES_INDEX.get(int(title))
        if fallback and fallback != date_:
            url2 = f"{_API_BASE}/full/{fallback}/title-{title}.xml?{'&'.join(qs)}"
            r2 = await _http_get(url2, accept="application/xml")
            if r2.status_code == 200:
                logger.info("cfr: %s 404'd; fell back to %s", date_, fallback)
                return r2.content
            r2.raise_for_status()
        raise RuntimeError(
            f"cfr: 404 from {url} (title {title}, part {part}, section {section}); "
            f"verify the citation exists at the requested date"
        )
    r.raise_for_status()
    return r.content


async def _fetch_ancestry(date_: str, title: int, *, part: str, section: str | None) -> list[dict]:
    """Return the hierarchy chain (Title → Chapter → ... → leaf)."""
    qs = [f"part={part}"]
    if section:
        qs.append(f"section={section}")
    url = f"{_API_BASE}/ancestry/{date_}/title-{title}.json?{'&'.join(qs)}"
    try:
        r = await _http_get(url)
        if r.status_code != 200:
            return []
        payload = r.json() or {}
        return payload.get("ancestors") or []
    except Exception as e:
        logger.debug("cfr: ancestry lookup failed (%s): %s", url, e)
        return []


# ── XML → markdown ────────────────────────────────────────────────

# Inline formatting: I/B and the eCFR <E T="..."> emphasis variants.
_INLINE_TAGS_TO_WRAP = {
    "I": ("*", "*"),       # italic
    "B": ("**", "**"),     # bold
    "STRONG": ("**", "**"),
    "EM": ("*", "*"),
}
# <E T="03"> = italic, <E T="04"> = bold; T="01"/"02" = different italic variants
_E_TYPE_WRAP = {
    "01": ("*", "*"),
    "02": ("*", "*"),
    "03": ("*", "*"),
    "04": ("**", "**"),
    "52": ("*", "*"),
    "75": ("*", "*"),
}


def _strip_ns(tag: str) -> str:
    """Drop XML namespace if any. eCFR XML doesn't really use one but be safe."""
    if "}" in tag:
        return tag.rsplit("}", 1)[1]
    return tag


def _inline_text(elem: ET.Element) -> str:
    """Render an element's children as inline markdown (no block breaks)."""
    parts: list[str] = []
    if elem.text:
        parts.append(elem.text)
    for child in list(elem):
        tag = _strip_ns(child.tag).upper()
        wrap_l = wrap_r = ""
        if tag in _INLINE_TAGS_TO_WRAP:
            wrap_l, wrap_r = _INLINE_TAGS_TO_WRAP[tag]
        elif tag == "E":
            t = child.attrib.get("T", "")
            wrap_l, wrap_r = _E_TYPE_WRAP.get(t, ("", ""))
        elif tag == "FTREF":
            # Footnote reference — keep the visible text, drop the link.
            wrap_l = wrap_r = ""
        # Render the child's inner content recursively.
        inner = _inline_text(child).strip()
        if inner:
            parts.append(f"{wrap_l}{inner}{wrap_r}")
        if child.tail:
            parts.append(child.tail)
    return "".join(parts)


_DIV_LEVELS = {
    "DIV1": 1, "DIV2": 2, "DIV3": 2, "DIV4": 3,
    "DIV5": 2, "DIV6": 3, "DIV7": 4, "DIV8": 3, "DIV9": 4,
}


def _xml_to_markdown(root: ET.Element, *, base_heading_level: int = 1) -> str:
    """Walk the eCFR XML tree and render it as markdown."""
    out: list[str] = []

    def walk(elem: ET.Element, depth: int) -> None:
        tag = _strip_ns(elem.tag).upper()

        # Block-level elements with their own heading
        if tag in _DIV_LEVELS:
            head_elem = elem.find("HEAD")
            if head_elem is not None:
                head_text = _collapse(_inline_text(head_elem))
                level = max(1, min(6, base_heading_level + _DIV_LEVELS[tag] - 1))
                out.append(f"\n{'#' * level} {head_text}\n")
            for child in list(elem):
                if _strip_ns(child.tag).upper() == "HEAD":
                    continue
                walk(child, depth + 1)
            return

        # Authority / source / citation blocks — keep visible but understated
        if tag in {"AUTH", "SOURCE", "CITA", "EFFDNOT"}:
            head_elem = elem.find("HED")
            label = ""
            if head_elem is not None:
                label = _collapse(_inline_text(head_elem))
            body_text_parts: list[str] = []
            for child in list(elem):
                if _strip_ns(child.tag).upper() == "HED":
                    continue
                body_text_parts.append(_collapse(_inline_text(child)))
            body = " ".join(p for p in body_text_parts if p)
            if not body:
                # Some CITA elements have direct text content
                body = _collapse(_inline_text(elem))
            line = (f"*{label}* {body}".strip()) if label else body
            if line:
                out.append(f"\n_{line}_\n")
            return

        # Paragraph-like
        if tag in {"P", "FP", "PSPACE"}:
            txt = _collapse(_inline_text(elem))
            if txt:
                out.append(txt + "\n")
            return

        # Block quote / extract
        if tag == "EXTRACT":
            inner_md = _xml_to_markdown(elem, base_heading_level=base_heading_level + 1)
            out.append("\n" + "\n".join(f"> {ln}" if ln else ">" for ln in inner_md.splitlines()) + "\n")
            return

        # Lists
        if tag in {"LIST", "OL", "UL"}:
            for li in elem.findall(".//LI"):
                txt = _collapse(_inline_text(li))
                if txt:
                    out.append(f"- {txt}\n")
            return

        # Notes
        if tag in {"NOTE", "EDNOTE"}:
            txt = _collapse(_inline_text(elem))
            if txt:
                out.append(f"\n> **Note:** {txt}\n")
            return

        # HEAD outside a DIV — emit as a sub-heading
        if tag == "HEAD":
            txt = _collapse(_inline_text(elem))
            if txt:
                out.append(f"\n**{txt}**\n")
            return

        # Default: descend, keep any direct text
        if elem.text and elem.text.strip():
            out.append(_collapse(elem.text) + "\n")
        for child in list(elem):
            walk(child, depth + 1)
            if child.tail and child.tail.strip():
                out.append(_collapse(child.tail) + "\n")

    walk(root, 0)
    md = "".join(out)
    md = re.sub(r"\n{3,}", "\n\n", md).strip()
    return md + "\n"


_WS_RE = re.compile(r"\s+")


def _collapse(s: str) -> str:
    return _WS_RE.sub(" ", (s or "").replace("\xa0", " ")).strip()


# ── Adapters ──────────────────────────────────────────────────────

class _CFRBaseAdapter(SourceAdapter):
    bucket_aliases = ("cfr", "regulation")
    requires_mcp = ()
    extractor_strategy = "llm_default"

    want_section: bool = False  # subclass override

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        parts = _parse_identifier(req.identifier, want_section=self.want_section)
        title_n = int(parts["title"])
        part_n = parts["part"]
        section_n = parts["section"] if self.want_section else None

        date_ = await _resolve_date(
            title_n,
            (req.extras or {}).get("date") or parts.get("date"),
        )
        xml_bytes = await _fetch_full_xml(
            date_, title_n, part=part_n, section=section_n,
        )
        try:
            root = ET.fromstring(xml_bytes)
        except ET.ParseError as e:
            raise RuntimeError(f"cfr: XML parse failed: {e}")

        body = _xml_to_markdown(root, base_heading_level=1)
        if not body or len(body.strip()) < 50:
            raise RuntimeError(
                f"cfr: rendered markdown < 50 chars from "
                f"title={title_n} part={part_n} section={section_n}"
            )

        # Pull citation + heading from the root DIV
        citation = ""
        heading = ""
        try:
            import json as _json
            hm = root.attrib.get("hierarchy_metadata", "")
            if hm:
                hm_dict = _json.loads(hm)
                citation = hm_dict.get("citation") or ""
            head_elem = root.find("HEAD")
            if head_elem is not None:
                heading = _collapse(_inline_text(head_elem))
        except Exception:
            pass

        ancestors = await _fetch_ancestry(
            date_, title_n, part=part_n, section=section_n,
        )
        hierarchy = _hierarchy_from_ancestors(ancestors)

        title_str = heading or citation or req.identifier
        return FetchedContent(
            text=body,
            title=title_str,
            author_or_publisher="Office of the Federal Register",
            url=_canonical_url(title_n, part_n, section_n, date_),
            published_at=date_,
            extra_meta={
                "citation": citation,
                "title_number": title_n,
                "part_number": part_n,
                "section_number": section_n or "",
                "hierarchy": hierarchy,
                "as_of_date": date_,
                "retrieved_at": (req.extras or {}).get("retrieved_at"),
                "fetch_method": "ecfr_versioner_v1",
            },
        )

    def build_frontmatter(self, req, fetched) -> dict[str, Any]:
        fm = super().build_frontmatter(req, fetched)
        fm["citation"] = fetched.extra_meta.get("citation", "")
        fm["hierarchy"] = fetched.extra_meta.get("hierarchy", {})
        fm["as_of_date"] = fetched.extra_meta.get("as_of_date", "")
        return fm


def _hierarchy_from_ancestors(ancestors: list[dict]) -> dict[str, str]:
    """Squash the ancestry list into a flat dict keyed by hierarchy type."""
    out: dict[str, str] = {}
    for a in ancestors or []:
        t = a.get("type", "")
        label = a.get("label", "").strip()
        if t and label and t not in out:
            out[t] = label
    return out


def _canonical_url(title_n: int, part: str, section: str | None, date_: str) -> str:
    base = f"https://www.ecfr.gov/current/title-{title_n}/part-{part}"
    if section:
        return f"{base}/section-{section}"
    return base


class CFRSection(_CFRBaseAdapter):
    source_type = "cfr/section"
    display_name = "CFR — single section"
    artifact_path_template = "cfr/title-{title_number}/part-{part_number}/section-{section_number}.md"
    want_section = True

    def render_artifact_path(self, req, fetched):
        return self.artifact_path_template.format(
            title_number=fetched.extra_meta.get("title_number", "0"),
            part_number=fetched.extra_meta.get("part_number", "0"),
            section_number=fetched.extra_meta.get("section_number", "0"),
        )


class CFRPart(_CFRBaseAdapter):
    source_type = "cfr/part"
    display_name = "CFR — entire part"
    artifact_path_template = "cfr/title-{title_number}/part-{part_number}/index.md"
    want_section = False

    def render_artifact_path(self, req, fetched):
        return self.artifact_path_template.format(
            title_number=fetched.extra_meta.get("title_number", "0"),
            part_number=fetched.extra_meta.get("part_number", "0"),
        )


register_adapter(CFRSection())
register_adapter(CFRPart())
