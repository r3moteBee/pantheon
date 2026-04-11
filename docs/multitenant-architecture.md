# Pantheon Multitenant Architecture

**Status:** Draft
**Date:** 2026-04-11
**Branch:** `multitenant`

---

## 1. Overview

This document defines the architecture for transforming Pantheon from a single-user agent harness into a multitenant agent server. The design prioritizes tenant isolation, security, modularity, and a clear path to scale while avoiding premature optimization.

---

## 2. Design Principles

- **Tenant isolation by default.** Every tenant's data, execution, and configuration are separated at the infrastructure level, not just by convention.
- **Backend modularity.** Core services (vector DB, file storage, database) are accessed through abstraction layers, allowing backends to be swapped without application code changes.
- **Security-first execution.** Tenant-authored code (skills, scripts) runs in sandboxed environments with explicit capability grants, never on the host directly.
- **Centralized shared services where appropriate.** Embedding, reranking, and the database engine are shared infrastructure. Generative inference is tenant-provided.

---

## 3. Tenancy Model

### 3.1 Database: PostgreSQL + pgvector

PostgreSQL replaces SQLite as the primary data store. pgvector provides vector search capabilities, replacing ChromaDB as the default vector backend.

**Schema-per-tenant isolation:**

Each tenant receives a dedicated Postgres schema (e.g., `tenant_{tenant_id}`). This provides:

- Strong data isolation without separate database instances
- Simple tenant data export via `pg_dump --schema=tenant_xxx`
- Clean tenant deletion via `DROP SCHEMA tenant_xxx CASCADE`
- Per-tenant tables for episodic memory, graph memory, semantic memory (pgvector), conversation history, skills, jobs, schedules, workflows, and configuration

A shared `public` schema holds cross-tenant data: tenant registry, global skills, system configuration, KV store, and admin/audit logs.

### 3.2 Tenant Context

All request handling uses an explicit `TenantContext` object rather than implicit connection state. This is the foundation of safe multitenancy.

**Why not `SET search_path`:** Setting `search_path` on a database connection is unsafe with async connection pooling. If two concurrent requests share a connection (or a connection is returned to the pool with a stale `search_path`), one tenant could read another's data.

**Approach: Explicit schema qualification.**

The API middleware resolves the tenant from the API key and constructs a `TenantContext`:

```python
@dataclass
class TenantContext:
    tenant_id: UUID
    schema_name: str                  # e.g., "tenant_abc123"
    credentials: TenantCredentials    # decrypted LLM/vision/fallback API keys
    feature_flags: dict               # web_browsing_enabled, etc.
```

This object is passed through the request lifecycle via FastAPI dependency injection. The `DatabaseBackend` uses `schema_name` to construct fully-qualified table references in every query:

```sql
-- Never this:
SET search_path TO tenant_abc123;
SELECT * FROM episodic_messages;

-- Always this:
SELECT * FROM tenant_abc123.episodic_messages WHERE ...;
```

Every downstream component — memory manager, skill executor, task runner, inference router — receives the `TenantContext` explicitly. No global state, no implicit connection configuration. This is connection-safe, pooling-safe, and makes tenant boundaries visible in the code.

For scheduled tasks and background jobs, the worker reconstructs the `TenantContext` from the `tenant_id` stored on the job record before executing.

### 3.3 Database Abstraction Layer

All application code accesses the database through a `DatabaseBackend` interface. This maintains modularity and allows future backend swaps.

```
DatabaseBackend (ABC)
  ├── PostgresBackend     (production / multitenant)
  └── SQLiteBackend       (development / single-tenant)
```

Methods accept `tenant_id` (or `TenantContext`) and use it for schema qualification. No raw SQL in business logic — all queries go through the backend interface.

### 3.4 Vector Store Abstraction

Vector operations are accessed through a `VectorStore` interface, decoupled from any specific vector database.

```
VectorStore (ABC)
  ├── PgVectorStore       (default, uses pgvector within Postgres)
  ├── ChromaStore          (legacy / development)
  └── QdrantStore          (future option)
```

Interface methods: `store()`, `search()`, `delete()`, `list()`, `count()`. All methods receive `tenant_id` and `project_id` for scoping. Implementations handle backend-specific details (collection naming, distance metrics, metadata filtering). The application layer passes pre-computed embedding vectors — the vector store does not call embedding models directly.

### 3.5 Postgres KV Store

A general-purpose key-value store built on Postgres, used for transient and semi-persistent state that doesn't fit into the structured memory tiers or configuration tables. This avoids introducing Redis or another external dependency while keeping the architecture simple.

**Table: `public.kv_store`**

```sql
CREATE TABLE public.kv_store (
    namespace   TEXT NOT NULL,       -- scoping: 'working_memory', 'service', 'session', etc.
    key         TEXT NOT NULL,       -- lookup key
    value       JSONB NOT NULL,      -- flexible payload
    tenant_id   UUID,                -- null for system-level entries (e.g., service registry)
    expires_at  TIMESTAMPTZ,         -- null = no expiration; auto-cleanup via periodic job
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (namespace, key)
);

CREATE INDEX idx_kv_tenant ON public.kv_store (tenant_id) WHERE tenant_id IS NOT NULL;
CREATE INDEX idx_kv_expires ON public.kv_store (expires_at) WHERE expires_at IS NOT NULL;
```

