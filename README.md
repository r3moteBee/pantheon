# Agent Harness

Agent Harness is a self-hosted, production-ready agentic AI framework with a 5-tier memory system, project isolation, autonomous task scheduling, and a polished web UI.

## Features

- **Self-Hosted**: Run entirely on your own infrastructure with full data privacy
- **Multi-Agent Architecture**: Support for concurrent agents with independent memory and state
- **5-Tier Memory System**: Episodic, Semantic, Procedural, Emotional, and Personality layers
- **Project Isolation**: Each agent runs in a sandboxed project environment
- **Task Scheduling**: Autonomous task scheduling with cron-like expressions
- **Web Dashboard**: Modern, responsive React UI for monitoring and control
- **LLM Flexibility**: Support for OpenAI, Anthropic, Ollama, and custom providers
- **Vector Search**: ChromaDB integration for semantic memory retrieval
- **WebSocket Communication**: Real-time agent status and message streaming
- **Telegram Integration**: Optional Telegram bot for remote agent control

## Quick Start

### 1. Clone and Setup

```bash
git clone https://github.com/r3moteBee/agent-harness.git
cd agent-harness
```

### 2. Copy Environment Configuration

```bash
cp .env.example .env
```

### 3. Configure API Keys

Edit `.env` and add your LLM provider credentials:

```bash
nano .env
```

