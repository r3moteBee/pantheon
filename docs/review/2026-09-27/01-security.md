# Pantheon security review (read-only, code-verified)

Scope: `/home/user/pantheon` at commit `cb4cc7e`. Every CLAUDE.md security claim was checked against the code; findings below cite file:line. Severity is rated for the real deployment: a single-user harness on a LAN box, password-gated, with an LLM agent that reads untrusted content (web pages, transcripts, MCP tool output) and can call tools.

## Findings table

| ID | Severity | Title | Location |
|----|----------|-------|----------|
| F1 | **High** | SSRF guard bypass: `sec/edgar` adapter fetches any LLM-supplied URL with raw httpx | `backend/sources/adapters/sec_edgar.py:49-52` (reachable via `agent/tools.py:2411-2423`) |
| F2 | **Medium** | Any vault secret can be sent to an attacker URL via vault-key indirection (`webhook_token_key`, `api_key_vault_key`) | `backend/jobs/sinks/webhook_sink.py:23-29,43-44`; `backend/api/jobs.py:57-64`; `backend/api/settings.py:415-424` |
| F3 | **Medium** | Per-project personality read/write does not validate `project_id` (path traversal, fixed filename) | `backend/agent/personality.py:75,98,108`; `backend/api/personality.py:33-48,57-72` |
| F4 | **Medium** | Public default `VAULT_MASTER_KEY`/`SECRET_KEY` only produce a log warning; `.env.example` placeholder password is accepted as a real password | `backend/config.py:39-43`; `backend/main.py:110-120`; `start.sh:37` |
| F5 | **Medium** | Outdated `python-multipart==0.0.9` (multipart DoS, fixed 0.0.18) and other stale pins | `backend/requirements.txt:6,1,5,11` |
| F6 | **Low** | `str.startswith` path checks (the pattern CLAUDE.md forbids) in skill executor and project import | `backend/skills/executor.py:66`; `backend/api/project_import.py:920` |
| F7 | **Low** | Host-fixed outbound fetches skip `safe_http_get` and follow redirects unguarded | `sources/adapters/github.py:87,292`, `cfr.py:173`, `forum.py:80`, `malegislature.py:57,401`, `sec_edgar.py:66,89,117`, `agent/tools.py:3831,3855`, `skills/importer.py:360,529`, `agent/search_providers.py:358-552` |
| F8 | **Low** | Login throttle keyed on `request.client.host` collapses to one global bucket behind the shipped nginx/Caddy proxies; unbounded dict | `backend/api/auth.py:151-170` |
| F9 | **Low** | Skill security-override password compared with `!=` | `backend/api/skills.py:711` |
| F10 | **Low** | Partial API-key disclosure in MCP debug/test endpoints; Tavily key travels in query string | `backend/api/mcp.py:275,343-358`; `backend/mcp_client/client.py:124-126` |
| F11 | **Low** | `POST /api/jobs` accepts arbitrary `job_type`/`payload` with no validation | `backend/api/jobs.py:18-26,57-64` |
| F12 | **Low** | LibreOffice preview render lacks the throwaway profile used elsewhere; office/HTML conversions can make unguarded network fetches | `backend/artifacts/preview.py:138-142` vs `backend/utils/document_converter.py:302-313` |
| F13 | **Low** | Whole-file/zip buffering in memory (2 GB cap) on file endpoints | `backend/api/files.py:121,169,267-291` |
| F14 | **Info** | `/docs`, `/redoc`, `/openapi.json` are public | `backend/main.py:47-49` |
| F15 | **Info** | Browser guard caches host verdict 60 s and cannot pin DNS (documented); `is_global` treats NAT64/6to4 IPv6 as public | `backend/agent/browser_tools.py:101-125`; `backend/utils/net.py:32-36` |
| F16 | **Info** | Untrusted tool/ingest output is handed to the LLM without an "untrusted data" delimiter | `backend/mcp_client/manager.py:96-125` |
| F17 | **Info** | Docker image runs as root | `backend/Dockerfile:44` (no `USER`) |
| F18 | **Info** | Slack/Discord allowlists are read once at adapter start | `backend/messaging/adapters/slack.py:115-119`; `discord.py:108,121` |
| F19 | **Info** | Stale/incorrect security docs (`.env.example`, `docs/SECURITY_FEATURES.md`) | see section |

