# Phase 2 — Publisher/Subscriber artifact model (design notes)

**Date:** 2026-05-17
**Status:** parking lot — not yet brainstormed in depth, not yet a spec
**Predecessor:** `2026-05-17-artifacts-folder-tree-and-move-design.md` (Phase 1)

## Purpose of this doc

This is a **placeholder for the Phase 2 brainstorm**. It captures:

1. The motivation and the chosen model.
2. The open usage decisions that need answers before any code.

When Phase 1 ships, the next step is to run a proper brainstorm against this doc and produce a full spec from it.

## Motivation

Projects in Pantheon are memory boundaries (graph, semantic, and episodic memory are all `project_id`-scoped). Today every artifact belongs to exactly one project, which means reference material like SEC filings, eCFR sections, datasheets, or research papers either gets re-ingested per project (3× storage, 3× embedding cost, 3× extraction calls) or stays trapped in one project where other projects can't reach it during recall.

Common use case: a user creates a domain-focused project (e.g. "SEC Filings", "Real Estate Trends", "Cloud Provider Benchmarks") whose role is to ingest and synthesize a domain. Downstream project work in other contexts (a specific deal analysis, a coding project, a strategy doc) benefits from reaching into that synthesized library.

## Chosen model: publisher/subscriber

**Asymmetric authority.** One canonical owner ("publisher") of each artifact; other projects ("subscribers") can read but not modify it.

**Why this shape:** the symmetric "shared/canonical" alternative requires solving concurrent-edit semantics, peer ownership of paths, and ambiguous delete propagation. Publisher/subscriber sidesteps all of those.

**Rough mental model:**
- Like git remotes: one source of truth, others pull.
- Like RSS: a feed has a publisher; consumers subscribe.
- Subscribers can always **fork** a published artifact into their own project via the existing duplicate-copy operation (Phase 1), at which point it becomes editable in their project.

**Terminology:**
- **Publisher project** — the project that owns canonical artifacts.
- **Subscriber project** — a project that has subscribed to a publisher.
- **Subscription** — a directed relationship from subscriber → publisher with a defined scope.
- **Fork** — creating an editable, divergent copy of a published artifact in the subscriber's project (this is just Phase 1's duplicate-copy applied across the publisher/subscriber boundary).

> Note: "researcher" and "analyst" came up during the Phase 1 conversation as natural use cases (a user creates a researcher or analyst project that publishes synthesized findings). Those are descriptive use-case labels, not type names. The model is publisher/subscriber; "researcher" / "analyst" / "library curator" / "domain expert" are all roles a publisher project might play.

## Sketch of the data model

```sql
CREATE TABLE subscriptions (
  subscriber_project_id TEXT NOT NULL,
  publisher_project_id  TEXT NOT NULL,
  scope                 TEXT NOT NULL,  -- 'all' initially; later 'folder:<path>' or 'tag:<name>'
  created_at            DATETIME NOT NULL,
  PRIMARY KEY (subscriber_project_id, publisher_project_id, scope)
);
```

Read queries (list, search, recall) get a project predicate that unions `project_id = :p` with `project_id IN (SELECT publisher_project_id FROM subscriptions WHERE subscriber_project_id = :p)`.

Writes (`save_to_artifact`, edits, deletes, moves) stay strictly scoped to `project_id = :p` — subscribers can never mutate canonical state.

## Open decisions (the usage questions Phase 2 must answer)

These are the decisions that need to be made before Phase 2 can be specified. They are roughly in dependency order.

### 1. Subscription granularity

What can be subscribed to?

- **Whole publisher project (simplest).** A subscription pulls in everything in the publisher's library.
- **A folder within a publisher.** Subscribe to `SEC Filings/10-Ks/` but not `SEC Filings/internal-notes/`.
- **A tag.** Subscribe to anything tagged `regulations` in any publisher.
- **Individual artifacts.** Pin specific artifacts to a subscriber.

**Trade-offs:** finer granularity → more useful, more UI, more query complexity. Whole-project is shippable in days; tag-based subscriptions could require rebuilding tag indexes.

**Recommended start:** whole-project. Add folder/tag filtering later if friction shows up.

### 2. Annotations on published artifacts

Subscribers can't edit the canonical artifact. Can they annotate it?

- **No annotations.** If you want to add notes, fork the artifact (duplicate into your project). Simplest.
- **Per-subscriber annotation layer.** A separate row keyed on `(subscriber_project_id, canonical_artifact_id)` holds notes/highlights/tags-by-subscriber. Doesn't touch the canonical content. Cleaner UX but adds a model.

**Trade-offs:** no-annotation forces forking, which works but creates many fork copies and dilutes the canonical store. Annotation layer preserves a single canonical source but requires the artifact UI to render two layers (publisher content + subscriber-local notes).

### 3. Re-ingest collision

