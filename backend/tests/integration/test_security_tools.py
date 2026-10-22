"""Regression tests for the 2026-10-22 tool security review (branch sec/tools).

GitHub URL building, interactive gates (github / rerun_job), sandbox script
names, symlink-safe workspace walks, artifact project scoping, git tools
without a checkout, repo binding validation, legacy upload inlining and the
download size cap.
"""
from __future__ import annotations

import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")

import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from agent.tools import execute_tool


# ── 1. GitHub client: every request stays under /repos/{owner}/{repo}/ ──────

TRAVERSAL = [
    "..", "../other", "../../../../other", "a/../../b", "../../../x/y/contents/f",
    "%2e%2e", "%2e%2e/%2e%2e/other", "%252e%252e/x", "a/%2E%2E/../b",
    "/../../etc", "..\\..\\other", "a\\b", "x/./y", ".", "a\x00b", "a\nb",
]


def _recording_client():
    from integrations.github import GitHubClient
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if "/git/ref/" in path:
            return httpx.Response(200, json={"object": {"sha": "a" * 40}})
        if "/git/commits/" in path:
            return httpx.Response(200, json={"tree": {"sha": "b" * 40}})
        if path.endswith(("/git/blobs", "/git/trees", "/git/commits")):
            return httpx.Response(201, json={"sha": "c" * 40})
        if "/contents/" in path:
            return httpx.Response(200, json={"content": "", "sha": "d"})
        return httpx.Response(200, json={"default_branch": "main", "merged": True, "number": 1})

    return GitHubClient("tok", transport=httpx.MockTransport(handler)), seen


