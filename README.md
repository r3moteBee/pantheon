# Pantheon

A **single-user, self-hosted agent harness** with persistent multi-tier memory, a source-adapter ingestion pipeline, autonomous scheduled tasks, and a knowledge-graph substrate for long-running research workflows.

You run Pantheon locally (or on your own box), point it at a few MCP servers and an LLM endpoint, and it accumulates context over time — daily ingests, schedules, graph queries — so the agent can pick up where you left off across sessions.

## What's in the box

- **Five-tier memory** — working (in-process), episodic (SQLite chat history), semantic (ChromaDB embeddings), graph (typed nodes + edges in SQLite), archival (reserved for whole documents). All tiers queried in a single `recall()` call.
- **Source adapters** — 28 plug-in adapters across 9 mechanisms (youtube, blog, pdf, web, forum, podcast, github, CFR, MA Legislature) that turn a URL or video ID into a typed-topics artifact + graph nodes/edges in one call.
- **Knowledge graph** — typed nodes (concept, technology, person, organization, market…) + relationships (DISCUSSES, PRODUCES, FEATURES_SPEAKER, SEMANTICALLY_SIMILAR_TO). Cross-artifact similarity surfaces mergeable duplicates for review.
- **Artifacts store** — versioned, indexed, searchable. Markdown / HTML / PDF / images / Office docs all preview in-browser; SVG + Mermaid diagrams export to SVG / PNG / PDF.
- **Jobs system** — unified async worker. Job types: autonomous task, scheduled job, coding task, extraction, file indexing, image extraction, iteration loop. APScheduler-driven; stall watchdog catches stuck jobs.
- **Skills** — `slug + instructions.md` recipes the agent reads as prompt context. Invoked via `/skill-name` in chat, or bound to scheduled tasks. Auto-discovery is keyword-scored, not embedding-based (deterministic + debuggable).
- **MCP** — full **MCP 2025-11-25** client over Streamable HTTP. Supports static API-key auth **and** OAuth 2.1 with Protected Resource Metadata (RFC 9728), Dynamic Client Registration (RFC 7591), PKCE, OIDC discovery fallback, and structured tool outputs.
- **LLM flexibility** — named endpoints + role mapping (chat / prefill / vision / embed / rerank). Mix OpenAI, Anthropic, Ollama, or any OpenAI-compatible API per role.
- **Web UI** — chat, memory browser, artifact tree, personality editor, scheduled tasks, MCP connections, settings — all served from FastAPI; no separate frontend server in normal use.
- **Encrypted secrets vault** — Fernet + PBKDF2; per-key entries for LLM keys, MCP API keys, OAuth tokens, integrations.

## System requirements

**Local mode (default — no Docker):**
- Git
- Python 3.11+
- Node.js 18+ and npm
- ~2 GB free disk for the install + accumulated memory

**Docker mode:**
- Docker 24+ with the Compose plugin
- Git

The installer auto-installs Python and Node via your system package manager (Homebrew, apt, dnf, yum, pacman, apk) if they're missing.

## Quick start

One-line installer. Asks whether you want **local mode** (default) or **Docker mode** at the start.

```bash
curl -fsSL https://raw.githubusercontent.com/r3moteBee/pantheon/main/deploy.sh | bash
```

Skip the prompt by passing `--mode`:

```bash
# Local mode
curl -fsSL https://raw.githubusercontent.com/r3moteBee/pantheon/main/deploy.sh | bash -s -- --mode local

# Docker mode
curl -fsSL https://raw.githubusercontent.com/r3moteBee/pantheon/main/deploy.sh | bash -s -- --mode docker
```

The installer:
1. Clones the repo to `~/pantheon` (override with `--dir`)
2. Installs system deps (Python, Node, optional Caddy / LibreOffice / Ollama / SearXNG / Playwright)
3. Creates a Python venv at `~/pantheon/.venv` and installs `backend/requirements.txt`
4. Builds the frontend (`npm run build`)
5. Writes `.env` (asks interactively for LLM endpoint + API key + model choice, agent name, web-UI password)
6. Starts the backend with `./start.sh` and verifies `/api/health`