**Namespaces define usage:**

| Namespace | Scope | TTL | Purpose |
|-----------|-------|-----|---------|
| `working_memory` | Per-tenant | Conversation lifetime + grace period | Persisted working memory for crash recovery |
| `service` | System | None (manually managed) | Service registry — endpoint resolution for all platform services |
| `session` | Per-tenant | Session lifetime | WebSocket session state, active connection metadata |
| `feature_flags` | System / per-tenant | None | Feature toggles (e.g., `web_browsing_enabled`) |
| `cache` | Per-tenant or system | Short TTL | Ephemeral caches (rate limit counters, recent query results) |

**Cleanup:** A periodic background job (every 5 minutes) deletes rows where `expires_at < now()`. This is a single SQL statement, not a per-row check.

**Access pattern:** All KV operations go through a `KVStore` class that wraps the table:

```python
class KVStore:
    async def get(namespace, key, tenant_id=None) -> dict | None
    async def set(namespace, key, value, tenant_id=None, ttl_seconds=None)
    async def delete(namespace, key, tenant_id=None)
    async def list_keys(namespace, tenant_id=None) -> list[str]
    async def cleanup_expired()
```

This provides a single abstraction for working memory persistence, service discovery, feature flags, and session state — all backed by the Postgres instance already in the stack.

**Note on scoping:** The KV store lives in `public` because some entries are system-level (service registry, system feature flags). Tenant-scoped entries use the `tenant_id` column for isolation, and the `KVStore` class enforces that tenant operations always include `tenant_id`. A tenant can never read another tenant's KV entries.

---

## 4. Inference Architecture

### 4.1 Inference Client Abstraction

Inference is split into two layers that separate "which endpoint" from "make the call."

**InferenceClient** — handles the actual HTTP request to an OpenAI-compatible endpoint. Stateless and tenant-unaware. Takes a base URL, API key, model name, and request payload. Supports streaming (SSE) and non-streaming responses.

```python
class InferenceClient:
    def __init__(self, base_url: str, api_key: str, model: str)
    async def chat(self, messages, tools=None, stream=False) -> Response | AsyncIterator
    async def embed(self, texts: list[str]) -> list[list[float]]
```

**InferenceRouter** — resolves which `InferenceClient` to use based on the `TenantContext` and request type. This is where tenant credentials are decrypted and injected.

```python
class InferenceRouter:
    async def get_client(self, request_type: str, ctx: TenantContext) -> InferenceClient
```

Resolution logic:

| Request type | Endpoint source | Credentials source |
|-------------|----------------|-------------------|
| `chat` | Tenant's LLM endpoint | Tenant's encrypted API key (decrypted from tenant schema) |
| `vision` | Tenant's vision endpoint | Tenant's encrypted API key |
| `fallback` | Tenant's fallback endpoint | Tenant's encrypted API key |
| `embed` | Host Ollama (from service registry) | None (host service, no auth) |
| `rerank` | Host Ollama (from service registry) | None (host service, no auth) |

The agent core, extraction pipeline, and memory manager all call `router.get_client(type, ctx)` and receive a ready-to-use client. They never handle credentials or endpoint resolution directly. Adding new provider types or changing credential handling is contained to the router.

**Credentials are decrypted per-request and never cached in the client object.** The `InferenceRouter` decrypts from the tenant schema, constructs the client, and the client is discarded after the request.

### 4.2 Tenant-Provided Generative Models (BYO)

Each tenant configures their own inference endpoints for:

- **LLM model endpoint** — primary chat/reasoning model
- **Vision model endpoint** — multimodal/vision tasks
- **Fallback model endpoint** — used when primary is unavailable or for lower-priority tasks

All endpoints must be OpenAI API-compatible. Tenant model configuration is stored in the tenant's Postgres schema and managed through the UI. API keys for tenant endpoints are stored encrypted in the tenant schema.

### 4.3 Host-Provided Embedding and Reranking

Embedding and reranking models are centralized shared services, included in the hosting fee.

- **Default provider:** Ollama running on the host (localhost)
- **Configuration:** Hardcoded in the host-level `.env` file (not accessible through the application)
- **Models:** Configurable by the host administrator (e.g., `all-MiniLM-L6-v2` for embeddings, `bge-reranker-base` for reranking)

All tenants share the same embedding model. This ensures consistent vector spaces across the platform and predictable semantic search behavior.

### 4.4 Embedding Versioning

When the host administrator upgrades the embedding model, existing vectors become stale (different vector space). To handle this:

- Every stored vector includes an `embedding_model` metadata field recording which model produced it.
- On model change, a background re-embedding workflow is enqueued per tenant.
- Until re-embedding completes, queries use the old model for tenants with stale vectors.
- The admin dashboard shows re-embedding progress per tenant.

---

## 5. Memory Architecture

### 5.1 Memory Tiers

The existing five-tier memory architecture is preserved, with scoping changes for multitenancy:

