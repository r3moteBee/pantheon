"""Memory API — CRUD for all memory tiers, search, and audit."""
from __future__ import annotations
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


class StoreMemoryRequest(BaseModel):
    content: str
    tier: str = "semantic"
    project_id: str = "default"
    metadata: dict[str, Any] = {}


class SearchMemoryRequest(BaseModel):
    query: str
    project_id: str = "default"
    tiers: list[str] = ["semantic", "episodic"]
    limit: int = 10


class UpdateNoteRequest(BaseModel):
    content: str


class GraphNodeRequest(BaseModel):
    node_type: str
    label: str
    metadata: dict[str, Any] = {}
    project_id: str = "default"


class GraphEdgeRequest(BaseModel):
    label_a: str
    label_b: str
    relationship: str
    project_id: str = "default"


@router.post("/memory/store")
async def store_memory(req: StoreMemoryRequest) -> dict[str, str]:
    """Store a memory in the specified tier."""
    from memory.manager import create_memory_manager
    manager = create_memory_manager(project_id=req.project_id)
    result = await manager.remember(
        content=req.content,
        tier=req.tier,
        metadata=req.metadata,
    )
    return {"status": "stored", "reference": result, "tier": req.tier}


@router.post("/memory/search")
async def search_memory(req: SearchMemoryRequest) -> dict[str, Any]:
    """Search memories across specified tiers."""
    from memory.manager import create_memory_manager
    manager = create_memory_manager(project_id=req.project_id)
    results = await manager.recall(
        query=req.query,
        tiers=req.tiers,
        project_id=req.project_id,
        limit_per_tier=req.limit,
    )
    return {"results": results, "count": len(results), "query": req.query}


@router.get("/memory/audit/{tier}")
async def audit_memory(
    tier: str,
    project_id: str = Query(default="default"),
) -> dict[str, Any]:
    """Audit all memories in a specific tier."""
    valid_tiers = {"working", "episodic", "semantic", "graph", "archival"}
    if tier not in valid_tiers:
        raise HTTPException(status_code=400, detail=f"Invalid tier. Must be one of: {valid_tiers}")
    from memory.manager import create_memory_manager
    manager = create_memory_manager(project_id=project_id)
    return await manager.audit_memory(tier=tier, project_id=project_id)