def _calls(client):
    return {
        "read_file": lambda v: client.read_file("o", "r", v),
        "read_file_ref": lambda v: client.read_file("o", "r", "README.md", ref=v),
        "list_directory": lambda v: client.list_directory("o", "r", v),
        "delete_branch": lambda v: client.delete_branch("o", "r", v),
        "create_branch": lambda v: client.create_branch("o", "r", new_branch=v, base_branch="main"),
        "create_branch_base": lambda v: client.create_branch("o", "r", new_branch="ok", base_branch=v),
        "get_ref": lambda v: client.get_ref("o", "r", f"heads/{v}"),
        "write_files_branch": lambda v: client.write_files(
            "o", "r", branch=v, files=[{"path": "a.txt", "content": "x"}], message="m"),
        "create_pr_head": lambda v: client.create_pr("o", "r", title="t", head=v, base="main"),
        "create_pr_base": lambda v: client.create_pr("o", "r", title="t", head="feat", base=v),
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("value", TRAVERSAL)
@pytest.mark.parametrize("call", list(_calls(MagicMock()).keys()))
async def test_github_client_requests_stay_inside_repo(call, value):
    from integrations.github import GitHubError
    client, seen = _recording_client()
    try:
        await _calls(client)[call](value)
    except GitHubError:
        pass  # refusing is fine; leaving the repo is not
    for req in seen:
        assert req.url.host == "api.github.com"
        raw = req.url.raw_path.decode()
        assert req.url.path.startswith("/repos/o/r/") or req.url.path == "/repos/o/r", (call, value, raw)
        segs = [s.lower() for s in raw.split("?")[0].split("/")]
        assert ".." not in segs and "%2e%2e" not in segs, (call, value, raw)


@pytest.mark.asyncio
async def test_github_client_quotes_ordinary_paths_and_branches():
    client, seen = _recording_client()
    await client.read_file("o", "r", "docs/My File#1.md")
    await client.delete_branch("o", "r", "feature/x-1")
    paths = [r.url.raw_path.decode() for r in seen]
    assert paths[0] == "/repos/o/r/contents/docs/My%20File%231.md"
    assert paths[1] == "/repos/o/r/git/refs/heads/feature/x-1"


@pytest.mark.asyncio
@pytest.mark.parametrize("owner,repo", [("..", "r"), ("o", ".."), ("o/../x", "r"), ("o", "r/../../x"),
                                        ("o", "."), ("-o", "r"), ("o", "")])
async def test_github_client_rejects_bad_owner_repo(owner, repo):
    from integrations.github import GitHubError
    client, seen = _recording_client()
    with pytest.raises(GitHubError):
        await client.get_repo(owner, repo)
    assert seen == []


@pytest.mark.asyncio
async def test_github_request_guard_catches_raw_paths():
    """_request refuses dot segments even from callers that build their own
    path (integrations/github_push.py) and never sends the token off-host."""
    from integrations.github import GitHubError
    client, seen = _recording_client()
    for bad in ("/repos/o/r/git/refs/heads/../../../../x", "/repos/o/r/%2e%2e/x",
                "https://evil.example/repos/o/r"):
        with pytest.raises(GitHubError):
            await client._request("GET", bad)
    assert seen == []


@pytest.mark.parametrize("name", ["main", "feature/x", "iteration/run-3", "v1.2.3", "a_b", "pantheon/kit-20261022-1200"])
def test_validate_ref_name_accepts_normal_branches(name):
    from integrations.github import validate_ref_name
    assert validate_ref_name(name) == name


@pytest.mark.parametrize("name", ["", "..", "a..b", "/a", "a/", "a//b", "a.lock", "a@{1}", "@", "-x",
                                  ".hidden", "a/.b", "a b", "a~1", "a^", "a:b", "a?", "a*", "a[", "a\\b",
                                  "a%2e", "a\x7f", "a\tb", "a."])
def test_validate_ref_name_rejects(name):
    from integrations.github import GitHubInvalidName, validate_ref_name
    with pytest.raises(GitHubInvalidName):
        validate_ref_name(name)


# ── 2. github tool: merge_pr / delete_branch are interactive-only ────────────

_SPEC = {"connection_id": "c1", "owner": "o", "repo": "r", "default_branch": "main"}
_CONN = {"id": "c1", "default_branch": "main"}


def _github_patches():
    return [
        patch("api.connections.get_project_repo_for_tools", return_value=_SPEC),
        patch("api.connections.get_connection", return_value=_CONN),
        patch("api.connections.get_token", return_value="tok"),
        patch("api.connections.mark_used"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("args,method", [
    ({"action": "merge_pr", "pr_number": 3}, "merge_pr"),
    ({"action": "delete_branch", "branch": "old"}, "delete_branch"),
])
async def test_github_destructive_actions_need_interactive(args, method):
    ps = _github_patches()
    for p in ps:
        p.start()
    try:
        with patch(f"integrations.github.GitHubClient.{method}", new_callable=AsyncMock,
                   return_value={"merged": True}) as m:
            out = await execute_tool("github", args, None, project_id="p", interactive=False)
            assert "only runs in a chat the user is driving" in out
            m.assert_not_called()
            out = await execute_tool("github", args, None, project_id="p", interactive=True)
            m.assert_called_once()
            assert "only runs in a chat" not in out
    finally:
        for p in ps:
            p.stop()


@pytest.mark.asyncio
async def test_github_reads_and_branch_writes_stay_open_to_jobs():
    """iteration_loop / coding_task jobs commit through write_files."""
    ps = _github_patches()
    for p in ps:
        p.start()
    try:
        with patch("integrations.github.GitHubClient.write_files", new_callable=AsyncMock,
                   return_value={"files": ["a"], "branch": "b", "commit_sha": "abcdef12"}) as w, \
             patch("integrations.github.GitHubClient.list_branches", new_callable=AsyncMock, return_value=[]):
            out = await execute_tool("github", {"action": "write_files", "branch": "b", "message": "m",
                                                "files": [{"path": "a", "content": "x"}]},
                                     None, project_id="p", interactive=False)
            assert "Committed 1 file" in out
            w.assert_called_once()
            out = await execute_tool("github", {"action": "list_branches"}, None, project_id="p")
            assert "branch(es)" in out
    finally:
        for p in ps:
            p.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("channel", ["telegram", "slack", "discord", "matrix", "mattermost"])
async def test_github_writes_refused_from_messaging_apps(channel):
    """Bots relay chat-app users; repo writes stay with the web chat and approved jobs."""
    ps = _github_patches()
    for p in ps:
        p.start()
    try:
        with patch("integrations.github.GitHubClient.write_files", new_callable=AsyncMock,
                   return_value={"files": ["a"], "branch": "b", "commit_sha": "abcdef12"}) as w, \
             patch("integrations.github.GitHubClient.list_branches", new_callable=AsyncMock, return_value=[]) as lb:
            args = {"action": "write_files", "branch": "b", "message": "m", "files": [{"path": "a", "content": "x"}]}
            out = await execute_tool("github", args, None, project_id="p", channel=channel)
            assert f"isn't available from {channel}" in out
            w.assert_not_called()
            out = await execute_tool("github", {"action": "list_branches"}, None, project_id="p", channel=channel)
            assert "branch(es)" in out
            lb.assert_called_once()
            out = await execute_tool("github", args, None, project_id="p", channel="web", interactive=True)
            assert "Committed 1 file" in out
    finally:
        for p in ps:
            p.stop()


def test_agentcore_passes_channel_to_every_tool_call():
    import inspect
    from agent import core
    src = inspect.getsource(core)
    calls = src.count("await execute_tool(")
    assert calls and src.count("channel=self.channel") >= calls


# ── 3. sandbox script names ──────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["../../x.py", "/tmp/x.py", "a/b.py", "..", ".", "", None, "a\\b.py",
                                 "_runner.sh", ".bashrc", "x;rm -rf ~.py", "x y.py", "..py"])
def test_safe_script_filename_falls_back(bad):
    from sandbox.backend import safe_script_filename
    assert safe_script_filename(bad, "python") == "main.py"


def test_safe_script_filename_keeps_plain_names():
    from sandbox.backend import safe_script_filename
    assert safe_script_filename("analysis.py", "python") == "analysis.py"
    assert safe_script_filename("build-1.sh", "bash") == "build-1.sh"


@pytest.mark.asyncio
async def test_subprocess_sandbox_filename_cannot_escape(tmp_path):
    from sandbox.subprocess_backend import SubprocessSandbox
    target = tmp_path / "escaped.py"
    rel = os.path.relpath(target, "/tmp")
    res = await SubprocessSandbox().execute_inline(
        "python", "print('ran')", filename="../../../../../.." + str(target))
    assert res.exit_code == 0 and "ran" in res.stdout
    assert not target.exists(), rel


@pytest.mark.asyncio
@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
async def test_subprocess_sandbox_runs_node():
    """ulimit -v 256 MB made V8 abort at start; -d keeps a bound and works."""
    from sandbox.subprocess_backend import SubprocessSandbox
    res = await SubprocessSandbox().execute_inline("node", "console.log('hi from node')")
    assert res.exit_code == 0, res.stderr
    assert "hi from node" in res.stdout


@pytest.mark.asyncio
async def test_subprocess_sandbox_still_bounds_memory():
    from sandbox.backend import SandboxConfig
    from sandbox.subprocess_backend import SubprocessSandbox
    res = await SubprocessSandbox().execute_inline(
        "python", "x = bytearray(600 * 1024 * 1024)\nprint('allocated')",
        config=SandboxConfig(max_memory_mb=256))
    assert "allocated" not in res.stdout
    assert res.exit_code != 0


def test_firecracker_mirror_skips_symlinks_leading_out(tmp_path):
    from sandbox.firecracker_backend import _mirror
    src, dest = tmp_path / "out", tmp_path / "ws"
    src.mkdir(); dest.mkdir()
    (src / "sub").mkdir()
    (src / "sub" / "f.txt").write_text("x")
    os.symlink("/etc", src / "abs")
    os.symlink("../../..", src / "sub" / "up")
    os.symlink("f.txt", src / "sub" / "ok")
    err = _mirror(src, dest, set())
    assert (dest / "sub" / "f.txt").read_text() == "x"
    assert os.readlink(dest / "sub" / "ok") == "f.txt"
    assert not (dest / "abs").is_symlink() and not (dest / "abs").exists()
    assert not (dest / "sub" / "up").is_symlink()
    assert err and "outside the workspace" in err


# ── 5. symlinks out of the workspace are not followed ────────────────────────

def _ws_with_escape(tmp_path):
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "passwd.md").write_text("TOP SECRET")
    ws = (tmp_path / "ws")
    (ws / "docs").mkdir(parents=True)
    (ws / "docs" / "real.md").write_text("hello")
    os.symlink(secret / "passwd.md", ws / "docs" / "leak.md")
    os.symlink(secret, ws / "docs" / "leakdir")
    return ws.resolve()


