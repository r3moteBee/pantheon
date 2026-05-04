"""Source-adapter registry + the canonical ingest() entry point.

Adapters self-register at import time via register_adapter(). The
registry knows how to:
  - look up an adapter by source_type or by bucket alias
  - run the full ingest pipeline (fetch -> save -> index -> graph)
  - report what's available for UIs / agent tool descriptions
"""
from __future__ import annotations

import logging
from typing import Any

from sources.base import (
    AdapterResult,
    FetchedContent,
    IngestRequest,
    SourceAdapter,
)

logger = logging.getLogger(__name__)


_ADAPTERS: dict[str, SourceAdapter] = {}
_BUCKET_INDEX: dict[str, list[str]] = {}  # alias -> [source_type, ...]


def register_adapter(adapter: SourceAdapter) -> None:
    """Register an adapter. Idempotent — re-registering the same
    source_type replaces the previous one (useful for hot-reload)."""
    if not adapter.source_type:
        raise ValueError("Adapter is missing source_type")
    _ADAPTERS[adapter.source_type] = adapter
    for alias in (adapter.bucket_aliases or ()):
        _BUCKET_INDEX.setdefault(alias, []).append(adapter.source_type)
    logger.info("Registered source adapter: %s (%s)",
                adapter.source_type, adapter.display_name)


def get_adapter(source_type: str) -> SourceAdapter | None:
    """Look up an adapter by exact source_type."""
    return _ADAPTERS.get(source_type)


def list_adapters() -> list[dict[str, Any]]:
    """List every registered adapter — used by /api/sources/adapters
    for the UI and by the agent's tool description so it knows
    which source types are available."""
    return [
        {
            "source_type": a.source_type,
            "display_name": a.display_name,
            "bucket_aliases": list(a.bucket_aliases or ()),
            "requires_mcp": list(a.requires_mcp or ()),
        }
        for a in _ADAPTERS.values()
    ]


def resolve_by_bucket(bucket: str) -> list[str]:
    """Map a bucket alias (e.g. 'youtube', 'pdf') to the list of
    registered source_types under it. Used by the heuristic+bucket
    type resolver in skills."""
    return list(_BUCKET_INDEX.get(bucket, []))


# ── The pipeline ──────────────────────────────────────────────────

