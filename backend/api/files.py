"""File repository API — upload, download, list, delete workspace files.

Enhanced with automatic semantic indexing of uploaded files.
"""
from __future__ import annotations
import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import io
import zipfile

import aiofiles
from fastapi import APIRouter, Body, HTTPException, UploadFile, File, Query
from typing import List as TList
from fastapi.responses import FileResponse, StreamingResponse

from config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()
router = APIRouter()


def _get_workspace(project_id: str = "default") -> Path:
    if project_id and project_id != "default":
        path = settings.projects_dir / project_id / "workspace"
    else:
        path = settings.workspace_dir
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _safe_path(rel_path: str, project_id: str = "default") -> Path:
    base = _get_workspace(project_id)
    target = (base / rel_path).resolve()
    if not str(target).startswith(str(base)):
        raise HTTPException(status_code=400, detail="Path traversal denied")
    return target


@router.get("/files")
async def list_files(
    project_id: str = Query(default="default"),
    path: str = Query(default=""),
) -> dict[str, Any]:
    """List files and directories in the workspace."""
    try:
        base = _get_workspace(project_id)
        target = _safe_path(path, project_id) if path else base
        if not target.exists():
            return {"files": [], "directories": [], "path": path}

        files = []
        directories = []
        for item in sorted(target.iterdir()):
            rel = str(item.relative_to(base))
            stat = item.stat()
            if item.is_dir():
                directories.append({
                    "name": item.name,
                    "path": rel,
                    "type": "directory",
                })
            else:
                files.append({
                    "name": item.name,
                    "path": rel,
                    "type": "file",
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                    "extension": item.suffix.lower(),
                })

        return {
            "files": files,
            "directories": directories,
            "path": path,
            "total_files": len(files),
            "total_dirs": len(directories),
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    project_id: str = Query(default="default"),
    path: str = Query(default=""),
) -> dict[str, Any]:
    """Upload a file to the workspace."""
    filename = file.filename or "upload"
    # Sanitize filename
    filename = Path(filename).name  # Strip any path components
    dest_dir = _safe_path(path, project_id) if path else _get_workspace(project_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / filename

    # Handle filename conflicts
    if dest_path.exists():
        stem = dest_path.stem
        suffix = dest_path.suffix
        counter = 1
        while dest_path.exists():
            dest_path = dest_dir / f"{stem}_{counter}{suffix}"
            counter += 1

    content = await file.read()
    async with aiofiles.open(dest_path, "wb") as f:
        await f.write(content)

    base = _get_workspace(project_id)
    rel_path = str(dest_path.relative_to(base))

    # Auto-index the uploaded file if enabled
    indexed = False
    if settings.auto_index_uploads:
        asyncio.ensure_future(_index_uploaded_file(dest_path, project_id))
        indexed = True

    return {
        "status": "uploaded",
        "filename": dest_path.name,
        "path": rel_path,
        "size": len(content),
        "indexing": indexed,
    }


@router.post("/files/upload-multiple")
async def upload_multiple_files(
    files: TList[UploadFile] = File(...),
    project_id: str = Query(default="default"),
    path: str = Query(default=""),
) -> dict[str, Any]:
    """Upload multiple files to the workspace at once."""
    dest_dir = _safe_path(path, project_id) if path else _get_workspace(project_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    base = _get_workspace(project_id)
    results = []

    for file in files:
        filename = file.filename or "upload"
        filename = Path(filename).name
        dest_path = dest_dir / filename

        # Handle filename conflicts
        if dest_path.exists():
            stem = dest_path.stem
            suffix = dest_path.suffix
            counter = 1
            while dest_path.exists():
                dest_path = dest_dir / f"{stem}_{counter}{suffix}"
                counter += 1

        content = await file.read()
        async with aiofiles.open(dest_path, "wb") as f:
            await f.write(content)

        results.append({
            "filename": dest_path.name,
            "path": str(dest_path.relative_to(base)),
            "size": len(content),
        })

    return {
        "status": "uploaded",
        "count": len(results),
        "files": results,
    }


@router.get("/files/download")
async def download_file(
    path: str = Query(...),
    project_id: str = Query(default="default"),
) -> FileResponse:
    """Download a file from the workspace."""
    target = _safe_path(path, project_id)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type="application/octet-stream",
    )


@router.post("/files/download-zip")
async def download_zip(
    project_id: str = Query(default="default"),
    body: dict[str, Any] = Body(...),
) -> StreamingResponse:
    """Bundle multiple workspace files/folders into a single zip and stream it.

    Body: {"paths": ["rel/path1", "rel/path2", ...]}
    Folders are included recursively. Paths are validated against the workspace root.
    """
    paths = body.get("paths") or []
    if not isinstance(paths, list) or not paths:
        raise HTTPException(status_code=400, detail="Missing 'paths' list")
    if len(paths) > 1000:
        raise HTTPException(status_code=400, detail="Too many paths (max 1000)")

    base = _get_workspace(project_id)
    buf = io.BytesIO()
    total_bytes = 0
    MAX_BYTES = 2 * 1024 * 1024 * 1024  # 2 GB safety cap

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in paths:
            if not isinstance(rel, str) or not rel:
                continue
            target = _safe_path(rel, project_id)
            if not target.exists():
                continue
            if target.is_file():
                total_bytes += target.stat().st_size
                if total_bytes > MAX_BYTES:
                    raise HTTPException(status_code=413, detail="Selection exceeds 2GB limit")
                zf.write(target, arcname=str(target.relative_to(base)))
            elif target.is_dir():
                for sub in target.rglob("*"):
                    if sub.is_file():
                        total_bytes += sub.stat().st_size
                        if total_bytes > MAX_BYTES:
                            raise HTTPException(status_code=413, detail="Selection exceeds 2GB limit")
                        zf.write(sub, arcname=str(sub.relative_to(base)))

    buf.seek(0)
    zip_name = f"{project_id}-files.zip"
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}"'},
    )


