"""Artifacts API."""
from __future__ import annotations

import io
import logging
import zipfile
from typing import Any

from fastapi import APIRouter, HTTPException, Query, UploadFile, File, Form
from fastapi.responses import Response, StreamingResponse, FileResponse
from pydantic import BaseModel

from artifacts.store import get_store, is_text_type, MAX_TEXT_BYTES
from artifacts import embedder, preview as preview_mod

logger = logging.getLogger(__name__)
router = APIRouter()


class CreateArtifactRequest(BaseModel):
    project_id: str = "default"
    path: str
    content: str | None = None
    content_type: str = "text/markdown"
    title: str | None = None
    tags: list[str] | None = None
    source: dict[str, Any] | None = None
    edited_by: str = "user"


class UpdateArtifactRequest(BaseModel):
    content: str | None = None
    title: str | None = None
    tags: list[str] | None = None
    edit_summary: str | None = None
    edited_by: str = "user"


class RenameRequest(BaseModel):
    new_path: str


class MoveRequest(BaseModel):
    dest_project_id: str | None = None  # None ⇒ current project
    dest_folder: str = ""
    mode: str = "move"  # "move" | "duplicate"


class PinRequest(BaseModel):
    pinned: bool


class BulkTagsRequest(BaseModel):
    ids: list[str]
    tags: list[str]
    add: bool = True


class BulkIdsRequest(BaseModel):
    ids: list[str]


class BulkMoveRequest(BaseModel):
    ids: list[str]
    dest_project_id: str | None = None
    dest_folder: str = ""
    mode: str = "move"


# ── CRUD ────────────────────────────────────────────────────