### Zero-config demo extras

Bundle optional components with the install. Each flag runs `demo_setup.sh` post-install and restarts Pantheon automatically.

| Flag | What it adds |
|---|---|
| `--with-ollama` | Installs Ollama + Nemotron-3-Nano-4B as the default LLM |
| `--with-searxng` | Runs a local SearXNG container as the default web-search backend (needs Docker) |
| `--with-office` | Installs LibreOffice + poppler so Word / Excel / PowerPoint / PDF artifacts render in the preview pane |
| `--with-browser` | Installs Playwright chromium for the agent's browser tools |

Example — full offline-friendly demo:

```bash
curl -fsSL https://raw.githubusercontent.com/r3moteBee/pantheon/main/deploy.sh | \
  bash -s -- --yes --with-ollama --with-searxng --with-office
```

After install, your config lives at `~/pantheon/.env`. Override or extend it later — most LLM / endpoint config is also editable in the web UI under **Settings → LLM Endpoints**.

### Starting and stopping

**Local mode:**

```bash
~/pantheon/start.sh   # boots backend; serves frontend at the same port
~/pantheon/stop.sh    # graceful stop
```

- Web UI + API: `http://localhost:8000`
- API docs: `http://localhost:8000/docs`
- Health check (returns running version): `curl -s http://localhost:8000/api/health`

**Docker mode:**

```bash
cd ~/pantheon
make up         # build + start all services
make down       # stop all services
make logs       # tail logs
make ps         # show running services
```

- Web UI: `http://localhost:8000` (Docker maps `8000:8000` by default)
- API docs: `http://localhost:8000/docs`

To bind Docker mode to port 80 instead, either edit the `backend.ports` mapping in `docker-compose.yml` to `"80:8000"`, or front it with Caddy via the HTTPS section below (recommended for public deployments).

`make` targets are Docker-only — they're no-ops in local mode. Use `start.sh` / `stop.sh` for local.

### First-run setup

The actual "onboarding" is the interactive CLI flow in `deploy.sh`. When run without `--yes`, the installer:

