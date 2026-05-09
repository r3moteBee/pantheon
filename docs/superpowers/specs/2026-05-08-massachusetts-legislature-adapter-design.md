# Massachusetts Legislature source adapter — design

**Date:** 2026-05-08
**Status:** approved (brainstorm)
**Related:** `backend/sources/adapters/cfr.py` (the structural model)

## Goal

Add a source-adapter family for the Massachusetts Legislature Public API
(`malegislature.gov/api`, swagger at `/api/swagger/v1/swagger.json`),
parallel to the existing eCFR adapter. Cover four content categories
through seven adapters so that bills, codified laws, session laws, and
process records all become first-class artifacts in Pantheon's store and
graph.

The API requires no authentication, returns JSON, and exposes:

- `GeneralLawParts`, `GeneralLawChapters`, `GeneralLawSections` — the
  codified Massachusetts General Laws (MGL) hierarchy.
- `Documents` (bills/dockets), scoped to a `GeneralCourt`, with
  history actions, amendments, roll calls, and committee
  recommendations attached.
- `SessionLaws` — annual Acts and Resolves, identified by `(year,
  chapterNumber)`, including the originating bill.
- `Hearings`, `RollCalls`, `CommitteeVotes`, `SpecialEvents` —
  legislative process records.

## Mechanism