Required fields:
- `LLM_API_KEY`: Your API key (OpenAI, Anthropic, etc.)
- `LLM_BASE_URL`: Your LLM provider endpoint (or http://ollama:11434/v1 for local)
- `LLM_MODEL`: Model name (gpt-4o, claude-3-sonnet, llama3, etc.)
- `VAULT_MASTER_KEY`: Generate a random 32-character string
- `SECRET_KEY`: Generate another random secret

### 4. Start Services

```bash
make up
```

This command will:
- Build Docker images
- Create necessary data directories
- Start all services (backend, frontend, ChromaDB, Nginx)
- Initialize the database

### 5. Access the Application

- **Web UI**: http://localhost
- **Frontend**: http://localhost:3000
- **Backend API**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│                     Browser / Client                         │
└──────────────────────────┬──────────────────────────────────┘
                           │
                           ▼
              ┌────────────────────────┐
              │   Nginx (Reverse Proxy)│
              │   Port 80 / 443        │
              └──┬──────────────────┬──┘
                 │                  │
         ┌───────▼────────┐  ┌──────▼────────┐
         │  React Frontend │  │  FastAPI      │
         │  Port 3000      │  │  Backend      │
         │  Port 80        │  │  Port 8000    │
         └────────────────┘  └────┬──────┬──┘
                                  │      │
                        ┌─────────┴─┬────┴──────────┐
                        │           │               │
                    ┌───▼──┐  ┌─────▼────┐  ┌──────▼───┐
                    │SQLite│  │ ChromaDB  │  │ File     │
                    │ DB   │  │ Vector DB │  │ Storage  │
                    │      │  │           │  │          │
                    └──────┘  └───────────┘  └──────────┘
```

### Component Details

- **Nginx**: Reverse proxy routing API requests and WebSocket connections
- **FastAPI Backend**: Core application logic, agent orchestration, API endpoints
- **React Frontend**: User interface for project, agent, and task management
- **ChromaDB**: Vector database for semantic memory and retrieval-augmented generation
- **SQLite**: Relational database for episodic memory and application state
- **File Storage**: Persistent storage for project artifacts and knowledge bases

## Memory System

Agent Harness implements a comprehensive 5-tier memory architecture:

### 1. Episodic Memory

Stores specific events, interactions, and conversation history tied to timestamps and contexts.

- **Implementation**: SQLite with indexed timestamps
- **Storage Location**: `/data/db/episodic.db`
- **Features**:
  - Full conversation history
  - Context-tagged interactions
  - Time-series queries
  - Automatic retention policies

### 2. Semantic Memory

Stores generalized knowledge, facts, and conceptual understanding extracted from experiences.

- **Implementation**: ChromaDB vector embeddings
- **Features**:
  - Semantic similarity search
  - Knowledge extraction from conversations
  - Multi-modal support
  - Automatic summarization

### 3. Procedural Memory

Stores learned skills, workflows, and execution procedures.

- **Implementation**: Code-based skill repository
- **Storage Location**: `/data/skills/`
- **Features**:
  - Skill definitions in YAML/JSON
  - Executable procedures
  - Parameter validation
  - Skill chaining

### 4. Emotional Memory

Stores sentiment, preferences, and relational context about interactions.

- **Implementation**: SQLite + vector embeddings
- **Features**:
  - Sentiment analysis per interaction
  - User preference tracking
  - Relationship context
  - Tone and communication style adaptation

### 5. Personality Memory

Stores the agent's core identity, values, and behavioral patterns.

- **Implementation**: Markdown personality profiles
- **Storage Location**: `/data/personality/`
- **Features**:
  - Customizable personality templates
  - Behavior guidelines
  - Communication style presets
  - Role-specific configurations

## Configuration Guide

### Core LLM Configuration

```env
# OpenAI (default)
LLM_BASE_URL=https://api.openai.com/v1
LLM_API_KEY=sk-your-key-here
LLM_MODEL=gpt-4o

# Anthropic Claude
LLM_BASE_URL=https://api.anthropic.com/v1
LLM_API_KEY=sk-ant-your-key-here
LLM_MODEL=claude-3-5-sonnet-20241022

# Local Ollama
LLM_BASE_URL=http://ollama:11434/v1
LLM_API_KEY=ollama
LLM_MODEL=llama3
```

### Embedding Configuration

By default, embeddings use your configured LLM provider. To use a different provider:

```env
# OpenAI embeddings
EMBEDDING_MODEL=text-embedding-3-small

# Ollama embeddings
EMBEDDING_MODEL=nomic-embed-text
EMBEDDING_BASE_URL=http://ollama:11434/v1
```

### Security Configuration

```env
# Generate secure keys:
# VAULT_MASTER_KEY: openssl rand -hex 16
# SECRET_KEY: python -c "import secrets; print(secrets.token_hex(32))"

VAULT_MASTER_KEY=your-32-char-hex-string
SECRET_KEY=your-64-char-hex-string
APP_ENV=production
```

### CORS Configuration

```env
# Allow specific origins
CORS_ORIGINS=http://localhost:3000,http://localhost:80,https://yourdomain.com
```

### Logging Configuration

```env
LOG_LEVEL=INFO  # DEBUG, INFO, WARNING, ERROR, CRITICAL
```

### ChromaDB Configuration

```env
# Docker network communication (default for containerized setup)
CHROMA_HOST=chromadb
CHROMA_PORT=8001

# For direct host communication
# CHROMA_HOST=localhost
# CHROMA_PORT=8001
```

## How to Add a New LLM Provider

### Step 1: Create Provider Adapter

Create a new file in `backend/llm_providers/`:

```python
# backend/llm_providers/custom_provider.py

from typing import AsyncIterator, Optional
from backend.llm_providers.base import BaseLLMProvider

class CustomProvider(BaseLLMProvider):
    """Adapter for Custom LLM Provider"""

    def __init__(self, api_key: str, base_url: str, model: str):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    async def generate(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> str:
        """Generate a single completion"""
        # Implement provider-specific API call
        pass

    async def stream(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
    ) -> AsyncIterator[str]:
        """Stream tokens as they arrive"""
        # Implement streaming logic
        pass

    async def embed(self, text: str) -> list[float]:
        """Generate embeddings"""
        # Implement embedding logic
        pass
```

### Step 2: Register Provider

Update `backend/llm_providers/__init__.py`:

```python
from backend.llm_providers.custom_provider import CustomProvider

PROVIDERS = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "custom": CustomProvider,  # Add your provider
    "ollama": OllamaProvider,
}
```

### Step 3: Update Configuration

Add environment variables for your provider:

```env
# In .env
LLM_BASE_URL=https://api.customprovider.com/v1
LLM_API_KEY=your-api-key
LLM_MODEL=custom-model-name
```

### Step 4: Test Integration

```bash
# Backend shell
make shell-backend

# Test the provider
python
>>> from backend.llm import get_llm_provider
>>> provider = get_llm_provider()
>>> response = await provider.generate([{"role": "user", "content": "Hello"}])
>>> print(response)
```

## How to Add a New Tool

### Step 1: Define Tool Specification

Create a tool definition in `backend/tools/`:

```python
# backend/tools/my_tool.py

from typing import Any, Dict
from backend.tools.base import BaseTool, ToolParameter

class MyTool(BaseTool):
    """Description of what your tool does"""

    name = "my_tool"
    description = "Detailed description of tool functionality"

    parameters = [
        ToolParameter(
            name="input_text",
            type="string",
            description="Text input for the tool",
            required=True,
        ),
        ToolParameter(
            name="option",
            type="string",
            description="Optional parameter",
            required=False,
            enum=["option1", "option2"],
        ),
    ]

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """Execute the tool with given parameters"""
        input_text = kwargs.get("input_text")
        option = kwargs.get("option", "option1")

        # Implement tool logic
        result = f"Processed: {input_text} with {option}"

        return {
            "status": "success",
            "result": result,
            "metadata": {
                "tokens_used": 42,
                "execution_time_ms": 123,
            },
        }
```

### Step 2: Register Tool

Update `backend/tools/__init__.py`:

```python
from backend.tools.my_tool import MyTool

AVAILABLE_TOOLS = {
    "web_search": WebSearchTool,
    "file_read": FileReadTool,
    "my_tool": MyTool,  # Add your tool
}
```

### Step 3: Add Tool to Agent

In agent configuration or via API:

```python
agent = Agent(
    name="my-agent",
    tools=["web_search", "file_read", "my_tool"],  # Include your tool
)
```

### Step 4: Use Tool in Prompts

Tools are automatically available to the agent. The LLM will call them when appropriate:

```
Agent: "I'll use my_tool to help you with that task."
Agent calls: my_tool(input_text="sample", option="option1")
Agent: "Here are the results..."
```

## Telegram Setup

### Prerequisites

- Telegram bot token (create via @BotFather)
- Chat IDs where the bot is authorized (create test group)

### Configuration

1. Create your Telegram bot:
   - Chat with @BotFather on Telegram
   - Use `/newbot` command
   - Follow prompts to create a bot
   - Copy the bot token

2. Update `.env`:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklMNOpqrsTUVwxyzABCDEfg
TELEGRAM_ALLOWED_CHAT_IDS=123456789,987654321
```

3. Add bot to your chat:
   - Search for your bot in Telegram
   - Click "Start"
   - Get your chat ID using `/start` in the chat
   - Add chat ID to `TELEGRAM_ALLOWED_CHAT_IDS`

4. Test the connection:

```bash
make logs-backend | grep -i telegram
```

### Telegram Commands

Once configured, use these commands in any authorized chat:

```
/status        - Get agent status and stats
/list          - List all active agents
/project <id>  - Get project details
/tasks         - Show pending tasks
/stop <id>     - Stop a running agent
```

## Development

### Backend Development

Start backend with hot reload:

```bash
make dev-backend
```

This runs FastAPI with auto-reload on code changes.

### Frontend Development

Start frontend with Vite dev server:

```bash
make dev-frontend
```

Frontend will auto-reload on file changes.

### Running Tests

```bash
make test
```

### Code Quality

Format code:
```bash
make format
```

Run linter:
```bash
make lint
```

## Project Structure

```
agent-harness/
├── backend/
│   ├── main.py                 # FastAPI application entry point
│   ├── requirements.txt         # Python dependencies
│   ├── Dockerfile              # Backend container definition
│   ├── llm_providers/           # LLM provider implementations
│   │   ├── base.py
│   │   ├── openai.py
│   │   ├── anthropic.py
│   │   └── ollama.py
│   ├── tools/                  # Executable tools for agents
│   │   ├── base.py
│   │   ├── web_search.py
│   │   └── file_operations.py
│   ├── models/                 # Data models and schemas
│   │   ├── agent.py
│   │   ├── project.py
│   │   └── memory.py
│   ├── memory/                 # 5-tier memory system
│   │   ├── episodic.py
│   │   ├── semantic.py
│   │   ├── procedural.py
│   │   ├── emotional.py
│   │   └── personality.py
│   ├── api/                    # API route handlers
│   │   ├── agents.py
│   │   ├── projects.py
│   │   ├── tasks.py
│   │   ├── memory.py
│   │   └── websocket.py
│   ├── services/               # Business logic
│   │   ├── agent_service.py
│   │   ├── task_scheduler.py
│   │   └── vector_store.py
│   ├── data/                   # Template and configuration files
│   │   └── personality/        # Personality templates
│   │       └── soul.md
│   └── tests/                  # Test suite
│       ├── test_agents.py
│       └── test_memory.py
│
├── frontend/
│   ├── src/
│   │   ├── App.jsx             # Main React component
│   │   ├── main.jsx            # Vite entry point
│   │   ├── api/                # API client functions
│   │   │   └── client.js
│   │   ├── components/         # React components
│   │   │   ├── AgentDashboard.jsx
│   │   │   ├── ProjectManager.jsx
│   │   │   └── MemoryExplorer.jsx
│   │   ├── pages/              # Page components
│   │   │   ├── Dashboard.jsx
│   │   │   ├── Projects.jsx
│   │   │   └── Settings.jsx
│   │   ├── hooks/              # Custom React hooks
│   │   └── styles/             # CSS styles
│   ├── package.json
│   ├── vite.config.js
│   └── Dockerfile
│
├── nginx/
│   └── nginx.conf              # Nginx reverse proxy config
│
├── data/                        # Persistent data (gitignored)
│   ├── db/
│   │   └── episodic.db         # SQLite database
│   ├── chroma/                 # ChromaDB vector store
│   ├── personality/            # Agent personality files
│   └── projects/               # Agent project directories
│
├── docker-compose.yml          # Multi-container orchestration
├── .env.example                # Environment template
├── .gitignore                  # Git ignore rules
├── Makefile                    # Development commands
└── README.md                   # This file
```

## License

MIT License - See LICENSE file for details

## Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Make your changes and test thoroughly
4. Commit with clear messages (`git commit -m 'Add amazing feature'`)
5. Push to your branch (`git push origin feature/amazing-feature`)
6. Open a Pull Request

## Support

- Issues: GitHub Issues
- Discussions: GitHub Discussions
- Documentation: https://agent-harness.readthedocs.io

## Roadmap

- [x] Multi-agent orchestration
- [x] 5-tier memory system
- [x] Web UI dashboard
- [ ] GPU-accelerated embeddings
- [ ] Multi-modal input (images, audio)
- [ ] Advanced memory consolidation
- [ ] Distributed agent deployment
- [ ] Custom fine-tuning pipeline
