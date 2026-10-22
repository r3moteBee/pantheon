"""KubernetesBuilder - one pod per build, created through the in-cluster API.

The source travels in ConfigMaps (a gzipped tar split into parts that fit
the 1 MiB object limit), so the build pod needs no network and no access to
Pantheon. The pod runs as a non-root user with every capability dropped, a
read-only root filesystem, no service-account token and CPU / memory /
disk limits - it passes the "restricted" Pod Security Standard, which the
namespace should enforce (deploy/kubernetes/builder.yaml). Builds are
refused unless a NetworkPolicy in the namespace denies all egress, or
BUILDER_ALLOW_NETWORK is set.

Pantheon's service account needs, in that namespace only: pods
create/get/list/delete, pods/log get, configmaps create/get/patch/delete,
networkpolicies list.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from builder.backend import BuildBackend, BuildError, BuildResult, Toolchain, bundle
from config import get_settings

logger = logging.getLogger(__name__)

SA_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
PART_BYTES = 700_000          # raw bytes per ConfigMap; base64 keeps it under 1 MiB
LABELS = {"app.kubernetes.io/managed-by": "pantheon", "pantheon/build": "1"}
MAX_LOG_BYTES = 4 * 1024 * 1024
POLL_S = 2.0

# Unpack, then run the command from $BUILD_COMMAND (never interpolated here).
SCRIPT = ('set -e; mkdir -p "$HOME" "$XDG_CACHE_HOME"; cat /src/part-* | tar -xzf - -C /work; '
          'cd "/work/$BUILD_WORKDIR"; set +e; sh -c "$BUILD_COMMAND"; code=$?; '
          'echo; echo "[pantheon-build] exit code $code"; exit $code')


def _parse_ts(s: str | None) -> float | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


class KubernetesBuilder(BuildBackend):
    name = "kubernetes"

    def __init__(self, namespace: str, *, api_url: str, token_file: Path, ca_file: Path | None,
                 cpu: str, memory: str, disk: str, pull_timeout_s: int, allow_network: bool,
                 max_concurrent: int):
        self.namespace = namespace
        self.api_url = api_url.rstrip("/")
        self.token_file, self.ca_file = token_file, ca_file
        self.cpu, self.memory, self.disk = cpu, memory, disk
        self.pull_timeout_s = pull_timeout_s
        self.allow_network = allow_network
        self._sem = asyncio.Semaphore(max(1, max_concurrent))

    @classmethod
    def from_settings(cls) -> "KubernetesBuilder":
        s = get_settings()
        host, port = os.getenv("KUBERNETES_SERVICE_HOST"), os.getenv("KUBERNETES_SERVICE_PORT", "443")
        if not host or not (SA_DIR / "token").exists():
            raise BuildError("The kubernetes builder needs to run inside the cluster with a "
                             "service-account token (none found).")
        ca = SA_DIR / "ca.crt"
        return cls(s.builder_namespace, api_url=f"https://{host}:{port}", token_file=SA_DIR / "token",
                   ca_file=ca if ca.exists() else None, cpu=s.builder_cpu, memory=s.builder_memory,
                   disk=s.builder_disk, pull_timeout_s=s.builder_pull_timeout_seconds,
                   allow_network=s.builder_allow_network, max_concurrent=s.builder_max_concurrent)

    # ── API plumbing ──────────────────────────────────────────────

    def _client(self) -> httpx.AsyncClient:
        # The token is re-read per client: projected tokens rotate.
        token = self.token_file.read_text().strip()
        return httpx.AsyncClient(base_url=self.api_url, timeout=30.0,
                                 verify=str(self.ca_file) if self.ca_file else True,
                                 headers={"Authorization": f"Bearer {token}"})

    async def _req(self, c: httpx.AsyncClient, method: str, path: str, **kw: Any) -> httpx.Response:
        r = await c.request(method, f"/api/v1/namespaces/{self.namespace}{path}", **kw)
        if r.status_code >= 400 and not (method == "DELETE" and r.status_code == 404):
            try:
                msg = r.json().get("message") or r.text
            except ValueError:
                msg = r.text
            raise BuildError(f"Kubernetes API {method} {path}: {r.status_code} {msg[:300]}")
        return r

    async def _check_network_policy(self, c: httpx.AsyncClient) -> None:
        if self.allow_network:
            return
        r = await c.get(f"/apis/networking.k8s.io/v1/namespaces/{self.namespace}/networkpolicies")
        if r.status_code >= 400:
            raise BuildError(f"Cannot read NetworkPolicies in {self.namespace} ({r.status_code}); builds "
                             "are refused until a deny-all-egress policy can be confirmed.")
        for p in r.json().get("items", []):
            spec = p.get("spec") or {}
            if (not (spec.get("podSelector") or {}).get("matchLabels")
                    and not (spec.get("podSelector") or {}).get("matchExpressions")
                    and "Egress" in (spec.get("policyTypes") or []) and not spec.get("egress")):
                return
        raise BuildError(f"Namespace {self.namespace} has no NetworkPolicy denying all egress, so a build "
                         "could reach the network. Apply deploy/kubernetes/builder.yaml, or set "
                         "BUILDER_ALLOW_NETWORK=true to build with network access.")

    async def _sweep(self, c: httpx.AsyncClient) -> None:
        """Delete build pods left behind by a restart (their ConfigMaps go with them)."""
        try:
            r = await self._req(c, "GET", "/pods", params={"labelSelector": "pantheon/build=1"})
        except BuildError:
            return
        cutoff = time.time() - 6 * 3600
        for p in r.json().get("items", []):
            md = p.get("metadata") or {}
            if (_parse_ts(md.get("creationTimestamp")) or time.time()) < cutoff:
                await self._req(c, "DELETE", f"/pods/{md['name']}")

    # ── Build ─────────────────────────────────────────────────────

    def _pod(self, name: str, toolchain: Toolchain, parts: list[str], command: str,
             workdir: str, timeout_s: int, label: str) -> dict[str, Any]:
        env = {"BUILD_COMMAND": command, "BUILD_WORKDIR": workdir, "HOME": "/tmp/home",
               "XDG_CACHE_HOME": "/tmp/cache", "GOCACHE": "/tmp/cache/go", "GOPATH": "/tmp/go",
               "GOTOOLCHAIN": "local", "CARGO_HOME": "/tmp/cargo", "CARGO_TARGET_DIR": "/tmp/target",
               "npm_config_cache": "/tmp/cache/npm", "PYTHONDONTWRITEBYTECODE": "1", "CI": "1",
               "NO_COLOR": "1", "TERM": "dumb", "CARGO_TERM_COLOR": "never"}
        return {
            "apiVersion": "v1", "kind": "Pod",
            "metadata": {"name": name, "labels": {**LABELS, "pantheon/toolchain": toolchain.name},
                         "annotations": {"pantheon/label": label[:200]}},
            "spec": {
                "restartPolicy": "Never",
                "automountServiceAccountToken": False,
                "enableServiceLinks": False,
                "activeDeadlineSeconds": self.pull_timeout_s + timeout_s + 60,
                "securityContext": {"runAsNonRoot": True, "runAsUser": 1000, "runAsGroup": 1000,
                                    "fsGroup": 1000, "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{
                    "name": "build", "image": toolchain.image, "imagePullPolicy": "IfNotPresent",
                    "command": ["/bin/sh", "-c", SCRIPT],
                    "env": [{"name": k, "value": v} for k, v in env.items()],
                    "resources": {"requests": {"cpu": "250m", "memory": "256Mi"},
                                  "limits": {"cpu": self.cpu, "memory": self.memory,
                                             "ephemeral-storage": self.disk}},
                    "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True,
                                        "capabilities": {"drop": ["ALL"]}},
                    "volumeMounts": [{"name": "src", "mountPath": "/src", "readOnly": True},
                                     {"name": "work", "mountPath": "/work"},
                                     {"name": "tmp", "mountPath": "/tmp"}],
                }],
                "volumes": [
                    {"name": "src", "projected": {"sources": [{"configMap": {"name": p}} for p in parts]}},
                    {"name": "work", "emptyDir": {"sizeLimit": self.disk}},
                    {"name": "tmp", "emptyDir": {"sizeLimit": self.disk}},
                ],
            },
        }

    async def run(self, toolchain: Toolchain, files: dict[str, bytes], *, command: str,
                  workdir: str = "", timeout_s: int = 900, label: str = "") -> BuildResult:
        tgz = bundle(files)
        limit = get_settings().builder_max_source_mb * 1024 * 1024
        if len(tgz) > limit:
            raise BuildError(f"The folder is {len(tgz) / 1048576:.1f} MB compressed; the limit is "
                             f"{get_settings().builder_max_source_mb} MB (BUILDER_MAX_SOURCE_MB).")
        async with self._sem:
            name = f"pantheon-build-{uuid.uuid4().hex[:10]}"
            chunks = [tgz[i:i + PART_BYTES] for i in range(0, len(tgz), PART_BYTES)] or [b""]
            parts = [f"{name}-src-{i}" for i in range(len(chunks))]
            async with self._client() as c:
                await self._check_network_policy(c)
                await self._sweep(c)
                try:
                    for i, (cm, data) in enumerate(zip(parts, chunks)):
                        await self._req(c, "POST", "/configmaps", json={
                            "apiVersion": "v1", "kind": "ConfigMap",
                            "metadata": {"name": cm, "labels": LABELS},
                            "binaryData": {f"part-{i:03d}": base64.b64encode(data).decode()}})
                    pod = (await self._req(c, "POST", "/pods", json=self._pod(
                        name, toolchain, parts, command, workdir, timeout_s, label))).json()
                    owner = [{"apiVersion": "v1", "kind": "Pod", "name": name,
                              "uid": pod["metadata"]["uid"]}]
                    for cm in parts:   # garbage-collected with the pod from here on
                        await self._req(c, "PATCH", f"/configmaps/{cm}",
                                        headers={"Content-Type": "application/merge-patch+json"},
                                        json={"metadata": {"ownerReferences": owner}})
                    return await self._wait(c, name, timeout_s)
                finally:
                    # Each delete on its own: a failed pod delete used to skip
                    # the ConfigMaps, leaving the source behind in the namespace.
                    for path, params in [(f"/pods/{name}", {"gracePeriodSeconds": "0"})] + [
                            (f"/configmaps/{cm}", None) for cm in parts]:
                        try:
                            await self._req(c, "DELETE", path, params=params)
                        except Exception as e:
                            logger.warning("Build %s cleanup of %s failed: %s", name, path, e)

    async def _wait(self, c: httpx.AsyncClient, name: str, timeout_s: int) -> BuildResult:
        created = time.monotonic()
        started: float | None = None
        while True:
            await asyncio.sleep(POLL_S)
            pod = (await self._req(c, "GET", f"/pods/{name}")).json()
            st = pod.get("status") or {}
            cs = (st.get("containerStatuses") or [{}])[0]
            state = cs.get("state") or {}
            waiting = (state.get("waiting") or {}).get("reason", "")
            if waiting in ("ErrImagePull", "ImagePullBackOff", "InvalidImageName",
                           "CreateContainerConfigError", "CreateContainerError"):
                if waiting != "ErrImagePull" or time.monotonic() - created > 60:
                    raise BuildError(f"The build container could not start ({waiting}: "
                                     f"{(state.get('waiting') or {}).get('message', '')[:300]}).")
            if started is None and (state.get("running") or state.get("terminated")):
                started = time.monotonic()
            term = state.get("terminated")
            if term or st.get("phase") in ("Succeeded", "Failed"):
                log = await self._log(c, name)
                term = term or {}
                t0, t1 = _parse_ts(term.get("startedAt")), _parse_ts(term.get("finishedAt"))
                took = (t1 - t0) if t0 and t1 else time.monotonic() - (started or created)
                return BuildResult(exit_code=term.get("exitCode"), log=log, duration_s=round(took, 1),
                                   reason=term.get("reason") or st.get("reason") or "")
            if started is None and time.monotonic() - created > self.pull_timeout_s:
                raise BuildError(f"The build pod did not start within {self.pull_timeout_s} s "
                                 f"(status {st.get('phase')}, {waiting or 'no reason given'}).")
            if started is not None and time.monotonic() - started > timeout_s:
                log = await self._log(c, name)
                return BuildResult(exit_code=None, log=log, timed_out=True,
                                   duration_s=round(time.monotonic() - started, 1), reason="Timeout")

    async def _log(self, c: httpx.AsyncClient, name: str) -> str:
        try:
            r = await self._req(c, "GET", f"/pods/{name}/log",
                                params={"container": "build", "limitBytes": str(MAX_LOG_BYTES)})
            return r.text
        except BuildError as e:
            return f"(log unavailable: {e})"

    async def health(self) -> dict[str, Any]:
        out: dict[str, Any] = {"backend": self.name, "namespace": self.namespace,
                               "allow_network": self.allow_network}
        try:
            async with self._client() as c:
                await self._req(c, "GET", "/pods", params={"labelSelector": "pantheon/build=1", "limit": "1"})
                await self._check_network_policy(c)
            out["ok"] = True
        except Exception as e:
            out.update(ok=False, error=str(e))
        return out
