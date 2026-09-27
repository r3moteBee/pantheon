# Pantheon

A **single-user, self-hosted AI agent harness** designed for long-running research and analysis workflows. It features a persistent five-tier memory system, an automated source-adapter ingestion pipeline, background job execution, and a knowledge-graph substrate.

You run Pantheon locally, connect your LLMs and MCP servers, and it accumulates context over time—handling daily ingestion feeds, scheduled workflows, and semantic indexing so the agent can resume right where you left off.

---

## 🚀 Quick Start

Run the unified installer to get started. The script will automatically detect your OS, check requirements, and prompt you to choose between **Local** and **Docker** mode:

```bash
curl -fsSL https://raw.githubusercontent.com/r3moteBee/pantheon/main/deploy.sh | bash
```

### Handy Installer Flags:
* **Force Local Mode:** `curl -fsSL .../deploy.sh | bash -s -- --mode local` (Runs on Python + Node on host)
* **Force Docker Mode:** `curl -fsSL .../deploy.sh | bash -s -- --mode docker` (Runs isolated containers)
* **Offline Demo Setup:** Add `--with-ollama --with-searxng` to automatically deploy a local LLM (Qwen 2.5) and private search backend.
* **Skip Prompts:** Add `--yes` or `-y` to run non-interactively using defaults.

---

## ✨ Core Features

* **🧠 Five-Tier Memory:** Combines working (in-conversation), episodic (SQLite chat logs), semantic (ChromaDB embeddings), graph (concepts and relationships in SQLite), and archival memory in a single `recall()` query.
* **📥 Ingestion Pipeline:** 29 built-in source adapters across 10 mechanisms (YouTube, blog, PDF, web, forum, podcast, GitHub, eCFR, MA Legislature, SEC EDGAR) that turn a URL or ID into a structured artifact plus graph nodes. The agent's `ingest_source` tool keeps a source; `web_fetch` just reads a page without saving it.
* **🕸️ Knowledge Graph:** Automatically extracts entities and relationships (e.g. concepts, organizations, authors) to find semantic overlaps and connect related ideas.
* **⚙️ Async Jobs & Skills:** Unified background worker powered by APScheduler supporting autonomous workflows, scheduled tasks, and modular prompt recipes (skills).
* **🔌 MCP & LLM Flexibility:** Model Context Protocol (MCP) client with API-key or OAuth 2.1 auth. LLMs are configured as named endpoints plus per-task-class routes (`agent`, `code`, `quick`, `long_context`, `extract`, `summarize`, `vision`, `image_gen`, `embed`, `rerank`) with ordered fallbacks, and an optional per-turn chat router (`/model <class|auto>` pins a class).
* **🔒 Encrypted Vault:** Securely manages sensitive credentials, API keys, and OAuth tokens using Fernet encryption.
* **🖥️ Web UI:** Integrated responsive dashboard for chatting, viewing artifacts, exploring the knowledge graph, configuring MCP connections, and tuning model parameters.

---

## ⚙️ Running Pantheon

After running the installer, manage your services using the commands below depending on your selected mode:

### Local Mode (Direct Host)
Runs directly on your machine.
* **Requirements:** Git, Python 3.11+, Node.js 18+
* **Commands:**
  ```bash
  ~/pantheon/start.sh    # Start backend and serve frontend static files
  ~/pantheon/stop.sh     # Stop all background processes
  ```
* **Endpoints:** Web UI + API runs at `http://localhost:8000`, API documentation is at `http://localhost:8000/docs`.

### Docker Mode (Containers)
Runs all components in isolated Docker containers.
* **Requirements:** Docker 24+ and the Compose plugin
* **Commands:**
  ```bash
  cd ~/pantheon
  make up         # Build and start the container stack
  make down       # Stop and remove containers
  make logs       # Follow logs from all services
  make ps         # List running containers
  ```
* **Endpoints:** nginx serves the Web UI at `http://localhost` (port 80) and proxies `/api`, `/ws` and `/docs` to the backend; the backend's own port 8000 is bound to 127.0.0.1 only.

---

## 🔧 Configuration

Your primary configuration resides in `~/pantheon/.env`. LLMs are best configured in the Web UI under **Settings → LLMs**: add named endpoints, then route models to task classes. The `LLM_*` / `EMBEDDING_MODEL` env values are only the fallback used when no route is configured for the agent / embed class.