| Tier | Scope | Storage |
|------|-------|---------|
| Working Memory | Per-conversation | KV store (`working_memory` namespace) |
| Episodic Memory | Per-project (owned by tenant) | Postgres (tenant schema) |
| Semantic Memory | Per-project (owned by tenant) | pgvector (tenant schema) |
| Graph Memory | Per-project (owned by tenant) | Postgres (tenant schema) |
| Archival Memory | Per-project (owned by tenant) | Postgres (tenant schema) + StorageBackend |

### 5.2 Working Memory

Working memory is scoped per-conversation. Each active conversation maintains its own working memory context. This supports concurrent conversations per tenant without state bleeding.

**Persistence via KV store:**

Working memory is backed by the Postgres KV store (`working_memory` namespace) to survive process restarts. The in-process working memory object is the primary read/write surface for performance — the KV store acts as a write-behind persistence layer.

**Lifecycle:**

1. On conversation start, check KV store for existing working memory (keyed by `{tenant_id}:{conversation_id}`). If found, restore from it — this handles crash recovery and reconnection.
2. During conversation, working memory updates are written to the KV store periodically (e.g., after each assistant turn) and on WebSocket disconnect.
3. On conversation end (explicit close or consolidation), working memory is flushed to the KV store one final time, then the extraction pipeline runs. After extraction completes, the KV entry is deleted.
4. TTL is set to conversation lifetime plus a grace period (e.g., 24 hours). If a conversation is abandoned without explicit close, the cleanup job eventually removes it.

**Concurrency:** Two conversations in the same project operate on independent working memory contexts. Both have full read access to the project's episodic, semantic, and graph memory. Writes to project memory (e.g., during extraction/consolidation) use database-level row locking to prevent conflicts.

### 5.3 Tenant Ownership

All projects belong to a tenant. Memory is per-project, and projects are per-tenant. The tenant owns all data across all their projects. Tenant-level queries (e.g., "search across all my projects") aggregate across the tenant's project-scoped memories within the tenant's schema.

---

## 6. Task System

### 6.1 Terminology

The task system uses a precise vocabulary to avoid ambiguity in code, documentation, and the user-facing UI:

| Construct | Definition | Example |
|-----------|-----------|---------|
| **Job** | A single unit of work — atomic, executes once, has a terminal state (completed/failed/cancelled). Immediate or deferred. | "Index this uploaded file", "Send the weekly summary email Friday at 9am" |
| **Schedule** | A recurring trigger that produces job instances on a defined cadence. | "Every Monday at 9am, run the weekly summary job" |
| **Workflow** | An ordered sequence of steps with state tracking, transitions, and a defined outcome. Each step is a job. | "Research competitors → Draft comparison → Wait for approval → Create presentation" |

**Why these names:**

- **Job** (not "task") — "task" is overloaded across programming (asyncio tasks, celery tasks, UI task lists). "Job" is unambiguous and maps well to execution semantics.
- **Schedule** (not "cron" or "recurring task") — describes what it is (a trigger definition), not how it's implemented.
- **Workflow** (not "plan" or "pipeline") — "plan" conflicts with LLM reasoning/planning. "Pipeline" implies data flow. "Workflow" clearly communicates multi-step, stateful, outcome-oriented execution.

### 6.2 Job Types

Jobs are categorized by trigger and timing:

| Type | Trigger | Timing | Examples |
|------|---------|--------|----------|
| **Immediate** | API call, agent action, workflow step | Runs as soon as a worker picks it up | Memory extraction, file indexing, skill execution |
| **Deferred** | User request, agent decision | Runs at a specified future time | "Remind me Friday", "Send this report at 5pm" |
| **Scheduled** | Schedule trigger | Created by a schedule at each cadence tick | Weekly summaries, daily email digests |
| **Reactive** | Event match | Runs when a matching event fires | "When a file is uploaded, index it" |

### 6.3 Storage — Tenant Schema

All job, schedule, and workflow data lives in the **tenant schema**, scoped to the tenant and optionally to a project.

**Table: `{tenant_schema}.jobs`**

```sql
CREATE TABLE {tenant_schema}.jobs (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID,                     -- null = tenant-level job
    workflow_id     UUID REFERENCES {tenant_schema}.workflows(id),  -- null = standalone job
    workflow_step   INT,                      -- step index within workflow (null if standalone)
    job_type        TEXT NOT NULL,            -- 'extraction', 'file_index', 'skill_exec', 'reminder', etc.
    payload         JSONB NOT NULL,           -- job-specific parameters
    status          TEXT NOT NULL DEFAULT 'pending',
                    -- pending | queued | running | completed | failed | cancelled
    priority        INT DEFAULT 0,            -- higher = more urgent
    attempts        INT DEFAULT 0,
    max_attempts    INT DEFAULT 3,
    result          JSONB,                    -- output on success
    error           TEXT,                     -- error message on failure
    scheduled_for   TIMESTAMPTZ,             -- null = immediate; set = deferred
    created_at      TIMESTAMPTZ DEFAULT now(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

CREATE INDEX idx_jobs_status ON {tenant_schema}.jobs (status, priority DESC, created_at)
    WHERE status IN ('pending', 'queued');
CREATE INDEX idx_jobs_workflow ON {tenant_schema}.jobs (workflow_id) WHERE workflow_id IS NOT NULL;
CREATE INDEX idx_jobs_project ON {tenant_schema}.jobs (project_id) WHERE project_id IS NOT NULL;
CREATE INDEX idx_jobs_scheduled ON {tenant_schema}.jobs (scheduled_for)
    WHERE scheduled_for IS NOT NULL AND status = 'pending';
```

