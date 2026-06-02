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
* **📥 Ingestion Pipeline:** Includes 28 built-in source adapters (YouTube, blogs, PDFs, websites, GitHub, etc.) that fetch URLs and generate structured artifacts and graph nodes.
* **🕸️ Knowledge Graph:** Automatically extracts entities and relationships (e.g. concepts, organizations, authors) to find semantic overlaps and connect related ideas.
* **⚙️ Async Jobs & Skills:** Unified background worker powered by APScheduler supporting autonomous workflows, scheduled tasks, and modular prompt recipes (skills).
* **🔌 MCP & LLM Flexibility:** Compliant Model Context Protocol (MCP) client supporting API keys and OAuth 2.1 authentication. Map different models to distinct roles (chat, prefill, embedding, vision, rerank).
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
* **Endpoints:** Web UI is served at `http://localhost:8000`, API documentation at `http://localhost:8000/docs`.

---

## 🔧 Configuration

Your primary configuration resides in `~/pantheon/.env`. You can also configure named LLM endpoints and map roles directly in the Web UI under **Settings → LLM Endpoints** (which overrides env values).

### Common `.env` Settings:
```env
# Primary LLM Connection
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-your-key-here
LLM_MODEL=gpt-4o

# Optional Fast/Prefill Model (for summarization/background tasks)
LLM_PREFILL_MODEL=gpt-4o-mini

# Embedding Model Configuration
EMBEDDING_MODEL=text-embedding-3-small

# Security & Secrets (Vault keys are auto-generated on install)
VAULT_MASTER_KEY=...
SECRET_KEY=...
AUTH_PASSWORD=your-web-ui-password  # Leave empty to disable authentication
```

---

## 🧩 Customization & Extension

Pantheon is built to be easily customizable:

* **Add an LLM Endpoint:** Go to **Settings → LLM Endpoints** in the Web UI, add a custom provider/base URL, click **Probe** to pull models, and assign them to roles.
* **Add a Custom Tool:** Edit `backend/agent/tools.py` to add a new schema to `TOOL_SCHEMAS` and implement its execution block in the dispatch method.
* **Create a Source Adapter:** Subclass `SourceAdapter` in `backend/sources/adapters/` and import/register it in `backend/sources/adapters/__init__.py`.
* **Add a Custom Skill:** Type `/create-skill` in chat to let the agent scaffold one for you, or manually write a `skill.json` and `instructions.md` inside `data/skills/<slug>/`.

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
npm run dev      # Serves at http://localhost:5173 and proxies API requests
```

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
│   ├── api/               # FastAPI routers (19 endpoints)
│   ├── sources/           # Ingestion pipelines and adapters
│   ├── memory/            # Five-tier memory manager classes
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
* [`backend/README.md`](backend/README.md) — Backend services layout and memory architecture deep dive.
* [`backend/sources/SOURCE_ADAPTERS.md`](backend/sources/SOURCE_ADAPTERS.md) — How to design new ingestion adapters.
* [`docs/SECURITY_FEATURES.md`](docs/SECURITY_FEATURES.md) — Secrets vault and authentication architecture details.

---

## 📄 License

MIT — see [LICENSE](LICENSE).
