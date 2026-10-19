# Builds

The `run_build` tool lets the agent check that the code it wrote compiles and that its tests pass. It takes a folder of the project's artifacts, runs a build command on it in a clean, throwaway container with **no network**, and gets back PASSED/FAILED with the compiler and test output. When a build passes, the result links to the folder as a zip (`GET /api/artifacts/export-all?project_id=…&path_prefix=…`). The Artifacts page has the same download for the selected folder.

Pantheon never runs the compiler itself. The builder backend runs it somewhere else; the one that ships is Kubernetes.

## Toolchains

| Name | Image | Default command |
|---|---|---|
| `swift` | `swift:6.4` | `swift build && swift test` |
| `c` | `gcc:16` | `make` |
| `go` | `golang:1.27` | `go build ./... && go test ./...` |
| `rust` | `rust:1.99` | `cargo build --offline && cargo test --offline` |
| `python` | `python:3.15-slim` | `python -m unittest discover -v` |
| `node` | `node:24-slim` | `node --test` |
| `java` | `eclipse-temurin:25-jdk` | `javac` over every `.java` file |

The agent can pass its own `command` and a `workdir` inside the folder. Builds have no network, so third-party dependencies (SwiftPM packages, crates, Go modules, npm packages, Maven) can't be fetched: use images that already contain them, or set `BUILDER_ALLOW_NETWORK=true` and loosen the namespace's NetworkPolicy.

`BUILDER_TOOLCHAINS` (JSON) changes or adds toolchains, e.g. a mirror or an image with your dependencies baked in:

```
BUILDER_TOOLCHAINS={"swift": {"image": "registry.local/swift:6.4"}, "zig": {"image": "docker.io/library/zig:0.15", "command": "zig build test", "about": "Zig 0.15"}}
```

An entry with `"image": ""` removes a toolchain.

### Swift and Apple-platform apps

The `swift` toolchain is Swift on Linux: Swift packages, Foundation, XCTest and Swift Testing. SwiftUI, UIKit and AppKit don't exist there. For an iOS or macOS app, keep the platform-independent code (model, rules, networking) in a Swift package with tests, and guard UI code with `#if canImport(SwiftUI)`. The package then builds and tests here, and the downloaded folder opens in Xcode on a Mac for the app itself.

## Kubernetes backend

`PANTHEON_BUILDER=kubernetes`. Pantheon must run in the cluster. It talks to the API with its service-account token, and creates per build:

- ConfigMaps holding the folder as a gzipped tar (split to fit the 1 MiB object limit; `BUILDER_MAX_SOURCE_MB`, 8, caps the compressed size), owned by the pod;
- one pod: the toolchain image, non-root (uid 1000), every capability dropped, read-only root filesystem with `/work` and `/tmp` as emptyDirs, no service-account token, no service links, limits `BUILDER_CPU` / `BUILDER_MEMORY` / `BUILDER_DISK`.

The pod and ConfigMaps are deleted when the build ends; leftovers from a restart are swept after 6 hours. At most `BUILDER_MAX_CONCURRENT` (2) builds run at once per Pantheon process. `BUILDER_TIMEOUT_SECONDS` (900) limits a build; `BUILDER_PULL_TIMEOUT_SECONDS` (900) limits the time to start, which includes the first pull of an image (the Swift image is about 1.3 GB).

### Setup

1. Edit the RoleBinding subject in `deploy/kubernetes/builder.yaml` to the service account Pantheon runs as, then `kubectl apply -f deploy/kubernetes/builder.yaml`. It creates the `pantheon-builds` namespace with the `restricted` Pod Security Standard enforced, a deny-all NetworkPolicy (ingress and egress), a ResourceQuota, a LimitRange, and a Role limited to that namespace.
2. Make sure Pantheon's pod mounts its service-account token and that its own NetworkPolicy (if any) allows egress to the API server.
3. Set `PANTHEON_BUILDER=kubernetes` and restart. `GET /api/system/builder` should report `"ok": true`.

The builder refuses to run unless a NetworkPolicy in its namespace selects every pod and allows no egress. The cluster's network plugin has to enforce NetworkPolicy (k3s's default does; check yours).

### Why the namespace is locked down on its own

Anything running in Pantheon's pod can read its token, including commands the agent runs through `run_command` or `code_execute` in the subprocess sandbox. So the namespace doesn't rely on Pantheon creating safe pods: the Pod Security Standard rejects privileged, root and host-path pods from anyone, the NetworkPolicy covers every pod in the namespace, the quota caps what can run, and the Role can't edit any of them. The worst a stolen token can do is run unprivileged, offline pods within the quota.