1. Asks whether you want local mode or Docker mode (defaults to Docker)
2. Asks for your LLM endpoint URL and API key
3. Probes the endpoint and lets you pick your **primary** (chat), **prefill**, and **embedding** models from the live model list
4. Asks for an agent name (written into the default persona's `soul.md`)
5. Asks for a web-UI password (`AUTH_PASSWORD` — leave empty to disable auth, fine for local-only use)
6. Optionally installs Caddy if you pass `--domain`

When Pantheon boots, you land directly on the chat page. There's no in-app wizard — every setting (LLM endpoints + role mapping, projects, personas, personality, MCP connections, skills) is editable from the **Settings** page or its dedicated sub-page in the sidebar at any time. The first piece you'll want to confirm is **Settings → LLM Endpoints + Role Mapping** if you skipped the CLI prompts.

### HTTPS with Caddy

For public deployments, the installer can set up [Caddy](https://caddyserver.com) as a reverse proxy with automatic Let's Encrypt certificates.

```bash
curl -fsSL .../deploy.sh | bash -s -- --mode local --domain agent.example.com
```

Prerequisites: your domain's DNS points to the server, and ports 80 + 443 are open.

To add HTTPS to an existing install:

```bash
# Ubuntu / Debian
sudo apt-get install -y caddy
sudo cp ~/pantheon/Caddyfile /etc/caddy/Caddyfile
# Edit /etc/caddy/Caddyfile — replace {$DOMAIN:localhost} with your domain
sudo systemctl enable caddy && sudo systemctl start caddy
```

Caddy auto-renews certificates; no manual cert management.

### Updating after install

```bash
cd ~/pantheon && git pull
~/pantheon/.venv/bin/pip install -r backend/requirements.txt   # if backend deps changed
cd frontend && VITE_API_URL="" npm run build && cd ..          # if frontend changed
./stop.sh && ./start.sh
curl -s http://localhost:8000/api/health    # confirm version bumped
```

For Docker mode, `make down && make build && make up`.

## Architecture

```
                       ┌─────────────────────────┐
                       │  Browser / Agent client │
                       └────────────┬────────────┘
                                    │
                            (optional Caddy
                             for HTTPS only)
                                    │
                       ┌────────────▼────────────┐
                       │  FastAPI (uvicorn)      │
                       │  Serves the React UI    │
                       │  + 19 API routers       │
                       │  + APScheduler          │
                       │  + JobWorker (asyncio)  │
                       └────┬────────────────┬───┘
                            │                │
              ┌─────────────┴──┐    ┌────────┴─────────────┐
              │                │    │                      │
       ┌──────▼──────┐  ┌──────▼─────────┐         ┌───────▼────────┐
       │  Memory     │  │  Source        │         │  MCP servers   │
       │  Manager    │  │  adapters      │         │  (external)    │
       │             │  │  (28 across    │         │  YouTube,      │
       │ • episodic  │  │   9 mechs)     │         │  GitHub,       │
       │   (SQLite)  │  │                │         │  Tavily,       │
       │ • semantic  │  │  ingest URL →  │         │  Notion, …     │
       │   (Chroma)  │  │  typed-topics  │         │                │
       │ • graph     │  │  artifact +    │         │  static API    │
       │   (SQLite)  │  │  graph nodes   │         │  key OR        │
       │ • working   │  └────────────────┘         │  OAuth 2.1     │
       │   (memory)  │                             │  (PKCE+DCR)    │
       │ • archival  │  ┌────────────────┐         └────────────────┘
       └─────────────┘  │  Artifacts     │
                        │  (SQLite +     │
              ┌─────────┤  blob)         │
              │         └────────────────┘
       ┌──────▼─────────────────┐
       │  Encrypted vault       │
       │  (Fernet/PBKDF2 SQLite)│
       │  LLM keys, OAuth       │
       │  tokens, MCP secrets   │
       └────────────────────────┘
```

**Single process.** APScheduler and `JobWorker` run as asyncio tasks inside the FastAPI process. There is no separate queue worker, no Nginx, no message broker. This is intentional — Pantheon is a single-user harness, not a multi-tenant SaaS.

**No "file storage" tier.** Artifacts (the durable, indexed, searchable kind) are SQLite rows with blob storage. Workspace files (ephemeral scratch the agent reads/writes) live on disk under `data/projects/<slug>/`. Both are accessed via different agent tools — don't conflate them.

### Memory tiers (the real ones)

| Tier | Backend | Purpose |
|---|---|---|
| **Working** | in-process | Per-conversation scratch — not persisted |
| **Episodic** | SQLite (`data/db/episodic.db`) | Chat history + task logs, searchable by content + timestamp |
| **Semantic** | ChromaDB (`data/chroma/`) | Embedded chunks from artifacts and workspace files, topic-label embeddings |
| **Graph** | SQLite (`data/db/graph.db`) | Typed nodes + edges; idempotent inserts; powers cross-artifact similarity |
| **Archival** | SQLite (mostly unused) | Reserved for whole-document storage |

`mgr.recall(query, tiers=[…])` searches across them in a single call and returns provenance-tagged hits.

### Source adapters in one paragraph

Drop a file under `backend/sources/adapters/`, subclass `SourceAdapter`, set `source_type` / `bucket_aliases` / `extractor_strategy`, and call `register_adapter(YourClass())`. The pipeline runs fetch → extract topics → save artifact (dedup'd on canonical path) → embed → graph insert → optional cross-artifact similarity. See [`backend/sources/SOURCE_ADAPTERS.md`](backend/sources/SOURCE_ADAPTERS.md) for the full design.

## Configuration

`~/pantheon/.env` is the source of truth for boot-time config. Most LLM settings are also editable in **Settings → LLM Endpoints** in the web UI (and stored in the vault — UI changes win over the .env after first read).

### Core LLM

```env
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-your-key
LLM_MODEL=gpt-4o

# Optional cheaper/faster model used for summarization, prefill, background tasks
LLM_PREFILL_MODEL=gpt-4o-mini

# Optional dedicated vision model (otherwise uses LLM_MODEL if vision-capable)
LLM_VISION_MODEL=

# Embeddings
EMBEDDING_MODEL=text-embedding-3-small
```

### Security

```env
# Generate with: openssl rand -hex 32
VAULT_MASTER_KEY=...
SECRET_KEY=...

# Web UI password. Empty = no auth (don't do this on public servers).
AUTH_PASSWORD=...
```

### CORS / logging

```env
CORS_ORIGINS=http://localhost:8000,https://yourdomain.com
LOG_LEVEL=INFO
```

### LLM endpoints + role mapping (recommended)

Instead of the flat `LLM_*` vars, configure named endpoints in the **Settings** UI:

1. Add an endpoint (name + base URL + API key + provider type)
2. Map each role (chat, prefill, vision, embed, rerank) to an endpoint + model

Legacy `.env` flat-config keys are auto-migrated to the new shape on first read. The migration is idempotent; the flat keys still work for backward compat.

## MCP connections

Pantheon implements **MCP spec 2025-11-25** over Streamable HTTP.

Add a server in **MCP Connections** → choose **API key** auth (paste a token) or **OAuth 2.1** (browser sign-in). For OAuth, Pantheon handles the full flow:

1. Probes for `WWW-Authenticate: Bearer resource_metadata=…`
2. Fetches the Protected Resource Metadata doc
3. Discovers the AS via `/.well-known/oauth-authorization-server` (or OIDC `/.well-known/openid-configuration` as fallback)
4. Registers a public client via Dynamic Client Registration
5. Opens the browser for sign-in with PKCE S256
6. Catches the callback at `http://localhost:8000/api/mcp/oauth/callback`, exchanges code, persists tokens
7. Refreshes tokens on a 60s pre-expiry window; auto-retries once on 401

Connections that publish structured tool outputs (spec 2025-06-18+) come through as both human-readable text and a typed JSON payload the LLM can parse deterministically.

## Adding things

### A new LLM endpoint

Use the **Settings → LLM Endpoints** UI — paste base URL + key, click **Probe** to fetch the model list, then assign the endpoint to whichever roles you want. No code change needed.

### A new agent tool

Tools are JSON Schema entries + a dispatch branch in one file. Edit `backend/agent/tools.py`:

1. Add a schema entry to `TOOL_SCHEMAS`
2. Add an `elif` branch to the dispatch block at the bottom

Bump the version in `frontend/package.json`, restart, and the tool shows up in the agent's tool list.

### A new source adapter

1. Create `backend/sources/adapters/<name>.py`
2. Subclass `SourceAdapter` (or one of the `_*AdapterBase` classes for common patterns), set the class attrs, implement `fetch()`
3. Call `register_adapter(YourClass())` at module import
4. Import the module in `backend/sources/adapters/__init__.py`

See [`backend/sources/SOURCE_ADAPTERS.md`](backend/sources/SOURCE_ADAPTERS.md) for extractor strategies and the full lifecycle.

### A new skill

In chat: ask the agent to create a skill (e.g. *"create a skill that summarizes a YouTube video into a one-page brief"*). The `create_skill` tool scaffolds the manifest + `instructions.md` and registers it. Or hand-author `data/skills/<slug>/skill.json` + `instructions.md`.

## Development

### Backend with hot reload

```bash
cd ~/pantheon/backend
~/pantheon/.venv/bin/uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

### Frontend with Vite

```bash
cd ~/pantheon/frontend
npm run dev      # http://localhost:5173, proxies API calls to :8000
```

### Tests

```bash
cd ~/pantheon/backend
~/pantheon/.venv/bin/python -m pytest tests/integration/ -v
```

Coverage is sparse — expand it as you go. The autonomous-skill-resolution test is the canary for the autonomous-job code path.

### Docker dev commands

Available targets (`make help` for the full list):

```
make up               # start all services
make down             # stop all services
make logs             # tail all logs
make logs-backend     # tail backend only
make restart-backend  # restart backend container
make dev-backend      # backend with hot reload (Docker)
make dev-frontend     # frontend with Vite (Docker)
make shell-backend    # shell inside the backend container
make shell-db         # sqlite3 against episodic.db
make test             # pytest inside backend container
make format / lint    # ruff
make ps               # docker compose ps
make clean            # nuke containers, volumes, images
```

### Versioning

Single source of truth: `frontend/package.json` `"version"` field, format `YYYY.MM.DD.HXX` (e.g. `2026.05.24.H3`). The backend reads it at startup and surfaces it at `GET /api/health`. Bump on every push so the health check confirms the deploy picked up new code.

## Repository layout

```
~/pantheon/
├── backend/
│   ├── main.py                FastAPI app entry — reads version from frontend/package.json
│   ├── config.py              Pydantic v2 Settings; settings.db_dir is canonical
│   ├── db_utils.py            Shared SQLite PRAGMA helper (WAL + synchronous=NORMAL)
│   ├── agent/                 AgentCore + tool dispatch (single tools.py file) + prompts
│   ├── api/                   FastAPI routers (one per resource — 19 routers)
│   ├── sources/               Source-adapter plugin registry + adapters/
│   ├── memory/                Tier implementations + extraction + topic embeddings
│   ├── artifacts/             Versioned artifact store (SQLite + blob)
│   ├── jobs/                  Unified async job system + handlers/
│   ├── skills/                Skill registry + resolver + editor
│   ├── tasks/                 APScheduler integration
│   ├── mcp_client/            MCP client + OAuth module + manager
│   ├── llm_config/            Named endpoints + role mapping
│   ├── models/                ModelProvider + per-role getters
│   ├── secrets/               Fernet-encrypted vault
│   ├── plugins/               Plugin loader (sources + tools)
│   ├── integrations/          External-service integrations
│   ├── data/                  BUNDLED defaults (personas, personality) — in git
│   ├── tests/integration/     pytest tests
│   └── requirements.txt
│
├── frontend/                  Vite + React. package.json's "version" drives backend version
│   ├── src/
│   │   ├── pages/             Top-level page components
│   │   ├── components/        Chat, ChatTabs, Layout, Settings/, chat-tabs/
│   │   ├── api/client.js      Axios wrappers (chat, llmApi, mcpApi, …)
│   │   └── store/index.js     Zustand
│   └── package.json
│
├── data/                      RUNTIME data — NOT in git
│   ├── db/                    SQLite stores + projects.json + vault
│   ├── chroma/                ChromaDB collections
│   ├── projects/              Per-project workspace files
│   ├── skills/                User-installed skills
│   └── personality/           User-overridden soul.md / agent.md
│
├── docs/                      Design docs + usage guides
├── CLAUDE.md                  Working context for AI assistants (read this first if hacking)
├── deploy.sh                  One-command installer
├── demo_setup.sh              Optional component installer (Ollama, SearXNG, etc.)
├── start.sh / stop.sh         Local-mode lifecycle
├── Makefile                   Docker-mode lifecycle
├── docker-compose.yml         Multi-container orchestration
├── Caddyfile                  Reverse proxy + HTTPS config
└── .env.example
```

## Further reading

- [`CLAUDE.md`](CLAUDE.md) — working context for the codebase: design rationale, common dev tasks, gotchas, the "things explicitly not done yet" list
- [`docs/USAGE.md`](docs/USAGE.md) — using the agent effectively: projects, personalities, anaphora, source ingestion, scheduled tasks
- [`backend/README.md`](backend/README.md) — backend layout + API surface + LLM config + memory tiers
- [`backend/sources/SOURCE_ADAPTERS.md`](backend/sources/SOURCE_ADAPTERS.md) — adapter plugin protocol
- [`docs/api/artifacts-feed.md`](docs/api/artifacts-feed.md) — cursor-paginated artifact stream for agent-to-agent / RAG consumers
- [`docs/SECURITY_FEATURES.md`](docs/SECURITY_FEATURES.md) — vault, auth, sandboxing

## License

MIT — see [LICENSE](LICENSE).

## Support

- Issues: GitHub Issues
- Discussions: GitHub Discussions
