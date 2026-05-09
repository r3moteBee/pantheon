# Massachusetts Legislature Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add seven source adapters for the Massachusetts Legislature Public API (`malegislature.gov/api`), covering codified General Laws, session laws, bills, and process records (hearings, roll calls, committee votes).

**Architecture:** One file `backend/sources/adapters/malegislature.py` mirroring `cfr.py`'s "all genres for one mechanism in one file" convention. All adapters use httpx with no MCP. A small base class shares the HTTP helper, current-court cache, and the `mgl_citations` regex used by bills and session laws.

**Tech Stack:** Python 3, `httpx` (async HTTP), `markdownify` (HTML→MD for session laws), `pytest` (tests). All already in `backend/requirements.txt`.

**Spec:** `docs/superpowers/specs/2026-05-08-massachusetts-legislature-adapter-design.md`

---

## File Structure

| Path | Purpose |
|---|---|
| `backend/sources/adapters/malegislature.py` | All 7 adapters + shared helpers (~1200 LOC) |
| `backend/sources/adapters/__init__.py` | Add the `malegislature` import to register adapters |
| `backend/tests/integration/test_malegislature_adapters.py` | Offline tests: identifier parsing, body rendering, path templates, frontmatter, registration |
| `backend/tests/integration/test_malegislature_live.py` | Live API tests gated behind `MALEGIS_LIVE=1` |
| `frontend/package.json` | Bump version to ship |

---

## Task 1: Module skeleton, HTTP helper, current-court cache

**Files:**
- Create: `backend/sources/adapters/malegislature.py`
- Test:   `backend/tests/integration/test_malegislature_adapters.py`

This task lays down the bones every other adapter task will lean on: the HTTP helper, the current-court cache (with its fallback chain), and the empty registration of the file itself. No adapters yet — those land in Tasks 2–8.

- [ ] **Step 1: Create the test file with the bootstrap stanza**

Pantheon tests need `DATA_DIR` set before any backend import (the settings module reads it eagerly). Mirror what `test_cfr_adapter.py` does at line 14–15.

```python
# backend/tests/integration/test_malegislature_adapters.py
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
```

- [ ] **Step 2: Write the failing module-import test**

Append to `backend/tests/integration/test_malegislature_adapters.py`:

```python
def test_module_imports():
    from sources.adapters import malegislature  # noqa: F401
    assert hasattr(malegislature, "_API_BASE")
    assert malegislature._API_BASE == "https://malegislature.gov/api"
```

- [ ] **Step 3: Run the test — expected to fail (module missing)**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py::test_module_imports -v`

Expected: `ModuleNotFoundError: No module named 'sources.adapters.malegislature'`

- [ ] **Step 4: Create the module with constants and HTTP helper**

```python
# backend/sources/adapters/malegislature.py
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
from datetime import date, datetime, timezone
from typing import Any, Optional

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
```

- [ ] **Step 5: Run the import test — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py::test_module_imports -v`

Expected: PASS (1 test).

- [ ] **Step 6: Write the failing current-court fallback test**

The test patches the HTTP helper to simulate a network failure and asserts the floor is returned.

Append to `test_malegislature_adapters.py`:

```python
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
```

- [ ] **Step 7: Confirm pytest-asyncio is available**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -c "import pytest_asyncio; print(pytest_asyncio.__version__)"`

Expected: prints a version string. If `ModuleNotFoundError`, install it:

```bash
~/pantheon/.venv/bin/pip install pytest-asyncio
```

Then add to `backend/pytest.ini` (or create it if absent) so `asyncio_mode = auto` isn't required and `@pytest.mark.asyncio` works:

```ini
[pytest]
asyncio_mode = auto
```

(If `pytest.ini` already configures asyncio, leave it alone.)

- [ ] **Step 8: Run the two new tests — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -v`

Expected: 3 tests pass.

- [ ] **Step 9: Commit**

```bash
cd /home/pan/pantheon
git add backend/sources/adapters/malegislature.py backend/tests/integration/test_malegislature_adapters.py
# Add backend/pytest.ini only if Step 7 created it
git status --short backend/pytest.ini && git add backend/pytest.ini
git commit -m "$(cat <<'EOF'
malegis: scaffold adapter module + current-court resolver

HTTP helper, three-level current-court fallback (cache → API → floor 193),
and the offline test bootstrap. No adapters registered yet.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: General Law section adapter

**Files:**
- Modify: `backend/sources/adapters/malegislature.py`
- Test:   `backend/tests/integration/test_malegislature_adapters.py`

The single-section adapter for `M.G.L. c. {chapter} § {section}`. Body is the `Text` field rendered as markdown with an H2 heading.

- [ ] **Step 1: Write the failing identifier-parsing tests**

Append to `test_malegislature_adapters.py`:

```python
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
```

- [ ] **Step 2: Run — expected to fail (parser missing)**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k section -v`

Expected: ImportError on `_parse_section_identifier`.

- [ ] **Step 3: Implement the parser**

Append to `backend/sources/adapters/malegislature.py`:

```python
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
```

- [ ] **Step 4: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k section -v`

Expected: 5 section parsing tests pass.

- [ ] **Step 5: Write the failing body-rendering test**

Append:

```python
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
```

- [ ] **Step 6: Run — expected to fail (renderer missing)**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k render_section_body -v`

Expected: AttributeError on `_render_section_body`.

- [ ] **Step 7: Implement `_render_section_body`**

Append to `malegislature.py`:

```python
# ── Body rendering ────────────────────────────────────────────────

_WS_RE = re.compile(r"[ \t]+")
_PARA_BREAK_RE = re.compile(r"(?:\r\n|\r|\n){2,}")


def _normalize_prose(s: str) -> str:
    """Collapse \\r\\n line endings to \\n and tidy whitespace."""
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
    if text:
        # Restore paragraph breaks (\n\n) but keep single newlines as
        # subsection joiners.
        out.append(text)
    return "\n".join(out).rstrip() + "\n"
```

- [ ] **Step 8: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k render_section_body -v`

Expected: 3 tests pass.

- [ ] **Step 9: Write the failing path + adapter test**

Append:

```python
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
```

- [ ] **Step 10: Run — expected to fail (adapter class missing)**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k section_adapter -v`

Expected: ImportError on `GeneralLawSection`.

- [ ] **Step 11: Implement the base + section adapter**

Append to `malegislature.py`:

```python
# ── Adapter base ──────────────────────────────────────────────────

class _MALegisBaseAdapter(SourceAdapter):
    """Shared behavior: bucket aliases, common frontmatter additions."""
    bucket_aliases = ("malegis", "mass", "malaw")
    requires_mcp = ()
    extractor_strategy = "llm_default"
    auto_link_similarity = True

    def build_frontmatter(self, req, fetched) -> dict[str, Any]:
        fm = super().build_frontmatter(req, fetched)
        for key in (
            "citation", "jurisdiction", "as_of_date", "mgl_citations",
        ):
            if key in fetched.extra_meta:
                fm[key] = fetched.extra_meta[key]
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
```

- [ ] **Step 12: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k section_adapter -v`

Expected: 2 tests pass. Total file-level passes: 10.

- [ ] **Step 13: Commit**

```bash
cd /home/pan/pantheon
git add backend/sources/adapters/malegislature.py backend/tests/integration/test_malegislature_adapters.py
git commit -m "$(cat <<'EOF'
malegis: add general-law-section adapter

Identifier parser accepts formal citation, terse, alphanumeric codes
(3A, 3B, …), and malegislature.gov URLs. Body renders the section Text
field with an H2 heading and preserves paragraph structure.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: General Law chapter adapter

**Files:**
- Modify: `backend/sources/adapters/malegislature.py`
- Test:   `backend/tests/integration/test_malegislature_adapters.py`