---

## F1 — High — `sec/edgar` adapter is an SSRF hole in the ingest pipeline

**Code.** `backend/sources/adapters/sec_edgar.py:49-56`:

```python
if identifier.startswith("http://") or identifier.startswith("https://"):
    # Direct SEC URL provided
    url = identifier
    ...
    async with httpx.AsyncClient() as client:
        r = await client.get(url, headers=SEC_HEADERS, timeout=60)
        r.raise_for_status()
        html_content = r.text
```

No host check, no `safe_http_get`, and `html_content` is converted to markdown and saved as an artifact (returned to the model). The adapter is registered (`backend/sources/adapters/__init__.py:16`) with `bucket_aliases = ("sec", "edgar")`, and the agent tool `ingest_source` passes `source_type` and `identifier` straight through (`backend/agent/tools.py:2411-2423`).

**Why it matters here.** CLAUDE.md states "Outbound fetches go through `utils.net.safe_http_get`… Don't add `httpx.get(model_chosen_url…)`". This is exactly that. A prompt-injected page or transcript can make the agent call `ingest_source(source_type="sec/edgar", identifier="http://127.0.0.1:8000/api/settings")` (or `http://169.254.169.254/…`, ChromaDB on `127.0.0.1:8001`, any LAN device) and read the response back. Because the request originates from the backend, the auth middleware is irrelevant only if it has a cookie — but LAN services and ChromaDB (unauthenticated, per `docker-compose.yml`) are fully exposed. The `web`, `blog`, `pdf`, `podcast` adapters and `download_file` are correctly guarded, so this single adapter defeats the guarantee.

**Fix.** Replace `httpx.AsyncClient().get(url)` with `await safe_http_get(url, headers=SEC_HEADERS, timeout=60)` and additionally require `urlparse(url).hostname in {"www.sec.gov", "sec.gov", "data.sec.gov"}` for the direct-URL branch. Add an integration test that asserts `ingest_source` with a loopback URL is refused for every registered adapter (iterate `registry.list_adapters()`).

## F2 — Medium — Vault-key indirection turns any vault secret into an outbound Bearer token

**Code.** `backend/jobs/sinks/webhook_sink.py:17-29,43-44`:

```python
url = opts.get("webhook_url")
...
token_key = opts.get("webhook_token_key")
if token_key:
    tok = SecretsVault().get_secret(token_key)
    if tok:
        headers["Authorization"] = f"Bearer {tok}"
...
r = await client.post(url, json=body, headers=headers)
```

`opts` is the job payload's `output_sink` (`backend/jobs/handlers/scheduled_job.py:42,110-111`), and `POST /api/jobs` creates a job with an arbitrary `job_type` and `payload` (`backend/api/jobs.py:57-64`). The same pattern exists for search providers: `SearchProviderConfig.api_key_vault_key` (`backend/api/settings.py:415-424`) is a free-form vault key that `search_providers._get_api_key` sends as `Authorization`/`X-Subscription-Token` to a user-configured `url`.

**Why it matters here.** The rest of the API is careful never to return vault values (`api/settings.py:99-149` only returns `*_set` booleans; `EndpointPublic` drops `api_key`; `mcp.list_connections` masks). These two paths break that: a single authenticated request can push `llm_api_key`, any `github_pat::<id>`, `mcp_oauth_tokens__<name>`, or the whole `mcp_connections` JSON (which contains MCP API keys in clear) to an arbitrary URL. It also makes the session cookie a full vault-dump credential (a stolen cookie or an XSS would suffice), and the webhook POST itself is another unguarded outbound request (SSRF to internal HTTP services with a JSON body).

**Fix.** Namespace the lookups: only accept keys with a dedicated prefix (`webhook_token__*`, `search_api_key__*`) and reject anything else at both write (`PUT /api/secrets/{key}`, `SearchProviderConfig`) and read time. Route the webhook POST through a `safe_http_post` variant of `utils.net`. Validate `job_type` against `known_types()` in `create_job`.

## F3 — Medium — Personality endpoints trust `project_id` as a path segment

