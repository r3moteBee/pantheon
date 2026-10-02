"""Project delete removes everything the project owns (api/project_purge.py), and
export/import carries artifacts. Before 2026-10-02 a delete removed only the
project folder: conversations, notes, graph, artifacts, jobs and both vector
stores stayed, and export had no artifacts at all."""
from __future__ import annotations

import io
import json
import os
import sqlite3
import tempfile
import zipfile
from unittest.mock import patch

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))


class _Vault:
    def __init__(self):
        self.d = {}

    def get_secret(self, k, default=None):
        return self.d.get(k, default)

    def set_secret(self, k, v):
        self.d[k] = v

    def delete_secret(self, k):
        return self.d.pop(k, None) is not None


@pytest.fixture
def env(tmp_path):
    """Isolated data dir with two projects, 'doomed' and 'keep', each with data in every store."""
    from config import get_settings
    import artifacts.store as astore
    s = get_settings()
    vault = _Vault()
    schedules = [{"id": "sch1", "project_id": "doomed"}, {"id": "sch2", "project_id": "keep"}]
    cancelled = []

    def cancel_job(job_id):
        cancelled.append(job_id)
        schedules[:] = [j for j in schedules if j["id"] != job_id]
        return True
    import contextlib
    import api.project_export, api.project_import, api.projects
    stack = contextlib.ExitStack()
    # modules keep their own `settings = get_settings()`; another test may have swapped the cached one
    for obj in {id(m.settings): m.settings for m in (api.projects, api.project_export, api.project_import)}.values():
        if obj is not s:
            stack.enter_context(patch.object(obj, "data_dir", tmp_path))
    with stack, patch.object(s, "data_dir", tmp_path), patch.object(astore, "_INSTANCE", None), \
         patch("secrets.vault.get_vault", return_value=vault), \
         patch("tasks.scheduler.list_jobs", side_effect=lambda: list(schedules)), \
         patch("tasks.scheduler.cancel_job", cancel_job):
        s.db_dir.mkdir(parents=True)
        (s.db_dir / "projects.json").write_text(json.dumps({
            "default": {"id": "default", "name": "Default"},
            "doomed": {"id": "doomed", "name": "Doomed"}, "keep": {"id": "keep", "name": "Keep"}}))
        yield dict(s=s, vault=vault, cancelled=cancelled, tmp=tmp_path)


async def _fill(project_id: str, shared_blob: bytes):
    from config import get_settings
    from memory.episodic import EpisodicMemory
    from memory.graph import GraphMemory
    from artifacts.store import get_store
    s = get_settings()
    ep = EpisodicMemory(db_path=s.episodic_db_path)
    await ep.save_message(session_id=f"{project_id}-s", project_id=project_id, role="user", content="My dog is Rufus")
    await ep.add_note(content="note", project_id=project_id)
    g = GraphMemory(project_id=project_id, db_path=s.graph_db_path)
    await g.add_node(node_type="concept", label="Rufus")
    store = get_store()
    store.create(project_id=project_id, path="notes/a.md", content_type="text/markdown", content="# hello")
    store.create(project_id=project_id, path="img/a.png", content_type="image/png", content=shared_blob)
    store.create(project_id=project_id, path="img/own.png", content_type="image/png", content=project_id.encode() * 50)
    conn = sqlite3.connect(s.db_dir / "jobs.db")
    conn.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, project_id TEXT, status TEXT)")
    conn.execute("INSERT INTO jobs VALUES (?,?,?)", (f"{project_id}-j", project_id, "completed"))
    conn.commit(); conn.close()
    (s.projects_dir / project_id / "workspace").mkdir(parents=True, exist_ok=True)
    (s.projects_dir / project_id / "workspace" / "f.txt").write_text("x")
    import chromadb
    from memory.semantic import _sanitize_collection_name
    from memory.episodic import episodic_collection_name
    for d, name in ((project_id, _sanitize_collection_name(f"proj-{project_id}")),
                    (f"episodic-{project_id}", episodic_collection_name(project_id))):
        c = chromadb.PersistentClient(path=str(s.data_dir / "chroma" / d)).get_or_create_collection(name)
        c.add(ids=["1"], documents=["Rufus"], embeddings=[[0.1, 0.2, 0.3]])


