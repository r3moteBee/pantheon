"""Memory tools: remember/recall, graph nodes and links, consolidation, indexing."""
from __future__ import annotations

from typing import Any
from utils.paths import is_within
from agent.tools.registry import ToolContext, tool
from agent.tools import workspace as _ws


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "remember",
            "description": "Store information in memory for future recall: 'episodic' for a dated note about this conversation, 'semantic' for a key insight or fact to find by meaning later, 'graph' to extract the entities and relationships in the text into the knowledge graph. For documents or reports use save_to_artifact instead.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "The information to remember"},
                    "tier": {
                        "type": "string",
                        "enum": ["episodic", "semantic", "graph"],
                        "description": "Memory tier to store in"
                    },
                    "metadata": {
                        "type": "object",
                        "description": "Optional metadata tags",
                        "additionalProperties": True
                    }
                },
                "required": ["content", "tier"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "recall",
            "description": (
                "Search the project's memory across episodic "
                "(past chats), semantic (indexed artifacts and "
                "workspace files), and graph (linked concepts) "
                "tiers. ARTIFACTS are first-class memory: any "
                "transcript, note, or document saved via "
                "save_to_artifact is indexed into semantic + "
                "graph and turns up here. Always try this BEFORE "
                "telling the user you can't find something — "
                "the artifact may already be indexed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "What to search for"},
                    "tiers": {
                        "type": "array",
                        "items": {"type": "string", "enum": ["episodic", "semantic", "graph"]},
                        "description": "Which memory tiers to search (default: all)"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_graph_node",
            "description": "Create a node in the associative graph memory to represent a concept, person, project, or fact.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_type": {
                        "type": "string",
                        "enum": ["concept", "person", "project", "event", "fact"],
                        "description": "Type of the node"
                    },
                    "label": {"type": "string", "description": "Human-readable name for the node"},
                    "metadata": {
                        "type": "object",
                        "description": "Additional properties for this node",
                        "additionalProperties": True
                    }
                },
                "required": ["node_type", "label"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "link_concepts",
            "description": "Create a relationship edge between two nodes in the associative graph memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "node_a_label": {"type": "string", "description": "Label of the first node"},
                    "node_b_label": {"type": "string", "description": "Label of the second node"},
                    "relationship": {"type": "string", "description": "Description of the relationship (e.g., 'works on', 'is related to', 'caused by')"}
                },
                "required": ["node_a_label", "node_b_label", "relationship"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "index_workspace",
            "description": "Index workspace files into semantic memory and knowledge graph. Makes file contents searchable via recall. Supports Markdown (with YAML frontmatter), text, CSV, PDF, and code files. Can index a single file or entire directory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to a file or directory in the workspace. Empty string indexes the entire workspace.",
                        "default": ""
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Re-index even if file hasn't changed (default: false)",
                        "default": False
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "index_artifact",
            "description": (
                "Index one artifact, or every artifact under a path "
                "prefix, into semantic memory + knowledge graph. New "
                "artifacts are auto-indexed on save; use this to "
                "backfill artifacts saved before that landed, to "
                "force-reindex after editing tags, or to bulk-ingest "
                "a folder like NBJ/. Provide either id (one artifact) "
                "or path_prefix (folder) — pass a bare folder name like "
                "'NBJ/' (the project slug is added automatically)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {
                        "type": "string",
                        "description": "Single artifact id to index."
                    },
                    "path_prefix": {
                        "type": "string",
                        "description": "Folder prefix; index every text artifact whose path starts with this prefix. Pass bare folder name like 'NBJ/'."
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Re-index even if content hash is unchanged (default false).",
                        "default": False
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "consolidate_memory",
            "description": "Run memory consolidation: summarize the current session, extract entities/facts/relationships from recent conversation, and store them in semantic and graph memory. Use at the end of a productive conversation or when the user asks you to remember what was discussed.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
]


@tool('remember')
async def _tool_remember(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    memory_manager = ctx.memory_manager
    session_id = ctx.session_id
    effective_project = ctx.effective_project
    if memory_manager is not None:
        mgr = memory_manager
    else:
        from memory.manager import create_memory_manager
        mgr = create_memory_manager(project_id=effective_project, session_id=session_id)
    tier = tool_args.get("tier", "semantic")
    content = tool_args["content"]
    metadata = tool_args.get("metadata", {})
    ref = await mgr.remember(content=content, tier=tier, metadata=metadata)
    stored_tier = ref.split(":", 2)[1] if ref.startswith("stored:") else tier
    return f"Stored in {stored_tier} memory: {content[:100]} ({ref})"



@tool('recall')
async def _tool_recall(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    effective_project = ctx.effective_project
    from memory.manager import create_memory_manager
    mgr = create_memory_manager(project_id=effective_project)
    tiers = tool_args.get("tiers", ["semantic", "episodic", "graph"])
    query = tool_args["query"]
    results = await mgr.recall(query=query, tiers=tiers, project_id=effective_project)
    if not results:
        return "No memories found."

    def _format(r):
        tier = r.get("tier", "?")
        content = (r.get("content") or "")[:400]
        meta = r.get("metadata") or {}
        # Detect artifact-origin chunks — index_text writes these.
        artifact_id = meta.get("fm_artifact_id")
        artifact_path = meta.get("fm_artifact_path") or meta.get("source_path") or ""
        if artifact_id or (isinstance(artifact_path, str) and artifact_path.startswith("artifact://")):
            label = artifact_path.split("artifact://", 1)[-1].split("/", 1)[-1] if "artifact://" in artifact_path else artifact_path
            tags = meta.get("fm_tags") or ""
            tag_part = f" tags=[{tags}]" if tags else ""
            return (
                f"[{tier}/artifact] {content}\n"
                f"  ↳ source: {label}"
                f"  id={artifact_id or '?'}{tag_part}"
            )
        # Workspace-file chunk
        src_file = meta.get("source_file") or meta.get("source_path")
        if src_file and tier == "semantic":
            return f"[{tier}/file:{src_file}] {content}"
        # Episodic
        if tier == "episodic":
            sid = meta.get("session_id") or "?"
            ts = meta.get("timestamp") or ""
            return f"[{tier} session={sid[:8]} {ts}] {content}"
        return f"[{tier}] {content}"

    lines = [_format(r) for r in results[:10]]
    return "\n\n".join(lines)



@tool('create_graph_node')
async def _tool_create_graph_node(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    effective_project = ctx.effective_project
    from memory.graph import GraphMemory
    graph = GraphMemory(project_id=effective_project)
    label = tool_args["label"]
    node_type = tool_args.get("node_type", "concept")
    metadata = tool_args.get("metadata", {})
    node_id = await graph.add_node(node_type=node_type, label=label, metadata=metadata)
    return f"Graph node created: {label} (type: {node_type}, id: {node_id})"



@tool('link_concepts')
async def _tool_link_concepts(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    effective_project = ctx.effective_project
    from memory.graph import GraphMemory
    graph = GraphMemory(project_id=effective_project)
    node_a = tool_args["node_a_label"]
    node_b = tool_args["node_b_label"]
    relationship = tool_args["relationship"]
    result = await graph.add_edge_by_label(label_a=node_a, label_b=node_b, relationship=relationship)
    return result



@tool('index_workspace')
async def _tool_index_workspace(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    project_id = ctx.project_id
    effective_project = ctx.effective_project
    path_arg = tool_args.get("path", "")
    force = tool_args.get("force", False)
    from memory.manager import create_memory_manager
    mgr = create_memory_manager(project_id=effective_project)
    base = _ws._get_workspace_base(project_id)
    target = (base / path_arg).resolve() if path_arg else base
    if not is_within(target, base):
        return "Access denied: path outside workspace"
    if target.is_file():
        result = await mgr.index_workspace_file(str(target), force=force)
    elif target.is_dir():
        result = await mgr.index_workspace_directory(str(target), force=force)
    else:
        # Path not on disk — check whether the user actually meant
        # an artifact folder. If artifacts exist under that prefix,
        # auto-pivot to index_artifact instead of failing.
        from artifacts.store import get_store as _get_art_store, project_slug as _ps_x
        art_store = _get_art_store()
        proj_slug = _ps_x(effective_project)
        norm_pref = (path_arg or "").lstrip("/").strip().rstrip("/")
        if norm_pref and norm_pref != proj_slug and not norm_pref.startswith(f"{proj_slug}/"):
            norm_pref = f"{proj_slug}/{norm_pref}"
        if norm_pref:
            norm_pref = norm_pref + "/"
            matches = art_store.list(
                project_id=effective_project,
                path_prefix=norm_pref,
                limit=500,
            )
            if matches:
                # Auto-route to artifact indexing
                from artifacts.store import is_text_type as _is_text_x
                indexed = skipped = total_chunks = total_entities = 0
                for it in matches:
                    if not _is_text_x(it["content_type"]):
                        skipped += 1
                        continue
                    r = await mgr.index_artifact(it["id"], force=force)
                    if r.get("skipped"):
                        skipped += 1
                    else:
                        indexed += 1
                        total_chunks += r.get("chunks_stored", 0)
                        total_entities += r.get("entities_extracted", 0)
                return (
                    f"Path {path_arg!r} isn't on disk, but matched "
                    f"{len(matches)} artifact(s) under {norm_pref!r} — "
                    f"auto-routed to artifact indexing.\n"
                    f"Indexed {indexed} ({skipped} skipped): "
                    f"{total_chunks} chunks stored, "
                    f"{total_entities} entities extracted.\n"
                    f"Tip: prefer `index_artifact` for artifact "
                    f"folders; `index_workspace` is for files on disk."
                )
        return (
            f"Path not found: {path_arg!r}. If you meant an "
            f"artifact folder, use `index_artifact(""path_prefix={path_arg!r})` instead — workspace is for files on "
            f"disk, artifacts are the project's persistent store."
        )
    if result.get("skipped"):
        return f"File {path_arg} skipped: {result.get('reason', 'unchanged')}"
    chunks = result.get("chunks_stored", result.get("total_chunks", 0))
    entities = result.get("entities_extracted", result.get("total_entities", 0))
    files = result.get("files_processed", 1)
    return f"Indexed {files} file(s): {chunks} chunks stored, {entities} entities extracted to graph."



@tool('index_artifact')
async def _tool_index_artifact(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    effective_project = ctx.effective_project
    from artifacts.store import get_store, is_text_type, project_slug as _ps_idx
    from memory.manager import create_memory_manager
    store = get_store()
    mgr = create_memory_manager(project_id=effective_project)
    force = bool(tool_args.get("force", False))
    aid = tool_args.get("id")
    prefix = tool_args.get("path_prefix")

    if aid:
        result = await mgr.index_artifact(aid, force=force)
        if result.get("skipped"):
            return f"Artifact {aid} skipped: {result.get('reason', 'unchanged')}"
        return (
            f"Indexed artifact {aid}: "
            f"{result.get('chunks_stored', 0)} chunks stored, "
            f"{result.get('entities_extracted', 0)} entities extracted."
        )

    if prefix is not None:
        # Apply same normalization as save/list so callers can
        # pass a bare folder name like 'NBJ/'.
        proj = _ps_idx(effective_project)
        norm = (prefix or "").lstrip("/").strip()
        if norm and norm != proj and not norm.startswith(f"{proj}/"):
            norm = f"{proj}/{norm}"
        items = store.list(
            project_id=effective_project,
            path_prefix=norm or None,
            limit=500,
        )
        if not items:
            return f"(no artifacts match prefix {norm!r})"
        indexed = skipped = total_chunks = total_entities = 0
        for it in items:
            if not is_text_type(it["content_type"]):
                skipped += 1
                continue
            r = await mgr.index_artifact(it["id"], force=force)
            if r.get("skipped"):
                skipped += 1
            else:
                indexed += 1
                total_chunks += r.get("chunks_stored", 0)
                total_entities += r.get("entities_extracted", 0)
        return (
            f"Indexed {indexed} artifact(s) under {norm!r} "
            f"({skipped} skipped): {total_chunks} chunks stored, "
            f"{total_entities} entities extracted."
        )

    return "index_artifact requires either 'id' or 'path_prefix'."



@tool('consolidate_memory')
async def _tool_consolidate_memory(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    memory_manager = ctx.memory_manager
    if not memory_manager:
        return "No memory manager available."
    result = await memory_manager.consolidate_session()
    return result