**Table: `{tenant_schema}.schedules`**

```sql
CREATE TABLE {tenant_schema}.schedules (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID,                     -- null = tenant-level schedule
    name            TEXT NOT NULL,
    description     TEXT,
    schedule_expr   TEXT NOT NULL,            -- cron expression (5-field) or interval ('every:30m')
    job_type        TEXT NOT NULL,            -- job type to create on each trigger
    job_payload     JSONB NOT NULL,           -- template payload for created jobs
    enabled         BOOLEAN DEFAULT true,
    last_run_at     TIMESTAMPTZ,
    next_run_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ DEFAULT now(),
    updated_at      TIMESTAMPTZ DEFAULT now()
);
```

**Table: `{tenant_schema}.workflows`**

```sql
CREATE TABLE {tenant_schema}.workflows (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID,                     -- null = tenant-level workflow
    name            TEXT NOT NULL,
    description     TEXT,
    status          TEXT NOT NULL DEFAULT 'pending',
                    -- pending | running | paused | completed | failed | cancelled
    current_step    INT DEFAULT 0,
    definition      JSONB NOT NULL,           -- ordered step definitions (see below)
    context         JSONB DEFAULT '{}',       -- accumulated state passed between steps
    created_at      TIMESTAMPTZ DEFAULT now(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);
```

**Table: `{tenant_schema}.triggers`**

```sql
CREATE TABLE {tenant_schema}.triggers (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id      UUID,                     -- null = tenant-level trigger
    name            TEXT NOT NULL,
    event_type      TEXT NOT NULL,            -- 'file_uploaded', 'skill_completed', 'schedule_fired', etc.
    event_filter    JSONB DEFAULT '{}',       -- conditions on event payload (e.g., {"file_type": "pdf"})
    action_type     TEXT NOT NULL,            -- 'enqueue_job' or 'start_workflow'
    action_payload  JSONB NOT NULL,           -- job/workflow definition to create
    enabled         BOOLEAN DEFAULT true,
    created_at      TIMESTAMPTZ DEFAULT now()
);
```

### 6.4 Workflow Definition Format

The `definition` column in the workflows table is a JSONB array of step definitions:

```json
[
    {
        "step": 0,
        "name": "Research competitors",
        "job_type": "agent_task",
        "payload": {"prompt": "Research the top 5 competitors in..."},
        "on_success": "next",
        "on_failure": "fail_workflow"
    },
    {
        "step": 1,
        "name": "Draft comparison document",
        "job_type": "agent_task",
        "payload": {"prompt": "Based on the research, draft a comparison..."},
        "input_from": "previous",
        "on_success": "next",
        "on_failure": "retry"
    },
    {
        "step": 2,
        "name": "Review draft",
        "job_type": "approval",
        "payload": {"message": "Please review the comparison draft before proceeding."},
        "on_approve": "next",
        "on_reject": "goto:1"
    },
    {
        "step": 3,
        "name": "Create presentation",
        "job_type": "skill_exec",
        "payload": {"skill": "create_presentation", "input_from": "step:1"},
        "on_success": "complete",
        "on_failure": "fail_workflow"
    }
]
```

**Step transitions:**

- `"next"` — advance to step N+1
- `"complete"` — mark workflow as completed
- `"fail_workflow"` — mark workflow as failed
- `"retry"` — re-enqueue the current step's job (up to max_attempts)
- `"goto:N"` — jump to step N (for rejection loops)
- `"pause"` — set workflow status to paused, wait for external resume

**Approval steps:** When the workflow engine encounters a step with `job_type: "approval"`, it pauses the workflow and notifies the tenant (via WebSocket push or stored notification). The tenant approves or rejects through the UI/API, which resumes the workflow at the appropriate transition.

**Context passing:** The `context` field on the workflow accumulates state. Each step's result is merged into context (keyed by step index), and subsequent steps can reference prior results via `"input_from": "previous"` or `"input_from": "step:N"`.

### 6.5 Job Dispatch and Execution

**The dispatch problem:** With jobs living in per-tenant schemas, the worker needs to find pending work across all tenants without polling every schema individually.

**Solution: Postgres LISTEN/NOTIFY + dispatch table.**

A lightweight `public.job_dispatch` table acts as a cross-tenant work queue:

```sql
CREATE TABLE public.job_dispatch (
    id          UUID PRIMARY KEY,             -- matches the job id in the tenant schema
    tenant_id   UUID NOT NULL,
    priority    INT DEFAULT 0,
    created_at  TIMESTAMPTZ DEFAULT now()
);
```

When a job is enqueued in a tenant schema, a row is also inserted into `public.job_dispatch` and a `NOTIFY new_job` is fired. The worker listens on this channel. On receiving a notification (or on periodic poll as fallback), the worker:

1. Claims a job: `DELETE FROM public.job_dispatch WHERE id = (SELECT id FROM public.job_dispatch ORDER BY priority DESC, created_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING id, tenant_id`
2. Reconstructs the `TenantContext` from `tenant_id`.
3. Reads the full job record from `{tenant_schema}.jobs`.
4. Executes the job within the tenant's context (correct schema, correct credentials).
5. Updates the job status in the tenant schema.
6. If the job belongs to a workflow, notifies the workflow engine to evaluate the next step.