async def ingest(
    req: IngestRequest,
    *,
    memory_manager: Any | None = None,
    session_id: str | None = None,
) -> AdapterResult:
    """End-to-end ingest for one identifier.

    Steps:
      1. Resolve adapter from req.source_type
      2. adapter.fetch(req) -> FetchedContent
      3. adapter.build_frontmatter(req, fetched) -> dict
      4. Save to artifact store at adapter.render_artifact_path(...)
         with frontmatter + body, collision-safe
      5. Schedule embedding + run FileIndexer.index_artifact so the
         typed-topics frontmatter -> graph branch fires
      6. adapter.post_save_hook(...) for any source-specific cleanup
    """
    from artifacts.store import get_store, project_slug as _ps
    from artifacts import embedder as _emb
    import sqlite3 as _sqlite3
    from pathlib import Path
    import yaml as _yaml

    adapter = get_adapter(req.source_type)
    if adapter is None:
        return AdapterResult(
            artifact_id="", artifact_path="", chars_saved=0,
            graph_nodes_created=0, graph_edges_created=0,
            skipped=True,
            skip_reason=f"no adapter registered for {req.source_type!r}",
        )

    # 1. Fetch
    try:
        fetched: FetchedContent = await adapter.fetch(req)
    except Exception as e:
        logger.exception("adapter %s fetch failed for %s",
                         req.source_type, req.identifier)
        return AdapterResult(
            artifact_id="", artifact_path="", chars_saved=0,
            graph_nodes_created=0, graph_edges_created=0,
            skipped=True, skip_reason=f"fetch_failed: {e}",
        )

    if not fetched.text or not fetched.text.strip():
        return AdapterResult(
            artifact_id="", artifact_path="", chars_saved=0,
            graph_nodes_created=0, graph_edges_created=0,
            skipped=True, skip_reason="empty_content",
        )

    # 2. Build frontmatter
    fm = adapter.build_frontmatter(req, fetched)
    # Drop None values so the YAML stays clean.
    fm = {k: v for k, v in fm.items() if v is not None and v != ""}

    # 2a. Topic extraction (optional, adapter-controlled). Skill can
    #     override the strategy via extras["extractor_strategy"];
    #     extras["skip_extraction"]=True forces noop for this call.
    extraction_status: dict[str, Any] = {}
    if adapter.auto_extract and not req.extras.get("skip_extraction"):
        from sources.extraction import get_extractor
        strategy = req.extras.get("extractor_strategy") or adapter.extractor_strategy
        try:
            extractor = get_extractor(strategy)
            extracted = await extractor.extract(
                fetched.text,
                title=fetched.title,
                source_type=req.source_type,
                max_topics=int(req.extras.get("max_topics") or 12),
                hint=req.extras.get("extraction_hint"),
            )
            if extracted.topics:
                fm["topics"] = extracted.topics
            if extracted.speakers:
                fm["speakers"] = extracted.speakers
            if extracted.claims:
                fm["claims"] = extracted.claims
            # Specialized extractors contribute extra structured
            # fields (specifications, announcement, release, etc.)
            # via frontmatter_additions. Merge at top-level.
            for k, v in (extracted.frontmatter_additions or {}).items():
                if k in {"topics", "speakers", "claims",
                         "source", "extraction_status"}:
                    continue  # don't let extractors clobber canonical fields
                fm[k] = v
            extraction_status = dict(extracted.status or {})
            # Always record the status, success or failure, so reading
            # the artifact later tells you why topics is what it is.
            fm["extraction_status"] = extraction_status
        except Exception as e:
            extraction_status = {
                "strategy": strategy, "ok": False,
                "error": f"unhandled: {type(e).__name__}: {e}",
            }
            fm["extraction_status"] = extraction_status
            logger.warning("Topic extraction (%s) failed for %s: %s",
                           strategy, req.identifier, e)

    fm_yaml = _yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).strip()
    body = f"---\n{fm_yaml}\n---\n\n{fetched.text.strip()}\n"

    # 3. Path resolution + project slug normalization
    raw_path = adapter.render_artifact_path(req, fetched)
    proj = _ps(req.project_id)
    norm = raw_path.lstrip("/").strip()
    if norm and norm != proj and not norm.startswith(f"{proj}/"):
        norm = f"{proj}/{norm}"

    # 4. Save: dedup on canonical path by default. If an artifact
    #    already exists at this path, UPDATE it (creating a new
    #    version) instead of creating a duplicate. Set
    #    extras["force_new"]=True to opt out — useful when you
    #    legitimately want a separate artifact for a new product
    #    version, follow-up post, etc.
    def _candidate(base: str, n: int) -> str:
        if n == 0:
            return base
        p = Path(base)
        if p.suffix:
            return str(p.with_name(f"{p.stem}-{n}{p.suffix}"))
        return f"{base}-{n}"

    force_new = bool(req.extras.get("force_new", False))
    store = get_store()
    a = None
    final_path = norm
    update_mode = False

    if not force_new:
        # See if a previous ingest already lives at the canonical path.
        existing = store.get_by_path(req.project_id, norm)
        if existing:
            try:
                a = store.update(
                    existing["id"],
                    content=body,
                    title=fetched.title or req.identifier,
                    tags=["ingest", req.source_type],
                    edit_summary=f"re-ingest from {req.source_type} ({req.identifier})",
                    edited_by=session_id or "agent",
                )
                final_path = existing["path"]
                update_mode = True
                logger.info("re-ingest updated existing artifact %s at %s",
                            a["id"], final_path)
            except Exception as e:
                logger.warning(
                    "re-ingest update failed for %s: %s; falling through to create",
                    existing.get("path"), e,
                )

    if a is None:
        # No existing artifact (or force_new) — create with the
        # familiar UNIQUE-path collision retry.
        for n in range(50):
            cand = _candidate(norm, n)
            try:
                a = store.create(
                    project_id=req.project_id,
                    path=cand,
                    content=body,
                    content_type="text/markdown",
                    title=fetched.title or req.identifier,
                    tags=["ingest", req.source_type],
                    source={
                        "kind": "source_adapter",
                        "source_type": req.source_type,
                        "identifier": req.identifier,
                        "session_id": session_id or "",
                    },
                    edited_by=session_id or "agent",
                )
                final_path = cand
                break
            except _sqlite3.IntegrityError as e:
                if "UNIQUE" not in str(e) or "path" not in str(e):
                    raise
                continue

    if a is None:
        return AdapterResult(
            artifact_id="", artifact_path=norm, chars_saved=0,
            graph_nodes_created=0, graph_edges_created=0,
            skipped=True,
            skip_reason="path_collision_50_variants_taken",
        )

    # 5. Embed + graph index. The FileIndexer's typed-topics branch
    #    handles the source/topics/speakers frontmatter and produces
    #    the source -> content -> topic / speaker edges.
    _emb.schedule_embed(a["id"], req.project_id)
    nodes = edges = 0
    try:
        if memory_manager:
            stats = await memory_manager.index_artifact(a["id"])
            nodes = (stats or {}).get("entities_extracted", 0)
            # The current indexer doesn't separate nodes/edges in
            # its return; we report nodes only and leave edges=0
            # until the indexer is updated.
    except Exception as e:
        logger.debug("index_artifact for %s failed: %s", a["id"], e)

    # 5b. Cross-artifact similarity pipeline (opt-in per adapter).
    if adapter.auto_link_similarity:
        try:
            from sources.similarity import link_artifact_topics
            sim = await link_artifact_topics(
                a["id"], project_id=req.project_id,
                memory_manager=memory_manager,
            )
            edges = sim.edges_added
            # Surface counts back to the caller via extra.
            extra_sim = {
                "similarity_edges_added": sim.edges_added,
                "similarity_topics_processed": sim.topics_processed,
                "merge_proposals_queued": sim.proposals_queued,
            }
        except Exception as e:
            logger.warning("auto_link_similarity failed for %s: %s", a["id"], e)
            extra_sim = {"similarity_error": str(e)}
    else:
        extra_sim = {}

    # 6. Post-save hook
    result = AdapterResult(
        artifact_id=a["id"],
        artifact_path=a["path"],
        chars_saved=len(fetched.text),
        graph_nodes_created=nodes,
        graph_edges_created=edges,
        extra={
            "final_path": final_path,
            "update_mode": update_mode,
            **extra_sim,
            "extraction_status": extraction_status,
        },
    )
    try:
        await adapter.post_save_hook(req, result)
    except Exception as e:
        logger.debug("post_save_hook for %s failed: %s",
                     req.source_type, e)
    return result



async def batch_ingest(
    reqs: list[IngestRequest],
    *,
    memory_manager: Any | None = None,
    session_id: str | None = None,
    stop_on_error: bool = False,
) -> list[AdapterResult]:
    """Run ingest() over a list of IngestRequests with per-item
    failure isolation.

    Default behavior: a single item failing (fetch error, no
    adapter, MCP timeout, etc.) records a skipped AdapterResult
    for that item and continues with the rest. The caller gets a
    full list back, one entry per request, and can render a
    success/skip/fail breakdown without losing partial progress.

    Pass stop_on_error=True only if the caller really wants
    abort-on-first-failure semantics.
    """
    results: list[AdapterResult] = []
    for req in reqs:
        try:
            r = await ingest(req, memory_manager=memory_manager, session_id=session_id)
        except Exception as e:
            logger.exception("batch_ingest: %s/%s raised; recording skip",
                             req.source_type, req.identifier)
            r = AdapterResult(
                artifact_id="", artifact_path="", chars_saved=0,
                graph_nodes_created=0, graph_edges_created=0,
                skipped=True, skip_reason=f"unhandled: {e}",
            )
        results.append(r)
        if stop_on_error and r.skipped:
            break
    return results