@router.get("/memory/episodic/notes")
async def list_notes(
    project_id: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List episodic memory notes."""
    from memory.episodic import EpisodicMemory
    ep = EpisodicMemory()
    notes = await ep.get_notes(project_id=project_id, limit=limit)
    return {"notes": notes, "count": len(notes)}


@router.get("/memory/episodic/messages")
async def list_messages(
    project_id: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    """List recent episodic messages across all sessions."""
    from memory.episodic import EpisodicMemory
    ep = EpisodicMemory()
    messages = await ep.get_recent_messages(project_id=project_id, limit=limit)
    return {"messages": messages, "count": len(messages)}


@router.put("/memory/episodic/notes/{note_id}")
async def update_note(note_id: str, req: UpdateNoteRequest) -> dict[str, str]:
    """Update an episodic memory note."""
    from memory.episodic import EpisodicMemory
    ep = EpisodicMemory()
    updated = await ep.update_note(note_id=note_id, content=req.content)
    if not updated:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"status": "updated", "id": note_id}


@router.delete("/memory/episodic/notes/{note_id}")
async def delete_note(note_id: str) -> dict[str, str]:
    """Delete an episodic memory note."""
    from memory.episodic import EpisodicMemory
    ep = EpisodicMemory()
    deleted = await ep.delete_note(note_id=note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"status": "deleted", "id": note_id}


@router.delete("/memory/episodic/messages/{message_id}")
async def delete_message(message_id: str) -> dict[str, str]:
    """Delete a specific conversation message."""
    from memory.episodic import EpisodicMemory
    ep = EpisodicMemory()
    deleted = await ep.delete_message(message_id=message_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"status": "deleted", "id": message_id}


@router.get("/memory/semantic")
async def list_semantic(
    project_id: str = Query(default="default"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    """List semantic memories."""
    from memory.semantic import SemanticMemory
    sem = SemanticMemory(project_id=project_id)
    items = await sem.list_memories(limit=limit, offset=offset)
    count = await sem.count()
    return {"items": items, "total": count, "limit": limit, "offset": offset}


@router.delete("/memory/semantic/{doc_id}")
async def delete_semantic(
    doc_id: str,
    project_id: str = Query(default="default"),
) -> dict[str, str]:
    """Delete a semantic memory by ID."""
    from memory.semantic import SemanticMemory
    sem = SemanticMemory(project_id=project_id)
    deleted = await sem.delete(doc_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "deleted", "id": doc_id}


@router.get("/memory/graph/nodes")
async def list_graph_nodes(
    project_id: str = Query(default="default"),
    node_type: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    """List graph nodes."""
    from memory.graph import GraphMemory
    graph = GraphMemory(project_id=project_id)
    nodes = await graph.list_nodes(node_type=node_type, limit=limit)
    return {"nodes": nodes, "count": len(nodes)}


@router.get("/memory/graph/edges")
async def list_graph_edges(
    project_id: str = Query(default="default"),
    limit: int = Query(default=200, ge=1, le=1000),
) -> dict[str, Any]:
    """List graph edges."""
    from memory.graph import GraphMemory
    graph = GraphMemory(project_id=project_id)
    edges = await graph.list_edges(limit=limit)
    return {"edges": edges, "count": len(edges)}


@router.post("/memory/graph/nodes")
async def create_graph_node(req: GraphNodeRequest) -> dict[str, Any]:
    """Create a graph node."""
    from memory.graph import GraphMemory
    graph = GraphMemory(project_id=req.project_id)
    node_id = await graph.add_node(
        node_type=req.node_type,
        label=req.label,
        metadata=req.metadata,
    )
    return {"id": node_id, "label": req.label, "node_type": req.node_type}


@router.post("/memory/graph/edges")
async def create_graph_edge(req: GraphEdgeRequest) -> dict[str, Any]:
    """Create a graph edge by node labels."""
    from memory.graph import GraphMemory
    graph = GraphMemory(project_id=req.project_id)
    result = await graph.add_edge_by_label(
        label_a=req.label_a,
        label_b=req.label_b,
        relationship=req.relationship,
    )
    return {"status": "created", "result": result}


@router.delete("/memory/graph/nodes/{node_id}")
async def delete_graph_node(
    node_id: str,
    project_id: str = Query(default="default"),
) -> dict[str, str]:
    """Delete a graph node and its edges."""
    from memory.graph import GraphMemory
    graph = GraphMemory(project_id=project_id)
    deleted = await graph.delete_node(node_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Node not found")
    return {"status": "deleted", "id": node_id}


@router.delete("/memory/graph/edges/{edge_id}")
async def delete_graph_edge(
    edge_id: str,
    project_id: str = Query(default="default"),
) -> dict[str, str]:
    """Delete a graph edge."""
    from memory.graph import GraphMemory
    graph = GraphMemory(project_id=project_id)
    deleted = await graph.delete_edge(edge_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Edge not found")
    return {"status": "deleted", "id": edge_id}


@router.get("/memory/graph/related/{node_id}")
async def get_related_nodes(
    node_id: str,
    project_id: str = Query(default="default"),
    depth: int = Query(default=2, ge=1, le=4),
) -> dict[str, Any]:
    """Find nodes related to the given node."""
    from memory.graph import GraphMemory
    graph = GraphMemory(project_id=project_id)
    related = await graph.find_related(node_id=node_id, depth=depth)
    return {"node_id": node_id, "related": related, "count": len(related)}


@router.get("/memory/archival/notes")
async def list_archival_notes(
    project_id: str = Query(default="default"),
) -> dict[str, Any]:
    """List all archival notes for a project."""
    from memory.archival import ArchivalMemory
    from config import get_settings
    s = get_settings()
    arch = ArchivalMemory(project_id=project_id, base_dir=str(s.data_dir))
    notes = await arch.list_notes()
    return {"notes": notes, "count": len(notes)}


@router.get("/memory/archival/notes/{filename}")
async def read_archival_note(
    filename: str,
    project_id: str = Query(default="default"),
) -> dict[str, Any]:
    """Read the content of an archival note."""
    from memory.archival import ArchivalMemory
    from config import get_settings
    s = get_settings()
    arch = ArchivalMemory(project_id=project_id, base_dir=str(s.data_dir))
    content = await arch.read_file(f"notes/{filename}")
    if content.startswith("File not found") or content.startswith("Error reading"):
        raise HTTPException(status_code=404, detail=content)
    return {"filename": filename, "content": content}


@router.post("/memory/archival/notes")
async def create_archival_note(
    body: dict[str, Any],
    project_id: str = Query(default="default"),
) -> dict[str, Any]:
    """Create a new archival note."""
    content = body.get("content", "")
    if not content.strip():
        raise HTTPException(status_code=400, detail="Note content cannot be empty")
    from memory.archival import ArchivalMemory
    from config import get_settings
    s = get_settings()
    arch = ArchivalMemory(project_id=project_id, base_dir=str(s.data_dir))
    filename = await arch.append_note(content)
    return {"status": "created", "filename": filename}


@router.delete("/memory/archival/notes/{filename}")
async def delete_archival_note(
    filename: str,
    project_id: str = Query(default="default"),
) -> dict[str, str]:
    """Delete an archival note."""
    from memory.archival import ArchivalMemory
    from config import get_settings
    s = get_settings()
    arch = ArchivalMemory(project_id=project_id, base_dir=str(s.data_dir))
    deleted = await arch.delete_file(f"notes/{filename}")
    if not deleted:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"status": "deleted", "filename": filename}


@router.get("/memory/archival/summary")
async def get_archival_summary(
    project_id: str = Query(default="default"),
) -> dict[str, Any]:
    """Get the project summary from archival memory."""
    from memory.archival import ArchivalMemory
    from config import get_settings
    s = get_settings()
    arch = ArchivalMemory(project_id=project_id, base_dir=str(s.data_dir))
    content = await arch.get_project_summary()
    return {"content": content, "project_id": project_id}


@router.put("/memory/archival/summary")
async def update_archival_summary(
    body: dict[str, Any],
    project_id: str = Query(default="default"),
) -> dict[str, str]:
    """Update the project summary in archival memory."""
    content = body.get("content", "")
    from memory.archival import ArchivalMemory
    from config import get_settings
    s = get_settings()
    arch = ArchivalMemory(project_id=project_id, base_dir=str(s.data_dir))
    await arch.update_project_summary(content)
    return {"status": "updated", "project_id": project_id}


@router.post("/memory/consolidate")
async def consolidate_session(
    project_id: str = Query(default="default"),
    session_id: str = Query(default="current"),
) -> dict[str, str]:
    """Consolidate the current session's working memory to long-term storage."""
    from memory.manager import create_memory_manager
    manager = create_memory_manager(project_id=project_id, session_id=session_id)
    summary = await manager.consolidate_session()
    return {"status": "consolidated", "summary": summary}


@router.post("/memory/reembed")
async def reembed_stale_vectors(
    project_id: str = Query(default="default"),
) -> dict[str, Any]:
    """Re-embed semantic-memory vectors that were embedded with a different
    model than the one currently configured.

    Useful after switching embedding model (e.g., bumping `EMBEDDING_MODEL`
    or changing the `embedding_model` setting in the vault). Mismatched
    vectors produce unreliable similarity scores; this brings them back in
    line with the current model.
    """
    from memory.manager import create_memory_manager
    manager = create_memory_manager(project_id=project_id)
    if not manager.semantic._embedding_fn:
        raise HTTPException(
            status_code=400,
            detail=(
                "No embedding_fn configured. Configure an embedding provider "
                "in Settings before running re-embed."
            ),
        )
    result = await manager.semantic.reembed_stale()
    return {
        "status": "ok",
        "project_id": project_id,
        "current_model": manager.semantic._embedding_model,
        **result,
    }


@router.get("/memory/embedding-model-stats")
async def embedding_model_stats(
    project_id: str = Query(default="default"),
) -> dict[str, Any]:
    """Report how many semantic vectors exist per embedding_model.

    Useful for the Settings UI to surface "you have N stale vectors"
    before the user runs re-embed.
    """
    from memory.manager import create_memory_manager
    manager = create_memory_manager(project_id=project_id)
    items = await manager.semantic.list_by_model(embedding_model=None)
    counts: dict[str, int] = {}
    for item in items:
        md = item.get("metadata") or {}
        model = md.get("embedding_model", "<untagged>")
        counts[model] = counts.get(model, 0) + 1
    return {
        "project_id": project_id,
        "current_model": manager.semantic._embedding_model,
        "by_model": counts,
        "total": sum(counts.values()),
    }


@router.get("/memory/graph/full")
async def graph_full(
    project_id: str = Query(default="default"),
    type: str | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict[str, Any]:
    """Single-fetch dump for the visualizer. Returns nodes + edges."""
    from memory.graph import GraphMemory
    g = GraphMemory(project_id=project_id)
    nodes = await g.list_nodes(node_type=type, limit=limit)
    edges = await g.list_edges(limit=limit * 4)
    return {"nodes": nodes, "edges": edges, "project_id": project_id}


@router.get("/memory/graph/path")
async def graph_path(
    from_: str = Query(..., alias="from"),
    to: str = Query(...),
    project_id: str = Query(default="default"),
) -> dict[str, Any]:
    """Find the shortest path between two nodes by label.

    Returns:
      {
        from, to, found: bool,
        path:  [ {id, label, node_type, ...} ],   # nodes in order
        edges: [ {id, source, target, relationship, weight} ],   # edges between consecutive nodes
        hops:  int                                # len(path) - 1
      }
    """
    from memory.graph import GraphMemory
    g = GraphMemory(project_id=project_id)
    path_nodes = await g.get_path(label_a=from_, label_b=to)
    if not path_nodes:
        return {"from": from_, "to": to, "found": False, "path": [],
                "edges": [], "hops": 0}
    # For each consecutive pair, look up the edge connecting them.
    import sqlite3
    edges: list[dict[str, Any]] = []
    db = g.db_path
    conn = sqlite3.connect(db); conn.row_factory = sqlite3.Row
    try:
        for a, b in zip(path_nodes, path_nodes[1:]):
            row = conn.execute(
                """SELECT id, node_a_id as source, node_b_id as target,
                          relationship, weight
                   FROM graph_edges
                   WHERE project_id = ?
                     AND ((node_a_id = ? AND node_b_id = ?)
                       OR (node_a_id = ? AND node_b_id = ?))
                   LIMIT 1""",
                (project_id, a["id"], b["id"], b["id"], a["id"]),
            ).fetchone()
            if row:
                edges.append(dict(row))
    finally:
        conn.close()
    return {
        "from": from_, "to": to, "found": True,
        "path": path_nodes, "edges": edges, "hops": len(path_nodes) - 1,
    }