**Code.** `backend/agent/personality.py:95-99`:

```python
if project_id:
    path = settings.projects_dir / project_id / "personality" / "soul.md"
    path.parent.mkdir(parents=True, exist_ok=True)
...
path.write_text(content, encoding="utf-8")
```

`load_project_personality` (line 75) reads the same way. `api/personality.py` passes `project_id` from the query string with no `check_project_id` (compare `api/files.py:31-41` and `agent/tools.py:4265`, which do validate).

**Why it matters here.** `PUT /api/personality/soul?project_id=../../../../tmp/x` creates `/tmp/x/personality/soul.md` (or any writable location) with attacker content; `GET` reads `<anything>/personality/{soul,agent}.md`. The filename and `personality/` segment are fixed, so this is not arbitrary-file-write, but it is an out-of-workspace write/read as the service user and directly contradicts the CLAUDE.md rule "Untrusted names → paths. Use `utils.paths`". It is also reachable from the agent only if a tool wraps these functions (none found), so the caller is the authenticated user.

**Fix.** Call `utils.paths.check_project_id(project_id)` in `load_project_personality`, `save_soul`, `save_agent_config` (or in the router) and wrap with `is_within(path, settings.projects_dir)`.

## F4 — Medium — Default secrets and placeholder password are accepted

**Code.** `backend/config.py:39-43`:

```python
vault_master_key: str = Field(default="dev-key-change-in-production-32x", ...)
secret_key: str = Field(default="dev-secret-key-change-in-production", ...)
auth_password: str = Field(default="", ...)
```

`backend/main.py:110-120` only `logger.warning(...)`s when the defaults are in use. `start.sh:37` treats `AUTH_PASSWORD="insert-auth-password-here"` (the `.env.example` value) as *unset* for the bind decision, but the backend (`auth.py:35`) treats it as the real password.

**Why it matters here.** With the default master key, `data/db/vault.db` (LLM keys, GitHub PATs, OAuth tokens, MCP API keys, bot tokens) is decryptable by anyone who gets the file — a backup, a project export copied around, or the `data/` bind-mount. With the placeholder password plus `BIND_HOST=0.0.0.0`, the LAN-exposed UI is protected by a string that is in the public repo. The vault KDF v2 upgrade (600k PBKDF2, random salt — `vault.py:29-62`) does not help when the input key is public.

**Fix.** In `lifespan`, refuse to start (or refuse to bind to a non-loopback address) when either default is in use or `auth_password == "insert-auth-password-here"`. Have `deploy.sh` generate them (the `.env.example` comment says it does; make the backend enforce it).

## F5 — Medium — Dependency pins

