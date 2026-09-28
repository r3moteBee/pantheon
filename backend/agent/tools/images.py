"""Image generation and editing (generate_image)."""
from __future__ import annotations

from typing import Any
import re as _re_mod
from agent.tools.registry import ToolContext, tool


SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "Generate image(s) with the configured image model, or edit an existing "
                "image. Saves each result as an artifact and shows it in the chat — do "
                "not call show_file afterwards.\n"
                "Prompt writing: start with the main subject and what it IS or DOES "
                "(e.g. 'a basketball whose surface is the cratered moon, dribbled by a "
                "bunny'); name each object once — image models draw every noun they "
                "see, so 'the moon and a basketball' yields two objects, and writing "
                "'no basketball' tends to ADD one — never mention what should not "
                "appear. Then setting, composition, style, lighting.\n"
                "To fix or change a previous image, pass its artifact id as "
                "source_image and describe only the change ('make the bunny dribble "
                "the moon-ball with its front paw; remove the orange basketball') — "
                "this keeps what was right instead of starting over."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {"type": "string", "description": "What to draw (or, with source_image, what to change)"},
                    "source_image": {"type": "string", "description": "Optional: artifact id (or path) of an image to edit — e.g. the id from a previous generate_image result"},
                    "size": {"type": "string", "description": "WxH (1024x1024 default, 1536x1024, 1024x1536), an aspect ratio (16:9), or a named preset (square_hd, landscape_16_9, portrait_4_3, …). Any form works with any backend — it is converted automatically.", "default": "1024x1024"},
                    "n": {"type": "integer", "description": "Number of images (1-4)", "default": 1},
                    "quality": {"type": "string", "description": "Optional provider quality hint (e.g. low/medium/high, standard/hd)"},
                    "path": {"type": "string", "description": "Optional artifact folder (default images/generated/<date>/)"},
                    "name": {"type": "string", "description": "Optional short file name stem"}
                },
                "required": ["prompt"]
            }
        }
    },
]


_IMAGE_SIZE_RE = _re_mod.compile(r"^(\d{2,4}x\d{2,4}|\d{1,2}:\d{1,2}|[a-z][a-z0-9_]{2,30})$")


def _image_mime(data: bytes) -> tuple[str, str]:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", ".png"
    if data[:3] == b"\xff\xd8\xff":
        return "image/jpeg", ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp", ".webp"
    return "image/png", ".png"


async def _generate_image_tool(tool_args: dict[str, Any], project_id: str,
                               session_id: str | None) -> str:
    """generate_image: route to the image_gen class, save artifacts, display."""
    import re as _re
    import sqlite3 as _sqlite3
    from datetime import datetime, timezone
    from artifacts.store import get_store, project_slug
    from models.provider import get_provider_for

    prompt = (tool_args.get("prompt") or "").strip()
    if not prompt:
        return "generate_image: 'prompt' is required."
    provider = get_provider_for("image_gen")
    if provider is None:
        return ("Image generation isn't configured. Ask the user to add an image "
                "model under Settings → Model Routing → Image generation "
                "(an OpenAI-compatible /images/generations endpoint).")
    size = str(tool_args.get("size") or "1024x1024").strip().lower()
    if not _IMAGE_SIZE_RE.match(size):
        return (f"generate_image: invalid size {size!r} — use WxH (1024x1024), "
                "a ratio (16:9) or a preset (square_hd).")
    try:
        n = max(1, min(4, int(tool_args.get("n") or 1)))
    except (TypeError, ValueError):
        n = 1
    quality = (tool_args.get("quality") or "").strip() or None

    source_ref = (tool_args.get("source_image") or "").strip()
    source_bytes: list[bytes] | None = None
    source_artifact = None
    if source_ref:
        store_ = get_store()
        ref = source_ref.removeprefix("artifact://")
        a = store_.get(ref)
        if a is None:
            slug_ = project_slug(project_id)
            p_ = ref if ref.startswith(f"{slug_}/") else f"{slug_}/{ref.lstrip('/')}"
            a = store_.get_by_path(project_id, p_) or store_.get_by_path(project_id, ref)
        if a is None:
            return f"generate_image: source_image {source_ref!r} not found (use an artifact id from a previous result)."
        if not str(a.get("content_type") or "").startswith("image/") or not a.get("blob_path"):
            return f"generate_image: source_image {source_ref!r} is not an image artifact."
        source_bytes = [store_._load_blob(a["blob_path"])]
        source_artifact = a

    try:
        images = await provider.generate_image(prompt, size=size, n=n, quality=quality,
                                               images=source_bytes)
    except Exception as e:
        return (f"Image generation failed: {e}\n"
                "Size format is converted automatically (WxH / ratio / preset), so "
                "don't retry just to change its spelling. If the error names the "
                "model or endpoint, tell the user and point them to Settings → "
                "Model Routing → Image generation and Settings → Model usage.")

    slug = project_slug(project_id)
    folder = (tool_args.get("path") or "").strip().strip("/")
    if not folder:
        folder = f"images/generated/{datetime.now(timezone.utc):%Y-%m-%d}"
    if not (folder == slug or folder.startswith(f"{slug}/")):
        folder = f"{slug}/{folder}"
    stem = _re.sub(r"[^a-z0-9]+", "-", (tool_args.get("name") or prompt).lower()).strip("-")[:50] or "image"

    store = get_store()
    lines: list[str] = []
    for idx, data in enumerate(images):
        mime, ext = _image_mime(data)
        base = f"{folder}/{stem}" + (f"-{idx + 1}" if len(images) > 1 else "")
        a = None
        for k in range(50):
            path = base + (f"-{k}" if k else "") + ext
            try:
                a = store.create(
                    project_id=project_id, path=path, content=data, content_type=mime,
                    title=prompt[:120], tags=["generated-image"],
                    source={"kind": "image_generation", "prompt": prompt[:2000],
                            "edited_from": source_artifact["id"] if source_artifact else None,
                            "model": getattr(provider, "model", ""), "size": size,
                            "session_id": session_id or ""},
                    edited_by=session_id or "agent",
                )
                break
            except _sqlite3.IntegrityError as e:
                if "UNIQUE" not in str(e):
                    raise
        if a is None:
            lines.append(f"Could not save image {idx + 1}: no free path near {base}{ext}")
            continue
        lines.append(f"[DISPLAY:artifact://{a['id']}]")
        lines.append(f"Saved image artifact {a['path']} (id={a['id']}, {len(data) // 1024}KB).")
    lines.append("The image is displayed to the user. Do not call show_file for it. "
                 "To adjust it, call generate_image again with source_image=<its id>.")
    return "\n".join(lines)


@tool('generate_image')
async def _tool_generate_image(ctx: ToolContext, tool_name: str, tool_args: dict[str, Any]) -> Any:
    session_id = ctx.session_id
    effective_project = ctx.effective_project
    return await _generate_image_tool(tool_args, effective_project, session_id)