This keeps tenant data isolated in tenant schemas while providing efficient cross-tenant work discovery. The dispatch table contains no tenant data — just IDs and priority.

**Scheduled job creation:** A lightweight scheduler loop runs every minute, queries `{tenant_schema}.schedules` across active tenants for schedules where `next_run_at <= now() AND enabled = true`, creates the corresponding job rows, and updates `last_run_at` / `next_run_at`. The list of active tenant schemas is cached from `public.tenants`.

**Reactive job creation:** When an event occurs (file upload, skill completion, etc.), the system checks `{tenant_schema}.triggers` for matching `event_type` and `event_filter`. Matching triggers enqueue their defined job or start their defined workflow.

### 6.6 Task Runner Abstraction

All job dispatch is accessed through a `TaskRunner` interface, maintaining backend modularity:

```python
class TaskRunner(ABC):
    async def enqueue(self, job_type, payload, ctx: TenantContext,
                      project_id=None, priority=0, run_at=None) -> UUID
    async def get_status(self, job_id, ctx: TenantContext) -> JobStatus
    async def cancel(self, job_id, ctx: TenantContext)
    async def start_workflow(self, definition, ctx: TenantContext,
                             project_id=None, name=None) -> UUID
    async def create_schedule(self, schedule_expr, job_type, payload,
                              ctx: TenantContext, project_id=None) -> UUID
    async def create_trigger(self, event_type, event_filter, action_type,
                             action_payload, ctx: TenantContext, project_id=None) -> UUID
```

Initial implementation is `PostgresTaskRunner`. This could be swapped for a Celery or Dramatiq-backed runner in the future without changing call sites.

### 6.7 Project Construct Hierarchy

Within a project, the complete set of constructs is:

```
Project
  ├── Conversations (chat sessions with the agent)
  ├── Memory
  │   ├── Working (per-conversation, KV-persisted)
  │   ├── Episodic (conversation history)
  │   ├── Semantic (vector-searchable knowledge)
  │   ├── Graph (entities and relationships)
  │   └── Archival (long-term storage)
  ├── Files (workspace, uploads, exports)
  ├── Skills (reusable agent capabilities — global, tenant, or project scope)
  ├── Jobs (single-execution work units — immediate or deferred)
  ├── Schedules (recurring job triggers)
  ├── Workflows (multi-step sequences with state and defined outcome)
  └── Triggers (event-driven automation — "when X happens, do Y")
```

**In the UI and documentation:** "Ask the agent to do something simple — it's a job. Ask it to do something on a repeating schedule — that's a schedule. Ask it to do something complex with multiple steps and a defined outcome — that's a workflow. Set up an automation that reacts to events — that's a trigger."

---

## 7. Skills System

### 7.1 Storage

Skills move from the filesystem into the database. Each skill record contains:

- `id` — unique identifier
- `name`, `description` — metadata
- `scope` — one of: `global`, `tenant`, `project`
- `tenant_id` — null for global skills, set for tenant/project skills
- `project_id` — null unless project-scoped
- `skill_md` — the SKILL.md content (instructions, frontmatter)
- `scripts` — JSON/blob containing executable scripts
- `assets` — references to asset files (in StorageBackend or DB)
- `version` — integer, incremented on edit
- `created_at`, `updated_at` — timestamps

**Storage location:** Global skills are in the `public` schema. Tenant and project skills are in the tenant's schema.

### 7.2 Scope and Visibility

- **Global skills** — maintained by the platform administrator. Available to all tenants. Stored in the `public` schema.
- **Tenant skills** — created by the tenant. Available across all of that tenant's projects. Stored in the tenant's schema.
- **Project skills** — scoped to a single project within a tenant. Stored in the tenant's schema with a `project_id`.

Resolution order: project skills > tenant skills > global skills (most specific wins on name conflict).

### 7.3 Sandboxed Execution with Firecracker

Skill scripts execute inside Firecracker microVMs, providing strong isolation from the host and other tenants.

**Execution flow:**

1. Skill is triggered (by agent, schedule, workflow step, or user action).
2. Script and dependencies are extracted from the database.
3. A Firecracker microVM is provisioned with a pre-built base image (Python + common libraries).
4. Script is injected into the microVM.
5. Execution runs with defined resource limits (CPU time, memory, wall clock timeout).
6. Results (stdout, structured output, files) are captured.
7. MicroVM is destroyed.

Skill execution is tracked as a job in the tenant's `jobs` table, providing consistent status tracking and retry behavior.

**Capability grants:**

Skills are not restricted by default in what they can *do* inside the microVM, but their access to external resources is controlled:

- **Network access:** Allowed. Skills can make HTTP requests, call APIs, etc.
- **Tenant file access:** Read/write within the tenant's agent workspace and project workspaces, mediated through a mounted volume or API proxy.
- **Cross-tenant access:** Denied. No visibility into other tenants' data.
- **Host access:** Denied. The microVM has no access to the host filesystem, process space, or network services beyond what is explicitly exposed.

**Base images:**