The chapter adapter fans out to one HTTP call per section. Caps at `extras["max_sections"]` (default 200) so a runaway chapter doesn't make thousands of requests. Uses an `httpx.AsyncClient` with `max_connections=4` for politeness.

- [ ] **Step 1: Write the failing identifier-parsing tests**

Append to `test_malegislature_adapters.py`:

```python
# ── General Law chapter identifier parsing ────────────────────────

def test_chapter_formal_citation():
    from sources.adapters.malegislature import _parse_chapter_identifier
    assert _parse_chapter_identifier("M.G.L. c. 23A") == {"chapter": "23A"}
    assert _parse_chapter_identifier("Chapter 23A") == {"chapter": "23A"}
    assert _parse_chapter_identifier("23A") == {"chapter": "23A"}
    assert _parse_chapter_identifier("MGL 6A") == {"chapter": "6A"}


def test_chapter_url_form():
    from sources.adapters.malegislature import _parse_chapter_identifier
    p = _parse_chapter_identifier(
        "https://malegislature.gov/Laws/GeneralLaws/PartI/TitleII/Chapter23A",
    )
    assert p == {"chapter": "23A"}


def test_chapter_invalid():
    from sources.adapters.malegislature import _parse_chapter_identifier
    with pytest.raises(RuntimeError):
        _parse_chapter_identifier("not a citation")
```

- [ ] **Step 2: Run — expected to fail**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k chapter -v`

Expected: ImportError on `_parse_chapter_identifier`.

- [ ] **Step 3: Implement `_parse_chapter_identifier`**

Append to `malegislature.py`:

```python
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
```

- [ ] **Step 4: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k chapter -v`

Expected: 3 tests pass.

- [ ] **Step 5: Write the failing chapter-rendering test**

The renderer composes a chapter detail dict + an array of section dicts into a single markdown blob.

```python
# ── General Law chapter body rendering ────────────────────────────

_CHAPTER_FIXTURE = {
    "Code": "23A",
    "Name": "DEPARTMENT OF ECONOMIC DEVELOPMENT",
    "IsRepealed": False,
    "Part": {"Code": "I", "Details": "https://malegislature.gov/api/Parts/I"},
}

_SECTIONS_FIXTURE = [
    {
        "Code": "1",
        "Name": "Director; duties",
        "IsRepealed": False,
        "Text": "Section 1. The director shall...",
        "Chapter": {"Code": "23A"},
        "Part": {"Code": "I"},
    },
    {
        "Code": "2",
        "Name": "Powers of the office",
        "IsRepealed": False,
        "Text": "Section 2. MOBD shall...",
        "Chapter": {"Code": "23A"},
        "Part": {"Code": "I"},
    },
]


def test_render_chapter_body_has_h1_and_h2():
    from sources.adapters.malegislature import _render_chapter_body
    md = _render_chapter_body(_CHAPTER_FIXTURE, _SECTIONS_FIXTURE)
    assert md.startswith("# Chapter 23A —")
    assert "## Section 1." in md
    assert "## Section 2." in md
    assert "Director; duties" in md


def test_render_chapter_body_part_line():
    from sources.adapters.malegislature import _render_chapter_body
    md = _render_chapter_body(_CHAPTER_FIXTURE, _SECTIONS_FIXTURE)
    assert "Part I" in md
```

- [ ] **Step 6: Run — expected to fail**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k render_chapter_body -v`

Expected: AttributeError.

- [ ] **Step 7: Implement `_render_chapter_body`**

Append:

```python
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
```

- [ ] **Step 8: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k render_chapter_body -v`

Expected: 2 tests pass.

- [ ] **Step 9: Write the failing chapter-adapter path + frontmatter tests**

```python
def test_chapter_adapter_path():
    from sources.adapters.malegislature import GeneralLawChapter
    a = GeneralLawChapter()
    f = _StubFetched(extra_meta={"chapter_code": "23A"})
    assert a.render_artifact_path(_StubReq(), f) == \
        "mass-laws/chapter-23A/index.md"


def test_chapter_adapter_frontmatter():
    from sources.adapters.malegislature import GeneralLawChapter
    a = GeneralLawChapter()
    f = _StubFetched(
        title="Chapter 23A — DEPARTMENT OF ECONOMIC DEVELOPMENT",
        url="https://malegislature.gov/Laws/GeneralLaws/PartI/Chapter23A",
        published_at="2026-05-08",
        extra_meta={
            "chapter_code": "23A",
            "part_code": "I",
            "is_repealed": False,
            "section_count": 25,
            "citation": "M.G.L. c. 23A",
            "jurisdiction": "MA",
            "as_of_date": "2026-05-08",
            "hierarchy": {"part": "I", "chapter": "23A"},
            "mgl_citations": [],
        },
    )
    fm = a.build_frontmatter(_StubReq(extras={}), f)
    assert fm["citation"] == "M.G.L. c. 23A"
    assert fm["section_count"] == 25
    assert fm["hierarchy"]["chapter"] == "23A"
```

- [ ] **Step 10: Run — expected to fail**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k chapter_adapter -v`

Expected: ImportError on `GeneralLawChapter`.

- [ ] **Step 11: Implement the chapter adapter**

Append:

```python
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
```

- [ ] **Step 12: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -v`

Expected: all tests pass.

- [ ] **Step 13: Commit**

```bash
cd /home/pan/pantheon
git add backend/sources/adapters/malegislature.py backend/tests/integration/test_malegislature_adapters.py
git commit -m "$(cat <<'EOF'
malegis: add general-law-chapter adapter

Fetches chapter detail then iterates Sections[]. Caps fanout at
extras['max_sections']=200 default and limits concurrent connections to
4 for politeness.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Session law adapter + mgl_citations regex

**Files:**
- Modify: `backend/sources/adapters/malegislature.py`
- Test:   `backend/tests/integration/test_malegislature_adapters.py`

Session laws have HTML body content (`ChapterText`) with relative anchor URLs to General Laws. We convert with `markdownify`, rewrite anchors to absolute, and pull `mgl_citations` from the rendered text. The `_extract_mgl_citations` helper added here is reused by the bill adapter in Task 5.

- [ ] **Step 1: Write the failing identifier-parsing tests**

```python
# ── Session law identifier parsing ────────────────────────────────

def test_session_law_terse():
    from sources.adapters.malegislature import _parse_session_law_identifier
    assert _parse_session_law_identifier("2024/1") == {"year": "2024", "chapter": "1"}


def test_session_law_natural():
    from sources.adapters.malegislature import _parse_session_law_identifier
    assert _parse_session_law_identifier("2024 Chapter 1") == {"year": "2024", "chapter": "1"}
    assert _parse_session_law_identifier("Chapter 1 of 2024") == {"year": "2024", "chapter": "1"}
    assert _parse_session_law_identifier("Acts of 2024, Chapter 1") == {"year": "2024", "chapter": "1"}


def test_session_law_url():
    from sources.adapters.malegislature import _parse_session_law_identifier
    p = _parse_session_law_identifier(
        "https://malegislature.gov/Laws/SessionLaws/Acts/2024/Chapter1",
    )
    assert p == {"year": "2024", "chapter": "1"}
```

- [ ] **Step 2: Run — expected to fail**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k session_law -v`

Expected: ImportError.

- [ ] **Step 3: Implement `_parse_session_law_identifier`**

Append:

```python
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
```

- [ ] **Step 4: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k session_law -v`

Expected: 3 tests pass.

- [ ] **Step 5: Write the failing mgl_citations test**

```python
# ── mgl_citations regex extraction ────────────────────────────────

