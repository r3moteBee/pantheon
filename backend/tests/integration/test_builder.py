"""Builds in throwaway containers (builder/, the run_build tool) and the
folder zip a passing build links to."""
from __future__ import annotations

import base64
import io
import json
import os
import tarfile
import tempfile
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="pantheon-tests-"))

DENY_ALL = {"items": [{"metadata": {"name": "deny-all"},
                       "spec": {"podSelector": {}, "policyTypes": ["Ingress", "Egress"]}}]}


class FakeKube:
    """Just enough of the Kubernetes API: records objects, finishes the pod
    on its second GET with `exit_code`."""

    def __init__(self, exit_code=0, log="ok\n", policies=DENY_ALL):
        self.exit_code, self.log, self.policies = exit_code, log, policies
        self.configmaps, self.pods, self.deleted, self.patched = {}, {}, [], []
        self.gets = 0

    def handler(self, req: httpx.Request) -> httpx.Response:
        p, m = req.url.path, req.method
        if p.endswith("/networkpolicies"):
            return httpx.Response(200, json=self.policies)
        if m == "POST" and p.endswith("/configmaps"):
            body = json.loads(req.content)
            self.configmaps[body["metadata"]["name"]] = body
            return httpx.Response(201, json=body)
        if m == "POST" and p.endswith("/pods"):
            body = json.loads(req.content)
            body["metadata"]["uid"] = "uid-1"
            self.pods[body["metadata"]["name"]] = body
            return httpx.Response(201, json=body)
        if m == "PATCH":
            self.patched.append(p.rsplit("/", 1)[-1])
            return httpx.Response(200, json={})
        if m == "DELETE":
            self.deleted.append(p.rsplit("/", 1)[-1])
            return httpx.Response(200, json={})
        if m == "GET" and p.endswith("/log"):
            return httpx.Response(200, text=self.log)
        if m == "GET" and p.endswith("/pods"):
            return httpx.Response(200, json={"items": []})
        if m == "GET" and "/pods/" in p:
            self.gets += 1
            state = ({"running": {}} if self.gets < 2 else
                     {"terminated": {"exitCode": self.exit_code,
                                     "reason": "Completed" if self.exit_code == 0 else "Error"}})
            return httpx.Response(200, json={"status": {
                "phase": "Running" if self.gets < 2 else ("Succeeded" if self.exit_code == 0 else "Failed"),
                "containerStatuses": [{"state": state}]}})
        return httpx.Response(404, json={"message": f"unexpected {m} {p}"})


def _builder(kube: FakeKube, **kw):
    from builder.kubernetes import KubernetesBuilder
    tok = Path(tempfile.mkdtemp()) / "token"
    tok.write_text("t")
    b = KubernetesBuilder("pantheon-builds", api_url="https://k8s.test", token_file=tok, ca_file=None,
                          cpu="2", memory="4Gi", disk="4Gi", pull_timeout_s=60,
                          allow_network=kw.get("allow_network", False), max_concurrent=2)
    b._client = lambda: httpx.AsyncClient(base_url="https://k8s.test",
                                          transport=httpx.MockTransport(kube.handler))
    return b


SWIFT = None


def _swift():
    from builder.backend import DEFAULT_TOOLCHAINS
    return DEFAULT_TOOLCHAINS["swift"]


@pytest.fixture(autouse=True)
def _fast_poll():
    with patch("builder.kubernetes.POLL_S", 0):
        yield