### Common `.env` Settings:
```env
# Fallback LLM connection (used until routes are set in Settings → LLMs)
LLM_BASE_URL=insert-llm-base-url-here
LLM_API_KEY=insert-llm-api-key-here
LLM_MODEL=insert-llm-model-name-here
EMBEDDING_MODEL=insert-embedding-model-here

# Security & Secrets (Vault keys are auto-generated on install)
VAULT_MASTER_KEY=...
SECRET_KEY=...
AUTH_PASSWORD=insert-auth-password-here  # Empty = no login; then only IP, localhost and private-TLD Host names (or ALLOWED_HOSTS) are served
```

Logging in sets an HttpOnly `pantheon_session` cookie (expires after `AUTH_SESSION_DAYS`, default 30); scripts can send `Authorization: Bearer <token>` instead. If `VAULT_MASTER_KEY`, `SECRET_KEY` or `AUTH_PASSWORD` are left at their placeholder values, Pantheon only answers loopback clients.

---

## 🧩 Customization & Extension

Pantheon is built to be easily customizable:

* **Add an LLM Endpoint:** In **Settings → LLMs**, add an endpoint (OpenAI-compatible, Anthropic, Ollama or custom), click **Probe** to pull its models, then add them to task-class routes under **Model routing**.
* **Add a Custom Tool:** Edit `backend/agent/tools.py` to add a new schema to `TOOL_SCHEMAS` and implement its execution block in the dispatch method.
* **Create a Source Adapter:** Subclass `SourceAdapter` in `backend/sources/adapters/` and import/register it in `backend/sources/adapters/__init__.py`.
* **Add a Custom Skill:** Ask the agent to create one (it uses the `create_skill` tool), use **New** on the Skills page, or write `skill.json` + `instructions.md` under `data/skills/<slug>/`. Invoke a skill with `/<slug>` in chat.

---

## 🛠️ Development

If you are modifying Pantheon, use these commands to spin up development configurations:

### Run Dev Servers Locally
```bash
# Start Backend with Hot-Reload
cd ~/pantheon/backend
../.venv/bin/uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Start Frontend Vite Server
cd ~/pantheon/frontend
npm ci
npm run dev      # Serves at http://localhost:5173 and proxies /api + /ws to :8000
```

### Rebuild after pulling changes (local mode)
See the "Deploy / build / test workflow" section of [`CLAUDE.md`](CLAUDE.md). In short: `npm ci && VITE_API_URL="" npm run build` in `frontend/`, restart with `./stop.sh && ./start.sh`, then `curl -s http://localhost:8000/api/health` and check the version changed. The version comes from `"version"` in `frontend/package.json` (`YYYY.MM.DD.HXX`); after bumping it, run `npm install --package-lock-only` and commit the lockfile so `npm ci` keeps working.

### Run Python Tests
```bash
cd ~/pantheon/backend
../.venv/bin/python -m pytest tests/integration/ -v
```

### Handy Docker Development Targets
* `make dev-backend` — Start backend container with hot reload enabled.
* `make test` — Execute pytest suite inside the backend container.
* `make shell-backend` — Open a terminal session inside the backend container.
* `make clean` — Stop containers, delete volumes, and purge build cache.

---

## 📂 Repository Layout

```
~/pantheon/
├── backend/               # FastAPI backend + Agent execution loop
│   ├── agent/             # Core agent logic and tools definitions
│   ├── api/               # FastAPI routers (19 mounted under /api)
│   ├── sources/           # Ingestion pipelines and adapters
│   ├── memory/            # Five-tier memory manager classes
│   ├── jobs/              # Async job store, worker, watchdog, handlers
│   ├── llm_config/        # LLM endpoints, task-class routes, chat router
│   └── requirements.txt   # Python dependency list
├── frontend/              # Vite + React dashboard code
├── data/                  # Runtime storage (SQLite DBs, ChromaDB, workspaces)
├── docs/                  # API and feature documentation
├── deploy.sh              # Unified installer script
├── setup_options.sh       # Component toggle wizard
├── start.sh / stop.sh     # Host runner scripts
└── Makefile               # Docker helper command definitions
```

---

## 📖 Further Reading

* [`CLAUDE.md`](CLAUDE.md) — Hacking guidelines, testing instructions, and codebase constraints.
* [`docs/USAGE.md`](docs/USAGE.md) — User guide for projects, ingestion, memory retrieval, and skills.
* [`backend/README.md`](backend/README.md) — Backend layout, mounted routers and memory tiers.
* [`frontend/README.md`](frontend/README.md) — Frontend dev server, build and structure.
* [`backend/sources/SOURCE_ADAPTERS.md`](backend/sources/SOURCE_ADAPTERS.md) — How to design new ingestion adapters.
* [`docs/SECURITY_FEATURES.md`](docs/SECURITY_FEATURES.md) — Secrets vault and authentication architecture details.

---

## 📄 License

MIT — see [LICENSE](LICENSE).