@pytest.mark.asyncio
async def test_delete_removes_everything_the_project_owns(env):
    from api.project_purge import inventory
    from api.projects import delete_project, delete_preview
    s = env["s"]
    shared = b"\x89PNG shared" * 40
    await _fill("doomed", shared)
    await _fill("keep", shared)
    env["vault"].set_secret("skill_discovery_doomed", "auto")
    env["vault"].set_secret("messaging_default_project", "doomed")

    prev = await delete_preview("doomed")
    items = prev["items"]
    assert items["chat messages"] >= 1 and items["conversations"] == 1 and items["graph entities"] == 1
    assert items["memory notes"] == 1
    assert items["artifacts"] == 3 and items["background jobs"] == 1 and items["scheduled tasks"] == 1
    assert items["semantic memories and search chunks"] == 1 and items["message search vectors"] == 1
    assert items["files (workspace, notes, personality)"] == 1

    res = await delete_project("doomed")
    assert res["removed"]["artifacts"] == 3
    assert inventory("doomed") == {}                                   # nothing left behind
    assert env["cancelled"] == ["sch1"]
    assert not (s.data_dir / "chroma" / "doomed").exists() and not (s.data_dir / "chroma" / "episodic-doomed").exists()
    assert not (s.projects_dir / "doomed").exists()
    assert "skill_discovery_doomed" not in env["vault"].d and "messaging_default_project" not in env["vault"].d
    assert "doomed" not in json.loads((s.db_dir / "projects.json").read_text())
    # the other project is untouched - including the blob both projects share
    keep = inventory("keep")
    assert keep["artifacts"] == 3 and keep["conversations"] == 1 and keep["message search vectors"] == 1
    blobs = list((s.data_dir / "blobs").rglob("*"))
    assert len([b for b in blobs if b.is_file()]) == 2                 # shared + keep's own; doomed's own removed


@pytest.mark.asyncio
async def test_delete_refuses_while_a_job_runs(env):
    from api.projects import delete_project
    from fastapi import HTTPException
    s = env["s"]
    conn = sqlite3.connect(s.db_dir / "jobs.db")
    conn.execute("CREATE TABLE jobs (id TEXT PRIMARY KEY, project_id TEXT, status TEXT)")
    conn.execute("INSERT INTO jobs VALUES ('j1','doomed','running')")
    conn.commit(); conn.close()
    with pytest.raises(HTTPException) as e:
        await delete_project("doomed")
    assert e.value.status_code == 409
    assert "doomed" in json.loads((s.db_dir / "projects.json").read_text())


@pytest.mark.asyncio
async def test_export_includes_artifacts_and_import_restores_them(env):
    from api.project_export import export_project
    from api.project_import import import_project
    await _fill("keep", b"\x89PNG shared" * 40)
    archive = export_project("keep", components=["metadata", "artifacts"])
    zf = zipfile.ZipFile(io.BytesIO(archive))
    data = json.loads(zf.read("artifacts/artifacts.json"))
    assert len(data["artifacts"]) == 3 and len(data["versions"]) == 3
    assert len([n for n in zf.namelist() if n.startswith("artifacts/blobs/")]) == 2
    manifest = json.loads(zf.read("manifest.json"))
    assert manifest["stats"]["artifacts"] == 3

    with patch("artifacts.embedder.schedule_embed"):
        res = import_project(archive, target_project_id="copy", target_project_name="Copy")
    assert res.success, res.message
    assert res.stats["artifacts_imported"] == 3 and "artifacts" in res.components_imported
    conn = sqlite3.connect(env["s"].db_dir / "artifacts.db")
    rows = conn.execute("SELECT path, content, blob_path FROM artifacts WHERE project_id='copy' ORDER BY path").fetchall()
    assert [r[0] for r in rows] == ["img/a.png", "img/own.png", "notes/a.md"]
    assert rows[2][1] == "# hello" and rows[0][2]
    assert (env["s"].data_dir / "blobs" / rows[0][2]).read_bytes() == b"\x89PNG shared" * 40


