"""Artifact tools: save/update/read/list, save the last reply, save a transcript."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import logging
from agent.tools.registry import ToolContext, tool
from agent.tools.web import _web_search

logger = logging.getLogger(__name__)

SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "save_transcript_artifact",
            "description": (
                "Fetch a YouTube transcript server-side (via the YouTube MCP) and save it as an "
                "artifact with your frontmatter. Use this instead of pasting transcript text into "
                "save_to_artifact, which truncates long text. Prefer ingest_source for new work — "
                "it also extracts topics and links the graph."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "video_id": {
                        "type": "string",
                        "description": "YouTube video id (e.g. dQw4w9WgXcQ)."
                    },
                    "path": {
                        "type": "string",
                        "description": "Artifact path. Pass a bare folder/file path like 'youtube-transcripts/{date}/{channel}/{video_id}-{slug}.md'. Project slug is prepended automatically."
                    },
                    "frontmatter": {
                        "type": "string",
                        "description": "YAML frontmatter as a string, WITHOUT the surrounding '---' fences. The backend wraps it. Include source.type, video_title, channel_name, topics[], etc. per the skill spec."
                    },
                    "title": {
                        "type": "string",
                        "description": "Optional artifact title shown in the Artifacts list."
                    }
                },
                "required": ["video_id", "path", "frontmatter"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_last_response",
            "description": (
                "Save recent conversation as an artifact. Default: your previous reply verbatim; "
                "history_count widens it, and mode can summarize, research or custom-transform it. "
                "Use for 'save this', 'note the last few messages' — never ask the user to restate "
                "what's already in the conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Artifact path, e.g. 'ANALYSIS/2026-04-07-ai-maturity.md' (project prefix added automatically)"},
                    "history_count": {"type": "integer", "description": "How many of the most recent messages (both user and assistant) to include as source material. 1 = just the last assistant reply (default). Use a larger number when the user references 'the last few messages' or 'the conversation so far'.", "default": 1},
                    "mode": {
                        "type": "string",
                        "enum": ["verbatim", "summarize", "research", "custom"],
                        "description": "verbatim = save source material as-is (default for history_count=1). summarize = condense into key points. research = run web_search/recall to expand on the source material and produce a researched note. custom = apply the instructions in custom_prompt to the source material.",
                        "default": "verbatim"
                    },
                    "custom_prompt": {"type": "string", "description": "Required when mode='custom'. Instructions for how to transform the source messages (e.g. 'extract action items', 'rewrite as a formal brief')."},
                    "title": {"type": "string", "description": "Optional title for YAML frontmatter (markdown only)"},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional list of tags for YAML frontmatter"},
                    "prepend_header": {"type": "boolean", "description": "If true, prepend a title+date header to the content (default true for .md files)", "default": True}
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_to_artifact",
            "description": (
                "Save content into the user's Pantheon artifact store. THIS IS THE ONLY tool that persists anything into `list_artifacts` / `read_artifact` / project-scoped recall. MCP `save_*` / `*_save_to_library` / `*_upload_file` tools are NOT equivalents — they save to external services that Pantheon cannot see. Always use `save_to_artifact` when a skill says \"save as artifact\" or when the user asks you to save something for later use in this project. Pass a bare path like 'youtube-transcripts/foo.md' — the project slug is prepended automatically. If the path already exists, a NEW copy is saved as foo-1.md, foo-2.md, … unless overwrite=true, which replaces the existing file (a new version). To change an existing file, prefer update_artifact or pass overwrite=true. Returns the saved artifact id."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Logical path inside the project artifact tree."},
                    "content": {"type": "string", "description": "Full content body."},
                    "content_type": {"type": "string", "description": "Mime-ish type. Leave it out to pick one from the file extension (e.g. .swift -> text/x-swift); text/markdown when the extension says nothing."},
                    "overwrite": {"type": "boolean", "description": "If the path already exists, replace that file (saved as a new version) instead of creating a -1 copy.", "default": False},
                    "title": {"type": "string", "description": "Optional human title."},
                    "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags."}
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_artifact",
            "description": "Replace the content of an existing artifact, creating a new version. Use when the user asks to revise an existing note/file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Artifact id."},
                    "content": {"type": "string", "description": "The complete new content. It replaces the whole file - pass all of it, not just the changed part."},
                    "edit_summary": {"type": "string", "description": "Optional commit-message-style note."}
                },
                "required": ["id", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_artifact",
            "description": "Read an artifact by id or by path. Use to surface previously saved content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "path": {"type": "string", "description": "Logical path; alternative to id."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_artifact",
            "description": "Delete an artifact in the active project, by id or by path (e.g. a stray duplicate or a file that is no longer needed). It goes to the trash and can be restored from the Artifacts page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "Artifact id."},
                    "path": {"type": "string", "description": "Logical path; alternative to id."}
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_artifacts",
            "description": (
                "List artifacts in the active project. To find files in a folder use path_prefix with a BARE folder name like 'research/' — do NOT include the project slug yourself; the tool prepends it for you (so 'research/' and 'default-project/research/' both work). To find a single file use read_artifact with id or path."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tag": {"type": "string"},
                    "content_type": {"type": "string"},
                    "path_prefix": {"type": "string"},
                    "search": {"type": "string"},
                    "limit": {"type": "integer", "default": 20}
                },
                "required": []
            }
        }
    },
]


@tool('save_last_response')
async def _tool_save_last_response(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    session_id = ctx.session_id
    last_assistant_text = ctx.last_assistant_text
    effective_project = ctx.effective_project
    history_count = max(1, int(tool_args.get("history_count", 1)))
    mode = (tool_args.get("mode") or "verbatim").lower()
    custom_prompt = tool_args.get("custom_prompt", "")

    # ── Gather source messages ──────────────────────────────────
    source_msgs: list[dict[str, Any]] = []
    if session_id:
        try:
            from memory.manager import create_memory_manager
            mgr = create_memory_manager(project_id=effective_project, session_id=session_id)
            history = await mgr.episodic.get_history(session_id=session_id, limit=200)
            # history is ASC; take last N that have content
            filtered = [r for r in history if r.get("content")]
            source_msgs = filtered[-history_count:] if filtered else []
        except Exception as e:
            logger.warning("save_last_response episodic fallback failed: %s", e)

    # If nothing in episodic yet, fall back to the in-memory last assistant text
    if not source_msgs and last_assistant_text:
        source_msgs = [{"role": "assistant", "content": last_assistant_text}]

    if not source_msgs:
        return (
            "No prior messages found in the current session. "
            "Ask the user for the content they want saved, then save it with save_to_artifact."
        )

    # ── Build source text block ─────────────────────────────────
    if history_count == 1 and source_msgs[-1].get("role") == "assistant":
        source_text = source_msgs[-1]["content"]
        source_label = "last assistant response"
    else:
        lines = []
        for m in source_msgs:
            role = m.get("role", "?").upper()
            lines.append(f"[{role}]\n{m.get('content','')}")
        source_text = "\n\n".join(lines)
        source_label = f"last {len(source_msgs)} messages"

    # ── Transform via mode ──────────────────────────────────────
    content_body = source_text
    try:
        if mode == "verbatim":
            content_body = source_text
        elif mode in ("summarize", "research", "custom"):
            from models.provider import get_provider_for
            provider = get_provider_for("summarize")
            if mode == "summarize":
                instr = (
                    "Summarize the following conversation excerpt into clear, "
                    "structured markdown notes. Preserve key facts, figures, and "
                    "named entities. Use headers and bullet points where helpful."
                )
            elif mode == "research":
                # Do a quick web_search pass to enrich
                try:
                    search_query = source_text[:400]
                    enriched = await _web_search(search_query)
                except Exception:
                    enriched = ""
                instr = (
                    "Expand the following source material into a researched briefing. "
                    "Integrate the additional search results below where relevant, "
                    "cite sources inline, and produce a polished markdown note."
                )
                if enriched:
                    source_text = source_text + "\n\n---\n\n## Additional search results\n" + enriched
            else:  # custom
                if not custom_prompt:
                    return "mode='custom' requires a custom_prompt argument."
                instr = custom_prompt

            prompt_messages = [
                {"role": "system", "content": "You transform conversation excerpts into well-formatted markdown notes. Return only the note body — no preamble."},
                {"role": "user", "content": f"{instr}\n\n---\nSOURCE ({source_label}):\n\n{source_text}"},
            ]
            transformed = ""
            async for chunk in provider.chat(messages=prompt_messages, tools=[], stream=False):
                if isinstance(chunk, dict):
                    transformed += chunk.get("content", "") or ""
                elif isinstance(chunk, str):
                    transformed += chunk
            content_body = transformed.strip() or source_text
        else:
            return f"Unknown mode: {mode}"
    except Exception as e:
        logger.exception("save_last_response transform failed")
        return f"Transform failed ({mode}): {e}"

    # ── Save as artifact ────────────────────────────────────────
    # Durable + searchable, matching what read_file / list_artifacts
    # tell the model (it used to write a scratch workspace file the
    # model then couldn't find via list_artifacts).
    import sqlite3 as _sqlite3
    from artifacts.store import get_store, project_slug as _ps
    from artifacts import embedder as _emb
    rel = (tool_args.get("path") or "notes/saved-response.md").lstrip("/").strip()
    _slug = _ps(effective_project)
    if not (rel == _slug or rel.startswith(f"{_slug}/")):
        rel = f"{_slug}/{rel}"
    safe = Path(rel)
    content = content_body
    if safe.suffix.lower() in (".md", ".markdown") and tool_args.get("prepend_header", True):
        from datetime import datetime
        title = tool_args.get("title") or safe.stem.replace("-", " ").replace("_", " ").title()
        tags = tool_args.get("tags") or []
        frontmatter = [
            "---",
            f"title: {title}",
            f"date: {datetime.utcnow().strftime('%Y-%m-%d')}",
            f"mode: {mode}",
            f"source_messages: {len(source_msgs)}",
        ]
        if tags:
            frontmatter.append("tags: [" + ", ".join(tags) + "]")
        frontmatter.append("source: agent_response")
        frontmatter.append("---")
        content = "\n".join(frontmatter) + "\n\n# " + title + "\n\n" + content_body
    store = get_store()
    a = None
    for n in range(50):
        cand = rel if n == 0 else str(safe.with_name(f"{safe.stem}-{n}{safe.suffix}"))
        try:
            a = store.create(
                project_id=effective_project, path=cand, content=content,
                content_type="text/markdown" if safe.suffix.lower() in (".md", ".markdown") else "text/plain",
                title=tool_args.get("title") or safe.stem,
                tags=list(tool_args.get("tags") or []) or None,
                source={"kind": "agent", "tool": "save_last_response", "session_id": session_id or ""},
                edited_by=session_id or "agent",
            )
            rel = cand
            break
        except _sqlite3.IntegrityError as e:
            if "UNIQUE" not in str(e):
                raise
    if a is None:
        return f"save_last_response failed: could not find a free path near {rel!r}"
    _emb.schedule_embed(a["id"], effective_project)
    return (f"Saved {source_label} ({mode}, {len(content_body)} chars) as artifact "
            f"{rel} (id={a['id']}). Find it with list_artifacts / read_artifact.")



@tool('save_transcript_artifact')
async def _tool_save_transcript_artifact(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    memory_manager = ctx.memory_manager
    session_id = ctx.session_id
    effective_project = ctx.effective_project
    from artifacts.store import get_store, project_slug as _ps_st
    from artifacts import embedder as _emb_st
    from mcp_client.manager import get_mcp_manager
    import sqlite3 as _sqlite3st
    import json as _json_st

    video_id = (tool_args.get("video_id") or "").strip()
    raw_path = (tool_args.get("path") or "").strip()
    frontmatter = (tool_args.get("frontmatter") or "").strip()
    title = tool_args.get("title")
    if not video_id or not raw_path or not frontmatter:
        return (
            "save_transcript_artifact rejected: video_id, path, "
            "and frontmatter are all required."
        )

    # Strip any --- fences the agent may have included; we add
    # them ourselves so the result is always well-formed.
    fm = frontmatter
    if fm.startswith("---"):
        fm = fm[3:].lstrip("\n")
    if fm.endswith("---"):
        fm = fm[:-3].rstrip("\n")
    fm = fm.strip("\n").strip()

    # 1. Fetch transcript from the MCP server.
    mgr = get_mcp_manager()
    tool = mgr.find_tool("mcp_*_fetch_transcript")
    if not tool:
        return ("save_transcript_artifact: no connected MCP server offers a "
                "fetch_transcript tool. Connect a YouTube transcript MCP first.")
    try:
        fetch_result = await mgr.execute_tool(
            tool,
            {"video_id": video_id, "save": False},
        )
    except Exception as e:
        return f"save_transcript_artifact: transcript fetch failed: {e}"

    # The MCP wrapper may return either a Python dict or a JSON
    # string. Handle both. Look for result.text (full stitched
    # transcript) or fall back to stitching segments.
    payload = fetch_result
    if isinstance(payload, str):
        try:
            payload = _json_st.loads(payload)
        except Exception:
            return f"save_transcript_artifact: could not parse MCP response as JSON: {payload[:200]}"
    if not isinstance(payload, dict):
        return f"save_transcript_artifact: unexpected MCP response type {type(payload).__name__}"
    inner = payload.get("result") if isinstance(payload.get("result"), dict) else payload
    transcript_text = (inner or {}).get("text") or ""
    if not transcript_text and isinstance((inner or {}).get("segments"), list):
        transcript_text = "\n".join(
            (seg.get("text") or "").strip() for seg in inner["segments"]
        )
    if not transcript_text.strip():
        return f"save_transcript_artifact: MCP returned no transcript text for {video_id!r}"

    # 2. Stitch frontmatter + transcript.
    body = f"---\n{fm}\n---\n\n{transcript_text.strip()}\n"

    # 3. Normalize path the same way save_to_artifact does.
    proj = _ps_st(effective_project)
    norm = raw_path.lstrip("/").strip()
    if norm and norm != proj and not norm.startswith(f"{proj}/"):
        norm = f"{proj}/{norm}"

    # 4. Save with UNIQUE-path collision retry, mirroring
    #    save_to_artifact behavior.
    def _candidate(base: str, n: int) -> str:
        if n == 0:
            return base
        p = Path(base)
        if p.suffix:
            return str(p.with_name(f"{p.stem}-{n}{p.suffix}"))
        return f"{base}-{n}"

    store = get_store()
    final_path = norm
    a = None
    for n in range(50):
        cand = _candidate(norm, n)
        try:
            a = store.create(
                project_id=effective_project,
                path=cand,
                content=body,
                content_type="text/markdown",
                title=title or f"Transcript: {video_id}",
                tags=["transcript", "youtube"],
                source={"kind": "agent_transcript_save", "video_id": video_id, "session_id": session_id or ""},
                edited_by=session_id or "agent",
            )
            final_path = cand
            break
        except _sqlite3st.IntegrityError as e:
            if "UNIQUE" not in str(e) or "path" not in str(e):
                raise
            continue
    if a is None:
        return (
            f"save_transcript_artifact: path {norm!r} and 50 "
            f"numbered variants are all taken."
        )

    _emb_st.schedule_embed(a["id"], effective_project)
    try:
        if memory_manager:
            await memory_manager.index_artifact(a["id"])
    except Exception as _ie:
        logger.debug("graph index for transcript %s failed: %s", a["id"], _ie)

    chars = len(transcript_text)
    suffix_note = ""
    if final_path != norm:
        suffix_note = f" (path {norm!r} was taken; auto-suffixed)"
    return (
        f"Saved transcript artifact {a['path']} "
        f"(id={a['id']}, {chars} chars of transcript){suffix_note}"
    )



# Extension -> content type for saves that do not name one. Everything here
# is a text type (artifacts.store.is_text_type), so read_artifact returns it.
_EXT_TYPES = {
    ".md": "text/markdown", ".markdown": "text/markdown", ".txt": "text/plain",
    ".csv": "text/csv", ".html": "text/html", ".htm": "text/html", ".css": "text/css",
    ".json": "application/json", ".yaml": "application/yaml", ".yml": "application/yaml",
    ".xml": "application/xml", ".plist": "application/xml", ".svg": "image/svg+xml",
    ".js": "text/javascript", ".mjs": "text/javascript", ".jsx": "text/javascript",
    ".ts": "text/x-typescript", ".tsx": "text/x-typescript", ".py": "text/x-python",
    ".swift": "text/x-swift", ".kt": "text/x-kotlin", ".java": "text/x-java",
    ".c": "text/x-c", ".h": "text/x-c", ".m": "text/x-objectivec", ".cpp": "text/x-c++",
    ".cc": "text/x-c++", ".hpp": "text/x-c++", ".cs": "text/x-csharp", ".go": "text/x-go",
    ".rs": "text/x-rust", ".rb": "text/x-ruby", ".php": "text/x-php", ".sh": "text/x-shellscript",
    ".sql": "text/x-sql", ".toml": "text/x-toml", ".ini": "text/plain", ".pbxproj": "text/plain",
    ".xcscheme": "application/xml", ".entitlements": "application/xml", ".gradle": "text/plain",
}
# Source code: kept out of graph-memory extraction, which pulled "facts" out of
# every Swift file of the iOS bake-off on the local model (2026-10-18).
_CODE_EXTS = {".js", ".mjs", ".jsx", ".ts", ".tsx", ".py", ".swift", ".kt", ".java", ".c", ".h",
              ".m", ".cpp", ".cc", ".hpp", ".cs", ".go", ".rs", ".rb", ".php", ".sh", ".sql",
              ".css", ".pbxproj", ".xcscheme", ".plist", ".entitlements", ".gradle"}


def _content_type_for(path: str, given: str | None) -> str:
    """The model's content_type, unless it is missing or the old schema
    default (text/markdown) on a file whose extension says otherwise."""
    if given and given != "text/markdown":
        return given
    return _EXT_TYPES.get(Path(path).suffix.lower(), given or "text/markdown")


def _is_code(path: str) -> bool:
    return Path(path).suffix.lower() in _CODE_EXTS


@tool('save_to_artifact', 'update_artifact', 'read_artifact', 'list_artifacts', 'delete_artifact')
async def _tool_artifact_tools(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    memory_manager = ctx.memory_manager
    session_id = ctx.session_id
    effective_project = ctx.effective_project
    from artifacts.store import get_store, is_text_type, project_slug as _ps
    from artifacts import embedder as _emb
    store = get_store()

    def _normalize_artifact_path(p: str | None) -> str | None:
        """Make path/path_prefix consistent with how save stores them.

        Saves auto-prepend '<project_slug>/' if missing. Apply the
        same rule to lookups so agents can save 'research/x.md' and
        find it again with either 'research/' or 'default-project/research/'.
        Idempotent: never double-prefixes.
        """
        if p is None:
            return None
        norm = p.lstrip("/").strip()
        if not norm:
            return norm
        proj = _ps(effective_project)
        if norm == proj or norm.startswith(f"{proj}/"):
            return norm
        return f"{proj}/{norm}"

    async def _index(a: dict) -> None:
        # Auto-index into graph memory so cross-artifact concept
        # queries work without an explicit indexer call. Not source code.
        try:
            if memory_manager and is_text_type(a["content_type"]) and not _is_code(a["path"]):
                await memory_manager.index_artifact(a["id"])
        except Exception as _ie:
            logger.debug("graph index for %s failed: %s", a["id"], _ie)

    if tool_name == "save_to_artifact":
        import sqlite3 as _sqlite3
        norm = _normalize_artifact_path(tool_args["path"])
        # Auto-resolve UNIQUE path collisions by suffixing -1, -2, ...
        # before the final extension. We retry up to 50 times before
        # surfacing the error.
        def _candidate(base: str, n: int) -> str:
            if n == 0:
                return base
            p = Path(base)
            # path may have no suffix or multiple dots; use the last
            # component's last suffix only.
            if p.suffix:
                return str(p.with_name(f"{p.stem}-{n}{p.suffix}"))
            return f"{base}-{n}"

        # Harness-guaranteed organization: anything saved under the
        # task-ledger/ folder always carries the 'task-ledger' tag,
        # so ledgers stay tag-searchable regardless of whether the
        # model remembered to pass tags.
        _tags = list(tool_args.get("tags") or [])
        _proj_prefix = f"{_ps(effective_project)}/"
        if (norm.startswith(_proj_prefix + "task-ledger/")
                and "task-ledger" not in _tags):
            _tags.append("task-ledger")

        content_type = _content_type_for(norm, tool_args.get("content_type"))
        if tool_args.get("overwrite"):
            existing = store.get_by_path(effective_project, norm)
            if existing:
                a = store.update(
                    existing["id"],
                    content=tool_args["content"],
                    edit_summary="Agent overwrite",
                    edited_by=session_id or "agent",
                )
                _emb.schedule_embed(a["id"], effective_project)
                await _index(a)
                return (f"Overwrote artifact {a['path']} (id={a['id']}, "
                        f"now v{len(store.list_versions(a['id']))})")

        final_path = norm
        a = None
        for n in range(50):
            cand = _candidate(norm, n)
            try:
                a = store.create(
                    project_id=effective_project,
                    path=cand,
                    content=tool_args["content"],
                    content_type=content_type,
                    title=tool_args.get("title"),
                    tags=_tags or None,
                    source={"kind": "agent", "session_id": session_id or ""},
                    edited_by=session_id or "agent",
                )
                final_path = cand
                break
            except _sqlite3.IntegrityError as e:
                if "UNIQUE" not in str(e) or "path" not in str(e):
                    raise
                continue
        if a is None:
            return (
                f"save_to_artifact failed: path {norm!r} and 50 "
                f"numbered variants are all taken. Pick a different "
                f"path or delete the existing artifact first."
            )
        _emb.schedule_embed(a["id"], effective_project)
        await _index(a)
        suffix_note = ""
        if final_path != norm:
            # Said plainly: models took "auto-suffixed" for success and left
            # stale originals next to -1 copies (iOS bake-off, 2026-10-18).
            existing = store.get_by_path(effective_project, norm)
            suffix_note = (
                f". NOTE: {norm!r} already exists"
                + (f" (id={existing['id']})" if existing else "")
                + f", so this was saved as a SEPARATE copy at {final_path!r}. To change the "
                "existing file instead, call update_artifact with its id or save_to_artifact "
                "with overwrite=true; delete the copy with delete_artifact if it is not wanted."
            )
        return f"Saved artifact {a['path']} (id={a['id']}, v{1}){suffix_note}"
    if tool_name == "update_artifact":
        aid = tool_args.get("id")
        if not aid:
            return "Error: update_artifact needs the artifact's id (list_artifacts or read_artifact shows it)."
        # A call without content used to raise KeyError inside the try below
        # and came back as "Artifact <id> not found" for an artifact that
        # exists; the model then re-saved it as -1, -2 copies (2026-10-19).
        if not isinstance(tool_args.get("content"), str):
            return (f"Error: update_artifact was not run - it needs `content`, the complete new text of "
                    f"artifact {aid}. It replaces the whole file, so pass all of it (read_artifact shows "
                    "the current text); an edit_summary alone changes nothing.")
        try:
            a = store.update(
                aid,
                content=tool_args["content"],
                edit_summary=tool_args.get("edit_summary") or "Agent update",
                edited_by=session_id or "agent",
            )
        except KeyError:
            return f"Artifact {aid} not found."
        _emb.schedule_embed(a["id"], effective_project)
        await _index(a)
        versions = store.list_versions(aid)
        return f"Updated artifact {a['path']} (now v{len(versions)})"
    if tool_name == "read_artifact":
        aid = tool_args.get("id")
        path = tool_args.get("path")
        a = None
        if aid:
            a = store.get(aid)
        elif path:
            norm_path = _normalize_artifact_path(path)
            a = store.get_by_path(effective_project, norm_path)
            # Tolerate agent passing the bare path even when the
            # caller already saved it under a non-prefixed form.
            if not a and norm_path != path:
                a = store.get_by_path(effective_project, path)
        if not a:
            return f"Artifact not found: id={aid} path={path}"
        if is_text_type(a["content_type"]):
            return f"--- {a['path']} (v{store.list_versions(a['id'])[0]['version_number']}) ---\n{a.get('content') or ''}"
        return f"Artifact {a['path']} is binary ({a['content_type']}); fetch via /api/artifacts/{a['id']}/raw"
    if tool_name == "list_artifacts":
        norm_prefix = _normalize_artifact_path(tool_args.get("path_prefix"))
        items = store.list(
            project_id=effective_project,
            tag=tool_args.get("tag"),
            content_type=tool_args.get("content_type"),
            path_prefix=norm_prefix,
            search=tool_args.get("search"),
            limit=int(tool_args.get("limit") or 20),
        )
        if not items:
            hint = ""
            if norm_prefix and norm_prefix != (tool_args.get("path_prefix") or ""):
                hint = (
                    f" (searched normalized prefix {norm_prefix!r}; "
                    f"caller passed {tool_args.get('path_prefix')!r})"
                )
            return f"(no artifacts match){hint}"
        lines = [
            f"- id={i['id']} path={i['path']} type={i['content_type']} tags={i.get('tags')}"
            for i in items
        ]
        return "\n".join(lines)
    if tool_name == "delete_artifact":
        aid, path = tool_args.get("id"), tool_args.get("path")
        a = store.get(aid) if aid else (store.get_by_path(effective_project, _normalize_artifact_path(path))
                                         if path else None)
        if aid and a and a["project_id"] != effective_project:
            a = None   # only this project's artifacts
        if not a:
            return f"Artifact not found: id={aid} path={path}"
        store.soft_delete(a["id"])
        try:
            await _emb.drop_for_artifact(a["id"], a["project_id"])
        except Exception:
            logger.debug("drop semantic vectors failed", exc_info=True)
        try:
            from artifacts import preview as _preview
            _preview.invalidate_for_artifact(a["id"])
        except Exception:
            logger.debug("preview invalidation failed", exc_info=True)
        return f"Deleted artifact {a['path']} (id={a['id']}). It can be restored from the Artifacts page."
    return f"Unknown artifact tool: {tool_name}"

