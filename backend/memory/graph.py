"""Tier 4: Associative graph memory — SQLite-backed concept network."""
from __future__ import annotations
import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class GraphMemory:
    """Tier 4: Associative graph for relating concepts, people, projects, and facts.
    
    Nodes represent entities. Edges represent named relationships between them.
    Enables multi-hop reasoning like "what projects is Alice working on?" or 
    "what concepts are related to machine learning?"
    """

    NODE_TYPES = {"concept", "person", "project", "event", "fact"}

    def __init__(self, project_id: str = "default", db_path: str | None = None):
        self.project_id = project_id
        if db_path is None:
            try:
                from config import get_settings
                db_path = get_settings().graph_db_path
            except Exception:
                db_path = "data/graph.db"
        self.db_path = db_path
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL DEFAULT 'default',
                    node_type TEXT NOT NULL,
                    label TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS graph_edges (
                    id TEXT PRIMARY KEY,
                    project_id TEXT NOT NULL DEFAULT 'default',
                    node_a_id TEXT NOT NULL,
                    node_b_id TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (node_a_id) REFERENCES graph_nodes(id) ON DELETE CASCADE,
                    FOREIGN KEY (node_b_id) REFERENCES graph_nodes(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_nodes_project ON graph_nodes(project_id);
                CREATE INDEX IF NOT EXISTS idx_nodes_label ON graph_nodes(label);
                CREATE INDEX IF NOT EXISTS idx_edges_node_a ON graph_edges(node_a_id);
                CREATE INDEX IF NOT EXISTS idx_edges_node_b ON graph_edges(node_b_id);
                CREATE INDEX IF NOT EXISTS idx_edges_project ON graph_edges(project_id);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_nodes_project_label 
                    ON graph_nodes(project_id, label);
            """)
            conn.commit()

    async def add_node(
        self,
        node_type: str,
        label: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Add or update a node. Returns node ID."""
        if node_type not in self.NODE_TYPES:
            raise ValueError(f"Invalid node type: {node_type}. Must be one of {self.NODE_TYPES}")

        now = _now_iso()
        node_id = str(uuid.uuid4())
        with self._connect() as conn:
            # Check if node with this label already exists in project
            existing = conn.execute(
                "SELECT id FROM graph_nodes WHERE project_id = ? AND label = ?",
                (self.project_id, label)
            ).fetchone()

            if existing:
                # Update existing node
                node_id = existing["id"]
                conn.execute("""
                    UPDATE graph_nodes SET node_type = ?, metadata = ?, updated_at = ?
                    WHERE id = ?
                """, (node_type, json.dumps(metadata or {}), now, node_id))
            else:
                conn.execute("""
                    INSERT INTO graph_nodes (id, project_id, node_type, label, metadata, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (node_id, self.project_id, node_type, label, json.dumps(metadata or {}), now, now))
            conn.commit()

        logger.debug(f"Graph node upserted: {label} ({node_type}) -> {node_id}")
        return node_id

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Get a node by ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM graph_nodes WHERE id = ? AND project_id = ?",
                (node_id, self.project_id)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["metadata"] = json.loads(d.get("metadata") or "{}")
        return d

    async def get_node_by_label(self, label: str) -> dict[str, Any] | None:
        """Get a node by its label."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM graph_nodes WHERE label = ? AND project_id = ?",
                (label, self.project_id)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["metadata"] = json.loads(d.get("metadata") or "{}")
        return d

    async def add_edge(
        self,
        node_a_id: str,
        node_b_id: str,
        relationship: str,
        weight: float = 1.0,
    ) -> str:
        """Add an edge between two nodes."""
        edge_id = str(uuid.uuid4())
        with self._connect() as conn:
            # Idempotency: skip if this exact edge already exists in
            # this project. Re-ingesting the same artifact should
            # not pile parallel edges.
            existing = conn.execute(
                """SELECT id FROM graph_edges
                   WHERE project_id = ? AND node_a_id = ? AND node_b_id = ?
                     AND relationship = ?""",
                (self.project_id, node_a_id, node_b_id, relationship),
            ).fetchone()
            if existing:
                return existing["id"]
            conn.execute("""
                INSERT OR REPLACE INTO graph_edges
                (id, project_id, node_a_id, node_b_id, relationship, weight, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (edge_id, self.project_id, node_a_id, node_b_id, relationship, weight, _now_iso()))
            conn.commit()
        return edge_id

    async def add_edge_by_label(
        self,
        label_a: str,
        label_b: str,
        relationship: str,
    ) -> str:
        """Add an edge between nodes identified by their labels. Creates nodes if missing."""
        node_a = await self.get_node_by_label(label_a)
        node_b = await self.get_node_by_label(label_b)

        if not node_a:
            node_a_id = await self.add_node("concept", label_a)
        else:
            node_a_id = node_a["id"]

        if not node_b:
            node_b_id = await self.add_node("concept", label_b)
        else:
            node_b_id = node_b["id"]

        await self.add_edge(node_a_id, node_b_id, relationship)
        return f"Linked '{label_a}' --[{relationship}]--> '{label_b}'"

    async def find_related(
        self,
        node_id: str,
        depth: int = 2,
        max_nodes: int = 50,
    ) -> list[dict[str, Any]]:
        """Find all nodes related to the given node up to `depth` hops away."""
        visited: set[str] = {node_id}
        frontier: set[str] = {node_id}
        all_results: list[dict[str, Any]] = []

        for hop in range(depth):
            if not frontier:
                break
            new_frontier: set[str] = set()
            with self._connect() as conn:
                # Find neighbors via edges in both directions
                for fid in frontier:
                    rows = conn.execute("""
                        SELECT 
                            CASE WHEN e.node_a_id = ? THEN e.node_b_id ELSE e.node_a_id END as neighbor_id,
                            e.relationship,
                            e.weight,
                            n.label,
                            n.node_type,
                            n.metadata
                        FROM graph_edges e
                        JOIN graph_nodes n ON n.id = CASE WHEN e.node_a_id = ? THEN e.node_b_id ELSE e.node_a_id END
                        WHERE (e.node_a_id = ? OR e.node_b_id = ?)
                          AND e.project_id = ?
                          AND n.project_id = ?
                    """, (fid, fid, fid, fid, self.project_id, self.project_id)).fetchall()

                    for row in rows:
                        nid = row["neighbor_id"]
                        if nid not in visited:
                            visited.add(nid)
                            new_frontier.add(nid)
                            all_results.append({
                                "id": nid,
                                "label": row["label"],
                                "node_type": row["node_type"],
                                "relationship": row["relationship"],
                                "weight": row["weight"],
                                "hop": hop + 1,
                                "metadata": json.loads(row["metadata"] or "{}"),
                            })
                            if len(all_results) >= max_nodes:
                                return all_results

            frontier = new_frontier

        return all_results

    async def get_path(
        self,
        label_a: str,
        label_b: str,
    ) -> list[dict[str, Any]]:
        """Find a path between two nodes using BFS."""
        node_a = await self.get_node_by_label(label_a)
        node_b = await self.get_node_by_label(label_b)
        if not node_a or not node_b:
            return []

        start_id = node_a["id"]
        end_id = node_b["id"]

        # BFS
        from collections import deque
        queue: deque = deque([[start_id]])
        visited: set[str] = {start_id}

        while queue:
            path = queue.popleft()
            current_id = path[-1]

            if current_id == end_id:
                # Reconstruct path as node info
                result = []
                with self._connect() as conn:
                    for nid in path:
                        row = conn.execute(
                            "SELECT id, label, node_type FROM graph_nodes WHERE id = ?", (nid,)
                        ).fetchone()
                        if row:
                            result.append(dict(row))
                return result

            with self._connect() as conn:
                neighbors = conn.execute("""
                    SELECT CASE WHEN e.node_a_id = ? THEN e.node_b_id ELSE e.node_a_id END as nid
                    FROM graph_edges e
                    WHERE (e.node_a_id = ? OR e.node_b_id = ?) AND e.project_id = ?
                """, (current_id, current_id, current_id, self.project_id)).fetchall()

            for nb in neighbors:
                nid = nb["nid"]
                if nid not in visited:
                    visited.add(nid)
                    queue.append(path + [nid])

        return []  # No path found

    async def get_paths(
        self,
        label_a: str,
        label_b: str,
        *,
        k: int = 1,
        weighted: bool = False,
    ) -> list[list[dict[str, Any]]]:
        """Find up to k shortest paths between two nodes.

        weighted=True uses Dijkstra over inverted edge weights (so
        higher-weight edges produce shorter logical distance — i.e.
        relationships marked "strong" are preferred). weighted=False
        does pure BFS hop count, which is just k-shortest-paths via
        Yen's-algorithm-lite.

        Returns a list of paths; each path is a list of node dicts.
        """
        node_a = await self.get_node_by_label(label_a)
        node_b = await self.get_node_by_label(label_b)
        if not node_a or not node_b:
            return []
        start, end = node_a["id"], node_b["id"]
        if k <= 1 and not weighted:
            single = await self.get_path(label_a, label_b)
            return [single] if single else []

        # Build adjacency + edge weight in one query
        with self._connect() as conn:
            edge_rows = conn.execute(
                "SELECT node_a_id, node_b_id, weight FROM graph_edges WHERE project_id = ?",
                (self.project_id,),
            ).fetchall()
        adj: dict[str, list[tuple[str, float]]] = {}
        for er in edge_rows:
            w = float(er["weight"] or 0.5)
            adj.setdefault(er["node_a_id"], []).append((er["node_b_id"], w))
            adj.setdefault(er["node_b_id"], []).append((er["node_a_id"], w))

        # Yen's k-shortest-paths
        if weighted:
            cost_fn = lambda w: 1.0 / max(w, 0.01)  # invert weight for Dijkstra
        else:
            cost_fn = lambda w: 1.0  # uniform = BFS hops

        first = self._dijkstra(start, end, adj, cost_fn)
        if not first:
            return []
        paths_out: list[list[str]] = [first]
        candidates: list[tuple[float, list[str]]] = []
        import heapq

        for ki in range(1, k):
            base = paths_out[-1]
            for i in range(len(base) - 1):
                spur_node = base[i]
                root_path = base[: i + 1]
                # Block edges that would re-create a previously-found path
                blocked: set[tuple[str, str]] = set()
                for p in paths_out:
                    if len(p) > i and p[: i + 1] == root_path:
                        blocked.add((p[i], p[i + 1]))
                        blocked.add((p[i + 1], p[i]))
                # Block nodes already in root (avoid loops)
                blocked_nodes = set(root_path[:-1])
                spur = self._dijkstra(spur_node, end, adj, cost_fn,
                                      blocked_edges=blocked, blocked_nodes=blocked_nodes)
                if spur:
                    full = root_path[:-1] + spur
                    cost = sum(
                        cost_fn(w) for j in range(len(full) - 1)
                        for n, w in adj.get(full[j], []) if n == full[j + 1]
                    )
                    heapq.heappush(candidates, (cost, full))
            if not candidates:
                break
            _, next_path = heapq.heappop(candidates)
            paths_out.append(next_path)

        # Hydrate node id paths into dicts
        result: list[list[dict[str, Any]]] = []
        with self._connect() as conn:
            for p in paths_out:
                nodes_out = []
                for nid in p:
                    row = conn.execute(
                        "SELECT id, label, node_type FROM graph_nodes WHERE id = ?", (nid,)
                    ).fetchone()
                    if row:
                        nodes_out.append(dict(row))
                if nodes_out:
                    result.append(nodes_out)
        return result

    @staticmethod
    def _dijkstra(start, end, adj, cost_fn, *, blocked_edges=None, blocked_nodes=None):
        import heapq
        blocked_edges = blocked_edges or set()
        blocked_nodes = blocked_nodes or set()
        if start in blocked_nodes:
            return []
        dist = {start: 0.0}
        prev: dict[str, str] = {}
        heap = [(0.0, start)]
        while heap:
            d, u = heapq.heappop(heap)
            if u == end:
                # Reconstruct
                path = [u]
                while path[-1] in prev:
                    path.append(prev[path[-1]])
                path.reverse()
                return path
            if d > dist.get(u, float("inf")):
                continue
            for v, w in adj.get(u, []):
                if v in blocked_nodes:
                    continue
                if (u, v) in blocked_edges:
                    continue
                nd = d + cost_fn(w)
                if nd < dist.get(v, float("inf")):
                    dist[v] = nd
                    prev[v] = u
                    heapq.heappush(heap, (nd, v))
        return []

    async def list_nodes(
        self,
        node_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List all nodes for this project."""
        with self._connect() as conn:
            if node_type:
                rows = conn.execute("""
                    SELECT * FROM graph_nodes
                    WHERE project_id = ? AND node_type = ?
                    ORDER BY label LIMIT ?
                """, (self.project_id, node_type, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT * FROM graph_nodes
                    WHERE project_id = ?
                    ORDER BY label LIMIT ?
                """, (self.project_id, limit)).fetchall()
        return [
            {**dict(r), "metadata": json.loads(r["metadata"] or "{}")}
            for r in rows
        ]

    async def list_edges(self, limit: int = 200) -> list[dict[str, Any]]:
        """List all edges for this project.

        Returns both node ids and labels so the visualizer can stitch
        edges to nodes by id while the list view can show labels.
        """
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT e.id, e.relationship, e.weight, e.created_at,
                       e.node_a_id, e.node_b_id,
                       na.label as node_a_label, na.node_type as node_a_type,
                       nb.label as node_b_label, nb.node_type as node_b_type
                FROM graph_edges e
                JOIN graph_nodes na ON na.id = e.node_a_id
                JOIN graph_nodes nb ON nb.id = e.node_b_id
                WHERE e.project_id = ?
                ORDER BY e.created_at DESC LIMIT ?
            """, (self.project_id, limit)).fetchall()
        # Frontend-friendly aliases: source/target = node ids
        out = []
        for r in rows:
            d = dict(r)
            d["source"] = d.get("node_a_id")
            d["target"] = d.get("node_b_id")
            out.append(d)
        return out

    async def delete_node(self, node_id: str) -> bool:
        """Delete a node and all its edges."""
        with self._connect() as conn:
            conn.execute("DELETE FROM graph_edges WHERE node_a_id = ? OR node_b_id = ?", (node_id, node_id))
            cursor = conn.execute("DELETE FROM graph_nodes WHERE id = ? AND project_id = ?", (node_id, self.project_id))
            conn.commit()
        return cursor.rowcount > 0

    async def delete_edge(self, edge_id: str) -> bool:
        """Delete an edge."""
        with self._connect() as conn:
            cursor = conn.execute(
                "DELETE FROM graph_edges WHERE id = ? AND project_id = ?",
                (edge_id, self.project_id)
            )
            conn.commit()
        return cursor.rowcount > 0

    async def search_nodes(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search nodes by label (text match)."""
        pattern = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT * FROM graph_nodes
                WHERE project_id = ? AND label LIKE ?
                ORDER BY label LIMIT ?
            """, (self.project_id, pattern, limit)).fetchall()
        return [
            {**dict(r), "metadata": json.loads(r["metadata"] or "{}")}
            for r in rows
        ]