@pytest.mark.asyncio
async def test_build_pod_is_locked_down_and_gets_the_source():
    kube = FakeKube(log="Build complete!\n")
    files = {"Package.swift": b"// swift-tools-version:5.9", "Sources/Game/Game.swift": b"struct G {}"}
    res = await _builder(kube).run(_swift(), files, command="swift build && swift test", workdir="")
    assert res.passed and res.log == "Build complete!\n"

    (pod,) = kube.pods.values()
    spec, c = pod["spec"], pod["spec"]["containers"][0]
    assert spec["automountServiceAccountToken"] is False and spec["enableServiceLinks"] is False
    assert spec["securityContext"]["runAsNonRoot"] is True
    assert spec["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    assert c["securityContext"] == {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                    "capabilities": {"drop": ["ALL"]}}
    assert c["image"] == "docker.io/library/swift:6.4"
    assert {"name": "BUILD_COMMAND", "value": "swift build && swift test"} in c["env"]
    assert set(c["resources"]["limits"]) == {"cpu", "memory", "ephemeral-storage"}

    # the source arrives as a tar in the ConfigMaps the pod mounts, owned by the pod
    mounted = [s["configMap"]["name"] for s in spec["volumes"][0]["projected"]["sources"]]
    assert mounted == list(kube.configmaps) and kube.patched == mounted
    raw = b"".join(base64.b64decode(v) for cm in kube.configmaps.values() for v in cm["binaryData"].values())
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        assert sorted(tar.getnames()) == sorted(files)
    # everything is cleaned up
    assert set(kube.deleted) >= {pod["metadata"]["name"], *kube.configmaps}


@pytest.mark.asyncio
async def test_large_source_is_split_under_the_configmap_limit():
    kube = FakeKube()
    files = {f"f{i}.bin": os.urandom(400_000) for i in range(4)}   # incompressible, ~1.6 MB
    await _builder(kube).run(_swift(), files, command="true")
    assert len(kube.configmaps) == 3
    assert all(len(json.dumps(cm)) < 1_000_000 for cm in kube.configmaps.values())


@pytest.mark.asyncio
async def test_failed_build_reports_exit_code():
    kube = FakeKube(exit_code=1, log="Game.swift:3:5: error: cannot find 'x' in scope\n")
    res = await _builder(kube).run(_swift(), {"a.swift": b"x"}, command="swift build")
    assert not res.passed and res.exit_code == 1


@pytest.mark.asyncio
async def test_refuses_without_a_deny_all_egress_policy():
    from builder.backend import BuildError
    kube = FakeKube(policies={"items": [{"spec": {"podSelector": {"matchLabels": {"a": "b"}},
                                                  "policyTypes": ["Egress"]}}]})
    with pytest.raises(BuildError, match="denying all egress"):
        await _builder(kube).run(_swift(), {"a": b"x"}, command="true")
    assert kube.pods == {}
    # ...unless network access is explicitly allowed
    await _builder(FakeKube(policies={"items": []}), allow_network=True).run(_swift(), {"a": b"x"}, command="true")


def test_toolchain_overrides():
    from builder.backend import toolchains
    from config import get_settings
    extra = json.dumps({"swift": {"image": "registry.local/swift:6.4"}, "java": {"image": ""},
                        "zig": {"image": "zig:0.15", "command": "zig build test"}})
    with patch.object(get_settings(), "builder_toolchains", extra):
        t = toolchains()
    assert t["swift"].image == "registry.local/swift:6.4" and t["swift"].command == "swift build && swift test"
    assert "java" not in t and t["zig"].command == "zig build test"


# ── run_build tool ───────────────────────────────────────────────────────────

@pytest.fixture
def store(tmp_path):
    from artifacts.store import ArtifactStore
    s = ArtifactStore(db_path=str(tmp_path / "a.db"), blobs_dir=tmp_path / "blobs")
    s.create(project_id="ios", path="ios/Spider/Package.swift", content="// pkg", content_type="text/plain")
    s.create(project_id="ios", path="ios/Spider/Sources/Game.swift", content="struct G {}", content_type="text/plain")
    s.create(project_id="ios", path="ios/Other/x.md", content="x", content_type="text/markdown")
    import api.artifacts  # noqa: F401  (it imports get_store by name)
    with patch("artifacts.store.get_store", return_value=s), patch("api.artifacts.get_store", return_value=s):
        yield s


async def _run_build(kube, **args):
    from agent.tools import execute_tool
    from config import get_settings
    with patch.object(get_settings(), "pantheon_builder", "kubernetes"), \
         patch("builder.get_builder", return_value=_builder(kube)):
        return await execute_tool("run_build", args, None, project_id="ios", session_id="s")


@pytest.mark.asyncio
async def test_run_build_passes_and_links_the_zip(store):
    kube = FakeKube(log="Test Suite 'All tests' passed\n")
    out = await _run_build(kube, path="Spider/", toolchain="swift")
    assert out.startswith("Build PASSED - toolchain swift")
    assert "2 files from ios/Spider/" in out
    assert "[Spider.zip](/api/artifacts/export-all?project_id=ios&path_prefix=ios/Spider/)" in out
    raw = b"".join(base64.b64decode(v) for cm in kube.configmaps.values() for v in cm["binaryData"].values())
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as tar:
        assert sorted(tar.getnames()) == ["Package.swift", "Sources/Game.swift"]


@pytest.mark.asyncio
async def test_run_build_failure_lists_the_errors(store):
    log = "Compiling\n/work/Sources/Game.swift:1:8: \x1b[1;31merror: \x1b[1;39mexpected '{'\x1b[0;0m\nerror: fatalError\n"
    out = await _run_build(FakeKube(exit_code=1, log=log), path="ios/Spider", toolchain="swift",
                           command="swift build")
    assert out.startswith("Build FAILED") and "Download" not in out
    assert "Error lines:\n  /work/Sources/Game.swift:1:8: error: expected '{'" in out
    assert "\x1b" not in out


@pytest.mark.asyncio
async def test_run_build_rejects_bad_input(store):
    assert "Unknown toolchain" in await _run_build(FakeKube(), path="Spider/", toolchain="cobol")
    assert "no artifacts under 'ios/Nope/'" in await _run_build(FakeKube(), path="Nope", toolchain="swift")
    assert "plain relative folder" in await _run_build(FakeKube(), path="Spider", toolchain="swift", workdir="../x")


def test_run_build_only_offered_with_a_builder():
    from agent.tools import get_all_tool_schemas
    from config import get_settings
    names = lambda: [s["function"]["name"] for s in get_all_tool_schemas()]
    with patch.object(get_settings(), "pantheon_builder", ""):
        assert "run_build" not in names()
    with patch.object(get_settings(), "pantheon_builder", "kubernetes"), \
         patch.object(get_settings(), "builder_toolchains", json.dumps({"zig": {"image": "zig:0.15"}})):
        (s,) = [s for s in get_all_tool_schemas() if s["function"]["name"] == "run_build"]
        assert "zig" in s["function"]["parameters"]["properties"]["toolchain"]["enum"]


# ── Folder zip ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_folder_zip_has_the_folder_at_its_root(store):
    # through the router: /artifacts/{artifact_id} used to swallow "export-all"
    from fastapi import FastAPI
    from api.artifacts import router
    app = FastAPI()
    app.include_router(router, prefix="/api")
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/artifacts/export-all", params={"project_id": "ios", "path_prefix": "ios/Spider"})
    assert resp.status_code == 200, resp.text
    body = resp.content
    assert resp.headers["content-disposition"] == "attachment; filename=Spider.zip"
    with zipfile.ZipFile(io.BytesIO(body)) as zf:
        assert sorted(zf.namelist()) == ["Spider/Package.swift", "Spider/Sources/Game.swift"]