def test_iter_files_within_skips_symlinks(tmp_path):
    from utils.paths import iter_files_within
    ws = _ws_with_escape(tmp_path)
    names = [p.name for p in iter_files_within(ws / "docs", ws)]
    assert names == ["real.md"]


def test_files_api_zip_and_to_artifacts_skip_symlinks(tmp_path):
    import io
    import zipfile
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import api.files as files_api
    ws = _ws_with_escape(tmp_path)
    app = FastAPI()
    app.include_router(files_api.router, prefix="/api")
    client = TestClient(app)
    with patch.object(files_api, "_get_workspace", return_value=ws):
        r = client.post("/api/files/download-zip", json={"project_id": "default", "paths": ["docs"]})
        assert r.status_code == 200, r.text
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        assert names == ["docs/real.md"]
        created = []
        store = MagicMock()
        store.get_by_path.return_value = None
        store.create.side_effect = lambda **kw: created.append(kw["path"]) or {"id": "x", **kw}
        with patch("artifacts.store.get_store", return_value=store), \
             patch("artifacts.embedder.schedule_embed", create=True):
            r = client.post("/api/files/to-artifacts", json={"project_id": "default", "path": "docs"})
        assert r.status_code == 200, r.text
        assert all("leak" not in p for p in created), created
        assert "TOP SECRET" not in r.text