A single mechanism prefix `malegis` (bucket aliases: `mass`, `malaw`).
All adapters live in one file `backend/sources/adapters/malegislature.py`
(~1000–1400 lines, mirroring `cfr.py`'s convention of "all genres for
one mechanism in one file"). All seven use `httpx`, no MCP required.

### Adapter list

| `source_type` | Returns | Body shape | Default extractor |
|---|---|---|---|
| `malegis/general-law-section` | one MGL section | rich text | `llm_default` |
| `malegis/general-law-chapter` | one MGL chapter (sections concatenated) | rich text | `llm_default` |
| `malegis/session-law` | one act/resolve from a year | rich text + provenance | `llm_default` |
| `malegis/bill` | one bill/docket | text + sponsors + history + amendments | `llm_default` |
| `malegis/hearing` | one hearing event | structured metadata summary | `noop` |
| `malegis/roll-call` | one floor roll call | tally summary | `noop` |
| `malegis/committee-vote` | one committee vote on a doc | tally summary | `noop` |

`auto_link_similarity = True` for all adapters, consistent with CFR.
Process records use `noop` extraction because their bodies are vote
tallies and metadata — topics come from the linked bill, not the event
itself.

## Identifier formats

Parsing is case-insensitive and tolerant of underscore/hyphen
substitution, mirroring CFR's `_CITATION_RE`/`_URL_RE`/`_SLUG_RE`
pattern. One regex set per adapter family, dispatched by per-genre
`_parse_identifier()` helpers.

| Adapter | Accepted shapes |
|---|---|
| `general-law-section` | `M.G.L. c. 23A § 1`, `MGL 23A § 1`, `Chapter 23A Section 1`, `23A/1`, `https://malegislature.gov/Laws/GeneralLaws/.../Chapter23A/Section1` |
| `general-law-chapter` | `M.G.L. c. 23A`, `Chapter 23A`, `23A`, `https://malegislature.gov/Laws/GeneralLaws/.../Chapter23A` |
| `session-law` | `2024 Chapter 1`, `Chapter 1 of 2024`, `Acts of 2024, Chapter 1`, `2024/1`, `https://malegislature.gov/Laws/SessionLaws/Acts/2024/Chapter1` |
| `bill` | `H4038`, `H.4038`, `S100`, `H4038@193`, `https://malegislature.gov/Bills/193/H4038` |
| `hearing` | `5655`, `https://malegislature.gov/Events/Hearings/Detail/5655` |
| `roll-call` | `193/House/123`, `H/123`, `https://malegislature.gov/RollCall/193/House/123` |
| `committee-vote` | `J10/H4038`, `193/J10/H4038` |

Section codes are alphanumeric (e.g. `3A`, `3B`, …, `3M`) — the
identifier parser must accept letter suffixes, not strip them.

### Current-court resolution

Bills, roll calls, and committee votes are scoped to a `GeneralCourt`
(currently 193, soon 194). A small helper `_current_court()` queries
`/GeneralCourts/Documents` once and caches the highest court number for
the process lifetime — same pattern as CFR's `_TITLES_INDEX` cache. A
caller may pin to a specific court via `extras["general_court"]=193` or
inline `H4038@193` syntax. If the helper can't reach the API at all, it
falls back to the most recent court number it has seen, with a
hard-coded floor of `193` and a logged warning.

## Body rendering per genre

### General Law section

The `Text` field is plain prose with `\r\n` paragraph breaks and
embedded subsection markers like `(a)`, `(b)`. Render as markdown with
the section heading as H2:

```
## Section {Code}. {Name}

{Text, with \r\n collapsed to paragraph breaks}
```

### General Law chapter

Fetch chapter detail (`/Chapters/{code}`), then iterate the embedded
`Sections[]` array (one HTTP call per section — chapters typically have
5–80 sections). Render as:

```
# Chapter {code} — {name}

(part: {part_code} — {part_name})

## Section {code}. {name}
{text}

## Section {code}. {name}
{text}
…
```

A safety knob `extras["max_sections"]` (default 200) short-circuits
pathologically large chapters so a single ingest doesn't fan out into
thousands of HTTP calls.

### Session law

`ChapterText` is HTML using `<p>`, `<em>`, and anchor tags pointing at
internal `/Laws/GeneralLaws/...` paths. Convert with `markdownify`
(already a dependency, used by blog/web adapters). Anchor URLs are
relative — rewrite to absolute `https://malegislature.gov/...` so they
survive copy-paste.

Body:

```
# {Title}

*{Type} of {Year}, Chapter {ChapterNumber}* — {Status}

{markdownified ChapterText}

---

**Origin bill:** {OriginBill.BillNumber} ({OriginBill.Title})
**Primary sponsor:** {OriginBill.PrimarySponsor.Name}
```

### Bill

`DocumentText` is plain prose. Body sections, in order:

```
# {Title}

**{BillNumber}** · {LegislationTypeName} · {GeneralCourtNumber}th General Court

> {Pinslip}

## Bill text
{DocumentText}

## Sponsors
- Primary: {PrimarySponsor.Name}
- Cosponsors: {names…}
- Joint sponsor: {JointSponsor.Name if present}

## Committee recommendations
- {Committee.CommitteeCode}: {Action}
…

## Amendments
- {AmendmentNumber}: {Title}  (only if Amendments[] is non-empty)
…

## Roll calls
- Branch {b} #{n}  (only if RollCalls[] is non-empty)
…
```

History actions (the `BillHistory` URL) require a secondary HTTP call
and are bulky. Off by default; opt in with
`extras["include_history"]=True`. When included, append a "## History"
section with date + chamber + action rows.

### Hearing

Body is a structured summary:

```
# {Name}

**Status:** {Status}
**Date:** {EventDate}  (start {StartTime})
**Host committee:** {HearingHost.CommitteeCode}
**Location:** {Location.LocationName}, {Location.City}, {Location.State}

## Description
{Description}

## Agenda  (only if HearingAgendas[] non-empty)
- {agenda items…}
```

### Roll call

Fetch `/GeneralCourts/{c}/Branches/{b}/RollCalls/{n}`, render as:

```
# {Branch} Roll Call #{n} — {date}

**Court:** {GeneralCourtNumber} · **Result:** {tally summary}

| Member | Vote |
|---|---|
| … | Yea/Nay/Present/Absent |
```

### Committee vote

Render committee + document + action header followed by per-member vote
rows, similar layout to roll call.

## Cross-reference extraction (`mgl_citations`)

Bills and session laws routinely cite General Laws inline — e.g. "section 9
of chapter 40A of the General Laws". A small regex pass
`_extract_mgl_citations()` captures these as `{chapter, section}` pairs
and writes them into `extra_meta["mgl_citations"]`. The frontmatter
surfaces them so the typed-topics graph extractor can build cross-artifact
edges from a bill to the law sections it modifies.

Patterns:

- `chapter <N><A?>(?: of the General Laws)?`
- `section <N><A?> of chapter <N><A?>`
- `M.G.L. c. <N><A?>(?: § <N><A?>)?`

False positives (a bill that mentions "chapter 5" of some town bylaw)
are acceptable — the graph link will still resolve to the corresponding
MGL chapter artifact, and merge proposals can be reviewed normally.

## Path templates

```
mass-laws/chapter-{chapter_code}/section-{section_code}.md
mass-laws/chapter-{chapter_code}/index.md
mass-session-laws/{year}/chapter-{chapter_number}.md
mass-bills/court-{general_court}/{bill_number}.md
mass-hearings/{event_date}/event-{event_id}.md
mass-roll-calls/court-{general_court}/{branch}/rc-{roll_call_number}.md
mass-committee-votes/court-{general_court}/{committee_code}/{document_number}.md
```

`event_date` is `YYYY-MM-DD` parsed from `EventDate`; falls back to
`unknown-date` when missing. All paths are then prefixed with the
project slug by the registry's existing normalization.

## Frontmatter shape

All adapters extend the canonical typed-topics frontmatter (`source`
block, `topics: []`, `speakers: []`) produced by
`SourceAdapter.build_frontmatter`. Common additions:

- `citation` — human-readable, e.g. `M.G.L. c. 23A § 1` or
  `H.4038 (193rd General Court)`
- `jurisdiction: "MA"`
- `as_of_date` — UTC date of fetch
- `mgl_citations: []` — cross-references when applicable

Per-genre additions:

| Genre | Frontmatter additions |
|---|---|
| general-law-section | `hierarchy: {part, chapter, section}`, `is_repealed: bool` |
| general-law-chapter | `hierarchy: {part, chapter}`, `is_repealed: bool`, `section_count: int` |
| session-law | `year`, `chapter_number`, `act_type`, `approval_type`, `approved_date`, `origin_bill: {number, court, primary_sponsor}` |
| bill | `bill_number`, `docket_number`, `general_court`, `legislation_type`, `primary_sponsor`, `cosponsors: []`, `committee_recommendations: []`, `pinslip` |
| hearing | `event_id`, `event_date`, `host_committee`, `location`, `status` |
| roll-call | `general_court`, `branch`, `roll_call_number`, `tally: {yea, nay, present, absent}`, `vote_date` |
| committee-vote | `committee_code`, `document_number`, `general_court`, `action`, `tally` |

## Edge cases and operational details

1. **Repealed sections** — `IsRepealed: true` returns a thin record with
   minimal `Text`. Save the artifact anyway (with `is_repealed: true`
   and a one-line body) so the citation resolves; readers can filter.
2. **Trailing-slash inconsistency** — the API returns `Details` URLs
   with inconsistent trailing slashes. Normalize before fetching.
3. **HTML anchor rewrite** — session-law anchor URLs are relative;
   rewrite to absolute `https://malegislature.gov/...`.
4. **Future-dated hearings** — the API returns scheduled events whose
   `EventDate` is in the future. Save as-is; on re-ingest the dedup
   mechanism updates.
5. **Court resolution failure** — `_current_court()` falls back to the
   most recent court number seen, then hard-coded `193`, with a logged
   warning.
6. **Connection limits** — chapter-level ingest fans out to N section
   calls. Use `httpx.AsyncClient(limits=httpx.Limits(max_connections=4))`
   to stay polite. Per-call adapters (sections, bills, etc.) don't need
   this.
7. **HTTP timeout** — 60s like CFR; chapters with many sections can
   take a while.
8. **Adapter dedup** — `save_to_artifact`'s canonical-path dedup
   handles re-ingestion: re-fetching the same section updates the
   existing artifact (creating a new version) rather than creating a
   duplicate. This matches CFR behavior.

## Tests

Add `backend/tests/integration/test_malegislature_adapters.py`:

- One identifier-parsing test per adapter (no network). Asserts that
  each accepted shape resolves to the same canonical
  `(genre, parsed_fields)`.
- One live-fetch smoke test per adapter, gated behind a `MALEGIS_LIVE`
  env flag (so CI doesn't hit malegislature.gov on every run). The live
  tests fetch a known-good identifier per genre and assert the artifact
  saves with non-empty body and required frontmatter fields.
- One end-to-end test that ingests a bill that cites an MGL section,
  confirms the `mgl_citations` field is populated, and verifies the
  cross-reference graph edge appears after `index_artifact`.

## Frontend

No frontend changes. Adapter registers automatically; the
`ingest_source` agent tool already handles arbitrary registered source
types.

## Out of scope (explicit)

- **Bulk listing skills** — "ingest all chapters of Part I" is an
  orchestration concern, not an adapter concern. A future skill can
  enumerate chapters via the `Parts` and `Chapters` endpoints and call
  `ingest_source` per item.
- **SpecialEvents adapter** — the SpecialEvents endpoint exists but is
  rarely populated and overlaps Hearings. Skip for now; revisit if
  there's a real use case.
- **HouseJournals / SenateJournals adapter** — large, daily, and
  primarily procedural. Out of scope for this round.
- **Amendments deep-fetch** — the bill adapter lists amendments by
  number/title from the document payload. A separate
  `malegis/amendment` adapter could fetch full amendment text per
  identifier; defer until a need surfaces.
- **Reports / Leadership / Members** — structural metadata, not
  artifact-shaped. The registry already has a slot for these as graph
  nodes if they ever become useful, but no adapter is needed.

## Versioning

Bump `frontend/package.json` version when shipping (single source of
truth, surfaces at `/api/health`).
