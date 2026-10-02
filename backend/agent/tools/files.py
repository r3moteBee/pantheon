"""Workspace file tools: read/write/list, show, download, document conversion."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
import httpx
from utils.paths import is_within, safe_filename
from artifacts.conversions import save_converted_artifact
from agent.tools.registry import ToolContext, tool
from agent.tools import workspace as _ws


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a scratch file from the project workspace (sandbox runs, "
                "raw uploads). Saved notes, transcripts and reports are "
                "artifacts — use read_artifact for those."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file within the workspace"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": (
                "Write a scratch file to the project workspace. Not indexed and "
                "may be cleaned up — anything worth keeping goes to "
                "save_to_artifact."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file within the workspace"},
                    "content": {"type": "string", "description": "Content to write to the file"}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_workspace_files",
            "description": (
                "List scratch files in the project workspace. Saved artifacts "
                "are not here — use list_artifacts(path_prefix='NBJ/') to "
                "browse or verify them."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Subdirectory path to list (default: root workspace)", "default": ""}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "show_file",
            "description": "Display a file inline in the chat UI. Supports images (png/jpg/gif/svg/webp), PDFs, HTML, markdown, and text files. Use this instead of read_file when the user asks to 'show', 'display', or 'view' a file. The file will be rendered as a visual preview in the chat. IMPORTANT: Only call this ONCE per file — a single successful call displays the file. Never retry or call again for the same file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file within the workspace"},
                    "caption": {"type": "string", "description": "Optional caption to display below the file"}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "download_file",
            "description": "Download a file from a URL and save it to the workspace. Use this when the user asks you to download, fetch, or save a file from the internet (PDFs, images, documents, data files, etc.). The file is saved to the specified path in the workspace and can then be viewed with show_file or read with read_file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "The URL to download the file from"},
                    "path": {"type": "string", "description": "Relative path in workspace to save the file (e.g., 'documents/report.pdf'). Directories are created automatically."},
                    "filename": {"type": "string", "description": "Optional filename override. If omitted, derived from the URL or Content-Disposition header."}
                },
                "required": ["url", "path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "convert_document",
            "description": "Convert a single document in the workspace from one format to another (e.g. .md to .html, .docx to .pdf, .pdf to .txt). Note: High-fidelity conversions to PDF require LibreOffice, and Markdown-to-DOCX requires Pandoc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "source_path": {
                        "type": "string",
                        "description": "Relative path to the source file in the workspace (e.g., 'reports/doc.md')"
                    },
                    "target_format": {
                        "type": "string",
                        "description": "The desired target format extension (e.g. 'html', 'pdf', 'docx', 'md', 'txt')"
                    },
                    "out_dir": {
                        "type": "string",
                        "description": "Optional relative output directory. If omitted, target will be placed in the same folder as the source file."
                    },
                    "save_as_artifact": {
                        "type": "boolean",
                        "description": "Whether to also ingest the converted file as a permanent artifact in the store (default: false)."
                    }
                },
                "required": ["source_path", "target_format"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "batch_convert_documents",
            "description": "Batch-convert multiple files matching wildcards, folder names, or specific paths in the workspace into another format.",
            "parameters": {
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of relative paths, folders, or wildcards to match (e.g., ['reports/*.docx', 'data/txt/'])"
                    },
                    "target_format": {
                        "type": "string",
                        "description": "The desired target format extension (e.g. 'html', 'pdf', 'docx', 'md', 'txt')"
                    },
                    "out_dir": {
                        "type": "string",
                        "description": "Optional relative output directory. If omitted, target will be placed in the same folder as the source file."
                    },
                    "save_as_artifact": {
                        "type": "boolean",
                        "description": "Whether to also ingest each converted file as a permanent artifact in the store (default: false)."
                    }
                },
                "required": ["paths", "target_format"]
            }
        }
    },
]


@tool('show_file')
async def _tool_show_file(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    project_id = ctx.project_id
    safe_path = _ws._safe_workspace_path(tool_args["path"], project_id)
    if not safe_path.exists():
        return f"File not found: {tool_args['path']}"
    rel_path = tool_args["path"]
    from urllib.parse import quote
    encoded_path = quote(rel_path, safe="/")
    # Return structured result: display directive for frontend + clear success for LLM
    # The [DISPLAY:...] tag is parsed by the frontend to render a preview.
    # IMPORTANT: Do NOT call show_file again for this file — it is already displayed.
    size_kb = safe_path.stat().st_size / 1024
    return (
        f"[DISPLAY:workspace://{encoded_path}]\n"
        f"Successfully displayed {safe_path.name} ({size_kb:.0f}KB) inline in the chat. "
        f"The user can now see the file. Do not call show_file again for this file."
    )



@tool('download_file')
async def _tool_download_file(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    project_id = ctx.project_id
    url = tool_args.get("url", "")
    dest_path_str = tool_args.get("path", "")
    if not url or not dest_path_str:
        return "Error: both 'url' and 'path' are required"

    from urllib.parse import urlparse, unquote
    if urlparse(url).scheme.lower() not in ("http", "https"):
        return "Error: only http(s) URLs can be downloaded"

    base = _ws._get_workspace_base(project_id)
    safe_path = _ws._safe_workspace_path(dest_path_str, project_id)
    # Directory-like destination: no extension, or an existing dir.
    dir_like = safe_path.is_dir() or not safe_path.suffix

    filename = tool_args.get("filename")
    if filename:
        name = safe_filename(filename)
        safe_path = (safe_path / name) if dir_like else (safe_path.parent / name)
    elif dir_like:
        url_name = unquote(urlparse(url).path.split("/")[-1])
        safe_path = safe_path / safe_filename(url_name)

    try:
        from utils.net import UnsafeURLError, safe_http_get
        try:
            resp = await safe_http_get(url, timeout=60.0)
        except UnsafeURLError as e:
            return f"Download refused: {e}"
        resp.raise_for_status()
        # Still no extension — try Content-Disposition. The header
        # is server-controlled, so keep only its basename.
        if not filename and not safe_path.suffix:
            cd = resp.headers.get("content-disposition", "")
            if "filename=" in cd:
                import re as _re
                match = _re.search(r'filename[*]?=["\']?([^"\';]+)', cd)
                if match:
                    safe_path = safe_path.parent / safe_filename(
                        unquote(match.group(1).strip()), safe_path.name,
                    )

        if not is_within(safe_path, base):
            return f"Error: download path escapes the workspace: {safe_path.name}"
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        safe_path.write_bytes(resp.content)
        size_kb = len(resp.content) / 1024
        content_type = resp.headers.get("content-type", "unknown")
        return (
            f"Downloaded {safe_path.name} ({size_kb:.1f}KB, {content_type}) "
            f"to {safe_path.relative_to(base)}. Use show_file to display it or read_file to read its contents."
        )
    except httpx.HTTPStatusError as e:
        return f"Download failed: HTTP {e.response.status_code} from {url}"
    except httpx.RequestError as e:
        return f"Download failed: {type(e).__name__}: {e}"
    except Exception as e:
        return f"Download failed: {e}"



@tool('convert_document')
async def _tool_convert_document(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    project_id = ctx.project_id
    effective_project = ctx.effective_project
    from utils.document_converter import validate_format
    try:
        tool_args["target_format"] = validate_format(tool_args.get("target_format", ""))
    except ValueError as e:
        return f"Conversion failed: {e}"
    source_path_str = tool_args["source_path"]
    target_format = tool_args["target_format"]
    out_dir = tool_args.get("out_dir")
    save_as_artifact = tool_args.get("save_as_artifact", False)

    try:
        source_path = _ws._safe_workspace_path(source_path_str, project_id)
        base = _ws._get_workspace_base(project_id)

        # Setup output path
        target_name = f"{source_path.stem}.{target_format.lower()}"
        if out_dir:
            out_dir_path = _ws._safe_workspace_path(out_dir, project_id)
            out_dir_path.mkdir(parents=True, exist_ok=True)
            target_path = out_dir_path / target_name
        else:
            target_path = source_path.parent / target_name

        from utils.document_converter import DocumentConverter, BinaryMissingError
        converter = DocumentConverter()

        # Run conversion (blocking call, so run in thread using to_thread)
        await asyncio.to_thread(converter.convert_file, source_path, target_path, target_format)

        # Ingest as artifact if requested
        if save_as_artifact:
            save_converted_artifact(
                project_id=effective_project, target_path=target_path, base=base,
                source_rel=str(source_path.relative_to(base)), fmt=target_format,
            )

        size_kb = target_path.stat().st_size / 1024
        return (
            f"Successfully converted document {source_path_str} to format {target_format} "
            f"({size_kb:.1f}KB). Target file is saved at: {target_path.relative_to(base)}."
        )
    except BinaryMissingError as e:
        return f"Conversion failed: {e}"
    except Exception as e:
        return f"Conversion failed: {e}"



@tool('batch_convert_documents')
async def _tool_batch_convert_documents(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    project_id = ctx.project_id
    effective_project = ctx.effective_project
    from utils.document_converter import validate_format
    try:
        tool_args["target_format"] = validate_format(tool_args.get("target_format", ""))
    except ValueError as e:
        return f"Conversion failed: {e}"
    paths = tool_args["paths"]
    target_format = tool_args["target_format"]
    out_dir = tool_args.get("out_dir")
    save_as_artifact = tool_args.get("save_as_artifact", False)

    try:
        import glob
        base = _ws._get_workspace_base(project_id)

        # 1. Expand paths safely
        expanded_paths: list[Path] = []
        for pattern in paths:
            if ".." in pattern:
                return f"Error: Path traversal not allowed: {pattern}"

            if any(char in pattern for char in ["*", "?", "[", "]"]):
                search_pattern = str(base / pattern)
                for matched_str in glob.glob(search_pattern, recursive=True):
                    matched_path = Path(matched_str).resolve()
                    if is_within(matched_path, base) and matched_path.is_file():
                        expanded_paths.append(matched_path)
            else:
                target = _ws._safe_workspace_path(pattern, project_id)
                if target.is_file():
                    expanded_paths.append(target)
                elif target.is_dir():
                    for p in target.rglob("*"):
                        if p.is_file():
                            expanded_paths.append(p)
                else:
                    return f"Error: Path not found: {pattern}"

        # Deduplicate
        seen = set()
        unique_paths = []
        for p in expanded_paths:
            if p not in seen:
                seen.add(p)
                unique_paths.append(p)

        if not unique_paths:
            return "No matching files found for batch conversion."

        # 2. Setup output directory
        out_dir_path: Path | None = None
        if out_dir:
            out_dir_path = _ws._safe_workspace_path(out_dir, project_id)
            out_dir_path.mkdir(parents=True, exist_ok=True)

        from utils.document_converter import DocumentConverter
        converter = DocumentConverter()

        converted_files = []
        failed_files = []

        for source_path in unique_paths:
            source_rel = str(source_path.relative_to(base))
            try:
                target_name = f"{source_path.stem}.{target_format.lower()}"
                if out_dir_path:
                    target_path = out_dir_path / target_name
                else:
                    target_path = source_path.parent / target_name

                await asyncio.to_thread(converter.convert_file, source_path, target_path, target_format)

                # Ingest as artifact if requested
                if save_as_artifact:
                    save_converted_artifact(
                        project_id=effective_project, target_path=target_path,
                        base=base, source_rel=source_rel, fmt=target_format,
                    )

                converted_files.append(f"{source_rel} -> {target_path.relative_to(base)}")
            except Exception as e:
                failed_files.append(f"{source_rel}: {e}")

        summary = []
        if converted_files:
            summary.append("Successfully converted:")
            summary.extend(f"  - {f}" for f in converted_files)
        if failed_files:
            summary.append("Failed conversions:")
            summary.extend(f"  - {f}" for f in failed_files)

        return "\n".join(summary)

    except Exception as e:
        return f"Batch conversion process failed: {e}"



@tool('read_file')
async def _tool_read_file(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    project_id = ctx.project_id
    safe_path = _ws._safe_workspace_path(tool_args["path"], project_id)
    if not safe_path.exists():
        return f"File not found: {tool_args['path']}"

    suffix = safe_path.suffix.lower()

    # PDF — extract text with pdfplumber, vision OCR fallback for scanned pages
    if suffix == ".pdf":
        try:
            import pdfplumber
            pages = []
            blank_page_nums = []
            with pdfplumber.open(safe_path) as pdf:
                total_pages = len(pdf.pages)
                for i, page in enumerate(pdf.pages):
                    text = (page.extract_text() or "").strip()
                    if text:
                        pages.append(f"--- Page {i + 1} ---\n{text}")
                    else:
                        blank_page_nums.append(i)

            # Vision OCR for scanned/image-based pages
            if blank_page_nums:
                try:
                    from memory.file_indexer import _ocr_pdf_pages
                    ocr_results = await _ocr_pdf_pages(safe_path, blank_page_nums)
                    for pn, desc in sorted(ocr_results.items()):
                        pages.append(f"--- Page {pn + 1} (OCR) ---\n{desc}")
                except Exception as ocr_err:
                    for pn in blank_page_nums:
                        pages.append(f"--- Page {pn + 1} ---\n[Scanned/image page — vision OCR unavailable: {ocr_err}]")

            if pages:
                # Sort by page number for correct order
                pages.sort(key=lambda p: int(p.split("Page ")[1].split(" ")[0].split("---")[0]))
                return "\n\n".join(pages)
            return f"[PDF has {total_pages} page(s) but no extractable text and OCR failed]"
        except Exception as e:
            return f"Error reading PDF: {e}"

    # Binary file types — return metadata instead of garbled content
    if suffix in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
                  ".mp3", ".mp4", ".wav", ".zip", ".tar", ".gz",
                  ".exe", ".dll", ".so", ".bin", ".dat"}:
        size = safe_path.stat().st_size
        return f"[Binary file: {safe_path.name}, {size:,} bytes — use a specialized tool to process this file type]"

    return safe_path.read_text(encoding="utf-8", errors="replace")



@tool('write_file')
async def _tool_write_file(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    project_id = ctx.project_id
    safe_path = _ws._safe_workspace_path(tool_args["path"], project_id)
    safe_path.parent.mkdir(parents=True, exist_ok=True)
    safe_path.write_text(tool_args["content"], encoding="utf-8")
    return f"File written: {tool_args['path']} ({len(tool_args['content'])} bytes)"



@tool('list_workspace_files')
async def _tool_list_workspace_files(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    project_id = ctx.project_id
    base = _ws._get_workspace_base(project_id)
    sub = tool_args.get("path", "")
    target = (base / sub) if sub else base
    target = target.resolve()
    if not is_within(target, base):
        return "Access denied: path outside workspace"
    if not target.exists():
        return f"Directory not found: {sub}"
    entries = []
    for item in sorted(target.iterdir()):
        kind = "dir" if item.is_dir() else "file"
        size = item.stat().st_size if item.is_file() else 0
        entries.append(f"[{kind}] {item.name}" + (f" ({size} bytes)" if kind == "file" else ""))
    return "\n".join(entries) if entries else "Empty directory"

