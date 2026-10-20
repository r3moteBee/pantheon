"""Pushing artifacts to GitHub (integrations/github_push.py, api/github_push.py,
github(action="push_artifacts")) against an in-memory fake of the Git Data API."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import tempfile
import uuid
from unittest.mock import patch

import httpx
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))


def _blob_sha(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


class FakeGitHub:
    """One repo o/r with a default branch 'main' holding README.md."""

    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self.trees: dict[str, dict[str, str]] = {}      # tree sha -> {path: blob sha}
        self.commits: dict[str, dict] = {}
        self.refs: dict[str, str] = {}
        self.pulls: list[dict] = []
        self.uploads = 0
        self.created_repos: list[dict] = []
        readme = self._blob(b"# r\n")
        self.refs["main"] = self._commit(self._tree({"README.md": readme}), [], "init")

    def _blob(self, data: bytes) -> str:
        sha = _blob_sha(data)
        self.blobs[sha] = data
        return sha

    def _tree(self, entries: dict[str, str]) -> str:
        sha = hashlib.sha1(json.dumps(sorted(entries.items())).encode()).hexdigest()
        self.trees[sha] = dict(entries)
        return sha

    def _commit(self, tree: str, parents: list[str], msg: str) -> str:
        sha = uuid.uuid4().hex
        self.commits[sha] = {"tree": tree, "parents": parents, "message": msg}
        return sha

    def files(self, branch: str) -> dict[str, bytes]:
        tree = self.trees[self.commits[self.refs[branch]]["tree"]]
        return {p: self.blobs[s] for p, s in tree.items()}

    def handler(self, req: httpx.Request) -> httpx.Response:
        p, m = req.url.path, req.method
        body = json.loads(req.content) if req.content else {}
        if p == "/user/repos" and m == "POST":
            self.created_repos.append(body)
            return httpx.Response(201, json={"name": body["name"], "full_name": f"o/{body['name']}",
                                             "owner": {"login": "o"}, "private": body["private"],
                                             "default_branch": "main", "html_url": f"https://github.com/o/{body['name']}"})
        base = "/repos/o/r"
        if not p.startswith(base):
            return httpx.Response(404, json={"message": "Not Found"})
        p = p[len(base):]
        if p == "" and m == "GET":
            return httpx.Response(200, json={"default_branch": "main", "full_name": "o/r"})
        if p.startswith("/git/ref/heads/") and m == "GET":
            b = p[len("/git/ref/heads/"):]
            if b not in self.refs:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json={"object": {"sha": self.refs[b]}})
        if p == "/git/refs" and m == "POST":
            self.refs[body["ref"][len("refs/heads/"):]] = body["sha"]
            return httpx.Response(201, json={})
        if p.startswith("/git/refs/heads/") and m == "PATCH":
            self.refs[p[len("/git/refs/heads/"):]] = body["sha"]
            return httpx.Response(200, json={})
        if p.startswith("/git/commits/") and m == "GET":
            return httpx.Response(200, json={"tree": {"sha": self.commits[p.rsplit("/", 1)[1]]["tree"]}})
        if p.startswith("/git/trees/") and m == "GET":
            tree = self.trees[p.rsplit("/", 1)[1]]
            return httpx.Response(200, json={"truncated": False, "tree": [
                {"path": k, "type": "blob", "sha": v} for k, v in tree.items()]})
        if p == "/git/blobs" and m == "POST":
            self.uploads += 1
            data = (base64.b64decode(body["content"]) if body["encoding"] == "base64"
                    else body["content"].encode())
            return httpx.Response(201, json={"sha": self._blob(data)})
        if p == "/git/trees" and m == "POST":
            entries = dict(self.trees[body["base_tree"]])
            for e in body["tree"]:
                if e["sha"] is None:
                    entries.pop(e["path"], None)
                else:
                    entries[e["path"]] = e["sha"]
            return httpx.Response(201, json={"sha": self._tree(entries)})
        if p == "/git/commits" and m == "POST":
            return httpx.Response(201, json={"sha": self._commit(body["tree"], body["parents"], body["message"])})
        if p == "/pulls" and m == "GET":
            q = req.url.params
            hits = [pr for pr in self.pulls if f"o:{pr['head']}" == q.get("head") and pr["base"] == q.get("base")]
            return httpx.Response(200, json=[{"number": pr["number"], "title": pr["title"], "state": "open",
                                              "head": {"ref": pr["head"]}, "base": {"ref": pr["base"]},
                                              "html_url": pr["html_url"]} for pr in hits])
        if p == "/pulls" and m == "POST":
            n = len(self.pulls) + 1
            pr = {"number": n, "title": body["title"], "head": body["head"], "base": body["base"],
                  "html_url": f"https://github.com/o/r/pull/{n}"}
            self.pulls.append(pr)
            return httpx.Response(201, json=pr)
        return httpx.Response(404, json={"message": f"unexpected {m} {p}"})


@pytest.fixture
def gh(tmp_path):
    from artifacts.store import ArtifactStore
    fake = FakeGitHub()
    real = httpx.AsyncClient
    store = ArtifactStore(db_path=str(tmp_path / "a.db"), blobs_dir=tmp_path / "blobs")
    proj = "p" + uuid.uuid4().hex[:8]
    conn = {"id": "c1", "default_branch": "main", "account_login": "o"}
    binding = {"connection_id": "c1", "owner": "o", "repo": "r", "default_branch": "main"}
    with patch("integrations.github.httpx.AsyncClient",
               side_effect=lambda **kw: real(**kw) if "transport" in kw else real(transport=httpx.MockTransport(fake.handler), **kw)), \
         patch("artifacts.store.get_store", return_value=store), \
         patch("api.github_push.get_connection", return_value=conn), \
         patch("api.github_push.get_token", return_value="tok"), \
         patch("api.github_push.get_project_binding", return_value=binding), \
         patch("api.github_push.mark_used"), \
         patch("api.connections.get_project_repo_for_tools", return_value=binding), \
         patch("api.connections.get_connection", return_value=conn), \
         patch("api.connections.get_token", return_value="tok"), \
         patch("api.connections.mark_used"):
        yield fake, store, proj


def _seed(store, proj, files):
    for path, content in files.items():
        store.create(project_id=proj, path=f"{proj}/{path}", content=content, content_type="text/plain")


async def _api(method, path, **kw):
    from fastapi import FastAPI
    from api.github_push import router
    app = FastAPI()
    app.include_router(router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        return await c.request(method, path, **kw)


@pytest.mark.asyncio
async def test_push_folder_to_new_branch_opens_pr_and_resends_only_changes(gh):
    fake, store, proj = gh
    _seed(store, proj, {"Kit/Package.swift": "// pkg", "Kit/Sources/A.swift": "struct A {}"})
    body = {"path_prefix": f"{proj}/Kit/", "branch": "pantheon/kit", "message": "Add Kit"}
    r = await _api("POST", f"/api/projects/{proj}/github/push", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["created_branch"] and out["pr_number"] == 1 and sorted(out["uploaded"]) == ["Package.swift", "Sources/A.swift"]
    assert fake.files("pantheon/kit") == {"README.md": b"# r\n", "Package.swift": b"// pkg", "Sources/A.swift": b"struct A {}"}
    assert fake.files("main") == {"README.md": b"# r\n"}           # default branch untouched

    st = (await _api("GET", f"/api/projects/{proj}/github/pushes", params={"path_prefix": f"{proj}/Kit/"})).json()
    assert (st["pushed"], st["changed"]) == (2, 0)

    # same content again: no commit, the PR is found, nothing uploaded
    uploads = fake.uploads
    again = (await _api("POST", f"/api/projects/{proj}/github/push", json=body)).json()
    assert again["commit_sha"] is None and again["pr_existing"] and fake.uploads == uploads

    a = store.get_by_path(proj, f"{proj}/Kit/Sources/A.swift")
    store.update(a["id"], content="struct A { let x = 1 }", edit_summary="e", edited_by="t")
    st = (await _api("GET", f"/api/projects/{proj}/github/pushes", params={"path_prefix": f"{proj}/Kit/"})).json()
    assert st["changed"] == 1
    third = (await _api("POST", f"/api/projects/{proj}/github/push", json=body)).json()
    assert third["uploaded"] == ["Sources/A.swift"] and third["unchanged"] == ["Package.swift"]
    assert fake.uploads == uploads + 1 and len(fake.pulls) == 1


@pytest.mark.asyncio
async def test_default_branch_needs_opt_in(gh):
    fake, store, proj = gh
    _seed(store, proj, {"Kit/a.txt": "a"})
    body = {"path_prefix": f"{proj}/Kit/", "branch": "main"}
    r = await _api("POST", f"/api/projects/{proj}/github/push", json=body)
    assert r.status_code == 400 and "default branch" in r.json()["detail"]
    r = await _api("POST", f"/api/projects/{proj}/github/push", json={**body, "allow_default_branch": True})
    assert r.status_code == 200 and r.json()["pr_url"] is None
    assert fake.files("main")["a.txt"] == b"a"


@pytest.mark.asyncio
async def test_mirror_deletes_only_inside_the_folder(gh):
    fake, store, proj = gh
    _seed(store, proj, {"Kit/a.txt": "a", "Kit/b.txt": "b"})
    body = {"path_prefix": f"{proj}/Kit/", "branch": "main", "allow_default_branch": True, "dest_dir": "apps/kit"}
    await _api("POST", f"/api/projects/{proj}/github/push", json=body)
    store.soft_delete(store.get_by_path(proj, f"{proj}/Kit/b.txt")["id"])
    r = await _api("POST", f"/api/projects/{proj}/github/push", json={**body, "mirror": True})
    assert r.json()["deleted"] == ["apps/kit/b.txt"]
    assert set(fake.files("main")) == {"README.md", "apps/kit/a.txt"}
    r = await _api("POST", f"/api/projects/{proj}/github/push", json={**body, "dest_dir": "", "mirror": True})
    assert r.status_code == 400 and "folder inside the repo" in r.json()["detail"]


@pytest.mark.asyncio
async def test_credentials_need_confirmation(gh):
    fake, store, proj = gh
    _seed(store, proj, {"Kit/app.py": "print(1)", "Kit/.env": "X=1",
                        "Kit/conf.txt": "token ghp_" + "a" * 36})
    body = {"path_prefix": f"{proj}/Kit/", "branch": "pantheon/x"}
    r = await _api("POST", f"/api/projects/{proj}/github/push", json=body)
    assert r.status_code == 409 and r.json()["detail"]["sensitive"] == [".env", "conf.txt"]
    assert "pantheon/x" not in fake.refs
    r = await _api("POST", f"/api/projects/{proj}/github/push", json={**body, "confirm_sensitive": True})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_selected_artifacts_and_binary_files(gh):
    fake, store, proj = gh
    _seed(store, proj, {"Kit/Sources/A.swift": "a", "Kit/Tests/T.swift": "t"})
    png = b"\x89PNG\r\n\x1a\n\x00\xff"
    blob = store.create(project_id=proj, path=f"{proj}/Kit/icon.png", content=png, content_type="image/png")
    ids = [store.get_by_path(proj, f"{proj}/Kit/Sources/A.swift")["id"],
           store.get_by_path(proj, f"{proj}/Kit/Tests/T.swift")["id"], blob["id"]]
    r = await _api("POST", f"/api/projects/{proj}/github/push", json={"artifact_ids": ids, "branch": "pantheon/sel"})
    assert r.status_code == 200, r.text
    files = fake.files("pantheon/sel")
    assert files["Sources/A.swift"] == b"a" and files["Tests/T.swift"] == b"t"
    assert files["icon.png"] == png


@pytest.mark.asyncio
async def test_create_repo():
    real = httpx.AsyncClient
    fake = FakeGitHub()
    with patch("integrations.github.httpx.AsyncClient",
               side_effect=lambda **kw: real(**kw) if "transport" in kw else real(transport=httpx.MockTransport(fake.handler), **kw)), \
         patch("api.github_push.get_connection", return_value={"id": "c1"}), \
         patch("api.github_push.get_token", return_value="tok"), patch("api.github_push.mark_used"):
        r = await _api("POST", "/api/connections/github/c1/create-repo", json={"name": "spider-kit"})
    assert r.status_code == 200 and r.json()["full_name"] == "o/spider-kit"
    assert fake.created_repos == [{"name": "spider-kit", "private": True, "description": "", "auto_init": True}]
    bad = await _api("POST", "/api/connections/github/c1/create-repo", json={"name": "no spaces"})
    assert bad.status_code == 422


@pytest.mark.asyncio
async def test_agent_push_is_interactive_only_branch_and_pr(gh):
    from agent.tools import execute_tool
    fake, store, proj = gh
    _seed(store, proj, {"Kit/a.swift": "a"})
    out = await execute_tool("github", {"action": "push_artifacts", "folder": "Kit/"}, None, project_id=proj)
    assert "only runs in a chat the user is driving" in out and len(fake.refs) == 1
    out = await execute_tool("github", {"action": "push_artifacts", "folder": "Kit/", "branch": "main"},
                             None, project_id=proj, interactive=True)
    assert "Refusing to push straight to the default branch" in out
    out = await execute_tool("github", {"action": "push_artifacts", "folder": "Kit", "title": "Kit"},
                             None, project_id=proj, interactive=True)
    assert "Pushed to o/r@pantheon/kit-" in out and "Pull request #1" in out
    (branch,) = [b for b in fake.refs if b.startswith("pantheon/kit-")]
    assert fake.files(branch)["a.swift"] == b"a" and fake.pulls[0]["title"] == "Kit"
    _seed(store, proj, {"Kit/id_rsa": "-----BEGIN OPENSSH PRIVATE KEY-----"})
    out = await execute_tool("github", {"action": "push_artifacts", "folder": "Kit/"}, None,
                             project_id=proj, interactive=True)
    assert out.startswith("push_artifacts refused") and "id_rsa" in out
