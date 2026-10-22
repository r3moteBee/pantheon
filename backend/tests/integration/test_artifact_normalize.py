"""Agent-side normalization of artifact paths, titles and tags (artifacts/normalize.py)."""
import pytest

from artifacts.normalize import canonical_tags, clean_title, normalize_tag, resolve_agent_path

MM = [
    "marble-maze-game/MarbleMaze/App/MarbleMazeApp.swift",
    "marble-maze-game/MarbleMaze/App/MarbleScene.swift",
    "marble-maze-game/MarbleMaze/App/MotionProvider.swift",
    "marble-maze-game/MarbleMaze/MarbleMazeCore/Package.swift",
    "marble-maze-game/MarbleMaze/README.md",
    "marble-maze-game/MarbleMaze/project.yml",
]


@pytest.mark.parametrize("given,expected", [
    # the Marble Maze iteration loop's invented extra folder
    ("marble-maze-game/MarbleMaze/MarbleMaze/App/MarbleScene.swift", "marble-maze-game/MarbleMaze/App/MarbleScene.swift"),
    ("marble-maze-game/MarbleMaze/MarbleMaze/App/ContentView.swift", "marble-maze-game/MarbleMaze/App/ContentView.swift"),
    # unchanged: existing file, new sibling in an existing folder, new folder, new top-level project
    ("marble-maze-game/MarbleMaze/App/MarbleScene.swift", "marble-maze-game/MarbleMaze/App/MarbleScene.swift"),
    ("marble-maze-game/MarbleMaze/App/Haptics.swift", "marble-maze-game/MarbleMaze/App/Haptics.swift"),
    ("marble-maze-game/MarbleMaze/Assets/Icon.json", "marble-maze-game/MarbleMaze/Assets/Icon.json"),
    ("marble-maze-game/Other/Other/App.swift", "marble-maze-game/Other/Other/App.swift"),
    # a real Xcode layout: sources folder named like the project, next to project.yml
    ("marble-maze-game/MarbleMaze/MarbleMaze/AppDelegate.swift", "marble-maze-game/MarbleMaze/MarbleMaze/AppDelegate.swift"),
    # a common file name in a genuinely new folder is NOT redirected
    ("marble-maze-game/MarbleMaze/docs/README.md", "marble-maze-game/MarbleMaze/docs/README.md"),
])
def test_resolve_agent_path(given, expected):
    path, note = resolve_agent_path(given, MM)
    assert path == expected
    assert bool(note) == (path != given)


def test_existing_doubled_layout_is_kept():
    paths = ["p/Foo/Foo/App.swift", "p/Foo/project.yml"]
    assert resolve_agent_path("p/Foo/Foo/Views/X.swift", paths)[0] == "p/Foo/Foo/Views/X.swift"


@pytest.mark.parametrize("title,expected", [
    ("MarbleScene.swift (Updated)", "MarbleScene.swift"),
    ("Report [v2]", "Report"),
    ("Plan - revised", "Plan"),
    ("Notes (Updated) (Final)", "Notes"),
    ("Update log", "Update log"),
    ("(Updated)", "(Updated)"),
    (None, None),
])
def test_clean_title(title, expected):
    assert clean_title(title) == expected


def test_tags_normalize_and_reuse_existing_spelling():
    assert normalize_tag("Enhance MarbleMaze Game") == "enhance-marble-maze-game"
    assert normalize_tag("enhance-marblemaze-game") == "enhance-marblemaze-game"
    assert canonical_tags(["iteration", "Enhance Marble Maze Game"], {"enhance-marblemaze-game": 3}) == \
        ["iteration", "enhance-marblemaze-game"]
    assert canonical_tags(["MarbleMaze", "marble-maze", "Marble Maze"], {}) == ["marble-maze"]
    assert canonical_tags(["", None, "  "], {}) == []


@pytest.mark.asyncio
async def test_save_to_artifact_updates_instead_of_forking(tmp_path, monkeypatch):
    from artifacts import store as store_mod
    from agent.tools import execute_tool
    st = store_mod.ArtifactStore(db_path=str(tmp_path / "a.db"), blobs_dir=tmp_path / "blobs")
    monkeypatch.setattr(store_mod, "get_store", lambda: st)
    monkeypatch.setattr("artifacts.embedder.schedule_embed", lambda *a, **k: None)
    slug = store_mod.project_slug("p1")
    orig = st.create(project_id="p1", path=f"{slug}/MarbleMaze/App/MarbleScene.swift", content="v1",
                     content_type="text/x-swift", tags=["enhance-marblemaze-game"])
    out = await execute_tool("save_to_artifact", {
        "path": "MarbleMaze/MarbleMaze/App/MarbleScene.swift", "content": "v2",
        "title": "MarbleScene.swift (Updated)", "tags": ["Enhance MarbleMaze Game"]}, None, project_id="p1")
    assert "Updated artifact" in out and "NOTE" in out, out
    assert st.get(orig["id"])["content"] == "v2"
    assert len(st.list_versions(orig["id"])) == 2
    assert st.paths("p1") == [f"{slug}/MarbleMaze/App/MarbleScene.swift"]
    out = await execute_tool("save_to_artifact", {
        "path": "MarbleMaze/App/Haptics.swift", "content": "x", "title": "Haptics (Updated)",
        "tags": ["Enhance MarbleMaze Game"]}, None, project_id="p1")
    a = st.get_by_path("p1", f"{slug}/MarbleMaze/App/Haptics.swift")
    assert a["title"] == "Haptics" and a["tags"] == ["enhance-marblemaze-game"], (out, a)
