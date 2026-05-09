"""Live-network smoke tests for the Massachusetts Legislature adapters.

Skipped unless MALEGIS_LIVE=1 is set. Hits the real API; do not run
these in CI loops.

Run: MALEGIS_LIVE=1 pytest backend/tests/integration/test_malegislature_live.py -v
"""
from __future__ import annotations

import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)

import pytest

LIVE = os.environ.get("MALEGIS_LIVE") == "1"
pytestmark = pytest.mark.skipif(not LIVE, reason="set MALEGIS_LIVE=1 to run")


@pytest.mark.asyncio
async def test_live_general_law_section():
    from sources.adapters.malegislature import GeneralLawSection
    from sources.base import IngestRequest
    a = GeneralLawSection()
    req = IngestRequest(
        source_type="malegis/general-law-section",
        identifier="M.G.L. c. 23A § 1",
        project_id="default",
    )
    fc = await a.fetch(req)
    assert "Massachusetts office of business development" in fc.title
    assert "MOBD" in fc.text or "Massachusetts office" in fc.text
    assert fc.extra_meta["chapter_code"] == "23A"
    assert fc.extra_meta["section_code"] == "1"


@pytest.mark.asyncio
async def test_live_general_law_chapter_small_cap():
    from sources.adapters.malegislature import GeneralLawChapter
    from sources.base import IngestRequest
    a = GeneralLawChapter()
    req = IngestRequest(
        source_type="malegis/general-law-chapter",
        identifier="Chapter 23A",
        project_id="default",
        extras={"max_sections": 3},
    )
    fc = await a.fetch(req)
    assert fc.extra_meta["section_count"] <= 3
    assert "Chapter 23A" in fc.title


@pytest.mark.asyncio
async def test_live_session_law():
    from sources.adapters.malegislature import SessionLaw
    from sources.base import IngestRequest
    a = SessionLaw()
    req = IngestRequest(
        source_type="malegis/session-law",
        identifier="2024/1",
        project_id="default",
    )
    fc = await a.fetch(req)
    assert fc.extra_meta["year"] == "2024"
    assert fc.extra_meta["chapter_number"] == "1"
    assert "Shutesbury" in fc.text or "planning board" in fc.text
    # Anchor rewrite should have produced absolute URLs.
    assert "(https://malegislature.gov/Laws/" in fc.text


@pytest.mark.asyncio
async def test_live_bill():
    from sources.adapters.malegislature import Bill
    from sources.base import IngestRequest
    a = Bill()
    req = IngestRequest(
        source_type="malegis/bill",
        identifier="H4038@193",
        project_id="default",
    )
    fc = await a.fetch(req)
    assert fc.extra_meta["bill_number"] == "H4038"
    assert fc.extra_meta["general_court"] == 193
    assert "SECTION 1." in fc.text


@pytest.mark.asyncio
async def test_live_hearing():
    """Pick the first hearing from the index and fetch it."""
    from sources.adapters.malegislature import Hearing, _http_get_json, _API_BASE
    from sources.base import IngestRequest
    index = await _http_get_json(f"{_API_BASE}/Hearings")
    assert index, "hearings index is empty"
    first = index[0]
    eid = first["EventId"]
    a = Hearing()
    req = IngestRequest(
        source_type="malegis/hearing",
        identifier=str(eid),
        project_id="default",
    )
    fc = await a.fetch(req)
    assert fc.extra_meta["event_id"] == eid
    assert fc.title  # non-empty