@pytest.mark.asyncio
async def test_batch_convert_skips_symlinks(tmp_path):
    ws = _ws_with_escape(tmp_path)
    seen = []

    def fake_convert(self, src, dst, fmt):
        seen.append(Path(src).name)
        Path(dst).write_text("converted")

    with patch("agent.tools.workspace._get_workspace_base", return_value=ws), \
         patch("utils.document_converter.DocumentConverter.convert_file", fake_convert):
        await execute_tool("batch_convert_documents",
                           {"paths": ["docs"], "target_format": "html"}, None)
    assert seen == ["real.md"]


# ── 4. rerun_job ─────────────────────────────────────────────────────────────

@pytest.fixture
def job_store(tmp_path):
    from jobs.store import JobStore
    s = JobStore(db_path=str(tmp_path / "jobs.db"))
    with patch("jobs.store.get_store", return_value=s):
        yield s


def _finished(store, job_type, project="p1"):
    j = store.create(job_type=job_type, project_id=project, title=job_type)
    store.claim_next()
    store.complete(j["id"], result={})
    return j


@pytest.mark.asyncio
@pytest.mark.parametrize("job_type", ["coding_task", "autoresearch"])
async def test_rerun_host_exec_job_needs_interactive(job_store, job_type):
    j = _finished(job_store, job_type)
    out = await execute_tool("rerun_job", {"job_id": j["id"]}, None, project_id="p1")
    assert "rerun_job refused" in out
    assert len(job_store.list(project_id="p1")) == 1
    out = await execute_tool("rerun_job", {"job_id": j["id"]}, None, project_id="p1", interactive=True)
    assert "Rerun queued" in out


@pytest.mark.asyncio
async def test_rerun_other_project_job_not_found(job_store):
    j = _finished(job_store, "autonomous_task", project="other")
    out = await execute_tool("rerun_job", {"job_id": j["id"]}, None, project_id="p1", interactive=True)
    assert "no job" in out
    out = await execute_tool("rerun_job", {"job_id": j["id"][:8]}, None, project_id="p1", interactive=True)
    assert "no job" in out
    out = await execute_tool("rerun_job", {"job_id": j["id"]}, None, project_id="other")
    assert "Rerun queued" in out


# ── 6. artifacts by id are scoped to the project ─────────────────────────────

@pytest.fixture
def art_store(tmp_path):
    from artifacts.store import ArtifactStore
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    s = ArtifactStore(db_path=str(tmp_path / "a.db"), blobs_dir=blobs)
    with patch("artifacts.store.get_store", return_value=s):
        yield s