def test_mgl_citations_extracts_chapter_section():
    from sources.adapters.malegislature import _extract_mgl_citations
    text = "section 9 of chapter 40A of the General Laws"
    cites = _extract_mgl_citations(text)
    assert {"chapter": "40A", "section": "9"} in cites


def test_mgl_citations_extracts_chapter_only():
    from sources.adapters.malegislature import _extract_mgl_citations
    text = "as provided by chapter 41 of the General Laws"
    cites = _extract_mgl_citations(text)
    assert {"chapter": "41", "section": None} in cites


def test_mgl_citations_extracts_formal_form():
    from sources.adapters.malegislature import _extract_mgl_citations
    text = "M.G.L. c. 23A § 1 governs this matter"
    cites = _extract_mgl_citations(text)
    assert {"chapter": "23A", "section": "1"} in cites


def test_mgl_citations_dedupes():
    from sources.adapters.malegislature import _extract_mgl_citations
    text = "chapter 40A and chapter 40A and section 9 of chapter 40A of the General Laws"
    cites = _extract_mgl_citations(text)
    # Should contain {40A, None} and {40A, 9}, no duplicates of either.
    assert len(cites) == len({(c["chapter"], c["section"]) for c in cites})
```

- [ ] **Step 6: Run — expected to fail**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k mgl_citations -v`

Expected: AttributeError.

- [ ] **Step 7: Implement `_extract_mgl_citations`**

Append:

```python
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


def _extract_mgl_citations(text: str) -> list[dict[str, Optional[str]]]:
    """Pull MGL citations out of free text. Returns a deduped list of
    {'chapter', 'section'} dicts (section may be None when only the
    chapter is mentioned).

    Order: section-bearing patterns first so '...section 9 of chapter
    40A...' captures the pair before the chapter-only pattern matches
    'chapter 40A' alone.
    """
    if not text:
        return []
    seen: set[tuple[str, Optional[str]]] = set()
    out: list[dict[str, Optional[str]]] = []

    # First pass: section + chapter
    for rx in _MGL_CITATION_PATTERNS[:2]:
        for m in rx.finditer(text):
            key = (_norm_code(m.group("chapter")), _norm_code(m.group("section")))
            if key not in seen:
                seen.add(key)
                out.append({"chapter": key[0], "section": key[1]})

    # Second pass: chapter only — skip if we already have a section
    # citation for that chapter (avoids the chapter-only pattern from
    # double-counting "chapter 40A" inside "section 9 of chapter 40A").
    chapters_with_section = {c for (c, s) in seen if s is not None}
    for rx in _MGL_CITATION_PATTERNS[2:]:
        for m in rx.finditer(text):
            chapter = _norm_code(m.group("chapter"))
            key = (chapter, None)
            if key in seen:
                continue
            if chapter in chapters_with_section:
                # We already captured a more-specific cite for this chapter;
                # don't add the bare-chapter version as a separate entry.
                continue
            seen.add(key)
            out.append({"chapter": chapter, "section": None})
    return out
```

- [ ] **Step 8: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k mgl_citations -v`

Expected: 4 tests pass.

- [ ] **Step 9: Write the failing session-law body and adapter tests**

```python
# ── Session law body rendering ────────────────────────────────────

_SESSION_LAW_FIXTURE = {
    "Year": 2024,
    "ChapterNumber": "1",
    "Type": "Acts",
    "ApprovalType": "Executive approval (signed)",
    "Title": "AN ACT PROVIDING FOR THE APPOINTMENT OF ASSOCIATE MEMBERS",
    "Status": "Approved by the Governor, January 8, 2024",
    "ApprovedDate": "Jan 08 2024",
    "ChapterText": (
        '<p><em>Be it enacted by the Senate and House...</em></p>\n'
        '<p>SECTION 1. Notwithstanding <a href="/Laws/GeneralLaws/PartI/TitleVII/Chapter40A/Section9">'
        'section 9 of chapter 40A of the General Laws</a> or any other law to the contrary, '
        'the chair may designate an associate member.</p>\n'
        '<p>SECTION 2. This act shall take effect upon its passage.</p>'
    ),
    "OriginBill": {
        "BillNumber": "H4038",
        "DocketNumber": "HD4496",
        "Title": "An Act relative to the planning board",
        "PrimarySponsor": {"Name": "Aaron L. Saunders"},
        "GeneralCourtNumber": 193,
    },
}


def test_render_session_law_has_title():
    from sources.adapters.malegislature import _render_session_law_body
    body, cites = _render_session_law_body(_SESSION_LAW_FIXTURE)
    assert body.startswith("# AN ACT PROVIDING FOR THE APPOINTMENT")
    assert "Acts of 2024, Chapter 1" in body
    assert "Approved by the Governor" in body


def test_render_session_law_anchor_rewrite():
    from sources.adapters.malegislature import _render_session_law_body
    body, _ = _render_session_law_body(_SESSION_LAW_FIXTURE)
    # Relative anchor must be rewritten to absolute.
    assert "https://malegislature.gov/Laws/GeneralLaws/PartI/TitleVII/Chapter40A/Section9" in body
    assert "(/Laws/GeneralLaws/" not in body


def test_render_session_law_origin_bill_footer():
    from sources.adapters.malegislature import _render_session_law_body
    body, _ = _render_session_law_body(_SESSION_LAW_FIXTURE)
    assert "Origin bill" in body
    assert "H4038" in body
    assert "Aaron L. Saunders" in body


def test_render_session_law_extracts_citations():
    from sources.adapters.malegislature import _render_session_law_body
    _, cites = _render_session_law_body(_SESSION_LAW_FIXTURE)
    assert {"chapter": "40A", "section": "9"} in cites


def test_session_law_adapter_path():
    from sources.adapters.malegislature import SessionLaw
    a = SessionLaw()
    f = _StubFetched(extra_meta={"year": 2024, "chapter_number": "1"})
    assert a.render_artifact_path(_StubReq(), f) == \
        "mass-session-laws/2024/chapter-1.md"
```

- [ ] **Step 10: Run — expected to fail**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k session_law -v`

Expected: AttributeError on the renderer / class.

- [ ] **Step 11: Implement `_render_session_law_body` and the SessionLaw adapter**

Append:

```python
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


def _parse_approved_date(s: str) -> Optional[str]:
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
```

- [ ] **Step 12: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k "session_law or mgl_citations" -v`

Expected: all session_law and mgl_citations tests pass.

- [ ] **Step 13: Commit**

```bash
cd /home/pan/pantheon
git add backend/sources/adapters/malegislature.py backend/tests/integration/test_malegislature_adapters.py
git commit -m "$(cat <<'EOF'
malegis: add session-law adapter + mgl_citations extractor

Session-law ChapterText is HTML; render via markdownify, then rewrite
relative anchors to absolute malegislature.gov URLs. The
_extract_mgl_citations helper pulls 'chapter X / section Y' references
out of free text and is reused by the bill adapter.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Bill adapter

**Files:**
- Modify: `backend/sources/adapters/malegislature.py`
- Test:   `backend/tests/integration/test_malegislature_adapters.py`

Bills carry the richest payload: text, sponsors, cosponsors, committee recommendations, amendments, roll calls. Default behaviour skips the secondary `BillHistory` HTTP call; opt in with `extras["include_history"]=True`. Identifier accepts current-court shorthand (`H4038`) or court-pinned (`H4038@193`).

- [ ] **Step 1: Write the failing identifier-parsing tests**