def test_import_rejects_a_tampered_artifact_blob(env):
    from api.project_import import _import_artifacts
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("artifacts/artifacts.json", json.dumps({
            "artifacts": [{"id": "a1", "path": "x.png", "content_type": "image/png", "blob_path": "ab/" + "0" * 64,
                           "size_bytes": 3, "sha256": "0" * 64, "current_version_id": "v1"}],
            "versions": [{"id": "v1", "artifact_id": "a1", "version_number": 1, "blob_path": "ab/" + "0" * 64,
                          "size_bytes": 3, "sha256": "0" * 64}]}))
        zf.writestr("artifacts/blobs/ab/" + "0" * 64, b"evil")
    stats, warnings = {}, []
    with patch("artifacts.embedder.schedule_embed"):
        _import_artifacts(zipfile.ZipFile(io.BytesIO(buf.getvalue())), "p", stats, warnings)
    assert stats["artifacts_imported"] == 0 and "does not match its hash" in warnings[0]


def test_default_project_export_reads_data_workspace(env):
    from api.project_export import export_project
    s = env["s"]
    s.workspace_dir.mkdir(parents=True, exist_ok=True)
    (s.workspace_dir / "report.md").write_text("default workspace file")
    zf = zipfile.ZipFile(io.BytesIO(export_project("default", components=["files"])))
    assert zf.read("files/workspace/report.md") == b"default workspace file"


@pytest.mark.asyncio
async def test_import_next_to_the_original_copies_memory_with_fresh_ids(env):
    """Same-instance copies reused every id; the importer skipped those rows silently."""
    from api.project_export import export_project
    from api.project_import import import_project
    from api.project_purge import inventory
    from memory.graph import GraphMemory
    s = env["s"]
    await _fill("keep", b"\x89PNG shared" * 40)
    g = GraphMemory(project_id="keep", db_path=s.graph_db_path)
    a = await g.add_node(node_type="person", label="Brent")
    b = await g.add_node(node_type="concept", label="Pantheon")
    await g.add_edge(a, b, "builds")
    archive = export_project("keep", components=["metadata", "memory", "artifacts"])
    with patch("artifacts.embedder.schedule_embed"):
        res = import_project(archive, target_project_id="keep-copy", target_project_name="Copy")
    assert res.success and res.stats["conversations_imported"] == 1 and res.stats["notes_imported"] == 1
    assert res.stats["graph_nodes_imported"] == 3 and res.stats["graph_edges_imported"] == 1
    orig, copy = inventory("keep"), inventory("keep-copy")
    for label in ("conversations", "chat messages", "memory notes", "graph entities", "graph links", "artifacts"):
        assert copy.get(label) == orig.get(label), label
    conn = sqlite3.connect(s.episodic_db_path)
    sids = conn.execute("SELECT project_id, session_id FROM conversations WHERE project_id IN ('keep','keep-copy')").fetchall()
    assert len({sid for _, sid in sids}) == 2                          # the copy got its own session
    gc = sqlite3.connect(s.graph_db_path)
    edge = gc.execute("SELECT node_a_id, node_b_id FROM graph_edges WHERE project_id='keep-copy'").fetchone()
    owners = {r[0] for r in gc.execute("SELECT project_id FROM graph_nodes WHERE id IN (?,?)", edge)}
    assert owners == {"keep-copy"}                                     # edge points at the copy's nodes