@pytest.mark.asyncio
async def test_artifact_tools_refuse_other_projects_ids(art_store):
    a = art_store.create(project_id="other", path="other/secret.md", content="SECRET",
                         content_type="text/markdown")
    out = await execute_tool("read_artifact", {"id": a["id"]}, None, project_id="p1")
    assert "not found in this project" in out and "SECRET" not in out
    out = await execute_tool("update_artifact", {"id": a["id"], "content": "pwned"}, None, project_id="p1")
    assert "not found in this project" in out
    assert art_store.get(a["id"])["content"] == "SECRET"
    with patch("memory.manager.create_memory_manager") as mk:
        mk.return_value.index_artifact = AsyncMock(return_value={})
        out = await execute_tool("index_artifact", {"id": a["id"]}, None, project_id="p1")
        assert "not found in this project" in out
        mk.return_value.index_artifact.assert_not_called()
    out = await execute_tool("read_artifact", {"id": a["id"]}, None, project_id="other")
    assert "SECRET" in out


@pytest.mark.asyncio
async def test_generate_image_source_from_other_project_refused(art_store):
    a = art_store.create(project_id="other", path="other/pic.png", content=b"\x89PNG\r\n\x1a\nxx",
                         content_type="image/png")
    provider = MagicMock()
    provider.generate_image = AsyncMock(return_value=[])
    with patch("models.provider.get_provider_for", return_value=provider):
        out = await execute_tool("generate_image", {"prompt": "x", "source_image": a["id"]},
                                 None, project_id="p1")
    assert "not found" in out
    provider.generate_image.assert_not_called()


# ── 7. git tools need a checkout; the workspace root is never git-init'ed ────

@pytest.mark.asyncio
@pytest.mark.parametrize("tool,args", [
    ("git_commit", {"message": "m"}), ("git_merge", {"branch": "x"}),
    ("git_push_pr", {"title": "t"}), ("git_create_branch", {"branch_name": "x"}),
    ("git_status", {}),
])
async def test_git_tools_refuse_without_checkout(tmp_path, tool, args):
    with patch("agent.tools.workspace._resolve_repo_checkout", return_value=None), \
         patch("agent.tools.workspace._get_workspace_base", return_value=tmp_path), \
         patch("agent.tools.workspace._run_git_cmd", new_callable=AsyncMock) as run:
        out = await execute_tool(tool, args, None, project_id="p", host_exec=True)
    assert "git_sync_repo first" in out
    run.assert_not_called()


@pytest.mark.asyncio
async def test_run_git_cmd_never_inits(tmp_path):
    from agent.tools.workspace import _run_git_cmd
    code, _, _ = await _run_git_cmd(["status"], tmp_path, auto_init=True)
    assert code != 0
    assert not (tmp_path / ".git").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["-x", "--orphan", "a..b", "a b"])
async def test_git_branch_names_validated(tmp_path, bad):
    with patch("agent.tools.workspace._resolve_repo_checkout", return_value=tmp_path), \
         patch("agent.tools.workspace._run_git_cmd", new_callable=AsyncMock) as run:
        out = await execute_tool("git_create_branch", {"branch_name": bad}, None, project_id="p", host_exec=True)
    assert "Invalid branch name" in out
    run.assert_not_called()


# ── 8. repo binding owner/repo ───────────────────────────────────────────────

@pytest.mark.parametrize("owner,repo", [("..", "x"), ("o", ".."), ("../..", "x"), ("o", "a/b"),
                                        ("o/..", "r"), ("o", "."), ("", "r")])
def test_repo_checkout_dir_rejects_bad_names(tmp_path, owner, repo):
    from agent.tools.workspace import _repo_checkout_dir
    with patch("agent.tools.workspace._get_workspace_base", return_value=tmp_path):
        with pytest.raises(ValueError):
            _repo_checkout_dir("p", owner, repo)