```python
# ── Bill identifier parsing ───────────────────────────────────────

def test_bill_terse_current_court():
    from sources.adapters.malegislature import _parse_bill_identifier
    p = _parse_bill_identifier("H4038")
    assert p == {"bill_number": "H4038", "general_court": None}


def test_bill_with_dot():
    from sources.adapters.malegislature import _parse_bill_identifier
    assert _parse_bill_identifier("H.4038")["bill_number"] == "H4038"
    assert _parse_bill_identifier("S.100")["bill_number"] == "S100"


def test_bill_court_pinned():
    from sources.adapters.malegislature import _parse_bill_identifier
    p = _parse_bill_identifier("H4038@193")
    assert p == {"bill_number": "H4038", "general_court": 193}


def test_bill_url_form():
    from sources.adapters.malegislature import _parse_bill_identifier
    p = _parse_bill_identifier("https://malegislature.gov/Bills/193/H4038")
    assert p == {"bill_number": "H4038", "general_court": 193}


def test_bill_invalid():
    from sources.adapters.malegislature import _parse_bill_identifier
    with pytest.raises(RuntimeError):
        _parse_bill_identifier("just a string")
```

- [ ] **Step 2: Run — expected to fail**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k bill -v`

Expected: ImportError.

- [ ] **Step 3: Implement `_parse_bill_identifier`**

Append:

```python
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
```

- [ ] **Step 4: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k bill -v`

Expected: 5 tests pass.

- [ ] **Step 5: Write the failing body-rendering tests**

```python
# ── Bill body rendering ───────────────────────────────────────────

_BILL_FIXTURE = {
    "Title": "An Act relative to the planning board in the town of Shutesbury",
    "BillNumber": "H4038",
    "DocketNumber": "HD4496",
    "GeneralCourtNumber": 193,
    "PrimarySponsor": {"Id": "ALS1", "Name": "Aaron L. Saunders"},
    "Cosponsors": [
        {"Id": "ALS1", "Name": "Aaron L. Saunders"},
        {"Id": "JMC0", "Name": "Joanne M. Comerford"},
    ],
    "JointSponsor": {"Id": "JMC0", "Name": "Joanne M. Comerford"},
    "LegislationTypeName": "Bill",
    "Pinslip": "By Representative Saunders... a joint petition relative to the membership of the planning board.",
    "DocumentText": (
        "\tSECTION 1. Notwithstanding section 9 of chapter 40A of the General Laws "
        "to the contrary, the chair may designate an associate member.\r\n"
        "\tSECTION 2. This act shall take effect upon its passage.\r\n"
    ),
    "EmergencyPreamble": None,
    "RollCalls": [],
    "Attachments": [],
    "CommitteeRecommendations": [
        {"Action": "Favorable", "Committee": {"CommitteeCode": "J10"}},
        {"Action": "Place in OD", "Committee": {"CommitteeCode": "H52"}},
    ],
    "Amendments": [],
}


def test_render_bill_body_has_title_and_meta():
    from sources.adapters.malegislature import _render_bill_body
    body, cites = _render_bill_body(_BILL_FIXTURE, history=None)
    assert body.startswith("# An Act relative to the planning board")
    assert "**H4038**" in body
    assert "193rd General Court" in body


def test_render_bill_body_pinslip_blockquote():
    from sources.adapters.malegislature import _render_bill_body
    body, _ = _render_bill_body(_BILL_FIXTURE, history=None)
    assert "> By Representative Saunders" in body


def test_render_bill_body_includes_text_and_sponsors():
    from sources.adapters.malegislature import _render_bill_body
    body, _ = _render_bill_body(_BILL_FIXTURE, history=None)
    assert "## Bill text" in body
    assert "SECTION 1." in body
    assert "## Sponsors" in body
    assert "Aaron L. Saunders" in body
    assert "Joanne M. Comerford" in body


def test_render_bill_body_committee_recommendations():
    from sources.adapters.malegislature import _render_bill_body
    body, _ = _render_bill_body(_BILL_FIXTURE, history=None)
    assert "## Committee recommendations" in body
    assert "J10" in body
    assert "Favorable" in body


def test_render_bill_body_extracts_mgl_citations():
    from sources.adapters.malegislature import _render_bill_body
    _, cites = _render_bill_body(_BILL_FIXTURE, history=None)
    assert {"chapter": "40A", "section": "9"} in cites


def test_render_bill_body_with_history():
    from sources.adapters.malegislature import _render_bill_body
    history = [
        {"Date": "2023-07-21", "Branch": "House", "Action": "Filed", "Description": ""},
        {"Date": "2024-01-08", "Branch": "Joint", "Action": "Approved by the Governor", "Description": ""},
    ]
    body, _ = _render_bill_body(_BILL_FIXTURE, history=history)
    assert "## History" in body
    assert "Filed" in body
    assert "Approved by the Governor" in body


def test_bill_adapter_path():
    from sources.adapters.malegislature import Bill
    a = Bill()
    f = _StubFetched(extra_meta={"general_court": 193, "bill_number": "H4038"})
    assert a.render_artifact_path(_StubReq(), f) == \
        "mass-bills/court-193/H4038.md"
```

- [ ] **Step 6: Run — expected to fail**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k bill -v`

Expected: AttributeError on `_render_bill_body` / `Bill`.

- [ ] **Step 7: Implement `_render_bill_body` and the Bill adapter**

Append:

```python
# ── Bill body rendering ───────────────────────────────────────────

