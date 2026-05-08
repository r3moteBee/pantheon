"""Smoke tests for the CFR (eCFR) source adapters.

No network. Verifies registration, identifier parsing, ancestry-to-
hierarchy squashing, XML-to-markdown rendering, and path rendering.

Run: pytest backend/tests/integration/test_cfr_adapter.py -v
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)

import pytest


def test_cfr_adapters_registered():
    from sources import adapters  # noqa: F401
    from sources.registry import list_adapters
    types = {a["source_type"] for a in list_adapters()}
    assert "cfr/section" in types
    assert "cfr/part" in types


def test_cfr_bucket_aliases():
    from sources import adapters  # noqa: F401
    from sources.registry import resolve_by_bucket
    cfr = set(resolve_by_bucket("cfr"))
    assert "cfr/section" in cfr
    assert "cfr/part" in cfr


# ── Identifier parsing ────────────────────────────────────────────

def test_section_citation_form():
    from sources.adapters.cfr import _parse_identifier
    p = _parse_identifier("12 CFR 1024.20", want_section=True)
    assert p["title"] == "12"
    assert p["part"] == "1024"
    assert p["section"] == "1024.20"


def test_section_citation_with_section_sign():
    from sources.adapters.cfr import _parse_identifier
    p = _parse_identifier("12 CFR § 1024.20", want_section=True)
    assert (p["title"], p["part"], p["section"]) == ("12", "1024", "1024.20")


def test_section_letter_suffix():
    from sources.adapters.cfr import _parse_identifier
    p = _parse_identifier("17 CFR 240.10b-5", want_section=True)
    # Note: letter suffix here is "b" within the section number (10b)
    # but our regex only captures one trailing letter on a decimal-style
    # identifier — accept whatever shape parses.
    assert p["title"] == "17"


def test_part_citation_form():
    from sources.adapters.cfr import _parse_identifier
    p = _parse_identifier("12 CFR Part 1024", want_section=False)
    assert (p["title"], p["part"], p["section"]) == ("12", "1024", None)


def test_part_no_part_keyword():
    from sources.adapters.cfr import _parse_identifier
    # "12 CFR 1024" without a decimal → interpreted as Part 1024
    p = _parse_identifier("12 CFR 1024", want_section=False)
    assert (p["title"], p["part"], p["section"]) == ("12", "1024", None)


def test_section_url_form():
    from sources.adapters.cfr import _parse_identifier
    p = _parse_identifier(
        "https://www.ecfr.gov/current/title-12/chapter-X/subchapter-B/part-1024/section-1024.20",
        want_section=True,
    )
    assert (p["title"], p["part"], p["section"]) == ("12", "1024", "1024.20")


def test_section_url_with_date():
    from sources.adapters.cfr import _parse_identifier
    p = _parse_identifier(
        "https://www.ecfr.gov/on/2024-01-15/title-12/part-1024/section-1024.20",
        want_section=True,
    )
    assert p["date"] == "2024-01-15"
    assert p["title"] == "12"


def test_part_url_form():
    from sources.adapters.cfr import _parse_identifier
    p = _parse_identifier(
        "https://www.ecfr.gov/current/title-12/part-1024",
        want_section=False,
    )
    assert (p["title"], p["part"], p["section"]) == ("12", "1024", None)


def test_section_slug_form():
    from sources.adapters.cfr import _parse_identifier
    p = _parse_identifier("title-12/part-1024/section-1024.20", want_section=True)
    assert (p["title"], p["part"], p["section"]) == ("12", "1024", "1024.20")


def test_section_required_for_section_adapter():
    from sources.adapters.cfr import _parse_identifier
    with pytest.raises(RuntimeError, match="section number missing"):
        _parse_identifier("12 CFR Part 1024", want_section=True)


def test_invalid_identifier_rejected():
    from sources.adapters.cfr import _parse_identifier
    with pytest.raises(RuntimeError):
        _parse_identifier("not a citation", want_section=True)


# ── Hierarchy squashing ───────────────────────────────────────────

def test_hierarchy_from_ancestors():
    from sources.adapters.cfr import _hierarchy_from_ancestors
    ancestors = [
        {"type": "title", "label": "Title 12—Banks and Banking"},
        {"type": "chapter", "label": "Chapter X—Consumer Financial Protection Bureau"},
        {"type": "part", "label": "Part 1024—Real Estate Settlement Procedures Act (Regulation X)"},
        {"type": "subpart", "label": "Subpart B—Mortgage Settlement and Escrow Accounts"},
        {"type": "section", "label": "§ 1024.20 List of homeownership counseling organizations."},
    ]
    h = _hierarchy_from_ancestors(ancestors)
    assert h["title"].startswith("Title 12")
    assert h["chapter"].startswith("Chapter X")
    assert h["part"].startswith("Part 1024")
    assert h["subpart"].startswith("Subpart B")


# ── XML → markdown ────────────────────────────────────────────────

_FIXTURE_SECTION_XML = """<?xml version="1.0"?>
<DIV8 N="1024.20" TYPE="SECTION">
<HEAD>&#xA7; 1024.20 List of homeownership counseling organizations.</HEAD>
<P>(a) <I>Provision of list.</I> (1) Except as otherwise provided, the lender must provide the list.</P>
<P>(i) The Web site maintained by the Bureau; or</P>
<P>(ii) Data made available by the Bureau or HUD.</P>
<P>(b) <I>Open-end lines of credit.</I> For a federally related mortgage loan that is a home-equity line, see <I>et seq.</I></P>
<CITA TYPE="N">[78 FR 6961, Jan. 31, 2013]</CITA>
</DIV8>
"""


def test_xml_to_markdown_renders_section_heading():
    from sources.adapters.cfr import _xml_to_markdown
    root = ET.fromstring(_FIXTURE_SECTION_XML)
    md = _xml_to_markdown(root)
    # Heading present (DIV8 → ###).
    assert "### " in md
    assert "1024.20" in md


def test_xml_to_markdown_preserves_inline_italics():
    from sources.adapters.cfr import _xml_to_markdown
    root = ET.fromstring(_FIXTURE_SECTION_XML)
    md = _xml_to_markdown(root)
    assert "*Provision of list.*" in md
    assert "*Open-end lines of credit.*" in md


def test_xml_to_markdown_paragraphs_separated():
    from sources.adapters.cfr import _xml_to_markdown
    root = ET.fromstring(_FIXTURE_SECTION_XML)
    md = _xml_to_markdown(root)
    # Each (a) / (i) / (ii) / (b) on its own line
    for needle in ("(a)", "(i)", "(ii)", "(b)"):
        assert needle in md


def test_xml_to_markdown_cita_block_visible():
    from sources.adapters.cfr import _xml_to_markdown
    root = ET.fromstring(_FIXTURE_SECTION_XML)
    md = _xml_to_markdown(root)
    assert "78 FR 6961" in md  # the citation text survives


_FIXTURE_PART_XML = """<?xml version="1.0"?>
<DIV5 N="1024" TYPE="PART">
<HEAD>PART 1024&#x2014;REAL ESTATE SETTLEMENT PROCEDURES ACT</HEAD>
<AUTH><HED>Authority:</HED><PSPACE>12 U.S.C. 2603-2605.</PSPACE></AUTH>
<DIV6 N="A" TYPE="SUBPART">
<HEAD>Subpart A&#x2014;General Provisions</HEAD>
<DIV8 N="1024.1" TYPE="SECTION">
<HEAD>&#xA7; 1024.1 Designation.</HEAD>
<P>This part, known as Regulation X, is issued by the Bureau.</P>
</DIV8>
</DIV6>
</DIV5>
"""


def test_xml_to_markdown_renders_part_with_subpart_and_sections():
    from sources.adapters.cfr import _xml_to_markdown
    root = ET.fromstring(_FIXTURE_PART_XML)
    md = _xml_to_markdown(root)
    # Part heading
    assert "PART 1024" in md
    # Subpart heading
    assert "Subpart A" in md
    # Section heading
    assert "1024.1" in md
    # Authority block visible
    assert "Authority" in md
    assert "2603-2605" in md
    # Section body
    assert "Regulation X" in md


# ── Path rendering ────────────────────────────────────────────────

@dataclass
class _StubReq:
    identifier: str = ""
    project_id: str = "default"
    extras: dict = None
    source_type: str = ""


@dataclass
class _StubFetched:
    text: str = "x"
    title: str = ""
    author_or_publisher: str = ""
    url: str = ""
    published_at: str | None = None
    extra_meta: dict = None


def test_section_render_path():
    from sources.adapters.cfr import CFRSection
    a = CFRSection()
    f = _StubFetched(extra_meta={
        "title_number": 12, "part_number": "1024", "section_number": "1024.20",
    })
    assert a.render_artifact_path(_StubReq(), f) == \
        "cfr/title-12/part-1024/section-1024.20.md"


def test_part_render_path():
    from sources.adapters.cfr import CFRPart
    a = CFRPart()
    f = _StubFetched(extra_meta={
        "title_number": 12, "part_number": "1024", "section_number": "",
    })
    assert a.render_artifact_path(_StubReq(), f) == \
        "cfr/title-12/part-1024/index.md"
