"""Offline tests for the Massachusetts Legislature source adapters.

No network. Verifies registration, identifier parsing, body rendering,
path templates, frontmatter shape, and the mgl_citations regex.

Run: pytest backend/tests/integration/test_malegislature_adapters.py -v
"""
from __future__ import annotations

import os
from dataclasses import dataclass

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)

import pytest


def test_module_imports():
    from sources.adapters import malegislature  # noqa: F401
    assert hasattr(malegislature, "_API_BASE")
    assert malegislature._API_BASE == "https://malegislature.gov/api"


@pytest.mark.asyncio
async def test_current_court_falls_back_to_floor(monkeypatch):
    from sources.adapters import malegislature
    malegislature._CURRENT_COURT_CACHE.clear()

    async def boom(url):
        raise RuntimeError("network down")
    monkeypatch.setattr(malegislature, "_http_get_json", boom)
    n = await malegislature._current_court()
    assert n == malegislature._DEFAULT_COURT_FLOOR


@pytest.mark.asyncio
async def test_current_court_picks_max(monkeypatch):
    from sources.adapters import malegislature
    malegislature._CURRENT_COURT_CACHE.clear()

    async def fake(url):
        return [
            {"GeneralCourtNumber": 192},
            {"GeneralCourtNumber": 193},
            {"GeneralCourtNumber": 194},
        ]
    monkeypatch.setattr(malegislature, "_http_get_json", fake)
    n = await malegislature._current_court()
    assert n == 194


# ── General Law section identifier parsing ────────────────────────

def test_section_formal_citation():
    from sources.adapters.malegislature import _parse_section_identifier
    p = _parse_section_identifier("M.G.L. c. 23A § 1")
    assert p == {"chapter": "23A", "section": "1"}


def test_section_alt_separator():
    from sources.adapters.malegislature import _parse_section_identifier
    assert _parse_section_identifier("MGL 23A § 1") == {"chapter": "23A", "section": "1"}
    assert _parse_section_identifier("Chapter 23A Section 1") == {"chapter": "23A", "section": "1"}
    assert _parse_section_identifier("23A/1") == {"chapter": "23A", "section": "1"}


def test_section_alphanumeric_codes():
    from sources.adapters.malegislature import _parse_section_identifier
    p = _parse_section_identifier("Chapter 6 Section 3M")
    assert p == {"chapter": "6", "section": "3M"}
    p = _parse_section_identifier("MGL 6A § 3B")
    assert p == {"chapter": "6A", "section": "3B"}


def test_section_url_form():
    from sources.adapters.malegislature import _parse_section_identifier
    p = _parse_section_identifier(
        "https://malegislature.gov/Laws/GeneralLaws/PartI/TitleII/Chapter23A/Section1",
    )
    assert p == {"chapter": "23A", "section": "1"}


def test_section_rejects_chapter_only():
    from sources.adapters.malegislature import _parse_section_identifier
    with pytest.raises(RuntimeError, match="section number missing"):
        _parse_section_identifier("Chapter 23A")


# ── General Law section body rendering ────────────────────────────

_SECTION_FIXTURE = {
    "Code": "1",
    "Name": "Massachusetts office of business development; director; duties",
    "IsRepealed": False,
    "Text": "Section 1. (a) Within the executive office...\r\n\r\n(b) MOBD may make discretionary grants...",
    "Chapter": {"Code": "23A", "Details": "https://malegislature.gov/api/Chapters/23A"},
    "Part": {"Code": "I", "Details": "https://malegislature.gov/api/Parts/I"},
}


def test_render_section_body_has_h2_heading():
    from sources.adapters.malegislature import _render_section_body
    md = _render_section_body(_SECTION_FIXTURE)
    assert md.startswith("## Section 1.")
    assert "Massachusetts office of business development" in md


def test_render_section_body_preserves_paragraphs():
    from sources.adapters.malegislature import _render_section_body
    md = _render_section_body(_SECTION_FIXTURE)
    assert "(a)" in md
    assert "(b)" in md
    # \r\n collapsed to blank lines, no raw \r leaks
    assert "\r" not in md


def test_render_section_body_repealed_marker():
    from sources.adapters.malegislature import _render_section_body
    repealed = {
        "Code": "5",
        "Name": "Repealed, 2010, 240, Sec. 117.",
        "IsRepealed": True,
        "Text": "",
        "Chapter": {"Code": "6"},
        "Part": {"Code": "I"},
    }
    md = _render_section_body(repealed)
    assert "_Repealed_" in md


# ── General Law section adapter wiring ────────────────────────────

@dataclass
class _StubReq:
    identifier: str = ""
    project_id: str = "default"
    extras: dict | None = None
    source_type: str = ""


@dataclass
class _StubFetched:
    text: str = "x"
    title: str = ""
    author_or_publisher: str = ""
    url: str = ""
    published_at: str | None = None
    extra_meta: dict | None = None


def test_section_adapter_path():
    from sources.adapters.malegislature import GeneralLawSection
    a = GeneralLawSection()
    f = _StubFetched(extra_meta={"chapter_code": "23A", "section_code": "1"})
    assert a.render_artifact_path(_StubReq(), f) == \
        "mass-laws/chapter-23A/section-1.md"


def test_section_adapter_frontmatter_has_citation():
    from sources.adapters.malegislature import GeneralLawSection
    a = GeneralLawSection()
    f = _StubFetched(
        title="Section 1. Massachusetts office of business development",
        url="https://malegislature.gov/Laws/GeneralLaws/PartI/TitleII/Chapter23A/Section1",
        published_at="2026-05-08",
        extra_meta={
            "chapter_code": "23A",
            "section_code": "1",
            "part_code": "I",
            "is_repealed": False,
            "citation": "M.G.L. c. 23A § 1",
            "jurisdiction": "MA",
            "as_of_date": "2026-05-08",
            "hierarchy": {"part": "I", "chapter": "23A", "section": "1"},
            "mgl_citations": [],
        },
    )
    fm = a.build_frontmatter(_StubReq(extras={}), f)
    assert fm["citation"] == "M.G.L. c. 23A § 1"
    assert fm["jurisdiction"] == "MA"
    assert fm["hierarchy"]["chapter"] == "23A"
    assert fm["is_repealed"] is False
