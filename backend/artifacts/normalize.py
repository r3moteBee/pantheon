"""Normalize what the agent writes into the artifact store: paths, titles, tags.

Marble Maze project, 2026-10-20: the first session saved the app as
`MarbleMaze/App/*.swift`; an iteration loop later saved its "updated" files to
`MarbleMaze/MarbleMaze/App/...` (an invented extra folder), titled
"MarbleScene.swift (Updated)", so the project ended up with two diverging copies
of the same sources. The loop's turns were tagged from each job's name, so
"Enhance MarbleMaze Game" and "Enhance Marble Maze" became two tags for one piece
of work. Everything here applies to agent writes only; the user's own saves and
renames through the API are left as typed.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

# ── titles ────────────────────────────────────────────────────────────────────
# A version is a version in the store; "(Updated)" in a title only goes stale.
_TITLE_SUFFIX = re.compile(
    r"\s*(?:[\(\[]\s*(?:updated|update|fixed|revised|edited|modified|new|final|latest|v\d+(?:\.\d+)*)\s*[\)\]]"
    r"|\s[-–—:]\s*(?:updated|revised|fixed|final)(?:\s+version)?)\s*$",
    re.I,
)


def clean_title(title: str | None) -> str | None:
    """Drop status suffixes such as "(Updated)", "[v2]", " - revised"."""
    if not title:
        return title
    t = title.strip()
    while True:
        new = _TITLE_SUFFIX.sub("", t).strip()
        if new == t or not new:
            return t
        t = new


# ── tags ──────────────────────────────────────────────────────────────────────
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def normalize_tag(tag: Any, max_len: int = 40) -> str:
    """'Enhance MarbleMaze Game' -> 'enhance-marble-maze-game'."""
    s = _CAMEL.sub("-", str(tag or "").strip())
    s = re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-").lower()
    return s[:max_len].rstrip("-")


def _compact(tag: str) -> str:
    return re.sub(r"[^a-z0-9]", "", tag.lower())


def canonical_tags(tags: Iterable[Any] | None, existing: Iterable[str] = ()) -> list[str]:
    """Normalize tags and reuse a project tag that differs only in case/separators.

    `existing` are the project's current tags; an existing spelling wins, so a
    user's "MarbleMaze" isn't forked into "marble-maze" by the agent."""
    by_compact: dict[str, str] = {}
    for t in existing or ():
        if t and _compact(t) not in by_compact:
            by_compact[_compact(t)] = t
    out: list[str] = []
    for raw in tags or ():
        n = normalize_tag(raw)
        if not n:
            continue
        t = by_compact.get(_compact(n), n)
        if t not in out:
            out.append(t)
    return out


# ── paths ─────────────────────────────────────────────────────────────────────

def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _inserted(short: list[str], long: list[str]) -> list[str] | None:
    """The segments of `long` that aren't part of `short` when `short` is an ordered
    subsequence of it (case-insensitive), else None."""
    out, i = [], 0
    for seg in long:
        if i < len(short) and seg.lower() == short[i].lower():
            i += 1
        else:
            out.append(seg)
    return out if i == len(short) else None


def resolve_agent_path(path: str, existing_paths: Iterable[str]) -> tuple[str, str]:
    """Map an agent's save path onto the project's existing layout.

    Returns (path, note). The path is unchanged unless the target doesn't exist
    and its folder is new, and either
      * the path repeats a folder ("App/App/x", "MarbleMaze/MarbleMaze/App/x")
        and the path without the repeat is an existing folder or file, or
      * a file with the same name already exists at exactly one place whose
        folders are the given folders minus one or two inserted ones
        ("MarbleMaze/MarbleMaze/App/MarbleScene.swift" when
        "MarbleMaze/App/MarbleScene.swift" exists).
    A layout that really has a doubled folder (Xcode's App/App/) is kept: its
    folder exists, so nothing is remapped."""
    paths = set(existing_paths)
    if not path or path in paths:
        return path, ""
    segs = _segments(path)
    folders: set[str] = set()
    for p in paths:
        parts = _segments(p)[:-1]
        for i in range(1, len(parts) + 1):
            folders.add("/".join(parts[:i]))
    parent = "/".join(segs[:-1])
    if not parent or parent in folders:
        return path, ""

    # 1. A repeated folder whose collapsed form exists.
    for i in range(len(segs) - 2):
        if segs[i].lower() == segs[i + 1].lower():
            cand = segs[:i + 1] + segs[i + 2:]
            cand_path = "/".join(cand)
            cand_parent = "/".join(cand[:-1])
            # Only when the remaining path goes deeper than the repeated folder: a new
            # Xcode-style "Foo/Foo/App.swift" next to an existing "Foo/project.yml" is a
            # real layout, "Foo/Foo/App/x.swift" next to an existing "Foo/App/" is not.
            if len(cand) - 1 > i + 1 and (cand_path in paths or cand_parent in folders):
                return cand_path, (f"the path repeated the folder '{segs[i]}'; the project already has "
                                   f"'{cand_parent}/', so the file went there")

    # 2. The same file name exists once, in the same folders minus 1-2 extra ones.
    name = segs[-1]
    same_name = [p for p in paths if _segments(p)[-1:] == [name]]
    if len(same_name) == 1:
        other = _segments(same_name[0])
        extra = len(segs) - len(other)
        inserted = _inserted(other[:-1], segs[:-1]) if 1 <= extra <= 2 else None
        # The inserted folders must repeat ones already in the path (MarbleMaze/MarbleMaze),
        # so "app/docs/README.md" next to "app/README.md" stays a new file.
        if (inserted and other[0].lower() == segs[0].lower()
                and all(x.lower() in (y.lower() for y in other[:-1]) for x in inserted)):
            return same_name[0], (f"'{name}' already exists at '{same_name[0]}'; the path you gave added "
                                  "folders that don't exist, so the existing file was updated instead of "
                                  "forking a second copy")
    return path, ""
