"""Persist document-conversion outputs as artifacts."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def save_converted_artifact(
    *, project_id: str, target_path: Path, base: Path, source_rel: str, fmt: str,
) -> dict[str, Any]:
    """Save a conversion output as an artifact.

    Path gets the project-slug prefix like save_to_artifact, so
    list_artifacts(path_prefix=...) finds it; re-converting the same file
    updates the existing artifact instead of failing on the UNIQUE path.
    """
    import mimetypes
    from artifacts.store import get_store, is_text_type, project_slug
    from artifacts import embedder

    content_type = mimetypes.guess_type(str(target_path))[0] or "application/octet-stream"
    is_txt = is_text_type(content_type)
    content: str | bytes
    if is_txt:
        try:
            content = target_path.read_text(encoding="utf-8")
        except Exception:
            content = target_path.read_bytes()
            is_txt = False
    else:
        content = target_path.read_bytes()

    store = get_store()
    rel = str(target_path.relative_to(base))
    slug = project_slug(project_id)
    path = rel if rel == slug or rel.startswith(f"{slug}/") else f"{slug}/{rel}"
    existing = store.get_by_path(project_id, path)
    if existing:
        a = store.update(
            existing["id"], content=content,
            edit_summary=f"re-converted from {source_rel}", edited_by="agent",
        )
    else:
        a = store.create(
            project_id=project_id,
            path=path,
            content=content,
            content_type=content_type,
            title=target_path.name,
            tags=["converted", fmt.lower()],
            source={"kind": "conversion", "source_file": source_rel},
            edited_by="agent",
        )
    if is_txt:
        embedder.schedule_embed(a["id"], project_id)
    return a