Pre-built Firecracker root filesystem images with common runtimes:
- `python-base` — Python 3.x + common data/AI libraries
- `node-base` — Node.js + common packages
- Additional images can be added by the platform administrator

---

## 8. Web Browsing Service

### 8.1 Overview

Agent web browsing is provided as a shared platform service using Playwright + Chromium. This is a toggleable feature — enabled or disabled per tenant via settings — to support future metered billing.

### 8.2 Architecture

Web browsing runs as a dedicated service, separate from both the Pantheon API and the Firecracker skill execution environment.

```
Shared Services:
  ├── Ollama              (embedding / reranking)
  ├── Browser Service     (Playwright + Chromium pool)
  └── Firecracker Pool    (skill execution)
```

**Why not inside Firecracker microVMs:**

- Chromium is heavyweight (~400MB+). Bundling it into every microVM base image would bloat images and slow boot times.
- Browser sessions are longer-lived than skill script executions. A skill runs and exits; a browsing session involves multiple navigations and waits.
- Memory footprint differs significantly — Chromium needs substantially more RAM than a typical skill script.

### 8.3 Implementation

The browser service exposes an internal API that the Pantheon agent can call during conversations or skill execution:

- `browse(url)` — navigate to a URL, return page content
- `screenshot(url)` — capture a rendered screenshot
- `extract(url, selector)` — extract specific content from a page
- `interact(url, actions)` — fill forms, click elements, multi-step navigation

Each request receives an isolated Playwright browser context (`browser.newContext()`), providing cookie, storage, and session isolation between requests and between tenants.

**Connection pooling:** A pool of Chromium browser instances is maintained by the service. Contexts are created on-demand within the pool and destroyed after each request completes. The pool size is configurable by the host administrator based on available resources.

### 8.4 Tenant Feature Toggle

Web browsing is a gated capability controlled per tenant:

- **Tenant setting:** `web_browsing_enabled` (boolean, default: `false`)
- **Admin control:** Host administrator can enable/disable per tenant via the management console
- **Self-service (optional):** Tenants can enable it in their settings if allowed by the admin policy
- **API enforcement:** The middleware checks the tenant's feature flag before routing any browse request. Disabled tenants receive a `403 Feature not enabled` response.

This toggle supports future metered billing — usage (page loads, screenshots, interaction steps) is logged to the audit table per tenant, providing the data needed for usage-based pricing.

### 8.5 Security

- **URL allowlist/denylist:** Block requests to `localhost`, private IP ranges (`10.x`, `172.16.x`, `192.168.x`), and internal service endpoints to prevent SSRF attacks against host infrastructure.
- **Tenant isolation:** Separate browser contexts per request. No shared cookies, storage, or session state between tenants.
- **Request logging:** All outbound URLs are logged per tenant for audit.
- **Resource limits:** Per-request timeout (configurable, e.g., 30 seconds). Maximum concurrent browser contexts per tenant to prevent resource exhaustion.
- **Content restrictions:** Optional content filtering policy configurable by the host administrator.

### 8.6 Integration with Skills

Skills executing in Firecracker microVMs can request web browsing through an API proxy exposed to the microVM. The skill does not run Chromium directly — it calls the browser service, which enforces the tenant's feature toggle and security policies. This keeps skill execution lightweight while still enabling web-capable workflows.

---

## 9. File Storage

### 9.1 Storage Backend Abstraction

File storage is accessed through a `StorageBackend` interface, allowing the underlying storage to be swapped without application changes.

```
StorageBackend (ABC)
  ├── LocalFilesystemBackend   (initial implementation)
  └── ObjectStorageBackend     (future — Oracle Object Storage / S3-compatible)
```

Interface methods: `read()`, `write()`, `delete()`, `list()`, `exists()`, `get_url()`. All methods require `tenant_id` for scoping.

### 9.2 Initial Implementation: Local Filesystem

Per-tenant directories on the host:

```
/data/tenants/{tenant_id}/
  ├── projects/
  │   ├── {project_id}/
  │   │   ├── workspace/
  │   │   ├── notes/
  │   │   └── exports/
  ├── uploads/
  └── archival/
```

Files are never written by constructing paths from user input. All path resolution goes through the `StorageBackend`, which validates tenant scoping and prevents traversal.

### 9.3 Future: Object Storage

The `ObjectStorageBackend` implementation would use Oracle Object Storage (S3-compatible API). Key mapping:

```
Bucket: pantheon-tenant-data
Key:    {tenant_id}/projects/{project_id}/workspace/file.txt
```

Migration from local filesystem to object storage would be a data migration + config change, with no application code changes required.

---

## 10. Service Registry

### 10.1 Overview

All platform services are registered in the KV store (`service` namespace). Components resolve service endpoints through the registry rather than hardcoding hostnames or ports. This supports both single-host deployment (where everything is `localhost`) and future containerized/distributed deployment (where services have container hostnames or external URLs).

### 10.2 Registry Entries

Each service registers its connection details as a JSONB value:

