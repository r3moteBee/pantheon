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


# MIME types for inline viewing
_VIEW_MIME: dict[str, str] = {
    ".html": "text/html",
    ".htm": "text/html",
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".svg": "image/svg+xml",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".ico": "image/x-icon",
    # Text-based files (served as plain text for frontend rendering)
    ".md": "text/plain",
    ".markdown": "text/plain",
    ".txt": "text/plain",
    ".csv": "text/plain",
    ".json": "application/json",
    ".yaml": "text/plain",
    ".yml": "text/plain",
}


@router.get("/files/view")
async def view_file(
    path: str = Query(...),
    project_id: str = Query(default="default"),
) -> FileResponse:
    """Serve a file with its correct MIME type for inline viewing."""
    target = _safe_path(path, project_id)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    ext = target.suffix.lower()
    mime = _VIEW_MIME.get(ext)
    if not mime:
        raise HTTPException(status_code=415, detail=f"Unsupported file type for viewing: {ext}")
    return FileResponse(
        path=str(target),
        media_type=mime,
        # No filename= param → Content-Disposition: inline (renders in browser)
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
        )
        result = await indexer.index_file(file_path)
        if not result.get("skipped"):
            logger.info(
                "Auto-indexed uploaded file %s: %d chunks",
                file_path.name, result.get("chunks_stored", 0),
            )
    except Exception as e:
        logger.warning("Auto-indexing failed for %s: %s", file_path.name, e)


# ── Document Conversion Endpoints ────────────────────────────

from pydantic import BaseModel
import glob
from utils.document_converter import DocumentConverter, BinaryMissingError

class FileConvertRequest(BaseModel):
    paths: list[str]
    target_format: str
    out_dir: str | None = None
    save_as_artifact: bool = False

class ConversionResult(BaseModel):
    source_path: str
    target_path: str | None = None
    success: bool
    error: str | None = None
    size_bytes: int | None = None

class BatchConvertResponse(BaseModel):
    converted: list[ConversionResult]
    total_files: int
    successful_count: int
    failed_count: int

@router.post("/files/convert", response_model=BatchConvertResponse)
async def convert_files(
    req: FileConvertRequest,
    project_id: str = Query(default="default"),
) -> BatchConvertResponse:
    """Batch-convert files matching wildcards, folders, or individual paths."""
    base = _get_workspace(project_id)
    converter = DocumentConverter()
    
    # 1. Expand paths safely
    expanded_paths: list[Path] = []
    for pattern in req.paths:
        if ".." in pattern:
            raise HTTPException(status_code=400, detail=f"Path traversal not allowed: {pattern}")
        
        if any(char in pattern for char in ["*", "?", "[", "]"]):
            search_pattern = str(base / pattern)
            for matched_str in glob.glob(search_pattern, recursive=True):
                matched_path = Path(matched_str).resolve()
                if str(matched_path).startswith(str(base)) and matched_path.is_file():
                    expanded_paths.append(matched_path)
        else:
            try:
                target = _safe_path(pattern, project_id)
            except HTTPException:
                raise HTTPException(status_code=400, detail=f"Invalid path: {pattern}")
                
            if target.is_file():
                expanded_paths.append(target)
            elif target.is_dir():
                for p in target.rglob("*"):
                    if p.is_file():
                        expanded_paths.append(p)
            else:
                raise HTTPException(status_code=404, detail=f"Path not found: {pattern}")

    # Remove duplicates while preserving order
    seen = set()
    unique_paths = []
    for p in expanded_paths:
        if p not in seen:
            seen.add(p)
            unique_paths.append(p)

    if not unique_paths:
        return BatchConvertResponse(
            converted=[],
            total_files=0,
            successful_count=0,
            failed_count=0
        )

    # 2. Setup output directory
    out_dir_path: Path | None = None
    if req.out_dir:
        out_dir_path = _safe_path(req.out_dir, project_id)
        out_dir_path.mkdir(parents=True, exist_ok=True)

    results: list[ConversionResult] = []
    success_count = 0
    fail_count = 0

    for source_path in unique_paths:
        source_rel = str(source_path.relative_to(base))
        try:
            # Determine target filename and path
            target_name = f"{source_path.stem}.{req.target_format.lower()}"
            if out_dir_path:
                target_path = out_dir_path / target_name
            else:
                target_path = source_path.parent / target_name

            # Run conversion in a thread pool to avoid blocking the async event loop
            await asyncio.to_thread(
                converter.convert_file,
                source_path,
                target_path,
                req.target_format
            )

            # Ingest as artifact if requested
            if req.save_as_artifact:
                import mimetypes
                from artifacts.store import get_store, is_text_type
                from artifacts import embedder
                
                content_type = mimetypes.guess_type(str(target_path))[0] or "application/octet-stream"
                is_txt = is_text_type(content_type)
                if is_txt:
                    try:
                        content = target_path.read_text(encoding="utf-8")
                    except Exception:
                        content = target_path.read_bytes()
                        is_txt = False
                else:
                    content = target_path.read_bytes()

                store = get_store()
                target_rel = str(target_path.relative_to(base))
                a = store.create(
                    project_id=project_id,
                    path=target_rel,
                    content=content,
                    content_type=content_type,
                    title=target_path.name,
                    tags=["converted", req.target_format.lower()],
                    source={
                        "kind": "conversion",
                        "source_file": source_rel
                    },
                    edited_by="agent",
                )
                if is_txt:
                    embedder.schedule_embed(a["id"], project_id)

            size_bytes = target_path.stat().st_size if target_path.exists() else None
            results.append(
                ConversionResult(
                    source_path=source_rel,
                    target_path=str(target_path.relative_to(base)),
                    success=True,
                    size_bytes=size_bytes
                )
            )
            success_count += 1

        except BinaryMissingError as e:
            results.append(
                ConversionResult(
                    source_path=source_rel,
                    success=False,
                    error=str(e)
                )
            )
            fail_count += 1
        except Exception as e:
            results.append(
                ConversionResult(
                    source_path=source_rel,
                    success=False,
                    error=str(e)
                )
            )
            fail_count += 1

    return BatchConvertResponse(
        converted=results,
        total_files=len(results),
        successful_count=success_count,
        failed_count=fail_count
    )