@router.post("/artifacts")
async def create_artifact(req: CreateArtifactRequest) -> dict[str, Any]:
    if req.content is None:
        raise HTTPException(status_code=400, detail="content required for text-create; use /artifacts/upload for binary")
    try:
        a = get_store().create(
            project_id=req.project_id,
            path=req.path,
            content=req.content,
            content_type=req.content_type,
            title=req.title,
            tags=req.tags,
            source=req.source,
            edited_by=req.edited_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    embedder.schedule_embed(a["id"], req.project_id)
    return a


@router.post("/artifacts/upload")
async def upload_artifact(
    file: UploadFile = File(...),
    project_id: str = Form("default"),
    path: str = Form(...),
    title: str | None = Form(None),
    tags: str | None = Form(None),  # JSON-encoded list
) -> dict[str, Any]:
    blob = await file.read()
    content_type = file.content_type or "application/octet-stream"
    parsed_tags: list[str] | None = None
    if tags:
        import json
        try:
            parsed_tags = list(json.loads(tags))
        except Exception:
            parsed_tags = None
    try:
        a = get_store().create(
            project_id=project_id,
            path=path,
            content=blob,
            content_type=content_type,
            title=title or file.filename,
            tags=parsed_tags,
            source={"kind": "upload", "filename": file.filename},
            edited_by="user",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if is_text_type(content_type):
        embedder.schedule_embed(a["id"], project_id)
    return a


@router.get("/artifacts")
async def list_artifacts(
    project_id: str = Query("default"),
    tag: str | None = None,
    content_type: str | None = None,
    path_prefix: str | None = None,
    pinned_only: bool = False,
    search: str | None = None,
    sort: str = "modified_desc",
    limit: int = 200,
    offset: int = 0,
) -> dict[str, Any]:
    # 'all' or empty means cross-project browse
    if project_id in ("all", ""):
        items = get_store().list_all_projects(
            tag=tag, content_type=content_type,
            path_prefix=path_prefix, pinned_only=pinned_only, search=search,
            sort=sort, limit=limit, offset=offset,
        )
    else:
        items = get_store().list(
            project_id=project_id, tag=tag, content_type=content_type,
            path_prefix=path_prefix, pinned_only=pinned_only, search=search,
            sort=sort, limit=limit, offset=offset,
        )
    # Strip heavy fields the list UI never reads — keeps the payload from
    # ballooning to multiple MB on large projects.
    for item in items:
        item.pop("content", None)
        item.pop("blob_path", None)
    return {"artifacts": items, "count": len(items)}


@router.get("/artifacts/folders")
async def list_folders(project_id: str = Query("default")) -> dict[str, Any]:
    if project_id in ("all", ""):
        return {"folders": get_store().folder_tree_all()}
    return {"folders": get_store().folder_tree(project_id)}


@router.get("/artifacts/tags")
async def list_tags(project_id: str = Query("default")) -> dict[str, Any]:
    if project_id in ("all", ""):
        return {"tags": get_store().tag_counts_all()}
    return {"tags": get_store().tag_counts(project_id)}


@router.post("/artifacts/bulk/move")
async def bulk_move(req: BulkMoveRequest) -> dict[str, Any]:
    store = get_store()
    if req.mode not in ("move", "duplicate"):
        raise HTTPException(status_code=400, detail=f"invalid mode: {req.mode}")
    results: list[dict[str, Any]] = []
    for aid in req.ids:
        try:
            src = store.get(aid)
            if not src:
                results.append({"id": aid, "error": "artifact not found"})
                continue
            dest_project = req.dest_project_id or src["project_id"]
            cross = dest_project != src["project_id"]
            if req.mode == "duplicate":
                new_row = store.duplicate(aid, dest_project, req.dest_folder)
                try:
                    from memory.manager import create_memory_manager
                    mgr = create_memory_manager(project_id=dest_project)
                    await mgr.index_artifact(new_row["id"], force=True)
                except Exception as e:
                    logger.warning("index_artifact failed for dup %s: %s", new_row["id"], e)
                results.append({
                    "id": new_row["id"],
                    "src_id": aid,
                    "old_path": src["path"],
                    "new_path": new_row["path"],
                    "new_project_id": new_row["project_id"],
                    "mode": "duplicate",
                })
            else:
                updated = store.move(aid, dest_project, req.dest_folder)
                if cross:
                    try:
                        from memory.graph import GraphMemory
                        from memory.semantic import SemanticMemory
                        await GraphMemory(project_id=src["project_id"]).strip_artifact(aid)
                        await SemanticMemory(project_id=src["project_id"]).strip_artifact(aid)
                        from memory.manager import create_memory_manager
                        mgr = create_memory_manager(project_id=dest_project)
                        await mgr.index_artifact(aid, force=True)
                    except Exception as e:
                        logger.warning("memory steps failed for move %s: %s", aid, e)
                results.append({
                    "id": aid,
                    "old_path": src["path"],
                    "new_path": updated["path"],
                    "new_project_id": updated["project_id"],
                    "mode": "move",
                })
        except Exception as e:
            logger.exception("bulk_move row failed")
            results.append({"id": aid, "error": str(e)})
    return {"results": results}


@router.get("/artifacts/{artifact_id}")
async def get_artifact(artifact_id: str) -> dict[str, Any]:
    a = get_store().get(artifact_id)
    if not a:
        raise HTTPException(status_code=404, detail="not found")
    return a


@router.get("/artifacts/{artifact_id}/raw")
async def raw_artifact(artifact_id: str):
    store = get_store()
    a = store.get(artifact_id)
    if not a:
        raise HTTPException(status_code=404, detail="not found")
    if a.get("content") is not None:
        return Response(content=(a["content"] or ""), media_type=a["content_type"])
    if a.get("blob_path"):
        return Response(content=store._load_blob(a["blob_path"]), media_type=a["content_type"])
    raise HTTPException(status_code=500, detail="artifact has no content")


@router.get("/artifacts/{artifact_id}/preview")
async def preview_artifact(artifact_id: str) -> dict[str, Any]:
    store = get_store()
    a = store.get(artifact_id)
    if not a:
        raise HTTPException(status_code=404, detail="not found")
    blob = None
    if a.get("blob_path"):
        blob = store._load_blob(a["blob_path"])
    return await preview_mod.render_preview(a, blob)


@router.get("/artifacts/{artifact_id}/preview-pdf")
async def preview_pdf(artifact_id: str):
    store = get_store()
    a = store.get(artifact_id)
    if not a:
        raise HTTPException(status_code=404, detail="not found")
    p = await preview_mod.get_preview_pdf(artifact_id, a["current_version_id"])
    if not p:
        raise HTTPException(status_code=404, detail="no PDF preview cached")
    return FileResponse(p, media_type="application/pdf")


@router.patch("/artifacts/{artifact_id}")
async def update_artifact(artifact_id: str, req: UpdateArtifactRequest) -> dict[str, Any]:
    try:
        a = get_store().update(
            artifact_id,
            content=req.content,
            title=req.title,
            tags=req.tags,
            edit_summary=req.edit_summary,
            edited_by=req.edited_by,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="not found")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if req.content is not None:
        preview_mod.invalidate_for_artifact(artifact_id)
        embedder.schedule_embed(artifact_id, a["project_id"])
    return a


@router.post("/artifacts/{artifact_id}/rename")
async def rename_artifact(artifact_id: str, req: RenameRequest) -> dict[str, Any]:
    try:
        return get_store().rename(artifact_id, req.new_path)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/artifacts/{artifact_id}/move")
async def move_artifact(artifact_id: str, req: MoveRequest) -> dict[str, Any]:
    store = get_store()
    src = store.get(artifact_id)
    if not src:
        raise HTTPException(status_code=404, detail="artifact not found")
    if req.mode not in ("move", "duplicate"):
        raise HTTPException(status_code=400, detail=f"invalid mode: {req.mode}")
    dest_project = req.dest_project_id or src["project_id"]

    if req.mode == "duplicate":
        new_row = store.duplicate(artifact_id, dest_project, req.dest_folder)
        # Re-extract in dest (best-effort).
        try:
            from memory.manager import create_memory_manager
            mgr = create_memory_manager(project_id=dest_project)
            await mgr.index_artifact(new_row["id"], force=True)
        except Exception as e:
            logger.warning("index_artifact failed for duplicate %s: %s", new_row["id"], e)
        return new_row

    # mode == "move"
    cross_project = dest_project != src["project_id"]
    updated = store.move(artifact_id, dest_project, req.dest_folder)
    if cross_project:
        # Strip source project's memory for this artifact.
        try:
            from memory.graph import GraphMemory
            from memory.semantic import SemanticMemory
            src_graph = GraphMemory(project_id=src["project_id"])
            src_sem = SemanticMemory(project_id=src["project_id"])
            await src_graph.strip_artifact(artifact_id)
            await src_sem.strip_artifact(artifact_id)
        except Exception as e:
            logger.warning("memory strip failed on move %s: %s", artifact_id, e)
        # Re-extract in dest project.
        try:
            from memory.manager import create_memory_manager
            mgr = create_memory_manager(project_id=dest_project)
            await mgr.index_artifact(artifact_id, force=True)
        except Exception as e:
            logger.warning("index_artifact failed for moved %s: %s", artifact_id, e)
    return updated


@router.post("/artifacts/{artifact_id}/pin")
async def pin_artifact(artifact_id: str, req: PinRequest) -> dict[str, Any]:
    return get_store().pin(artifact_id, req.pinned)


@router.delete("/artifacts/{artifact_id}")
async def delete_artifact(artifact_id: str) -> dict[str, str]:
    a = get_store().get(artifact_id)
    if not a:
        raise HTTPException(status_code=404, detail="not found")
    get_store().soft_delete(artifact_id)
    # Drop semantic vectors
    try:
        await embedder.drop_for_artifact(artifact_id, a["project_id"])
    except Exception:
        logger.debug("drop semantic vectors failed", exc_info=True)
    preview_mod.invalidate_for_artifact(artifact_id)
    return {"status": "deleted", "id": artifact_id}


@router.post("/artifacts/{artifact_id}/restore")
async def restore_artifact(artifact_id: str) -> dict[str, Any]:
    return get_store().restore(artifact_id)


# ── Versions ────────────────────────────────────────────────

@router.get("/artifacts/{artifact_id}/versions")
async def list_versions(artifact_id: str) -> dict[str, Any]:
    return {"versions": get_store().list_versions(artifact_id)}


@router.get("/artifacts/{artifact_id}/versions/{n}")
async def get_version(artifact_id: str, n: int) -> dict[str, Any]:
    v = get_store().get_version(artifact_id, n)
    if not v:
        raise HTTPException(status_code=404, detail="version not found")
    return v


@router.get("/artifacts/{artifact_id}/diff")
async def diff_versions(
    artifact_id: str, a: int = Query(...), b: int = Query(...)
) -> dict[str, Any]:
    diff = get_store().diff(artifact_id, a, b)
    if diff is None:
        raise HTTPException(status_code=400, detail="diff unavailable (binary or missing version)")
    return {"a": a, "b": b, "diff": diff}


@router.post("/artifacts/{artifact_id}/versions/{n}/restore")
async def restore_version(artifact_id: str, n: int) -> dict[str, Any]:
    try:
        a = get_store().restore_version(artifact_id, n)
    except KeyError:
        raise HTTPException(status_code=404, detail="version not found")
    embedder.schedule_embed(artifact_id, a["project_id"])
    return a


# ── Bulk ────────────────────────────────────────────────────

@router.post("/artifacts/bulk/tags")
async def bulk_tags(req: BulkTagsRequest) -> dict[str, int]:
    store = get_store()
    if req.add:
        n = store.bulk_add_tags(req.ids, req.tags)
    else:
        n = store.bulk_remove_tags(req.ids, req.tags)
    return {"updated": n}


@router.post("/artifacts/bulk/delete")
async def bulk_delete(req: BulkIdsRequest) -> dict[str, int]:
    n = get_store().bulk_delete(req.ids)
    return {"deleted": n}


@router.post("/artifacts/bulk/export")
async def bulk_export(req: BulkIdsRequest) -> StreamingResponse:
    blob = get_store().bulk_export(req.ids)
    return StreamingResponse(
        io.BytesIO(blob),
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=artifacts.zip"},
    )


@router.get("/artifacts/export-all")
async def export_all(project_id: str = Query("default")) -> StreamingResponse:
    items = get_store().list(project_id=project_id, limit=10_000)
    blob = get_store().bulk_export([a["id"] for a in items])
    return StreamingResponse(
        io.BytesIO(blob),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={project_id}-artifacts.zip"},
    )