| Key | Value (example — single host) | Value (example — containerized) |
|-----|-------------------------------|-------------------------------|
| `service:postgres` | `{"host": "localhost", "port": 5432, "database": "pantheon"}` | `{"host": "pantheon-db", "port": 5432, "database": "pantheon"}` |
| `service:ollama` | `{"host": "localhost", "port": 11434}` | `{"host": "ollama", "port": 11434}` |
| `service:browser` | `{"host": "localhost", "port": 9222, "enabled": true}` | `{"host": "browser-service", "port": 9222, "enabled": true}` |
| `service:firecracker` | `{"socket_path": "/tmp/firecracker.sock", "pool_size": 4}` | `{"host": "firecracker-mgr", "port": 8080, "pool_size": 4}` |

### 10.3 Initialization

On startup, the Pantheon API reads service endpoints from environment variables and writes them to the KV store. This is the single point where env vars are translated into runtime configuration — all other code reads from the registry.

### 10.4 Runtime Resolution

Components that need a service endpoint call the registry:

```python
ollama_config = await kv.get("service", "service:ollama")
base_url = f"http://{ollama_config['host']}:{ollama_config['port']}"
```

This means moving from single-host to multi-host deployment is a `.env` change — no code changes, no config file rewrites. The registry also provides a single place to check the health/status of all services from the admin console.

### 10.5 Health Checks

The service registry powers a `/health` endpoint on the Pantheon API:

```json
GET /health
{
  "status": "healthy",
  "services": {
    "postgres": {"status": "connected", "latency_ms": 1},
    "ollama": {"status": "connected", "latency_ms": 3},
    "browser": {"status": "connected", "enabled": true},
    "firecracker": {"status": "available", "pool_active": 2, "pool_max": 4}
  },
  "uptime_seconds": 84321
}
```

Each registered service has a health check function. The `/health` endpoint iterates the registry and probes each one. This serves as both a readiness probe (for future container orchestration) and an admin monitoring tool.

---

## 11. Authentication and API Design

### 11.1 Authentication

Tenants authenticate via **API keys**. This supports:

- Web UI sessions (API key stored in session)
- Mobile app connections
- Desktop app connections
- Programmatic API access

Each tenant can have multiple API keys (e.g., one per device/app). Keys are stored as hashed values in the `public.api_keys` table with a `tenant_id` foreign key.

Future consideration: OAuth2 / OIDC for SSO integrations. The API key model does not preclude adding this later.

### 11.2 Secrets Management

Tenant secrets (LLM API keys, external service credentials) are stored encrypted in the tenant's Postgres schema. The encryption key is derived from a host-level secret in the `.env` file. The current SQLite vault is replaced by this mechanism.

The host `.env` file is not accessible through the application. It contains only host-level configuration:

- Database connection string
- Host encryption key for tenant secrets
- Service endpoints (Ollama, browser service, Firecracker) — loaded into the service registry on startup
- Logging configuration

### 11.3 API Gateway

All tenant requests flow through a shared API server that:

1. Extracts the API key from the request header.
2. Resolves the `tenant_id` from the key (lookup in `public.api_keys`).
3. Constructs a `TenantContext` (schema name, decrypted credentials, feature flags).
4. Injects the `TenantContext` into the request via FastAPI dependency injection.
5. Enforces rate limiting per tenant.
6. Routes to the appropriate handler.

**No `SET search_path` is used.** All database queries use explicit schema qualification via the `TenantContext.schema_name`. See Section 3.2 for details.

This is a middleware layer in the existing FastAPI application, not a separate service.

---

## 12. Administration

### 12.1 Management Console

A web-based admin console for platform administration:

**Tenant management:**
- Create / suspend / delete tenants
- View tenant resource usage (storage, memory records, active conversations, jobs, workflows)
- Export tenant data

**System monitoring:**
- Per-tenant logging (stored in Postgres `public.audit_log` table)
- Service registry health dashboard (live status of all registered services)
- Embedding/reranking service health
- Firecracker microVM pool status
- Job dispatch queue depth
- Database metrics

**Skill management:**
- CRUD for global skills
- Review tenant-created skills (optional moderation)

**Embedding management:**
- Current model info
- Trigger re-embedding workflows
- Monitor re-embedding progress per tenant

### 12.2 Tenant Provisioning Flow

1. Admin creates tenant via management console (or API).
2. System creates Postgres schema `tenant_{id}` with all required tables (memory tiers, skills, jobs, schedules, workflows, triggers, configuration).
3. System creates tenant directory in StorageBackend (`/data/tenants/{id}/`).
4. System initializes tenant feature flags in KV store (e.g., `web_browsing_enabled: false`).
5. System generates initial API key.
6. Admin provides API key to tenant.

No OS-level user accounts, no `su`, no per-user Pantheon installations. All tenants run on the shared Pantheon instance with schema-level isolation.

---

## 13. Logging and Observability

### 13.1 Per-Tenant Logging

All significant events are logged to `public.audit_log` with `tenant_id`:

- Authentication events (login, key usage)
- Memory operations (store, recall, consolidate)
- Skill executions (trigger, success, failure, duration)
- Job and workflow lifecycle events
- Configuration changes
- Errors and exceptions

### 13.2 Admin Visibility

The management console provides:

- Filterable log viewer (by tenant, time range, event type, severity)
- Aggregated metrics dashboard
- Service registry health view
- Job queue monitoring
- Alerting on error rate thresholds

### 13.3 Tenant Visibility

