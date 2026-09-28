"""Source ingestion and the topic graph: adapters, ingest, extraction, similarity, merges."""
from __future__ import annotations

from typing import Any
from agent.tools.registry import ToolContext, tool


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "link_topic_similarity",
            "description": (
                "Backfill the cross-artifact similarity pipeline "
                "for already-indexed artifacts. Adds "
                "SEMANTICALLY_SIMILAR_TO edges between topic nodes "
                "whose embeddings cosine-match >= 0.86, and queues "
                "merge proposals for >= 0.92 (proposals are NOT "
                "auto-applied — use merge_topics to review and apply).\n\n"
                "Use this after enabling similarity on an adapter, "
                "after a bulk ingest where you want to make sure "
                "everything is cross-linked, or whenever the user "
                "says \'cross-link existing topics\'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path_prefix": {
                        "type": "string",
                        "description": "Optional artifact path prefix to scope the run (e.g. \'youtube-transcripts/\'). Bare folder names are auto-prefixed with the project slug."
                    },
                    "link_threshold": {
                        "type": "number",
                        "description": "Cosine threshold for SEMANTICALLY_SIMILAR_TO edges. Default 0.86."
                    },
                    "merge_threshold": {
                        "type": "number",
                        "description": "Cosine threshold for queuing merge proposals. Default 0.92."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_merge_proposals",
            "description": (
                "List pending (or all) topic-merge proposals for "
                "the current project. Use to surface merge "
                "candidates for the user to review before any "
                "destructive change happens. Each entry shows the "
                "two labels, their types, the similarity score, "
                "and the proposal id needed for approve/reject."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "enum": ["pending", "approved", "rejected", "merged", "stale", "all"],
                        "description": "Filter by status. \'all\' returns every proposal."
                    },
                    "limit": {"type": "integer", "default": 50}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "approve_merge",
            "description": (
                "Approve and EXECUTE a topic-merge proposal. This "
                "rewrites every edge that touched the deprecated "
                "node to point at the canonical node, then deletes "
                "the deprecated node. Irreversible — only call "
                "when the user has explicitly confirmed in chat. "
                "When in doubt, list proposals first and ask which "
                "to merge.\n\n"
                "If the user doesn\'t specify which label is "
                "canonical, default to the longer one (e.g. \'Dell "
                "Technologies\' over \'Dell\') and say so in your "
                "reply."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"},
                    "canonical_label": {
                        "type": "string",
                        "description": "Which of the two labels survives. Must match exactly one of the proposal\'s node labels."
                    }
                },
                "required": ["proposal_id", "canonical_label"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "reject_merge",
            "description": (
                "Mark a merge proposal as rejected. The two nodes "
                "stay distinct; the SEMANTICALLY_SIMILAR_TO edge "
                "between them remains in the graph. Use when the "
                "user says \'these are different things\' or "
                "\'don\'t merge those\'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "proposal_id": {"type": "string"}
                },
                "required": ["proposal_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "force_merge",
            "description": (
                "Manually merge two topic nodes by label, bypassing "
                "the proposal queue. Use when the user names two "
                "nodes directly (\'merge Dell into Dell "
                "Technologies\') and there\'s no existing "
                "proposal for that pair. Requires explicit user "
                "intent — do NOT call speculatively."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "label_a": {"type": "string"},
                    "label_b": {"type": "string"},
                    "canonical_label": {
                        "type": "string",
                        "description": "Which of the two labels survives. Must equal label_a or label_b."
                    }
                },
                "required": ["label_a", "label_b", "canonical_label"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_source_adapters",
            "description": (
                "List the source adapters registered in the project. "
                "Each entry has source_type (e.g. 'youtube/keynote'), "
                "display_name, bucket aliases, and the MCP tools the "
                "adapter requires. Use this when the user asks 'what "
                "can I ingest' or before constructing an ingest_source "
                "call to confirm the source_type is supported."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ingest_source",
            "description": (
                "Ingest one item through a source adapter: fetch server-side, extract topics, save as "
                "an artifact (re-ingesting the same item updates it), embed and link the graph. Use "
                "list_source_adapters for source_types; batch_ingest_sources for many."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "source_type": {
                        "type": "string",
                        "description": "Canonical source type (e.g. 'youtube/keynote', 'youtube/interview', 'youtube/other'). Must be a registered adapter."
                    },
                    "identifier": {
                        "type": "string",
                        "description": "Source-specific id. For YouTube: video_id (e.g. dQw4w9WgXcQ). For blog: URL. For PDF: file path or URL."
                    },
                    "extras": {
                        "type": "object",
                        "description": "Optional per-call hints. Recognized keys: published (relative string from search like '4 months ago' — adapter parses to ISO), published_at (absolute YYYY-MM-DD; wins over published if both set), retrieved_at, searched_by (the criteria object), extractor_strategy (override default extractor), skip_extraction (bool), max_topics (int). When ingesting YouTube search results, ALWAYS forward each video's 'published' string so artifact paths get a real date instead of unknown-date/.",
                        "additionalProperties": True
                    }
                },
                "required": ["source_type", "identifier"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "batch_ingest_sources",
            "description": (
                "Run ingest_source over a list of items with "
                "per-item failure isolation. Use for the typical "
                "5-10 item ingest run from a search step. A single "
                "fetch failure does not abort the run; failed items "
                "appear in the response with skip_reason set."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "source_type": {"type": "string"},
                                "identifier": {"type": "string"},
                                "extras": {"type": "object", "additionalProperties": True}
                            },
                            "required": ["source_type", "identifier"]
                        },
                        "description": "List of ingest items."
                    }
                },
                "required": ["items"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "extract_topics",
            "description": (
                "Run topic extraction on an existing artifact (or "
                "raw text) and return the structured topics / "
                "speakers / claims without writing them anywhere. "
                "Use when you want to inspect what the extractor "
                "would produce before letting ingest_source commit "
                "the results, or to re-run extraction with a "
                "different strategy after the fact."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "artifact_id": {
                        "type": "string",
                        "description": "Existing artifact id to extract from. Either this or text is required."
                    },
                    "text": {
                        "type": "string",
                        "description": "Raw text to extract from when no artifact exists yet."
                    },
                    "strategy": {
                        "type": "string",
                        "description": "Extractor name. Defaults to 'llm_default'. Use 'noop' to skip extraction (no-op pass-through).",
                        "default": "llm_default"
                    },
                    "max_topics": {
                        "type": "integer",
                        "description": "Cap on returned topics. Default 12.",
                        "default": 12
                    }
                }
            }
        }
    },
]


@tool('list_source_adapters')
async def _tool_list_source_adapters(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    from sources import list_adapters
    adapters = list_adapters()
    if not adapters:
        return "(no source adapters registered)"
    lines = []
    for a in adapters:
        aliases = ", ".join(a["bucket_aliases"]) or "(none)"
        mcp = ", ".join(a["requires_mcp"]) or "(none)"
        lines.append(
            f"- {a['source_type']}: {a['display_name']}\n"
            f"    aliases: {aliases}\n"
            f"    requires MCP: {mcp}"
        )
    return "Registered source adapters:\n" + "\n".join(lines)



@tool('ingest_source')
async def _tool_ingest_source(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    memory_manager = ctx.memory_manager
    session_id = ctx.session_id
    effective_project = ctx.effective_project
    from sources import ingest, IngestRequest
    req = IngestRequest(
        source_type=tool_args.get("source_type") or "",
        identifier=tool_args.get("identifier") or "",
        project_id=effective_project,
        extras=tool_args.get("extras") or {},
    )
    if not req.source_type or not req.identifier:
        return "ingest_source rejected: source_type and identifier are required."
    r = await ingest(
        req, memory_manager=memory_manager, session_id=session_id,
    )
    if r.skipped:
        return (
            f"ingest_source skipped {req.source_type}/{req.identifier!r}: "
            f"{r.skip_reason or 'unknown'}"
        )
    est_status = (r.extra or {}).get("extraction_status") or {}
    est_line = ""
    if est_status:
        if est_status.get("ok"):
            est_line = (
                f"\n  extraction: {est_status.get('strategy')} OK "
                f"({est_status.get('topic_count', 0)} topics, "
                f"{est_status.get('speaker_count', 0)} speakers)"
            )
        else:
            est_line = (
                f"\n  extraction: {est_status.get('strategy')} FAILED "
                f"({est_status.get('error') or '?'})"
            )
    mode_line = ""
    if (r.extra or {}).get("update_mode"):
        mode_line = "\n  mode: UPDATED existing artifact (new version)"
    else:
        mode_line = "\n  mode: CREATED new artifact"
    return (
        f"Ingested {req.source_type}/{req.identifier} -> "
        f"{r.artifact_path}\n"
        f"  artifact_id: {r.artifact_id}\n"
        f"  chars_saved: {r.chars_saved}\n"
        f"  graph_nodes: {r.graph_nodes_created}"
        f"{mode_line}"
        f"{est_line}"
    )



@tool('batch_ingest_sources')
async def _tool_batch_ingest_sources(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    memory_manager = ctx.memory_manager
    session_id = ctx.session_id
    effective_project = ctx.effective_project
    from sources import batch_ingest, IngestRequest
    items = tool_args.get("items") or []
    reqs = [
        IngestRequest(
            source_type=it.get("source_type") or "",
            identifier=it.get("identifier") or "",
            project_id=effective_project,
            extras=it.get("extras") or {},
        )
        for it in items
        if it.get("source_type") and it.get("identifier")
    ]
    if not reqs:
        return "batch_ingest_sources rejected: items list is empty or malformed."
    results = await batch_ingest(
        reqs, memory_manager=memory_manager, session_id=session_id,
    )
    ok = [r for r in results if not r.skipped]
    sk = [r for r in results if r.skipped]
    lines = [
        f"Ingested: {len(ok)}, Skipped: {len(sk)}, Total: {len(results)}",
        "",
        "## Ingested",
    ]
    for r, req in zip(results, reqs):
        if r.skipped:
            continue
        lines.append(
            f"- {req.source_type}/{req.identifier} -> "
            f"{r.artifact_path} ({r.chars_saved} chars, "
            f"{r.graph_nodes_created} nodes)"
        )
    if sk:
        lines.append("")
        lines.append("## Skipped")
        for r, req in zip(results, reqs):
            if not r.skipped:
                continue
            lines.append(
                f"- {req.source_type}/{req.identifier}: {r.skip_reason}"
            )
    return "\n".join(lines)



@tool('extract_topics')
async def _tool_extract_topics(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    from sources.extraction import get_extractor
    from artifacts.store import get_store as _get_art_store_x
    text = tool_args.get("text") or ""
    artifact_id = tool_args.get("artifact_id") or ""
    title = ""
    source_type = ""
    if not text and artifact_id:
        a = _get_art_store_x().get(artifact_id)
        if not a:
            return f"extract_topics: artifact {artifact_id!r} not found."
        text = a.get("content") or ""
        title = a.get("title") or ""
        # Try to read source.type from the existing frontmatter
        # so the LLM sees consistent context.
        import re as _re
        m = _re.match(r"^---\n(.*?)\n---\n", text, _re.DOTALL)
        if m:
            fm_block = m.group(1)
            st_m = _re.search(r"type:\s*[\"\'\s]*([^\"\'\n]+)", fm_block)
            if st_m:
                source_type = st_m.group(1).strip().strip("\"\'")
            text = text[m.end():]
    if not text or not text.strip():
        return "extract_topics: no text provided (pass text= or artifact_id=)."
    extractor = get_extractor(tool_args.get("strategy"))
    extracted = await extractor.extract(
        text,
        title=title,
        source_type=source_type,
        max_topics=int(tool_args.get("max_topics") or 12),
    )
    import json as _json_x
    return (
        f"extractor: {extractor.name}\n"
        f"topics: {len(extracted.topics)}, "
        f"speakers: {len(extracted.speakers)}, "
        f"claims: {len(extracted.claims)}\n\n"
        + _json_x.dumps(
            {
                "topics": extracted.topics,
                "speakers": extracted.speakers,
                "claims": extracted.claims,
            },
            indent=2,
        )
    )



@tool('link_topic_similarity')
async def _tool_link_topic_similarity(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    memory_manager = ctx.memory_manager
    effective_project = ctx.effective_project
    from sources.similarity import backfill, DEFAULT_LINK_THRESHOLD, DEFAULT_MERGE_THRESHOLD
    from artifacts.store import project_slug as _ps_lt
    link_thr = float(tool_args.get("link_threshold") or DEFAULT_LINK_THRESHOLD)
    merge_thr = float(tool_args.get("merge_threshold") or DEFAULT_MERGE_THRESHOLD)
    pref = tool_args.get("path_prefix")
    if pref:
        proj = _ps_lt(effective_project)
        norm = pref.lstrip("/").strip()
        if norm and norm != proj and not norm.startswith(f"{proj}/"):
            norm = f"{proj}/{norm}"
        pref = norm
    if not memory_manager:
        return "link_topic_similarity: memory manager unavailable."
    r = await backfill(
        project_id=effective_project,
        memory_manager=memory_manager,
        path_prefix=pref,
        link_threshold=link_thr,
        merge_threshold=merge_thr,
    )
    err_part = ""
    if r.get("errors"):
        err_part = f"\n  {len(r['errors'])} errors (first: {r['errors'][0]})"
    return (
        f"Backfilled similarity for {r['artifacts_processed']} "
        f"artifact(s):\n"
        f"  topics processed: {r['topics_processed']}\n"
        f"  edges added: {r['edges_added']}\n"
        f"  merge proposals queued: {r['proposals_queued']}"
        f"{err_part}\n\n"
        f"Use merge_topics(action=\"list\") to review the queued "
        f"merges, then approve or reject them."
    )



@tool('list_merge_proposals')
async def _tool_list_merge_proposals(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    effective_project = ctx.effective_project
    from memory import merge_proposals as _mp
    status = (tool_args.get("status") or "pending").strip()
    if status == "all":
        status = None
    limit = int(tool_args.get("limit") or 50)
    rows = _mp.list_proposals(
        project_id=effective_project, status=status, limit=limit,
    )
    if not rows:
        return f"(no merge proposals with status={status or 'any'!r})"
    lines = [f"{len(rows)} merge proposal(s):"]
    for r in rows:
        lines.append(
            f"- id={r['id'][:8]} score={r['similarity_score']:.3f} "
            f"status={r['status']}\n"
            f"    A: {r['node_a_label']} ({r['node_a_type']})\n"
            f"    B: {r['node_b_label']} ({r['node_b_type']})\n"
            f"    reason: {r.get('reason') or '?'}"
        )
    return "\n".join(lines)



@tool('approve_merge')
async def _tool_approve_merge(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    memory_manager = ctx.memory_manager
    session_id = ctx.session_id
    effective_project = ctx.effective_project
    from memory import merge_proposals as _mp
    from sources.similarity import execute_merge
    pid = tool_args.get("proposal_id") or ""
    canonical = (tool_args.get("canonical_label") or "").strip()
    # Allow short prefix match on proposal_id for ergonomics.
    if pid and len(pid) < 36:
        cand = [
            r for r in _mp.list_proposals(
                project_id=effective_project, status=None, limit=200,
            )
            if r["id"].startswith(pid)
        ]
        if len(cand) == 1:
            pid = cand[0]["id"]
        elif len(cand) > 1:
            return f"approve_merge: prefix {pid!r} matches {len(cand)} proposals; provide more characters."
    proposal = _mp.get_proposal(pid)
    if not proposal:
        return f"approve_merge: no proposal with id {pid!r}."
    if not memory_manager:
        return "approve_merge: memory manager unavailable."
    res = await execute_merge(
        pid, canonical_label=canonical,
        project_id=effective_project,
        memory_manager=memory_manager,
        approved_by=session_id or "user",
    )
    if not res.get("ok"):
        return f"approve_merge failed: {res.get('reason')}"
    return (
        f"Merged {res.get('deprecated_label')} into "
        f"{res.get('canonical_label')} "
        f"({res.get('edges_rewritten', 0)} edges rewritten)."
    )



@tool('reject_merge')
async def _tool_reject_merge(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    session_id = ctx.session_id
    effective_project = ctx.effective_project
    from memory import merge_proposals as _mp
    pid = tool_args.get("proposal_id") or ""
    if pid and len(pid) < 36:
        cand = [
            r for r in _mp.list_proposals(
                project_id=effective_project, status=None, limit=200,
            )
            if r["id"].startswith(pid)
        ]
        if len(cand) == 1:
            pid = cand[0]["id"]
        elif len(cand) > 1:
            return f"reject_merge: prefix {pid!r} matches {len(cand)} proposals; provide more characters."
    ok = _mp.set_status(pid, "rejected", approved_by=session_id or "user")
    if not ok:
        return f"reject_merge: no proposal with id {pid!r}."
    return f"Rejected merge proposal {pid[:8]}. Nodes stay distinct."



@tool('force_merge')
async def _tool_force_merge(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    memory_manager = ctx.memory_manager
    session_id = ctx.session_id
    effective_project = ctx.effective_project
    from memory import merge_proposals as _mp
    from sources.similarity import execute_merge
    la = (tool_args.get("label_a") or "").strip()
    lb = (tool_args.get("label_b") or "").strip()
    canonical = (tool_args.get("canonical_label") or "").strip()
    if not la or not lb or not canonical:
        return "force_merge: label_a, label_b, and canonical_label are required."
    if canonical not in {la, lb}:
        return f"force_merge: canonical_label {canonical!r} must equal label_a or label_b."
    if not memory_manager:
        return "force_merge: memory manager unavailable."
    # Look up the node types for the proposal record.
    graph = memory_manager.graph
    na = await graph.get_node_by_label(la)
    nb = await graph.get_node_by_label(lb)
    if not na or not nb:
        return f"force_merge: node lookup failed (a={bool(na)}, b={bool(nb)})."
    type_a = (na.get("metadata") or {}).get("entity_type") or na.get("node_type") or "concept"
    type_b = (nb.get("metadata") or {}).get("entity_type") or nb.get("node_type") or "concept"
    pid, _ = _mp.propose(
        project_id=effective_project,
        label_a=la, type_a=type_a,
        label_b=lb, type_b=type_b,
        similarity_score=1.0,
        reason="manual_force_merge",
    )
    res = await execute_merge(
        pid, canonical_label=canonical,
        project_id=effective_project,
        memory_manager=memory_manager,
        approved_by=session_id or "user",
    )
    if not res.get("ok"):
        return f"force_merge failed: {res.get('reason')}"
    return (
        f"Force-merged {res.get('deprecated_label')} into "
        f"{res.get('canonical_label')} "
        f"({res.get('edges_rewritten', 0)} edges rewritten)."
    )



# ── Consolidated entry point ─────────────────────────────────────────────────
# merge_topics(action=…) replaces four schemas; the old names still work.

_MERGE_ACTIONS = {"list": "list_merge_proposals", "approve": "approve_merge",
                  "reject": "reject_merge", "force": "force_merge"}

SCHEMAS.append({
    "type": "function",
    "function": {
        "name": "merge_topics",
        "description": (
            "Review and apply merges of duplicate topic nodes in the graph. "
            "list(status?, limit?) shows proposals (labels, types, similarity, id). "
            "approve(proposal_id, canonical_label) EXECUTES a merge — irreversible, only after the user "
            "confirmed; if they don't say which label survives, keep the longer one and say so. "
            "reject(proposal_id) keeps both nodes. force(label_a, label_b, canonical_label) merges two "
            "named nodes with no proposal — only on explicit request."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": list(_MERGE_ACTIONS)},
                "status": {"type": "string", "enum": ["pending", "approved", "rejected", "merged", "stale", "all"]},
                "limit": {"type": "integer"},
                "proposal_id": {"type": "string"},
                "canonical_label": {"type": "string", "description": "The label that survives; must be one of the two."},
                "label_a": {"type": "string"},
                "label_b": {"type": "string"},
            },
            "required": ["action"],
        },
    },
})


@tool("merge_topics")
async def _tool_merge_topics(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    from agent.tools.registry import resolve
    target = _MERGE_ACTIONS.get((tool_args.get("action") or "").strip())
    if not target:
        return f"Error: merge_topics action must be one of {', '.join(_MERGE_ACTIONS)}."
    args = {k: v for k, v in tool_args.items() if k != "action"}
    return await resolve(target)(ctx, target, args)
