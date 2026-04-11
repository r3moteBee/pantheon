# Pantheon Multitenant Implementation Plan

**Date:** 2026-04-11
**Branch:** `multitenant`
**Reference:** [Multitenant Architecture](multitenant-architecture.md)

---

## Phasing Strategy

Each phase produces a deployable, testable system. No phase depends on a future phase. Each phase builds on the previous one and can be validated independently before moving forward.

**Convention:** Each phase lists its deliverables, database tables, API endpoints, and acceptance criteria. A phase is complete when all acceptance criteria pass on the Oracle A1 deployment target.

---

## Phase 1 — Foundation: Database, Auth, Admin, and Tenant Management

**Goal:** A running Pantheon multitenant backend with PostgreSQL, API key auth, tenant CRUD, admin console, and basic system monitoring. No agent functionality yet — this is the platform chassis.

### 1.1 PostgreSQL Setup

- Install PostgreSQL + pgvector extension on Oracle A1.
- Create `pantheon` database.
- Create `public` schema tables:

```
public.tenants          (id, username, display_name, email, status, created_at, updated_at)
public.api_keys         (id, tenant_id, key_hash, name, role, is_active, created_at, last_used_at)
public.kv_store         (namespace, key, value, tenant_id, expires_at, created_at, updated_at)
public.audit_log        (id, tenant_id, event_type, severity, detail, created_at)
public.schema_versions  (tenant_id, schema_version, migrated_at)
public.global_skills    (id, name, description, skill_md, scripts, assets, version, created_at, updated_at)
public.job_dispatch     (id, tenant_id, priority, created_at)
```

- KV store cleanup job (delete expired rows every 5 minutes).
- Implement `ConnectionManager` class wrapping asyncpg pool (`max_connections=10` default from `.env`).

### 1.2 Tenant Schema Template

Define the canonical tenant schema. On tenant creation, all these tables are created under `tenant_{id}`:

```
{schema}.configuration      (key, value, updated_at)
{schema}.secrets            (id, name, ciphertext, nonce, created_at, updated_at)
{schema}.projects           (id, name, description, personality, created_at, updated_at)
```

Additional tenant schema tables are added in later phases (memory, skills, jobs, etc.). The schema migration system handles this.

### 1.3 Configuration and Service Registry

- `.env` file with all host-level config:
  - `PG_HOST`, `PG_PORT`, `PG_DATABASE`, `PG_USER`, `PG_PASSWORD`
  - `ENCRYPTION_KEY` (AES-256, 32 bytes, hex-encoded)
  - `OLLAMA_HOST`, `OLLAMA_PORT`
  - `BROWSER_SERVICE_HOST`, `BROWSER_SERVICE_PORT`
  - `MAX_DB_CONNECTIONS=10`
  - `LOG_LEVEL=INFO`
- Service registry initialization on startup (env vars → KV store `service` namespace).
- `KVStore` class implementation.

### 1.4 Core Abstractions