User triggers `ingest_source` in a subscriber project for a URL/video already published. Behavior?

- **Skip with note.** "This artifact is already available via your subscription to <publisher>. Open it there?" Cheapest; most predictable.
- **Force into subscriber.** New copy in the subscriber. Defeats the dedup purpose.
- **Route as new version on publisher.** New version appended to publisher's `artifact_versions`. Magical and surprising; could leak subscriber's context into publisher.

**Recommended:** skip with note. Optionally surface a "fork into my project" button.

### 4. Memory recall: how do subscribed artifacts surface?

When a subscriber project recalls something whose match lives in a publisher's library:

- **Provenance tag.** Result tagged `[from publisher: SEC Filings]` so the user sees the source.
- **Identical surfacing.** No distinction between owned and subscribed in recall output. Cleanest UX, but obscures source.
- **Configurable.** Per-subscription flag: "include in recall" yes/no.

**Recommended:** provenance tag, always-on inclusion. (Filtering can come later.)

### 5. Similarity / topic-merge across publisher boundaries

The similarity pipeline runs `SEMANTICALLY_SIMILAR_TO` edges between artifact topics. With subscriptions, should it traverse publisher-subscriber boundaries?

- **Yes.** Subscriber's "REIT cap rate" links to publisher's "capitalization rate". Cross-project topic clustering becomes possible.
- **No.** Subscribed content's graph stays parallel to subscriber's own — siloed.

**Cross-project edges** are doable but need a flag (`cross_project: true`) so they're traversable but distinguishable from intra-project edges.

**Recommended:** yes, with the flag. Topic-merge proposals across boundaries should be reviewable, not auto-applied.

### 6. Delete and unsubscribe semantics

- **Publisher deletes a published artifact.** All subscribers immediately lose access. Show a "removed by publisher" tombstone? Silent disappearance? Quietly drop with a notification?
- **Subscriber unsubscribes.** All publisher artifacts vanish from subscriber's lists, recall, search. Their graph references (if any cross-boundary edges exist) get the cross-project flag cleared or the edges purged.

**Recommended:** silent disappearance on publisher delete (with the artifact's existence as memory if the subscriber forked it before). Unsubscribe is a clean break.

### 7. Discovery UI

How do subscribers find what's available to subscribe to?

- **Project settings panel.** "Subscriptions" section listing other projects in the system; checkbox to subscribe. Smallest UI.
- **Catalog view.** A browse-only view into another project's artifact list, "Subscribe" button visible.
- **Both.** Quick subscribe from settings; deep browse via catalog.

**Recommended:** start with the simple settings-panel list. Catalog can come later when there are enough publishers to warrant browsing.

### 8. Skill / persona / project-export implications

- **Skills are project-scoped.** Does a subscriber inherit the publisher's skills, or only their artifacts?
- **Persona context.** When a subscriber's persona recalls a publisher's artifact, whose framing dominates? (The agent has one persona at a time; the artifact is data, not behavior.)
- **Project export.** Today export bundles the project's artifacts. With subscriptions: export only owned, or include subscribed (as references / as snapshots)? On import, do subscriptions need to be re-resolved?

**Initial leaning:** Skills do **not** propagate (subscription is about artifacts, not behavior). Persona behavior is unaffected (the agent's persona is set by the active project). Export bundles owned artifacts only; subscription references export as a list of (publisher_id, scope) pairs which the importing system re-resolves if those publisher projects exist.

### 9. Agent-tool semantics

- `save_to_artifact` — always saves to the current project (never to a publisher). Subscriber projects cannot write into a publisher.
- `read_artifact` — should see subscribed artifacts in addition to owned.
- `list_artifacts` — same.
- New tool? `subscribe_to(publisher_project_id, scope='all')` — agent-callable, with confirmation. Or keep subscription as UI-only for now.

**Recommended:** read tools include subscribed artifacts; writes stay scoped. Subscription management is UI-only in v1 (don't expose to the agent until the human has lived with it).

### 10. Migration / install upgrade path

When this ships, existing single-user installs already have several projects. Nothing changes by default — everyone starts with zero subscriptions. The migration is just creating the `subscriptions` table; the rest is opt-in.

## Suggested phasing for Phase 2 itself

When the Phase 2 brainstorm happens, consider sub-phasing:

- **2a.** Subscriptions at project granularity, read-only inclusion in `list_artifacts` / `read_artifact` / `recall`, provenance tags, discovery via settings panel. No annotations, no cross-boundary similarity.
- **2b.** Cross-boundary similarity edges + topic-merge proposals.
- **2c.** Annotation layer (if friction shows up — may turn out to be unneeded).
- **2d.** Folder/tag-scoped subscriptions.

## Next step

When Phase 1 is shipped and stable, run the brainstorming skill against this doc to produce a real Phase 2 spec. The skill should pick up open questions 1–9 in order; question 10 is a fait accompli.