@pytest.mark.asyncio
async def test_git_sync_fresh_with_bad_binding_deletes_nothing(tmp_path):
    victim = tmp_path / "keep"
    victim.mkdir()
    spec = {"connection_id": "c1", "owner": "..", "repo": "..", "default_branch": "main"}
    with patch("agent.tools.workspace._get_workspace_base", return_value=tmp_path / "ws"), \
         patch("api.connections.get_project_repo_for_tools", return_value=spec), \
         patch("api.connections.get_token", return_value=None), \
         patch("agent.tools.workspace._run_git_cmd", new_callable=AsyncMock) as run:
        (tmp_path / "ws").mkdir()
        out = await execute_tool("git_sync_repo", {"fresh": True}, None, project_id="p", host_exec=True)
    assert "Invalid GitHub" in out
    assert victim.exists()
    run.assert_not_called()


def test_bind_repo_validates_names():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import api.connections as conns
    app = FastAPI()
    app.include_router(conns.router, prefix="/api")
    client = TestClient(app)
    fake_cn = MagicMock()
    fake_cn.__enter__.return_value.execute.return_value.fetchone.return_value = {"id": "c1"}
    with patch.object(conns, "_connect", return_value=fake_cn), \
         patch.object(conns, "get_project_binding", return_value={"ok": True}):
        for owner, repo, branch in [("..", "x", "main"), ("o", "..", "main"), ("o", "a/b", "main"),
                                    ("o", "r", "../x")]:
            r = client.post("/api/projects/p1/repo", json={"connection_id": "c1", "owner": owner,
                                                           "repo": repo, "default_branch": branch})
            assert r.status_code == 400, (owner, repo, branch, r.text)
        r = client.post("/api/projects/p1/repo", json={"connection_id": "c1", "owner": "octo",
                                                       "repo": "my.repo_1", "default_branch": "main"})
        assert r.status_code == 200, r.text


# ── 9. legacy uploads/ inlining stays inside uploads/ ────────────────────────

def test_legacy_upload_inlining_rejects_traversal(tmp_path):
    from agent.core import AgentCore
    import agent.core as core_mod
    fake = MagicMock()
    fake.workspace_dir = tmp_path / "data" / "workspace"
    ws = fake.workspace_dir
    (ws / "uploads").mkdir(parents=True)
    (ws / "uploads" / "ok.png").write_bytes(b"\x89PNG\r\n\x1a\nOK")
    (tmp_path / "data" / "outside.png").write_bytes(b"\x89PNG\r\n\x1a\nOUTSIDE")
    agent = AgentCore.__new__(AgentCore)
    agent.project_id = None
    with patch.object(core_mod, "settings", fake):
        out = agent._build_user_content("see uploads/../../outside.png please")
        assert out == "see uploads/../../outside.png please"
        out = agent._build_user_content("see uploads/ok.png")
        assert isinstance(out, list) and any(b.get("type") == "image_url" for b in out)


# ── 10. download size cap ────────────────────────────────────────────────────

def _client_factory(body: bytes, headers: dict | None = None):
    real = httpx.AsyncClient

    def handler(request):
        return httpx.Response(200, content=body, headers=headers or {})

    def factory(*a, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        return real(*a, **kw)
    return factory


@pytest.mark.asyncio
async def test_safe_http_get_caps_body():
    from utils.net import ResponseTooLargeError, safe_http_get
    with patch("utils.net.httpx.AsyncClient", _client_factory(b"x" * 5000)), \
         patch("utils.net._private_fetch_allowed", return_value=True):
        r = await safe_http_get("https://x.test/a", max_bytes=10_000)
        assert r.content == b"x" * 5000 and r.text.startswith("xxx")
        with pytest.raises(ResponseTooLargeError):
            await safe_http_get("https://x.test/a", max_bytes=1000)


@pytest.mark.asyncio
async def test_download_file_refuses_oversize(tmp_path, monkeypatch):
    monkeypatch.setenv("DOWNLOAD_MAX_MB", "0.001")   # ~1 KB
    ws = tmp_path / "ws"
    ws.mkdir()
    with patch("agent.tools.workspace._get_workspace_base", return_value=ws.resolve()), \
         patch("utils.net.httpx.AsyncClient", _client_factory(b"y" * 4096)), \
         patch("utils.net._private_fetch_allowed", return_value=True):
        out = await execute_tool("download_file", {"url": "https://x.test/big.bin", "path": "dl"}, None)
    assert "Download refused" in out and "limit" in out
    assert not any(ws.rglob("big.bin"))