def _render_bill_body(
    bill: dict[str, Any],
    *,
    history: Optional[list[dict[str, Any]]],
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
    out.append(f"**{bill_number}** · {legtype} · {court}rd General Court")
    out.append("")
    if pinslip:
        # Render as a blockquote, joining wrapped lines.
        for line in pinslip.splitlines() or [pinslip]:
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
            try:
                history = await _http_get_json(
                    f"{_API_BASE}/GeneralCourts/{court}/Documents/{bill_number}/DocumentHistoryActions"
                )
            except Exception as e:
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
                "citation": f"{bill_number} ({court}th General Court)",
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
```

- [ ] **Step 8: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k bill -v`

Expected: 12 tests pass (5 parsing + 7 render/adapter).

- [ ] **Step 9: Commit**

```bash
cd /home/pan/pantheon
git add backend/sources/adapters/malegislature.py backend/tests/integration/test_malegislature_adapters.py
git commit -m "$(cat <<'EOF'
malegis: add bill adapter

Renders title, pinslip, bill text, sponsors, committee recommendations,
amendments, and roll-call references. Optional History section gated
behind extras['include_history']=True. Uses the shared
_extract_mgl_citations to populate cross-references for graph linking.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Hearing adapter

**Files:**
- Modify: `backend/sources/adapters/malegislature.py`
- Test:   `backend/tests/integration/test_malegislature_adapters.py`

Hearings are metadata-only events. The body is a structured summary; extractor strategy is `noop` so we don't waste an LLM call.

- [ ] **Step 1: Write the failing identifier + render + path tests**

```python
# ── Hearing adapter ───────────────────────────────────────────────

def test_hearing_id_int_form():
    from sources.adapters.malegislature import _parse_hearing_identifier
    assert _parse_hearing_identifier("5655") == {"event_id": 5655}


def test_hearing_url_form():
    from sources.adapters.malegislature import _parse_hearing_identifier
    p = _parse_hearing_identifier("https://malegislature.gov/Events/Hearings/Detail/5655")
    assert p == {"event_id": 5655}


def test_hearing_invalid():
    from sources.adapters.malegislature import _parse_hearing_identifier
    with pytest.raises(RuntimeError):
        _parse_hearing_identifier("not an id")


_HEARING_FIXTURE = {
    "EventId": 5655,
    "Name": "House Committee on Intergovernmental Affairs",
    "Status": "Confirmed",
    "EventDate": "2026-05-29T13:00:00",
    "StartTime": "2026-05-29T13:00:00",
    "Description": "Higher Education and Federal relationships",
    "HearingHost": {"CommitteeCode": "Hxx", "GeneralCourtNumber": 194},
    "Location": {
        "LocationName": "Massachusetts Maritime Academy",
        "City": "Bourne",
        "State": "MA",
    },
    "HearingAgendas": [],
}


def test_render_hearing_body_has_header():
    from sources.adapters.malegislature import _render_hearing_body
    body = _render_hearing_body(_HEARING_FIXTURE)
    assert body.startswith("# House Committee on Intergovernmental Affairs")
    assert "Confirmed" in body
    assert "Hxx" in body
    assert "Massachusetts Maritime Academy" in body
    assert "Higher Education and Federal relationships" in body


def test_render_hearing_body_with_agenda():
    from sources.adapters.malegislature import _render_hearing_body
    h = dict(_HEARING_FIXTURE)
    h["HearingAgendas"] = [
        {"DocumentNumber": "H100", "Title": "An Act about widgets"},
        {"DocumentNumber": "S200", "Title": "An Act about gadgets"},
    ]
    body = _render_hearing_body(h)
    assert "## Agenda" in body
    assert "H100" in body
    assert "An Act about widgets" in body


def test_hearing_adapter_path():
    from sources.adapters.malegislature import Hearing
    a = Hearing()
    f = _StubFetched(extra_meta={"event_id": 5655, "event_date": "2026-05-29"})
    assert a.render_artifact_path(_StubReq(), f) == \
        "mass-hearings/2026-05-29/event-5655.md"


def test_hearing_adapter_unknown_date_path():
    from sources.adapters.malegislature import Hearing
    a = Hearing()
    f = _StubFetched(extra_meta={"event_id": 5655, "event_date": ""})
    assert a.render_artifact_path(_StubReq(), f) == \
        "mass-hearings/unknown-date/event-5655.md"


def test_hearing_uses_noop_extractor():
    from sources.adapters.malegislature import Hearing
    assert Hearing.extractor_strategy == "noop"
```

- [ ] **Step 2: Run — expected to fail**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k hearing -v`

Expected: ImportError.

- [ ] **Step 3: Implement parser, renderer, and Hearing adapter**

Append:

```python
# ── Hearing parser + renderer + adapter ───────────────────────────

_HEARING_URL_RE = re.compile(
    r"malegislature\.gov/Events/(?:Hearings|SpecialEvents)/Detail/(?P<id>\d+)",
    re.IGNORECASE,
)


def _parse_hearing_identifier(identifier: str) -> dict[str, int]:
    s = (identifier or "").strip()
    if not s:
        raise RuntimeError("malegis: empty identifier")
    m = _HEARING_URL_RE.search(s)
    if m:
        return {"event_id": int(m.group("id"))}
    if s.isdigit():
        return {"event_id": int(s)}
    raise RuntimeError(
        f"malegis/hearing: cannot parse identifier {identifier!r}; "
        f"expected an integer EventId or a malegislature.gov URL"
    )


def _short_date(iso_or_t: str) -> str:
    """Extract YYYY-MM-DD from '2026-05-29T13:00:00' or similar."""
    if not iso_or_t:
        return ""
    return iso_or_t[:10] if len(iso_or_t) >= 10 else ""


def _render_hearing_body(h: dict[str, Any]) -> str:
    name = (h.get("Name") or "Hearing").strip()
    status = (h.get("Status") or "").strip()
    event_date = (h.get("EventDate") or "").strip()
    start_time = (h.get("StartTime") or "").strip()
    description = (h.get("Description") or "").strip()
    host = h.get("HearingHost") or {}
    loc = h.get("Location") or {}
    agendas = h.get("HearingAgendas") or []

    out: list[str] = []
    out.append(f"# {name}")
    out.append("")
    if status:
        out.append(f"**Status:** {status}")
    if event_date:
        out.append(f"**Date:** {event_date}" + (f"  (start {start_time})" if start_time else ""))
    if host.get("CommitteeCode"):
        out.append(f"**Host committee:** {host['CommitteeCode']}")
    if loc.get("LocationName"):
        loc_str = loc["LocationName"]
        for k in ("City", "State"):
            if loc.get(k):
                loc_str += f", {loc[k]}"
        out.append(f"**Location:** {loc_str}")
    out.append("")
    if description:
        out.append("## Description")
        out.append("")
        out.append(description)
        out.append("")
    if agendas:
        out.append("## Agenda")
        out.append("")
        for ag in agendas:
            num = ag.get("DocumentNumber") or ag.get("Number") or "?"
            atitle = (ag.get("Title") or "").strip()
            out.append(f"- **{num}** — {atitle}".rstrip(" —"))
        out.append("")
    return "\n".join(out).rstrip() + "\n"


class Hearing(_MALegisBaseAdapter):
    source_type = "malegis/hearing"
    display_name = "MA hearing"
    artifact_path_template = "mass-hearings/{event_date}/event-{event_id}.md"
    extractor_strategy = "noop"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        parts = _parse_hearing_identifier(req.identifier)
        eid = parts["event_id"]
        payload = await _http_get_json(f"{_API_BASE}/Hearings/{eid}")
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if not payload:
            raise RuntimeError(f"malegis/hearing: empty payload for {eid}")

        body = _render_hearing_body(payload)
        as_of = datetime.now(timezone.utc).date().isoformat()
        event_date = _short_date(payload.get("EventDate") or "")
        host = payload.get("HearingHost") or {}
        loc = payload.get("Location") or {}
        return FetchedContent(
            text=body,
            title=(payload.get("Name") or f"Hearing {eid}").strip(),
            author_or_publisher="Massachusetts General Court",
            url=f"{_SITE_BASE}/Events/Hearings/Detail/{eid}",
            published_at=event_date or as_of,
            extra_meta={
                "event_id": eid,
                "event_date": event_date,
                "host_committee": host.get("CommitteeCode"),
                "location": {
                    "name": loc.get("LocationName"),
                    "city": loc.get("City"),
                    "state": loc.get("State"),
                },
                "status": payload.get("Status"),
                "citation": f"MA Hearing #{eid}",
                "jurisdiction": "MA",
                "as_of_date": as_of,
            },
        )

    def render_artifact_path(self, req, fetched):
        date_ = fetched.extra_meta.get("event_date") or "unknown-date"
        return self.artifact_path_template.format(
            event_date=date_,
            event_id=fetched.extra_meta.get("event_id", "0"),
        )

    def build_frontmatter(self, req, fetched) -> dict[str, Any]:
        fm = super().build_frontmatter(req, fetched)
        for key in ("event_id", "event_date", "host_committee", "location", "status"):
            if key in fetched.extra_meta:
                fm[key] = fetched.extra_meta[key]
        return fm


register_adapter(Hearing())
```

- [ ] **Step 4: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k hearing -v`

Expected: 7 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/sources/adapters/malegislature.py backend/tests/integration/test_malegislature_adapters.py
git commit -m "$(cat <<'EOF'
malegis: add hearing adapter

Body is a structured metadata summary (host committee, date, location,
description, agenda). Extractor strategy 'noop' since the value is in
the linked bills, not the hearing's own prose.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Roll-call adapter

**Files:**
- Modify: `backend/sources/adapters/malegislature.py`
- Test:   `backend/tests/integration/test_malegislature_adapters.py`

Roll calls are scoped to `(general_court, branch, roll_call_number)`. Body renders a header + tally + per-member vote table. `extractor_strategy = "noop"`.

- [ ] **Step 1: Write the failing identifier + render + path tests**

```python
# ── Roll-call adapter ─────────────────────────────────────────────

def test_roll_call_full_form():
    from sources.adapters.malegislature import _parse_roll_call_identifier
    p = _parse_roll_call_identifier("193/House/123")
    assert p == {"general_court": 193, "branch": "House", "roll_call_number": 123}


def test_roll_call_short_branch():
    from sources.adapters.malegislature import _parse_roll_call_identifier
    p = _parse_roll_call_identifier("H/123")
    assert p == {"general_court": None, "branch": "House", "roll_call_number": 123}
    p = _parse_roll_call_identifier("S/45")
    assert p == {"general_court": None, "branch": "Senate", "roll_call_number": 45}


def test_roll_call_url_form():
    from sources.adapters.malegislature import _parse_roll_call_identifier
    p = _parse_roll_call_identifier("https://malegislature.gov/RollCall/193/House/123")
    assert p == {"general_court": 193, "branch": "House", "roll_call_number": 123}


def test_roll_call_invalid():
    from sources.adapters.malegislature import _parse_roll_call_identifier
    with pytest.raises(RuntimeError):
        _parse_roll_call_identifier("nonsense")


_ROLLCALL_FIXTURE = {
    "RollCallNumber": 123,
    "Branch": "House",
    "GeneralCourtNumber": 193,
    "Date": "2024-03-15T10:00:00",
    "Question": "Shall the bill pass?",
    "Yeas": 100,
    "Nays": 50,
    "Present": 2,
    "Absent": 8,
    "Members": [
        {"Name": "Aaron L. Saunders", "Vote": "Yea"},
        {"Name": "Joanne M. Comerford", "Vote": "Nay"},
    ],
}


def test_render_roll_call_body():
    from sources.adapters.malegislature import _render_roll_call_body
    body = _render_roll_call_body(_ROLLCALL_FIXTURE)
    assert body.startswith("# House Roll Call #123")
    assert "Yea: 100" in body
    assert "Nay: 50" in body
    assert "| Aaron L. Saunders | Yea |" in body


def test_roll_call_adapter_path():
    from sources.adapters.malegislature import RollCall
    a = RollCall()
    f = _StubFetched(extra_meta={
        "general_court": 193, "branch": "House", "roll_call_number": 123,
    })
    assert a.render_artifact_path(_StubReq(), f) == \
        "mass-roll-calls/court-193/House/rc-123.md"
```

- [ ] **Step 2: Run — expected to fail**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k roll_call -v`

Expected: ImportError.

- [ ] **Step 3: Implement parser, renderer, and RollCall adapter**

Append:

```python
# ── Roll-call parser + renderer + adapter ─────────────────────────

_ROLLCALL_URL_RE = re.compile(
    r"malegislature\.gov/RollCall/(?P<court>\d+)/(?P<branch>House|Senate|H|S)/(?P<num>\d+)",
    re.IGNORECASE,
)
_ROLLCALL_FULL_RE = re.compile(
    r"^\s*(?P<court>\d+)\s*/\s*(?P<branch>House|Senate|H|S)\s*/\s*(?P<num>\d+)\s*$",
    re.IGNORECASE,
)
_ROLLCALL_SHORT_RE = re.compile(
    r"^\s*(?P<branch>H|S|House|Senate)\s*/\s*(?P<num>\d+)\s*$",
    re.IGNORECASE,
)


def _expand_branch(b: str) -> str:
    b = b.strip().lower()
    if b in ("h", "house"):
        return "House"
    if b in ("s", "senate"):
        return "Senate"
    raise RuntimeError(f"malegis: unknown branch {b!r}")


def _parse_roll_call_identifier(identifier: str) -> dict[str, Any]:
    s = (identifier or "").strip()
    if not s:
        raise RuntimeError("malegis: empty identifier")
    m = _ROLLCALL_URL_RE.search(s)
    if m:
        return {
            "general_court": int(m.group("court")),
            "branch": _expand_branch(m.group("branch")),
            "roll_call_number": int(m.group("num")),
        }
    m = _ROLLCALL_FULL_RE.match(s)
    if m:
        return {
            "general_court": int(m.group("court")),
            "branch": _expand_branch(m.group("branch")),
            "roll_call_number": int(m.group("num")),
        }
    m = _ROLLCALL_SHORT_RE.match(s)
    if m:
        return {
            "general_court": None,
            "branch": _expand_branch(m.group("branch")),
            "roll_call_number": int(m.group("num")),
        }
    raise RuntimeError(
        f"malegis/roll-call: cannot parse identifier {identifier!r}; "
        f"expected '<court>/<branch>/<num>', '<H|S>/<num>', or a RollCall URL"
    )


def _render_roll_call_body(rc: dict[str, Any]) -> str:
    branch = rc.get("Branch") or "?"
    num = rc.get("RollCallNumber") or rc.get("Number") or "?"
    court = rc.get("GeneralCourtNumber") or "?"
    when = (rc.get("Date") or "").strip()
    question = (rc.get("Question") or "").strip()
    yeas = rc.get("Yeas", 0)
    nays = rc.get("Nays", 0)
    present = rc.get("Present", 0)
    absent = rc.get("Absent", 0)
    members = rc.get("Members") or []

    out: list[str] = []
    out.append(f"# {branch} Roll Call #{num} — {when}".rstrip(" —"))
    out.append("")
    out.append(f"**Court:** {court} · **Tally:** Yea: {yeas} · Nay: {nays} · Present: {present} · Absent: {absent}")
    if question:
        out.append("")
        out.append(f"> {question}")
    out.append("")
    if members:
        out.append("| Member | Vote |")
        out.append("|---|---|")
        for mem in members:
            out.append(f"| {mem.get('Name', '?')} | {mem.get('Vote', '?')} |")
    return "\n".join(out).rstrip() + "\n"


class RollCall(_MALegisBaseAdapter):
    source_type = "malegis/roll-call"
    display_name = "MA floor roll call"
    artifact_path_template = "mass-roll-calls/court-{general_court}/{branch}/rc-{roll_call_number}.md"
    extractor_strategy = "noop"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        parts = _parse_roll_call_identifier(req.identifier)
        court = (req.extras or {}).get("general_court") or parts["general_court"]
        if court is None:
            court = await _current_court()
        court = int(court)
        branch = parts["branch"]
        num = parts["roll_call_number"]
        url = f"{_API_BASE}/GeneralCourts/{court}/Branches/{branch}/RollCalls/{num}"
        payload = await _http_get_json(url)
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if not payload:
            raise RuntimeError(f"malegis/roll-call: empty payload for {court}/{branch}/{num}")

        body = _render_roll_call_body(payload)
        as_of = datetime.now(timezone.utc).date().isoformat()
        vote_date = _short_date(payload.get("Date") or "")
        return FetchedContent(
            text=body,
            title=f"{branch} Roll Call #{num}",
            author_or_publisher="Massachusetts General Court",
            url=f"{_SITE_BASE}/RollCall/{court}/{branch}/{num}",
            published_at=vote_date or as_of,
            extra_meta={
                "general_court": court,
                "branch": branch,
                "roll_call_number": num,
                "tally": {
                    "yea": payload.get("Yeas", 0),
                    "nay": payload.get("Nays", 0),
                    "present": payload.get("Present", 0),
                    "absent": payload.get("Absent", 0),
                },
                "vote_date": vote_date,
                "citation": f"{branch} Roll Call #{num} ({court}th General Court)",
                "jurisdiction": "MA",
                "as_of_date": as_of,
            },
        )

    def render_artifact_path(self, req, fetched):
        return self.artifact_path_template.format(
            general_court=fetched.extra_meta.get("general_court", "0"),
            branch=fetched.extra_meta.get("branch", "?"),
            roll_call_number=fetched.extra_meta.get("roll_call_number", "0"),
        )

    def build_frontmatter(self, req, fetched) -> dict[str, Any]:
        fm = super().build_frontmatter(req, fetched)
        for key in ("general_court", "branch", "roll_call_number", "tally", "vote_date"):
            if key in fetched.extra_meta:
                fm[key] = fetched.extra_meta[key]
        return fm


register_adapter(RollCall())
```

- [ ] **Step 4: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k roll_call -v`

Expected: 6 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/sources/adapters/malegislature.py backend/tests/integration/test_malegislature_adapters.py
git commit -m "$(cat <<'EOF'
malegis: add roll-call adapter

Identifier accepts full <court>/<branch>/<num>, short <H|S>/<num>, and
RollCall URL forms. Body is a tally header + member vote table.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Committee-vote adapter

**Files:**
- Modify: `backend/sources/adapters/malegislature.py`
- Test:   `backend/tests/integration/test_malegislature_adapters.py`

Committee votes are scoped to `(general_court, committee_code, document_number)` and return an array of vote records. We render a header per vote followed by a member table.

- [ ] **Step 1: Write the failing identifier + render + path tests**

```python
# ── Committee-vote adapter ────────────────────────────────────────

def test_committee_vote_terse():
    from sources.adapters.malegislature import _parse_committee_vote_identifier
    p = _parse_committee_vote_identifier("J10/H4038")
    assert p == {"general_court": None, "committee_code": "J10", "document_number": "H4038"}


def test_committee_vote_with_court():
    from sources.adapters.malegislature import _parse_committee_vote_identifier
    p = _parse_committee_vote_identifier("193/J10/H4038")
    assert p == {"general_court": 193, "committee_code": "J10", "document_number": "H4038"}


def test_committee_vote_invalid():
    from sources.adapters.malegislature import _parse_committee_vote_identifier
    with pytest.raises(RuntimeError):
        _parse_committee_vote_identifier("nope")


_COMMITTEE_VOTE_FIXTURE = [
    {
        "Action": "Favorable",
        "Date": "2024-02-10T14:00:00",
        "Yeas": 8,
        "Nays": 2,
        "Members": [
            {"Name": "Alice", "Vote": "Yea"},
            {"Name": "Bob", "Vote": "Nay"},
        ],
    },
]


def test_render_committee_vote_body():
    from sources.adapters.malegislature import _render_committee_vote_body
    body = _render_committee_vote_body(
        _COMMITTEE_VOTE_FIXTURE,
        committee_code="J10",
        document_number="H4038",
        general_court=193,
    )
    assert body.startswith("# Committee J10 vote on H4038")
    assert "Favorable" in body
    assert "Yea: 8" in body
    assert "| Alice | Yea |" in body


def test_committee_vote_adapter_path():
    from sources.adapters.malegislature import CommitteeVote
    a = CommitteeVote()
    f = _StubFetched(extra_meta={
        "general_court": 193, "committee_code": "J10", "document_number": "H4038",
    })
    assert a.render_artifact_path(_StubReq(), f) == \
        "mass-committee-votes/court-193/J10/H4038.md"
```

- [ ] **Step 2: Run — expected to fail**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k committee_vote -v`

Expected: ImportError.

- [ ] **Step 3: Implement parser, renderer, and CommitteeVote adapter**

Append:

```python
# ── Committee-vote parser + renderer + adapter ────────────────────

_CV_TERSE_RE = re.compile(
    r"^\s*(?P<committee>[A-Z][A-Z0-9]+)\s*/\s*(?P<doc>[HS]D?\d+)\s*$",
    re.IGNORECASE,
)
_CV_WITH_COURT_RE = re.compile(
    r"^\s*(?P<court>\d+)\s*/\s*(?P<committee>[A-Z][A-Z0-9]+)\s*/\s*(?P<doc>[HS]D?\d+)\s*$",
    re.IGNORECASE,
)


def _parse_committee_vote_identifier(identifier: str) -> dict[str, Any]:
    s = (identifier or "").strip()
    if not s:
        raise RuntimeError("malegis: empty identifier")
    m = _CV_WITH_COURT_RE.match(s)
    if m:
        return {
            "general_court": int(m.group("court")),
            "committee_code": m.group("committee").upper(),
            "document_number": m.group("doc").upper(),
        }
    m = _CV_TERSE_RE.match(s)
    if m:
        return {
            "general_court": None,
            "committee_code": m.group("committee").upper(),
            "document_number": m.group("doc").upper(),
        }
    raise RuntimeError(
        f"malegis/committee-vote: cannot parse identifier {identifier!r}; "
        f"expected '<committee>/<doc>' or '<court>/<committee>/<doc>'"
    )


def _render_committee_vote_body(
    votes: list[dict[str, Any]],
    *,
    committee_code: str,
    document_number: str,
    general_court: int,
) -> str:
    out: list[str] = []
    out.append(f"# Committee {committee_code} vote on {document_number}")
    out.append("")
    out.append(f"**Court:** {general_court}")
    out.append("")
    if not votes:
        out.append("_No votes recorded._")
        return "\n".join(out).rstrip() + "\n"

    for v in votes:
        action = v.get("Action") or "?"
        date_ = (v.get("Date") or "").strip()
        yeas = v.get("Yeas", 0)
        nays = v.get("Nays", 0)
        members = v.get("Members") or []
        out.append(f"## {action} — {date_}".rstrip(" —"))
        out.append("")
        out.append(f"**Tally:** Yea: {yeas} · Nay: {nays}")
        out.append("")
        if members:
            out.append("| Member | Vote |")
            out.append("|---|---|")
            for mem in members:
                out.append(f"| {mem.get('Name', '?')} | {mem.get('Vote', '?')} |")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


class CommitteeVote(_MALegisBaseAdapter):
    source_type = "malegis/committee-vote"
    display_name = "MA committee vote"
    artifact_path_template = "mass-committee-votes/court-{general_court}/{committee_code}/{document_number}.md"
    extractor_strategy = "noop"

    async def fetch(self, req: IngestRequest) -> FetchedContent:
        parts = _parse_committee_vote_identifier(req.identifier)
        court = (req.extras or {}).get("general_court") or parts["general_court"]
        if court is None:
            court = await _current_court()
        court = int(court)
        committee = parts["committee_code"]
        doc = parts["document_number"]
        url = (
            f"{_API_BASE}/GeneralCourts/{court}/Committees/{committee}"
            f"/Documents/{doc}/CommitteeVotes"
        )
        payload = await _http_get_json(url)
        if not isinstance(payload, list):
            payload = [payload] if payload else []

        body = _render_committee_vote_body(
            payload,
            committee_code=committee,
            document_number=doc,
            general_court=court,
        )
        as_of = datetime.now(timezone.utc).date().isoformat()
        # Pull the most recent action + date for frontmatter convenience.
        last_action = ""
        last_date = ""
        last_tally = {"yea": 0, "nay": 0}
        if payload:
            v = payload[-1]
            last_action = v.get("Action") or ""
            last_date = _short_date(v.get("Date") or "")
            last_tally = {"yea": v.get("Yeas", 0), "nay": v.get("Nays", 0)}
        return FetchedContent(
            text=body,
            title=f"Committee {committee} vote on {doc}",
            author_or_publisher="Massachusetts General Court",
            url=f"{_SITE_BASE}/Bills/{court}/{doc}",
            published_at=last_date or as_of,
            extra_meta={
                "general_court": court,
                "committee_code": committee,
                "document_number": doc,
                "action": last_action,
                "tally": last_tally,
                "citation": f"{committee} vote on {doc} ({court}th General Court)",
                "jurisdiction": "MA",
                "as_of_date": as_of,
            },
        )

    def render_artifact_path(self, req, fetched):
        return self.artifact_path_template.format(
            general_court=fetched.extra_meta.get("general_court", "0"),
            committee_code=fetched.extra_meta.get("committee_code", "?"),
            document_number=fetched.extra_meta.get("document_number", "?"),
        )

    def build_frontmatter(self, req, fetched) -> dict[str, Any]:
        fm = super().build_frontmatter(req, fetched)
        for key in ("general_court", "committee_code", "document_number", "action", "tally"):
            if key in fetched.extra_meta:
                fm[key] = fetched.extra_meta[key]
        return fm


register_adapter(CommitteeVote())
```

- [ ] **Step 4: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -k committee_vote -v`

Expected: 5 tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add backend/sources/adapters/malegislature.py backend/tests/integration/test_malegislature_adapters.py
git commit -m "$(cat <<'EOF'
malegis: add committee-vote adapter

CommitteeVotes endpoint returns an array of vote records per
(committee, doc); render each as a section with action, tally, and
member-by-member table.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Wire registration, bucket aliases, version bump

**Files:**
- Modify: `backend/sources/adapters/__init__.py`
- Modify: `frontend/package.json`
- Test:   `backend/tests/integration/test_malegislature_adapters.py`

Add the package import so the adapters register at backend startup, add an end-to-end registration sanity test, and bump the version.

- [ ] **Step 1: Write the failing all-adapters-registered test**

Append to `test_malegislature_adapters.py`:

```python
# ── End-to-end registration ───────────────────────────────────────

def test_all_seven_adapters_registered():
    from sources import adapters  # noqa: F401
    from sources.registry import list_adapters
    types = {a["source_type"] for a in list_adapters()}
    expected = {
        "malegis/general-law-section",
        "malegis/general-law-chapter",
        "malegis/session-law",
        "malegis/bill",
        "malegis/hearing",
        "malegis/roll-call",
        "malegis/committee-vote",
    }
    missing = expected - types
    assert not missing, f"Missing adapters: {missing}"


def test_bucket_aliases_resolve_all():
    from sources import adapters  # noqa: F401
    from sources.registry import resolve_by_bucket
    for alias in ("malegis", "mass", "malaw"):
        types = set(resolve_by_bucket(alias))
        assert "malegis/general-law-section" in types
        assert "malegis/bill" in types
```

- [ ] **Step 2: Run — expected to fail**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py::test_all_seven_adapters_registered -v`

Expected: FAIL — `malegislature` is not yet imported by `adapters/__init__.py`.

(Until the import is added, the per-adapter tests above import the module directly, so they pass; this end-to-end test is what catches the missing wiring.)

- [ ] **Step 3: Add the import to `adapters/__init__.py`**

Edit `backend/sources/adapters/__init__.py`:

```python
"""Built-in source adapters.

Importing this package registers every adapter listed below. Add
new adapters by dropping a file in this folder and adding the
import here.
"""
from sources.adapters import youtube       # noqa: F401  (registers YouTube adapters)
from sources.adapters import blog          # noqa: F401  (registers Blog adapters)
from sources.adapters import pdf           # noqa: F401  (registers PDF adapters)
from sources.adapters import web           # noqa: F401  (registers Web adapters)
from sources.adapters import forum         # noqa: F401  (registers Forum adapters)
from sources.adapters import podcast       # noqa: F401  (registers Podcast adapter)
from sources.adapters import github        # noqa: F401  (registers GitHub adapters)
from sources.adapters import cfr           # noqa: F401  (registers CFR adapters)
from sources.adapters import malegislature # noqa: F401  (registers MA Legislature adapters)
```

- [ ] **Step 4: Run — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_adapters.py -v`

Expected: all tests pass (registration + bucket-alias resolution included).

- [ ] **Step 5: Bump frontend version**

Edit `frontend/package.json`. Find the version line and change it:

```json
  "version": "2026.05.08.H2",
```

(If the file already shows H2 from a separate ship today, use H3, etc.)

- [ ] **Step 6: Run the full integration suite**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ -v`

Expected: all tests pass. No regressions in the existing 6 + new malegis suite.

- [ ] **Step 7: Commit**

```bash
cd /home/pan/pantheon
git add backend/sources/adapters/__init__.py frontend/package.json backend/tests/integration/test_malegislature_adapters.py
git commit -m "$(cat <<'EOF'
malegis: register all 7 adapters + bump version

Add the malegislature import to adapters/__init__.py so the registry
picks up GeneralLawSection, GeneralLawChapter, SessionLaw, Bill,
Hearing, RollCall, and CommitteeVote at backend startup.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Live API smoke tests (gated)

**Files:**
- Create: `backend/tests/integration/test_malegislature_live.py`

These tests hit the real malegislature.gov API and are skipped unless `MALEGIS_LIVE=1` is set. They're our canary that the API hasn't changed shape on us.

- [ ] **Step 1: Write the live test file**

```python
# backend/tests/integration/test_malegislature_live.py
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
```

(Roll-call and committee-vote live tests are skipped here — both depend on identifiers we don't have a stable known-good for. Add later if needed.)

- [ ] **Step 2: Run the offline suite to confirm nothing regressed**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ -v`

Expected: all offline tests pass; live tests SKIPPED.

- [ ] **Step 3: Optional — run live tests once locally**

Run: `cd /home/pan/pantheon/backend && MALEGIS_LIVE=1 ~/pantheon/.venv/bin/python -m pytest tests/integration/test_malegislature_live.py -v`

Expected: 5 live tests pass (or fail with a useful diff if the API has changed).

- [ ] **Step 4: Commit**

```bash
cd /home/pan/pantheon
git add backend/tests/integration/test_malegislature_live.py
git commit -m "$(cat <<'EOF'
malegis: add live API smoke tests (gated by MALEGIS_LIVE=1)

One canary per adapter where a stable known-good identifier exists
(section, chapter, session law, bill, hearing). Skipped by default so
CI/integration runs don't hit malegislature.gov.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Final verification

- [ ] **Run the full integration suite**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ -v
```

All offline tests pass; live tests skipped without `MALEGIS_LIVE=1`.

- [ ] **Verify adapter count from a Python REPL**

```bash
cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -c "
from sources import adapters
from sources.registry import list_adapters
ms = sorted(a['source_type'] for a in list_adapters() if a['source_type'].startswith('malegis/'))
for s in ms: print(s)
print(f'Total: {len(ms)}')
"
```

Expected output: 7 lines listing the malegis source types, then `Total: 7`.

- [ ] **Tell the user the rebuild command**

```bash
cd ~/pantheon && git pull
./stop.sh && pkill -f "uvicorn main:app" 2>/dev/null
find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
./start.sh && sleep 3 && curl -s http://localhost:8000/api/health
```

The version string at `/api/health` should match the bumped `frontend/package.json` version.

---

## Follow-up notes (out of scope for this plan)

**`mgl_citations` → graph edges.** The spec mentioned an end-to-end test
that ingests a bill citing an MGL section and verifies a cross-reference
graph edge appears after `index_artifact`. This plan populates the
`mgl_citations` frontmatter field on bills and session laws (verified by
unit tests in Tasks 4 and 5), but the file_indexer's typed-topics graph
branch does not yet know about `mgl_citations` — turning those entries
into actual graph edges to MGL artifacts requires a focused change to
`backend/memory/file_indexer.py::_index_typed_topics_to_graph`. That
should be its own brief plan once the adapters are live and we can
verify the frontmatter shape against real ingested data.

**Reddit-style OAuth gating** is irrelevant here — the malegislature.gov
API is unauthenticated.

**Bulk listing skills** ("ingest every chapter of Part I") are
orchestration concerns. A future skill can iterate `/Parts/{code}/Chapters`
and call `ingest_source` per chapter.