@router.get("/files/read")
async def read_file_content(
    path: str = Query(...),
    project_id: str = Query(default="default"),
) -> dict[str, Any]:
    """Read file content as text."""
    target = _safe_path(path, project_id)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
        return {
            "path": path,
            "content": content,
            "size": target.stat().st_size,
            "encoding": "utf-8",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/files/write")
async def write_file_content(
    path: str = Query(...),
    project_id: str = Query(default="default"),
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """Write text content to an existing or new file in the workspace."""
    content = body.get("content")
    if content is None:
        raise HTTPException(status_code=400, detail="Missing 'content' field")
    target = _safe_path(path, project_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    async with aiofiles.open(target, "w", encoding="utf-8") as f:
        await f.write(content)
    return {
        "status": "saved",
        "path": path,
        "size": len(content.encode("utf-8")),
    }


@router.delete("/files")
async def delete_file(
    path: str = Query(...),
    project_id: str = Query(default="default"),
) -> dict[str, str]:
    """Delete a file from the workspace."""
    target = _safe_path(path, project_id)
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    if target.is_dir():
        import shutil
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"status": "deleted", "path": path}


@router.post("/files/mkdir")
async def create_directory(
    path: str = Query(...),
    project_id: str = Query(default="default"),
) -> dict[str, str]:
    """Create a directory in the workspace."""
    target = _safe_path(path, project_id)
    target.mkdir(parents=True, exist_ok=True)
    return {"status": "created", "path": path}


@router.post("/files/index")
async def index_file_or_directory(
    path: str = Query(default=""),
    project_id: str = Query(default="default"),
    force: bool = Query(default=False),
) -> dict[str, Any]:
    """Manually trigger indexing of a file or directory into semantic memory."""
    base = _get_workspace(project_id)
    target = _safe_path(path, project_id) if path else base

    try:
        from memory.manager import create_memory_manager
        from memory.file_indexer import FileIndexer
        memory = create_memory_manager(project_id=project_id)
        indexer = FileIndexer(
            memory_manager=memory,
            project_id=project_id,
            chunk_size=settings.file_chunk_size,
            chunk_overlap=settings.file_chunk_overlap,
        )

        if target.is_file():
            result = await indexer.index_file(target, force=force)
        elif target.is_dir():
            result = await indexer.index_directory(target, force=force)
        else:
            raise HTTPException(status_code=404, detail="Path not found")

        return {"status": "indexed", **result}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/files/index-status")
async def get_index_status(
    project_id: str = Query(default="default"),
) -> dict[str, Any]:
    """Get the indexing status of files in the workspace."""
    try:
        from memory.file_indexer import FileIndex
        file_index = FileIndex()
        indexed = file_index.list_indexed(project_id)
        return {
            "project_id": project_id,
            "indexed_files": indexed,
            "total": len(indexed),
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


async def _index_uploaded_file(file_path: Path, project_id: str) -> None:
    """Background task to index a newly uploaded file."""
    try:
        from memory.file_indexer import SUPPORTED_EXTENSIONS
        if file_path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            return

        from memory.manager import create_memory_manager
        from memory.file_indexer import FileIndexer
        memory = create_memory_manager(project_id=project_id)
        indexer = FileIndexer(
            memory_manager=memory,
            project_id=project_id,
            chunk_size=settings.file_chunk_size,
            chunk_overlap=settings.file_chunk_overlap,
        )
        result = await indexer.index_file(file_path)
        if not result.get("skipped"):
            logger.info(
                "Auto-indexed uploaded file %s: %d chunks",
                file_path.name, result.get("chunks_stored", 0),
            )
    except Exception as e:
        logger.warning("Auto-indexing failed for %s: %s", file_path.name, e)