Tenants can view their own logs through the UI — limited to their schema's events. They cannot see other tenants' logs or system-level events.

---

## 14. Deployment Architecture

### 14.1 Target Environment

Oracle Cloud A1 instance (Ampere ARM):
- 4 OCPUs / 24GB RAM (initial; scalable via Oracle A1 Flex)
- Block volume for tenant data
- Tailscale for inference connectivity to Mac Studio

### 14.2 Services

| Service | Type | Notes |
|---------|------|-------|
| Pantheon API | FastAPI application | Shared instance, all tenants |
| PostgreSQL + pgvector | Database + KV store | Shared, schema-per-tenant + public KV/dispatch |
| Ollama | Embedding/reranking | Localhost, shared |
| Browser Service | Playwright + Chromium | Shared pool, toggleable per tenant |
| Firecracker | Skill execution | MicroVM pool, on-demand |
| Nginx (optional) | Reverse proxy / TLS | Frontend routing |

### 14.3 Containerization

Containerization is deferred for the initial implementation. The architecture is container-ready: all service endpoints are resolved through the KV-backed service registry, configuration is externalized via env vars, tenant context uses explicit schema qualification (no connection state dependency), and there is no filesystem coupling in business logic. Moving to containers requires only changing the service endpoint env vars — no application code changes.

---

## 15. Migration and Compatibility

This is a new installation, not a migration from the existing single-tenant deployment. The single-tenant codebase on `main` remains unchanged.

The `multitenant` branch diverges architecturally but preserves the core agent logic (memory tiers, conversation flow, extraction pipeline, skill execution model). Code that is backend-agnostic (LLM integration, prompt construction, memory consolidation) should be shared across branches where possible.

---

## 16. Summary of Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database | PostgreSQL + pgvector | Consolidates relational + vector storage; mature multitenancy |
| Tenant isolation | Schema-per-tenant | Clean export/delete, strong isolation, simpler than DB-per-tenant |
| Tenant context | Explicit `TenantContext` + schema-qualified queries | Connection-safe; no `SET search_path` race conditions |
| Vector store | Abstracted; pgvector default | Modularity; pgvector avoids a separate service |
| KV store | Postgres-backed (`public.kv_store`) | Working memory persistence, service registry, feature flags — no Redis dependency |
| Service discovery | KV-backed service registry | Container-ready endpoint resolution; single env-var-to-runtime translation point |
| Inference | `InferenceClient` + `InferenceRouter` abstraction | Separates endpoint resolution from API calls; clean credential handling |
| Generative inference | Tenant BYO (OpenAI-compatible) | Lower operational burden; tenant flexibility |
| Embedding/reranking | Host-provided via Ollama | Consistent vector space; included in hosting fee |
| Task system | Jobs, schedules, workflows, triggers in tenant schema | Full lifecycle tracking; tenant-isolated; supports all execution patterns |
| Job dispatch | `public.job_dispatch` + LISTEN/NOTIFY | Efficient cross-tenant work discovery without polling every schema |
| Skill storage | Database (global/tenant/project scope) | Security; no filesystem attack surface; supports marketplace |
| Skill execution | Firecracker microVMs | Strong isolation for untrusted tenant code |
| File storage | Abstracted; local filesystem initially | Defer object storage decision; interface allows future swap |
| Authentication | API keys | Simple; supports mobile/desktop/programmatic access |
| Working memory | Per-conversation, KV-persisted | Crash recovery; survives process restarts |
| Secrets | Encrypted in Postgres (tenant schema) | Replaces SQLite vault; per-tenant isolation |
| Logging | Per-tenant to Postgres | Admin and tenant visibility via UI |
| Web browsing | Shared Playwright/Chromium service, toggleable | Metered capability; separate from Firecracker for performance |
| Containerization | Deferred (architecture is container-ready) | Avoid premature complexity |

---

## 17. Open Questions

- **Object storage migration:** When does latency from object storage become acceptable for project files? Benchmark after initial tenant onboarding.
- **Skill marketplace:** Should tenants be able to publish skills for other tenants to use? Deferred but the DB-backed skill model supports it.
- **Horizontal scaling:** When tenant count exceeds single-instance capacity, how to distribute? Options: read replicas for Postgres, multiple Pantheon API instances behind a load balancer (requires shared job dispatch), dedicated Firecracker hosts.
- **Rate limiting strategy:** Per-tenant limits on API calls, memory operations, skill executions, and storage. Specific thresholds TBD based on usage patterns.
- **Tenant billing integration:** Usage metering and billing system. Out of scope for initial implementation.
- **Encryption key rotation:** Consider envelope encryption (per-tenant data keys wrapped by host master key) to simplify key rotation without re-encrypting every secret.
- **API versioning:** Define versioning strategy (e.g., `/api/v1/`) before mobile/desktop apps are in the field.
- **Tenant data export format:** Define a portable JSON-based export format beyond `pg_dump` for tenant self-service export.
- **Tenant lifecycle states:** Define what `active`, `suspended`, and `deleted` mean across all subsystems (API access, scheduled tasks, WebSocket connections, storage, job execution).
- **Workflow builder UI:** How do tenants create and manage workflows? Visual builder, YAML definition, or natural language ("set up a workflow that...")?