`backend/requirements.txt`: `python-multipart==0.0.9` (line 6) predates the fix for the malformed-boundary DoS (fixed in 0.0.18; FastAPI's form/upload parsing uses it — `api/files.py`, `api/artifacts.py`, `api/projects.py` import). `fastapi==0.111.0`, `httpx==0.27.0`, `cryptography==42.0.7`, `uvicorn==0.29.0` are 2024-era pins; `chromadb`, `trafilatura`, `PyMuPDF`, `pdfplumber` are unpinned ranges (parse untrusted PDFs/HTML). Frontend `package.json` uses caret ranges with a lockfile (fine); `dompurify ^3.4.16` is past the 3.2.4 mXSS fix; `axios ^1.7.2` allows <1.8.2 (absolute-URL SSRF, client-side only, low). No CI runs `pip-audit`/`npm audit`.

**Fix.** Bump `python-multipart>=0.0.18`, run `pip-audit` on the venv and `npm audit` on the dev box, and add both to the manual test checklist in CLAUDE.md.

## F6 — Low — Prefix-string path checks

`backend/skills/executor.py:63-68`:

```python
script_path = (skill_dir / script_name).resolve()
if not str(script_path).startswith(str(skill_dir.resolve())):
```

`backend/api/project_import.py:919-922`:

```python
target = (project_dir / relative).resolve()
if not str(target).startswith(str(dest_resolved)):
```

Both are the sibling-prefix bug CLAUDE.md calls out (`data/skills/foo` vs `data/skills/foo-evil`; `projects/x` vs `projects/x-1`). Mitigations: the import scanner rejects any member containing `..` (`project_import.py:201-207`) and `skip_scan` is not exposed by the API (`api/projects.py:253-283`); `execute_script` has no caller from agent tools or the API today (only `sandbox/subprocess_backend.py:46`). `skills/importer.py:51,68` uses the same idiom but appends `"/"`, which is correct. `docs/SECURITY_FEATURES.md` §5 advertises the executor check as traversal prevention.

**Fix.** Replace with `utils.paths.is_within`.

## F7 — Low — Host-fixed fetches outside `safe_http_get`

Sites: `sources/adapters/github.py:85-88,292` (api.github.com / raw.githubusercontent.com), `cfr.py:173`, `forum.py:80` (reddit / hn.algolia), `malegislature.py:57,401`, `sec_edgar.py:66,89,117` (data.sec.gov), `agent/tools.py:3831,3855` (data.sec.gov), `skills/importer.py:312,360,529,693,725`, `skills/publisher.py:98`, `agent/search_providers.py:358-552`, `models/discovery.py:17`, `llm_config/probe.py:37`, `mcp_client/oauth.py:204-441`. All build URLs from constants or user configuration, so the host is not model-controlled. The residual risk is `follow_redirects=True` with no per-hop check: an open redirect on one of those hosts (GitHub release assets → S3, Reddit `out/` links) or a compromised MCP/registry server could redirect to `127.0.0.1`. Identifier fragments (`owner/repo`, subreddit, chapter ids) are interpolated into paths without encoding, so `..`/`?` can alter the API path but not the host.

**Fix.** Give `utils.net` a `safe_http_request(method, url, ...)` and use it here too, or at least pass `follow_redirects=False` and re-check hops. URL-encode identifier segments.

## F8 — Low — Login throttle behind a proxy

`backend/api/auth.py:167-170`: `ip = request.client.host`. The shipped `nginx/*.conf` and `Caddyfile` terminate in front of uvicorn, so every request arrives from `127.0.0.1`: ten wrong passwords from anyone lock everyone out for five minutes, and a real attacker gets the same 10/5-min budget as the whole LAN. `_failures` (line 153) is never pruned by key. `POST /api/system/update/execute` (`api/system.py:123`) also calls `password_matches` without the throttle, though it already requires a session.

**Fix.** Add a `TRUSTED_PROXY_IPS` setting and use `X-Forwarded-For` only when `request.client.host` is in it; add a global cap; prune empty deques.

## F9 — Low — Non-constant-time override password compare

`backend/api/skills.py:711`: `if not req.override_password or req.override_password != stored_pw:`. `password_matches` (`auth.py:30-36`) does this correctly with `hmac.compare_digest`; reuse it (or `compare_digest`) here.

## F10 — Low — Partial key disclosure and key-in-URL

`backend/api/mcp.py:275` returns `api_key[:8] + "..."`; `:343-358` returns `api_key[:6] + "***" + api_key[-4:]` in `built_url`/`built_headers` and `api_key[:8]` again — 10–14 characters of a live key to any authenticated caller. `mcp_client/client.py:124-126` appends `tavilyApiKey=<key>` to the request URL (Tavily's design; the key lands in proxy/server logs). `client.py:166-170` logs `api_key[:6]` and the first 10 chars of `Authorization` at INFO. Low because the caller is already the single user, but it leaks into `backend.log`. Reduce to `[:4]`, log at DEBUG.

## F11 — Low — `POST /api/jobs` is a raw job-row writer

`backend/api/jobs.py:18-26,57-64` accepts any `job_type` and any `payload` dict. It is the entry point for F2 and can also enqueue `coding_task`/`iteration_loop` with hand-built payloads. Validate `job_type in known_types()` and use per-type Pydantic payload models.

## F12 — Low — LibreOffice/pandoc on untrusted documents

`utils/document_converter.py:31-50` adds `--sandbox` for pandoc and `:302-313` runs soffice with `-env:UserInstallation=file://<tmp>/lo-profile` (good). `artifacts/preview.py:138-142` runs soffice without that profile flag (`soffice --headless --convert-to pdf --outdir …`), so it shares the service user's real LibreOffice profile/macros and lock. Neither soffice path prevents LibreOffice from fetching remote images/links in DOCX/HTML (an SSRF-by-side-channel from ingested documents). Add the profile flag in `preview.py`; consider `--norestore --nolockcheck` and a network-less wrapper (e.g. `unshare -n`) where available.

## F13 — Low — In-memory buffering

`api/files.py:121,169` read entire uploads into memory; `:267-291` builds a zip of up to 2 GB in a `BytesIO`. Local mode has no body cap (nginx sets 100M in Docker mode). Stream uploads to disk and stream the zip. Single-user, so DoS impact is self-inflicted — but a background job could hit it.

## F14 — Info — Public API docs

`main.py:47-49` lists `/docs`, `/redoc`, `/openapi.json` in `_PUBLIC_PATHS`, so the full route/schema catalogue is readable without a session. Harmless for a single user; remove from `_PUBLIC_PATHS` in production if the box is LAN-exposed.

## F15 — Info — Browser guard TTL cache and `is_global`

`browser_tools.py:114-125` caches the allow verdict per `scheme://host:port` for 60 s; a DNS answer that flips to a private IP within that window is not re-checked (Playwright cannot pin, as the comment says). `utils/net.py:32-36` uses `ipaddress.is_global`; Python's IPv6 `is_global` treats `64:ff9b::/96` (NAT64) and `2002::/16` (6to4) as global, so on a NAT64-enabled host `http://[64:ff9b::7f00:1]/` would pass and reach 127.0.0.1. Add explicit denylist entries for those prefixes.

## F16 — Info — No untrusted-data framing for tool output

`mcp_client/manager.py:96-125` returns MCP text as-is (plus `<structured-output>`); ingest adapters return page text verbatim. There is no delimiter/instruction telling the model that tool results are data. The real mitigations are structural and present: host-exec tools are removed from the schema *and* refused at dispatch for background contexts (`agent/core.py:429-433,521-526`), `create_task` forces `skip_review=False` when `not interactive` (`agent/tools.py:2803-2804`). Remaining injected-content capabilities in background contexts: `ingest_source` (→ F1), `send_telegram_message` (`tools.py:2936`), artifact/workspace writes, `create_task` proposals. Worth documenting explicitly; consider wrapping tool results in a fixed `<tool_output source=…>` envelope in `core.py`.

## F17 — Info — Container runs as root

`backend/Dockerfile` has no `USER` directive; `CMD` runs uvicorn as root with `./data` bind-mounted. Add a non-root user.

## F18 — Info — Allowlist snapshot at start

`slack.py:115` and `discord.py:108` read the allowlist once in `start()`; Telegram (`telegram.py:96`), Matrix (`matrix.py:151`), Mattermost (`mattermost.py:193`) read it per message. Editing the Slack/Discord allowlist in Settings has no effect until the adapter restarts — a revoked channel keeps access. Re-read per event like the others.

## F19 — Info — Documentation drift

- `.env.example:90` "Leave empty to allow all users" and `:96` "Leave empty to allow all guilds" — false; all five adapters are deny-by-default (verified below). `README.md:86` similar phrasing for `AUTH_PASSWORD` is accurate.
- `docs/SECURITY_FEATURES.md` (dated 2026-04-06) covers only the skills subsystem. Inaccuracies: §5 "no `shell=True`" is true for `skills/executor.py:130` but agent inline code and `run_command` go through `sandbox/subprocess_backend.py:94` `create_subprocess_shell` (intentional, but undocumented); §5 "Path traversal prevention" rests on the `startswith` check (F6); the sandbox module (`PANTHEON_SANDBOX=subprocess|firecracker`), host-exec gating (`AGENT_HOST_EXEC`), the auth/session design, SSRF guard, browser guard, WebSocket auth and vault KDF v2 are absent. §8 "Known Gaps" is still accurate (network/memory/file-path permissions unenforced).

---

## Verified OK (CLAUDE.md claims confirmed in code)

- **Auth middleware / session model.** `main.py:244-286`: Origin check on non-GET, host allowlist when auth is off, exact-match `_PUBLIC_PATHS`, static shell passthrough only for non-`/api/`,`/ws/` paths, then `token_is_valid(request_token(...))`. Tokens come only from `Authorization: Bearer` or the `pantheon_session` cookie (`auth.py:114-120`); no query-string path exists. Sessions are random 32-byte tokens, SHA-256 stored in `data/db/auth_sessions.db` with expiry and a password fingerprint so changing `AUTH_PASSWORD`/`SECRET_KEY` invalidates them (`auth.py:73-111`). Cookie is `HttpOnly; SameSite=Strict; Path=/`, `Secure` when the request or `X-Forwarded-Proto` is https (`auth.py:123-132`). Frontend never stores the token (`client.js:14-15` removes the legacy `localStorage` key; `LoginPage.jsx:31` relies on the cookie).
- **Constant-time password check.** `auth.py:30-36` HMAC-SHA256 both sides then `hmac.compare_digest`; used by login and the update gate.
- **WebSocket auth.** The only WebSocket endpoint is `/ws/chat` (`chat.py:395`, mounted `main.py:313`); it calls `authorize_websocket` before `accept()` (`chat.py:398-401`). `authorize_websocket` checks Origin (same-host or `CORS_ORIGINS`), the DNS-rebinding host guard when auth is off, and the session (`auth.py:234-255`).
- **CORS.** Explicit default origin list (`config.py:79`), `allow_credentials=True` (`main.py:234-240`); no wildcard.
- **DNS-rebinding guard when auth is disabled.** `auth.py:258-285`; `start.sh:33-44` binds 127.0.0.1 without a password.
- **SSRF guard implementation.** `utils/net.py:39-68` rejects non-http(s), resolves every address, rejects non-global (incl. IPv4-mapped IPv6); `:81-128` pins the connection to the validated IP with `Host` header + `sni_hostname`, follows ≤5 redirects re-checking each hop, skips pinning only behind a proxy. Used by `download_file` (`tools.py:1874-1878`), `web`/`blog`/`podcast`/`pdf` adapters, and image fetches in `models/provider.py:194`. No `trafilatura.fetch_url` anywhere.
- **Browser route guard.** `browser_tools.py:66` installs `_guard_route` on every context; `:128-155` aborts non-public requests, fetches navigations with `max_redirects=0` and refuses private redirect targets; `_page_is_safe` re-checks `page.url` (`:158-168`).
- **Path safety in files/workspace tools.** `api/files.py:31-49` (`check_project_id` + `is_within`), `agent/tools.py:4264-4278` (`_get_workspace_base`/`_safe_workspace_path`), `read_file`/`write_file`/`list_workspace_files`/`index_workspace` (`tools.py:2053,2103,2108-2114,2947-2950`), `download_file` (`safe_filename` on user, URL and Content-Disposition names, final `is_within` — `tools.py:1866-1893`), `run_command` workdir (`tools.py:3208-3214`, `is_relative_to`), glob expansion re-validated (`tools.py:1974-1984`, `api/files.py:481-490`), conversion format via `validate_format` (`api/files.py:528-532`, `tools.py:1912`). Upload filenames reduced to `Path(name).name` (`files.py:107,157`). Skill editor uses a slug regex and `_safe_join` with `.parents` (`skills/editor.py:35-54`). Skill archives: `_safe_extract_zip`/`_safe_extract_tar` reject absolute paths, traversal (prefix + `/`), and symlinks/hardlinks (`importer.py:40-70`); install dir name is re-slugged (`importer.py:958-967`). Project import scanner rejects `..`, absolute names, symlinks, forbidden extensions, oversize members (`project_import.py:196-244`) and the API does not expose `skip_scan`.
- **Host-exec gating.** `HOST_EXEC_TOOLS` (`tools.py:28-32`), `host_exec_allowed` honours `AGENT_HOST_EXEC` (`:35-47`). Enforced twice: tools are stripped from the schema (`core.py:429-433`) and refused at dispatch (`core.py:521-526`); `core.py:528` is the only `execute_tool` call site. Chat and `coding_task` pass `"interactive"`; autonomous/scheduled/iteration jobs and all five bot adapters pass `"background"` (grep confirmed every `AgentCore(` call site).
- **Git credentials.** `_git_auth_env` uses `GIT_CONFIG_*` `http.https://github.com/.extraheader` with the clean URL (`tools.py:4341-4355`), `_scrub_git_config` removes legacy token URLs (`:4361-4372`), errors are redacted (`:3553-3554`).
- **Command execution.** All `subprocess`/`create_subprocess_exec` calls use argv lists (`document_converter.py:201-202,302-316,341-344`, `artifacts/preview.py:138`, `api/system.py:43-75,154-160`, `skills/executor.py:130-138`, `tools.py:4382-4410`). The only `create_subprocess_shell` uses are the agent's own `code_execute`/`run_command` (`sandbox/subprocess_backend.py:94`, shell-quoted argv, own process group, filtered env, ulimit) and `utils/autoresearch.py:100` (CLI runner, operator-supplied `eval_cmd`).
- **SQL.** Every query is parameterised. The four f-string sites are safe: `memory/graph.py:589` (`?` placeholder count), `utils/self_doc.py:91` (table names from `sqlite_master`), `artifacts/store.py:225` (fixed column names), `llm_config/usage.py:82` (constant column list).
- **Secrets never echoed.** `GET /api/settings` returns `*_set` booleans (`settings.py:99-149`); `GET /api/secrets` returns keys only (`:382-386`); `EndpointPublic` has `api_key_set` only (`llm_config/models.py:57-59`); `mcp.list_connections` masks headers and returns `has_api_key` (`manager.py:234-247`); GitHub PATs live in the vault under `github_pat::<id>` and `list_github_connections` never returns them (`connections.py:139-140,217-232`); PAT-listing endpoint takes the token in a POST body (`:254-257`). No logger call prints a token/key value (only key *names*, `vault.py:162`).
- **Vault KDF v2.** PBKDF2-SHA256, 600k iterations, random 16-byte per-vault salt in `vault_meta`, full master key; v1 vaults are re-encrypted in one `BEGIN IMMEDIATE` transaction on first open (`vault.py:29-121`).
- **Messaging allowlists are deny-by-default** in all five adapters: Telegram `telegram.py:93-104`, Slack `slack.py:117-125`, Discord `discord.py:119-127` (DMs have `guild_id=None` → denied), Matrix `matrix.py:151-156`, Mattermost `mattermost.py:193-198`. All adapters use outbound long-polling/socket mode — there are **no inbound webhook routes** to verify signatures on (grep of `@router` found none).
- **MCP OAuth.** PKCE S256 with a 48-byte verifier (`oauth.py:128-133`), 24-byte random `state`, single-use `take_pending` pop with 10-min TTL (`:116-125,370-373`), fixed `redirect_uri` (`:47`), RFC 8707 `resource` indicator, tokens/DCR secret stored in the vault (`:468-528`), refresh failure flips `needs_auth`. Callback page HTML-escapes AS-controlled strings and escapes `</` inside the inline script (`api/mcp_oauth.py:62-67,85-95,138-140`). `/api/mcp/oauth/callback` is the only state-bearing public route and is protected by `state`.
- **MCP port scan** is not steerable: `_scan_port` hard-codes `127.0.0.1` and the range 8120–8145 (`api/mcp.py:367-423`).
- **Frontend sinks.** `dangerouslySetInnerHTML` and `innerHTML` are wrapped in `DOMPurify.sanitize` (`ArtifactsPage.jsx:843-845,897`); `Mermaid.jsx:65` inserts mermaid output rendered with `securityLevel: 'strict'` (`:19`); untrusted workspace HTML goes through `SandboxedHtml` (`srcDoc`, `sandbox="allow-scripts"`, no `allow-same-origin`, `referrerPolicy="no-referrer"`) and `/api/files/view` adds `Content-Security-Policy: sandbox allow-scripts` for html/svg (`files.py:239-246`); markdown links use react-markdown 9's default URL sanitiser (no `urlTransform` override, no `rehype-raw`) and `rel="noopener noreferrer"` (`Chat.jsx:103,122,267,276`). `localStorage` holds only UI preferences.
- **Misc.** `serve_spa` checks `_FRONTEND_DIR in file_path.resolve().parents` (`main.py:340`); ChromaDB is bound to `127.0.0.1` in compose; background tasks use `utils.background.spawn`; `create_task` forces plan review for non-interactive callers (`tools.py:2803-2804`).