Implement the foundational interfaces (implementations can be minimal stubs where the full feature isn't in this phase):

- **`TenantContext`** dataclass — tenant_id, schema_name, credentials, feature_flags.
- **`DatabaseBackend`** ABC + `PostgresBackend` — explicit schema qualification in all queries.
- **`KVStore`** — full implementation (get, set, delete, list_keys, cleanup_expired).
- **`ConnectionManager`** — asyncpg pool wrapper.

### 1.5 Authentication Middleware

- API key validation: hash incoming key, lookup in `public.api_keys`, resolve `tenant_id`.
- Tenant status check: reject if not `active` (401).
- `TenantContext` construction and injection via FastAPI dependency.
- Rate limiting placeholder (log only, no enforcement — thresholds TBD).

### 1.6 Secrets Management

- AES-256-GCM encryption/decryption utility functions.
- Store/retrieve tenant secrets in `{schema}.secrets`.
- Decrypt tenant credentials into `TenantContext.credentials` during middleware.

### 1.7 API Endpoints — Admin

All under `/api/v1/admin/`. Admin API keys have `role='admin'` in `public.api_keys`.

**Tenant management:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/admin/tenants` | GET | List all tenants (with status, created_at, key count) |
| `/api/v1/admin/tenants` | POST | Create tenant (creates schema, storage dir, Linux user, deploys frontend, generates API key) |
| `/api/v1/admin/tenants/{id}` | GET | Get tenant details (config, resource usage summary) |
| `/api/v1/admin/tenants/{id}` | PATCH | Update tenant (display_name, email) |
| `/api/v1/admin/tenants/{id}/suspend` | POST | Suspend tenant (invalidate keys, disconnect WebSockets) |
| `/api/v1/admin/tenants/{id}/reactivate` | POST | Reactivate tenant (set status to active, generate new API key) |
| `/api/v1/admin/tenants/{id}/delete` | POST | Initiate deletion (export zip, schedule 14-day cleanup) |
| `/api/v1/admin/tenants/{id}/keys` | GET | List tenant's API keys |
| `/api/v1/admin/tenants/{id}/keys` | POST | Generate new API key for tenant |
| `/api/v1/admin/tenants/{id}/keys/{key_id}` | DELETE | Revoke specific API key |

**Tenant defaults (LLM endpoints):**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/admin/tenants/{id}/config` | GET | Get tenant configuration (LLM endpoints, model names, feature flags) |
| `/api/v1/admin/tenants/{id}/config` | PATCH | Update tenant configuration |
| `/api/v1/admin/tenants/{id}/secrets` | PUT | Set tenant secrets (LLM API keys — encrypted on write) |

**System monitoring:**

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/admin/logs` | GET | Query audit log (filterable by tenant_id, event_type, severity, time range, paginated) |
| `/api/v1/admin/health` | GET | Service registry health check (Postgres, Ollama, browser, Firecracker status) |
| `/api/v1/admin/stats` | GET | System stats (tenant count, total storage, active connections, DB pool usage) |

### 1.8 API Endpoints — Tenant (Minimal)

Minimal tenant-facing endpoints for validation:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/me` | GET | Return current tenant info (validates auth works) |
| `/api/v1/me/config` | GET | Return tenant's own configuration |
| `/api/v1/me/config` | PATCH | Update own configuration (LLM endpoints, model names) |
| `/api/v1/me/secrets` | PUT | Set own secrets (LLM API keys) |

### 1.9 Admin Console (Web UI)

A minimal React admin UI deployed at `https://hostname/admin/`:

- Login screen (API key entry).
- Tenant list with status indicators.
- Create tenant form (username, email, display name).
- Tenant detail view (config, keys, status, actions).
- Suspend / reactivate / delete actions with confirmation.
- LLM endpoint configuration form per tenant.
- Log viewer with filters (tenant, event type, severity, date range).
- Health dashboard showing service registry status.

### 1.10 Tenant Provisioning Script

Automate tenant creation end-to-end:

1. Create record in `public.tenants`.
2. Create Postgres schema `tenant_{id}` with all Phase 1 tables.
3. Record schema version in `public.schema_versions`.
4. Create Linux user account.
5. Deploy frontend static build to `~/public_html/pantheon/`.
6. Create tenant directory in StorageBackend (`/data/tenants/{id}/`).
7. Initialize feature flags in KV store.
8. Generate API key, store hash in `public.api_keys`.
9. Log provisioning event to `public.audit_log`.
10. Return API key to admin.

### 1.11 Schema Migration Runner

- `migrations/tenant/` directory with numbered SQL files.
- Runner reads `public.schema_versions`, compares to latest migration number.
- Applies pending migrations per tenant with `{schema}` placeholder substitution.
- Admin endpoint: `POST /api/v1/admin/migrate` — trigger migration run.
- Also runs automatically on application startup.

### 1.12 Logging

- All admin actions logged to `public.audit_log`.
- Auth events (success, failure, key usage) logged.
- Structured JSON log output to stdout for system-level logs.

### 1.13 Deployment

- Nginx reverse proxy with TLS (Let's Encrypt).
- `userdir` module enabled for `~/public_html/` serving.
- Systemd service for Pantheon API (FastAPI + uvicorn).
- `.env` file configured with production values.

### Acceptance Criteria — Phase 1

- [ ] Pantheon API starts, connects to Postgres, initializes service registry.
- [ ] `/api/v1/admin/health` returns status of all registered services.
- [ ] Admin can create a tenant via API. Schema, Linux user, frontend, and storage dir are created.
- [ ] Admin can log into admin console, see tenant list, create/suspend/reactivate/delete tenants.
- [ ] Admin can set LLM endpoint configuration and API keys for a tenant.
- [ ] Tenant can authenticate with API key and hit `/api/v1/me`.
- [ ] Tenant can update their own config and secrets.
- [ ] Suspended tenant gets 401 on all endpoints.
- [ ] Deleted tenant data is exported to zip. Schema dropped after 14 days.
- [ ] Audit log captures all admin and auth events. Log viewer works in admin console.
- [ ] Schema migration runner applies migrations across all tenant schemas.
- [ ] Tenant frontend loads at `https://hostname/~username`.

---

## Phase 2 — Inference, Memory, and Chat

**Goal:** Tenants can have conversations with the agent using their configured LLM endpoints. All five memory tiers are operational. The extraction pipeline runs after conversations.

### 2.1 Inference Abstraction

- **`InferenceClient`** — stateless OpenAI-compatible HTTP caller (streaming + non-streaming).
- **`InferenceRouter`** — resolves client config from `TenantContext` + request type (chat, vision, fallback, embed, rerank).
- Credentials decrypted per-request from tenant schema, never cached.
- Host Ollama endpoint resolved from service registry for embed/rerank.

### 2.2 Embedding and Reranking Service

- Ollama installed and configured on host with embedding model (e.g., `all-MiniLM-L6-v2`) and reranker (e.g., `bge-reranker-base`).
- Embedding endpoint integration via `InferenceRouter` (`request_type='embed'`).
- Reranker endpoint integration via `InferenceRouter` (`request_type='rerank'`).

### 2.3 Vector Store

- **`VectorStore`** ABC + **`PgVectorStore`** implementation.
- pgvector extension enabled in tenant schemas.
- Tenant schema table:

```
{schema}.semantic_memories  (id, project_id, content, embedding vector(dims), metadata, embedding_model, created_at)
```

- Store, search (cosine similarity), delete, list, count operations.
- All vectors tagged with `embedding_model` for versioning.

### 2.4 Memory Tiers

Tenant schema tables:

```
{schema}.conversations       (id, project_id, created_at, updated_at, summary)
{schema}.messages            (id, conversation_id, role, content, tool_calls, created_at)
{schema}.episodic_memories   (id, project_id, content, metadata, created_at)
{schema}.graph_entities      (id, project_id, name, entity_type, attributes, created_at, updated_at)
{schema}.graph_relationships (id, project_id, source_id, target_id, relationship_type, weight, metadata, created_at)
{schema}.archival_memories   (id, project_id, content, metadata, file_ref, created_at)
```

- **Working Memory** — in-process per-conversation, backed by KV store (`working_memory` namespace). Restore on reconnect, flush periodically.
- **Episodic Memory** — conversation history + extracted episodes.
- **Semantic Memory** — pgvector-backed via `VectorStore`.
- **Graph Memory** — entities and relationships in tenant schema.
- **Archival Memory** — long-term storage with StorageBackend references.
- **MemoryManager** — orchestrates all five tiers. Accepts `TenantContext`. Token-aware context budget.

### 2.5 Storage Backend

- **`StorageBackend`** ABC + **`LocalFilesystemBackend`** implementation.
- All methods require `tenant_id` for path scoping.
- Path traversal validation in all operations.
- Tenant directory structure: `/data/tenants/{id}/projects/{project_id}/workspace/`, etc.

### 2.6 Chat API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/projects` | GET | List tenant's projects |
| `/api/v1/projects` | POST | Create project |
| `/api/v1/projects/{id}` | GET | Get project details |
| `/api/v1/projects/{id}` | PATCH | Update project |
| `/api/v1/projects/{id}` | DELETE | Delete project |
| `/api/v1/projects/{id}/conversations` | GET | List conversations |
| `/api/v1/projects/{id}/conversations` | POST | Start new conversation |
| `/api/v1/projects/{id}/conversations/{cid}/messages` | GET | Get conversation history |
| `/api/v1/ws/chat` | WebSocket | Streaming chat (token in query param) |

- WebSocket authentication via query param token → `TenantContext`.
- Agent core receives `TenantContext`, calls `InferenceRouter.get_client('chat', ctx)`.
- Pre-recall: `MemoryManager.recall()` injects relevant memories into system prompt.
- Streaming response via WebSocket.
- Post-conversation: working memory flushed to KV store.

### 2.7 Extraction Pipeline

- Runs as a background job after conversation ends (or on explicit trigger).
- Uses `InferenceRouter.get_client('fallback', ctx)` (or 'chat' if no fallback configured).
- Extracts entities, relationships, facts, user preferences.
- Routes extracted data to appropriate memory tiers.
- Logged as event in `public.audit_log`.

### 2.8 Memory API (Tenant-Facing)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/projects/{id}/memory/search` | POST | Semantic search across memory tiers |
| `/api/v1/projects/{id}/memory/episodic` | GET | Browse episodic memories |
| `/api/v1/projects/{id}/memory/graph` | GET | Browse graph entities and relationships |
| `/api/v1/projects/{id}/memory/semantic` | GET | Browse semantic memories |
| `/api/v1/projects/{id}/memory` | DELETE | Clear specific memory tier for project |

### 2.9 Schema Migration

- Migration `002_add_memory_tables.sql` adds all Phase 2 tables to existing tenant schemas.
- New tenants created after Phase 2 get all tables directly.

### 2.10 Admin Additions

- Tenant detail view shows memory stats (record counts per tier, vector count, storage usage).
- Re-embedding admin endpoint: `POST /api/v1/admin/re-embed` — triggers re-embedding workflow across all tenants.

### Acceptance Criteria — Phase 2

- [ ] Tenant can configure LLM endpoints and have a streaming chat conversation.
- [ ] Agent recalls relevant memories from prior conversations during pre-recall.
- [ ] Extraction pipeline runs after conversation and populates memory tiers.
- [ ] Semantic search returns relevant results using pgvector.
- [ ] Working memory survives API process restart (KV store recovery).
- [ ] Two concurrent conversations in the same project don't interfere.
- [ ] Memory browser API returns data across all tiers.
- [ ] Embedding model version is stored with every vector.
- [ ] Admin can view memory stats per tenant.

---

## Phase 3 — Task System: Jobs, Schedules, and Workflows

**Goal:** Full job lifecycle, recurring schedules, multi-step workflows with approval gates, and reactive triggers.

### 3.1 Job System

Tenant schema tables:

```
{schema}.jobs        (id, project_id, workflow_id, workflow_step, job_type, payload, status, priority, attempts, max_attempts, result, error, scheduled_for, created_at, started_at, completed_at)
```

- `PostgresTaskRunner` implementation of `TaskRunner` ABC.
- `enqueue()` inserts into tenant schema + `public.job_dispatch` + fires `NOTIFY new_job`.
- Job worker process: listens on `new_job` channel, claims from dispatch table, reconstructs `TenantContext`, executes, updates status.
- Deferred jobs: worker checks `scheduled_for` — skips jobs not yet due. Separate ticker process advances deferred jobs to `queued` when their time arrives.
- Extraction pipeline (Phase 2) refactored to use job system instead of `asyncio.ensure_future()`.
- File indexing refactored to use job system.

### 3.2 Schedules

Tenant schema table:

```
{schema}.schedules   (id, project_id, name, description, schedule_expr, job_type, job_payload, enabled, last_run_at, next_run_at, created_at, updated_at)
```

- Scheduler loop: runs every 60 seconds, iterates active tenant schemas, finds schedules where `next_run_at <= now() AND enabled = true`.
- Creates job rows + dispatch entries.
- Updates `last_run_at` and computes `next_run_at`.
- Respects tenant status — skips suspended tenants.

### 3.3 Workflows

Tenant schema table:

```
{schema}.workflows   (id, project_id, name, description, status, current_step, definition, context, created_at, started_at, completed_at)
```

- Workflow engine: on workflow start, enqueues first step as a job.
- On step job completion: engine reads workflow definition, evaluates transition (`next`, `complete`, `fail_workflow`, `retry`, `goto:N`).
- Context accumulation: step results merged into `workflow.context` keyed by step index.
- Approval gates: step with `job_type: 'approval'` pauses workflow, creates notification (stored in KV or tenant table). Tenant approves/rejects via API, which resumes workflow.

### 3.4 Triggers

Tenant schema table:

```
{schema}.triggers    (id, project_id, name, event_type, event_filter, action_type, action_payload, enabled, created_at)
```

- Event bus: internal publish function called when events occur (file uploaded, skill completed, job completed, etc.).
- On event: query matching triggers in tenant schema, enqueue resulting jobs or start workflows.

### 3.5 Task API (Tenant-Facing)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/projects/{id}/jobs` | GET | List jobs (filterable by status, type) |
| `/api/v1/projects/{id}/jobs` | POST | Enqueue a job |
| `/api/v1/projects/{id}/jobs/{jid}` | GET | Job detail + result |
| `/api/v1/projects/{id}/jobs/{jid}/cancel` | POST | Cancel a pending/queued job |
| `/api/v1/projects/{id}/schedules` | GET | List schedules |
| `/api/v1/projects/{id}/schedules` | POST | Create schedule |
| `/api/v1/projects/{id}/schedules/{sid}` | PATCH | Update schedule (enable/disable, change expression) |
| `/api/v1/projects/{id}/schedules/{sid}` | DELETE | Delete schedule |
| `/api/v1/projects/{id}/workflows` | GET | List workflows |
| `/api/v1/projects/{id}/workflows` | POST | Start a workflow |
| `/api/v1/projects/{id}/workflows/{wid}` | GET | Workflow detail (status, current step, context) |
| `/api/v1/projects/{id}/workflows/{wid}/approve` | POST | Approve current approval step |
| `/api/v1/projects/{id}/workflows/{wid}/reject` | POST | Reject current approval step |
| `/api/v1/projects/{id}/workflows/{wid}/cancel` | POST | Cancel workflow |
| `/api/v1/projects/{id}/triggers` | GET | List triggers |
| `/api/v1/projects/{id}/triggers` | POST | Create trigger |
| `/api/v1/projects/{id}/triggers/{tid}` | PATCH | Update trigger (enable/disable) |
| `/api/v1/projects/{id}/triggers/{tid}` | DELETE | Delete trigger |

### 3.6 Tenant-Level Jobs, Schedules, Workflows

Same endpoints as above but without project scoping for tenant-wide operations:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/jobs` | GET | List all jobs across projects |
| `/api/v1/schedules` | GET | List all schedules across projects |
| `/api/v1/workflows` | GET | List all workflows across projects |

### 3.7 Admin Additions

- Job dispatch queue depth on admin dashboard.
- Per-tenant job/workflow stats.
- Admin can view and cancel any tenant's jobs/workflows.

### Acceptance Criteria — Phase 3

- [ ] Jobs can be enqueued, executed by worker, and return results.
- [ ] Deferred jobs execute at the specified time.
- [ ] Extraction pipeline runs as a job (not fire-and-forget).
- [ ] Schedules create job instances on cadence.
- [ ] Workflows execute multi-step sequences with correct state transitions.
- [ ] Approval gate pauses workflow; approve/reject resumes correctly.
- [ ] `goto:N` transition loops back to a prior step.
- [ ] Triggers fire when matching events occur.
- [ ] Suspended tenant's scheduled jobs are skipped.
- [ ] Worker survives restart without losing pending jobs.
- [ ] Admin dashboard shows queue depth and per-tenant task stats.

---

## Phase 4 — Skills System and Firecracker Sandboxing

**Goal:** Skills stored in the database with global/tenant/project scoping. Skill execution sandboxed in Firecracker microVMs.

### 4.1 Skills Database

Tenant schema table:

```
{schema}.skills  (id, project_id, name, description, scope, skill_md, scripts, assets, version, created_at, updated_at)
```

Global skills in `public.global_skills` (already created in Phase 1).

- Skill resolution: project → tenant → global (most specific wins).
- Skill CRUD for tenants (within their schema) and admins (global skills).

### 4.2 Firecracker Integration

- Install Firecracker on Oracle A1 (ARM build).
- Build base root filesystem images: `python-base`, `node-base`.
- MicroVM manager: provision, inject script, execute with resource limits, capture output, destroy.
- Integration with job system — skill execution is tracked as a job.

### 4.3 Skill Execution Flow

1. Agent or user triggers skill execution.
2. Job is enqueued with `job_type: 'skill_exec'`.
3. Worker claims job, reads skill definition from tenant schema (or public for global).
4. Worker provisions Firecracker microVM with appropriate base image.
5. Script injected, executed with resource limits.
6. Output captured, stored as job result.
7. MicroVM destroyed.

### 4.4 Capability Grants

- Network access: allowed (outbound only from microVM).
- Tenant file access: workspace directory mounted read/write (via virtio-fs or API proxy).
- Cross-tenant access: denied (no other tenant volumes mounted).
- Host access: denied (microVM network restricted to outbound only + API proxy).

### 4.5 Skills API (Tenant-Facing)

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/skills` | GET | List available skills (merged: project + tenant + global) |
| `/api/v1/skills` | POST | Create tenant-level skill |
| `/api/v1/projects/{id}/skills` | GET | List project skills |
| `/api/v1/projects/{id}/skills` | POST | Create project-level skill |
| `/api/v1/skills/{sid}` | GET | Get skill detail |
| `/api/v1/skills/{sid}` | PATCH | Update skill |
| `/api/v1/skills/{sid}` | DELETE | Delete skill |
| `/api/v1/skills/{sid}/execute` | POST | Execute skill (enqueues job) |

### 4.6 Admin Additions

- Global skill CRUD in admin console.
- Firecracker pool status on health dashboard.
- Skill execution logs visible in admin log viewer.

### Acceptance Criteria — Phase 4

- [ ] Global skills visible to all tenants. Tenant skills visible only to that tenant.
- [ ] Project skills override tenant skills on name conflict.
- [ ] Skill execution runs in Firecracker microVM with resource limits.
- [ ] Skill can read/write files in tenant's project workspace.
- [ ] Skill cannot access host filesystem or other tenant data.
- [ ] Skill execution tracked as a job with status, result, and error handling.
- [ ] Admin can manage global skills and view Firecracker pool status.
- [ ] MicroVM is destroyed after execution (no lingering processes).

---

## Phase 5 — Web Browsing Service

**Goal:** Shared Playwright + Chromium service for agent web browsing, toggleable per tenant.

### 5.1 Browser Service

- Deploy Chromium + Playwright as a persistent service on the host.
- Internal API: `browse()`, `screenshot()`, `extract()`, `interact()`.
- Connection pool of Chromium browser instances. Contexts created per-request and destroyed after.
- Register in service registry (`service:browser` in KV store).

### 5.2 Tenant Feature Toggle

- `web_browsing_enabled` feature flag in KV store per tenant.
- Middleware checks flag before routing browse requests. Returns 403 if disabled.
- Admin can toggle per tenant via admin console.
- Usage logging to `public.audit_log` for future metering.

### 5.3 Security

- URL denylist: block `localhost`, private IP ranges, internal service endpoints.
- Per-request timeout (configurable, default 30s).
- Max concurrent contexts per tenant.
- All outbound URLs logged per tenant.

### 5.4 Integration

- Agent can invoke browse during conversation (tool call).
- Skills can call browse via API proxy from Firecracker microVM.
- Browse results returned as structured data (text content, screenshots as base64).

### 5.5 Browse API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/browse` | POST | Browse a URL (requires `web_browsing_enabled`) |
| `/api/v1/browse/screenshot` | POST | Screenshot a URL |
| `/api/v1/browse/extract` | POST | Extract content by selector |
| `/api/v1/browse/interact` | POST | Multi-step browser interaction |

### 5.6 Admin Additions

- Browser service status on health dashboard.
- Per-tenant browse toggle in admin console.
- Browse usage stats per tenant.

### Acceptance Criteria — Phase 5

- [ ] Browser service starts and registers in service registry.
- [ ] Agent can browse URLs during conversation and receive page content.
- [ ] Screenshots are captured and returned.
- [ ] Tenant with `web_browsing_enabled=false` gets 403 on browse endpoints.
- [ ] Admin can toggle browsing per tenant.
- [ ] SSRF protection blocks requests to localhost and private IPs.
- [ ] Browser contexts are isolated between tenants.
- [ ] Browse actions are logged to audit log.

---

## Phase 6 — Frontend, Tenant Self-Service, and Polish

**Goal:** Tenant-facing frontend with full agent UI, self-service configuration, and production hardening.

### 6.1 Tenant Frontend

React application deployed to `~/public_html/pantheon/` per tenant:

- Login screen (API key entry → httpOnly cookie).
- Project list and creation.
- Chat interface with streaming WebSocket.
- Memory browser (episodic, semantic, graph, archival).
- Job/schedule/workflow management UI.
- Skill browser and execution.
- Settings page (LLM endpoints, model configuration, API key management).

### 6.2 Tenant Self-Service API

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/me/keys` | GET | List own API keys |
| `/api/v1/me/keys` | POST | Generate new API key |
| `/api/v1/me/keys/{kid}` | DELETE | Revoke own API key |
| `/api/v1/me/export` | POST | Request data export (enqueues export job) |
| `/api/v1/me/export/{eid}` | GET | Download export zip when ready |
| `/api/v1/me/logs` | GET | View own audit log events |

### 6.3 Admin Console Polish

- Tenant resource usage charts (storage, memory records, jobs over time).
- Batch operations (suspend/reactivate multiple tenants).
- System configuration editor (embedding model, Firecracker pool size, rate limits).
- Re-embedding progress dashboard.

### 6.4 Production Hardening

- Rate limiting enforcement (requests per minute per tenant, configurable).
- Request size limits on API endpoints.
- Graceful WebSocket reconnection handling in frontend.
- Error handling and user-friendly error messages across all endpoints.
- API response pagination on all list endpoints.
- CORS configuration for frontend origins.

### 6.5 Notification System (Foundation)

- Notification table in tenant schema:

```
{schema}.notifications  (id, type, title, message, read, data, created_at)
```

- WebSocket push for real-time notifications (workflow approvals, job completions, errors).
- Notification API:

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/api/v1/notifications` | GET | List notifications (unread first) |
| `/api/v1/notifications/{nid}/read` | POST | Mark as read |
| `/api/v1/notifications/read-all` | POST | Mark all as read |

### Acceptance Criteria — Phase 6

- [ ] Tenant can log into frontend at `https://hostname/~username`, chat with agent, browse memory, manage jobs/workflows/skills.
- [ ] Tenant can manage their own API keys and configuration.
- [ ] Tenant can request and download a data export.
- [ ] Tenant can view their own audit logs.
- [ ] Rate limiting rejects requests over threshold.
- [ ] Workflow approval notifications appear in real-time.
- [ ] Admin console shows usage charts and supports batch operations.
- [ ] All list endpoints are paginated.
- [ ] WebSocket reconnects gracefully on disconnect.

---

## Phase Summary

| Phase | Delivers | Depends On |
|-------|----------|-----------|
| **1 — Foundation** | Postgres, auth, admin console, tenant CRUD, config, secrets, logging, schema migrations | Nothing (greenfield) |
| **2 — Memory & Chat** | Inference abstraction, 5 memory tiers, chat API, extraction, vector search | Phase 1 |
| **3 — Task System** | Jobs, schedules, workflows, triggers, approval gates, event bus | Phase 2 (extraction → jobs) |
| **4 — Skills** | DB-backed skills, Firecracker sandboxing, skill execution as jobs | Phase 3 (job system) |
| **5 — Browsing** | Playwright/Chromium service, feature toggle, SSRF protection | Phase 2 (agent chat integration) |
| **6 — Frontend & Polish** | Tenant UI, self-service, notifications, rate limiting, production hardening | Phases 1-5 |

---

## Notes

- **Phases 4 and 5 can be developed in parallel** — they are independent of each other. Both depend on Phase 3 (for job tracking of skill execution and browse actions), but not on each other.
- **Phase 6 frontend work can begin earlier** — the chat UI portion can start as soon as Phase 2 is complete. The full frontend depends on all prior phases for feature completeness.
- **Each phase should include tests** — at minimum, integration tests for all API endpoints and unit tests for abstraction layer implementations.
- **Schema migrations accumulate** — each phase adds migrations that run against all existing tenant schemas. The migration runner (Phase 1) handles this automatically.
