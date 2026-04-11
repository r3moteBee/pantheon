# Pantheon Multitenant Architecture

**Status:** Draft
**Date:** 2026-04-10
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
- Per-tenant tables for episodic memory, graph memory, semantic memory (pgvector), conversation history, skills, scheduled tasks, and configuration

A shared `public` schema holds cross-tenant data: tenant registry, global skills, system configuration, and admin/audit logs.

### 3.2 Database Abstraction Layer

All application code accesses the database through a `DatabaseBackend` interface. This maintains modularity and allows future backend swaps.

```
DatabaseBackend (ABC)
  ├── PostgresBackend     (production / multitenant)
  └── SQLiteBackend       (development / single-tenant)
```

Methods cover standard CRUD for each memory tier, tenant management, and skill storage. No raw SQL in business logic — all queries go through the backend interface.

### 3.3 Vector Store Abstraction

Vector operations are accessed through a `VectorStore` interface, decoupled from any specific vector database.

```
VectorStore (ABC)
  ├── PgVectorStore       (default, uses pgvector within Postgres)
  ├── ChromaStore          (legacy / development)
  └── QdrantStore          (future option)
```

Interface methods: `store()`, `search()`, `delete()`, `list()`, `count()`. Implementations handle backend-specific details (collection naming, distance metrics, metadata filtering). The application layer passes pre-computed embedding vectors — the vector store does not call embedding models directly.

---

## 4. Inference Architecture

### 4.1 Tenant-Provided Generative Models (BYO)

Each tenant configures their own inference endpoints for:

- **LLM model endpoint** — primary chat/reasoning model
- **Vision model endpoint** — multimodal/vision tasks
- **Fallback model endpoint** — used when primary is unavailable or for lower-priority tasks

All endpoints must be OpenAI API-compatible. Tenant model configuration is stored in the tenant's Postgres schema and managed through the UI. API keys for tenant endpoints are stored encrypted in the database (migrated from the current SQLite vault).

### 4.2 Host-Provided Embedding and Reranking

Embedding and reranking models are centralized shared services, included in the hosting fee.

- **Default provider:** Ollama running on the host (localhost)
- **Configuration:** Hardcoded in the host-level `.env` file (not accessible through the application)
- **Models:** Configurable by the host administrator (e.g., `all-MiniLM-L6-v2` for embeddings, `bge-reranker-base` for reranking)

All tenants share the same embedding model. This ensures consistent vector spaces across the platform and predictable semantic search behavior.

### 4.3 Embedding Versioning

When the host administrator upgrades the embedding model, existing vectors become stale (different vector space). To handle this:

- Every stored vector includes an `embedding_model` metadata field recording which model produced it.
- On model change, a background re-embedding job is triggered per tenant schema.
- Until re-embedding completes, queries use the old model for tenants with stale vectors.
- The admin dashboard shows re-embedding progress per tenant.

---

## 5. Memory Architecture

### 5.1 Memory Tiers

The existing five-tier memory architecture is preserved, with scoping changes for multitenancy:

| Tier | Scope | Storage |
|------|-------|---------|
| Working Memory | Per-conversation | In-process (not persisted) |
| Episodic Memory | Per-project (owned by tenant) | Postgres (tenant schema) |
| Semantic Memory | Per-project (owned by tenant) | pgvector (tenant schema) |
| Graph Memory | Per-project (owned by tenant) | Postgres (tenant schema) |
| Archival Memory | Per-project (owned by tenant) | Postgres + StorageBackend |

### 5.2 Working Memory

Working memory is scoped per-conversation. Each active conversation maintains its own working memory context. This supports concurrent conversations per tenant without state bleeding.

### 5.3 Tenant Ownership

All projects belong to a tenant. Memory is per-project, and projects are per-tenant. The tenant owns all data across all their projects. Tenant-level queries (e.g., "search across all my projects") aggregate across the tenant's project-scoped memories.

---

## 6. Skills System

### 6.1 Storage

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

### 6.2 Scope and Visibility

- **Global skills** — maintained by the platform administrator. Available to all tenants. Stored in the `public` schema.
- **Tenant skills** — created by the tenant. Available across all of that tenant's projects. Stored in the tenant's schema.
- **Project skills** — scoped to a single project within a tenant. Stored in the tenant's schema with a `project_id`.

Resolution order: project skills > tenant skills > global skills (most specific wins on name conflict).

### 6.3 Sandboxed Execution with Firecracker

Skill scripts execute inside Firecracker microVMs, providing strong isolation from the host and other tenants.

**Execution flow:**

1. Skill is triggered (by agent, schedule, or user action).
2. Script and dependencies are extracted from the database.
3. A Firecracker microVM is provisioned with a pre-built base image (Python + common libraries).
4. Script is injected into the microVM.
5. Execution runs with defined resource limits (CPU time, memory, wall clock timeout).
6. Results (stdout, structured output, files) are captured.
7. MicroVM is destroyed.

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

## 7. File Storage

### 7.1 Storage Backend Abstraction

File storage is accessed through a `StorageBackend` interface, allowing the underlying storage to be swapped without application changes.

```
StorageBackend (ABC)
  ├── LocalFilesystemBackend   (initial implementation)
  └── ObjectStorageBackend     (future — Oracle Object Storage / S3-compatible)
```

Interface methods: `read()`, `write()`, `delete()`, `list()`, `exists()`, `get_url()`.

### 7.2 Initial Implementation: Local Filesystem

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

### 7.3 Future: Object Storage

The `ObjectStorageBackend` implementation would use Oracle Object Storage (S3-compatible API). Key mapping:

```
Bucket: pantheon-tenant-data
Key:    {tenant_id}/projects/{project_id}/workspace/file.txt
```

Migration from local filesystem to object storage would be a data migration + config change, with no application code changes required.

---

## 8. Authentication and API Design

### 8.1 Authentication

Tenants authenticate via **API keys**. This supports:

- Web UI sessions (API key stored in session)
- Mobile app connections
- Desktop app connections
- Programmatic API access

Each tenant can have multiple API keys (e.g., one per device/app). Keys are stored as hashed values in the `public.api_keys` table with a `tenant_id` foreign key.

Future consideration: OAuth2 / OIDC for SSO integrations. The API key model does not preclude adding this later.

### 8.2 Secrets Management

Tenant secrets (LLM API keys, external service credentials) are stored encrypted in the tenant's Postgres schema. The encryption key is derived from a host-level secret in the `.env` file. The current SQLite vault is replaced by this mechanism.

The host `.env` file is not accessible through the application. It contains only host-level configuration:

- Database connection string
- Host encryption key for tenant secrets
- Embedding/reranking model endpoints (Ollama)
- Firecracker configuration
- Logging configuration

### 8.3 API Gateway

All tenant requests flow through a shared API server that:

1. Extracts the API key from the request header.
2. Resolves the `tenant_id` from the key.
3. Sets the Postgres `search_path` to the tenant's schema for the duration of the request.
4. Enforces rate limiting per tenant.
5. Routes to the appropriate handler.

This is a middleware layer in the existing FastAPI application, not a separate service.

---

## 9. Administration

### 9.1 Management Console

A web-based admin console for platform administration:

**Tenant management:**
- Create / suspend / delete tenants
- View tenant resource usage (storage, memory records, active conversations)
- Export tenant data

**System monitoring:**
- Per-tenant logging (stored in Postgres `public.audit_log` table)
- Embedding/reranking service health
- Firecracker microVM pool status
- Database metrics

**Skill management:**
- CRUD for global skills
- Review tenant-created skills (optional moderation)

**Embedding management:**
- Current model info
- Trigger re-embedding jobs
- Monitor re-embedding progress

### 9.2 Tenant Provisioning Flow

1. Admin creates tenant via management console (or API).
2. System creates Postgres schema `tenant_{id}` with all required tables.
3. System creates tenant directory in StorageBackend (`/data/tenants/{id}/`).
4. System generates initial API key.
5. Admin provides API key to tenant.

No OS-level user accounts, no `su`, no per-user Pantheon installations. All tenants run on the shared Pantheon instance with schema-level isolation.

---

## 10. Logging and Observability

### 10.1 Per-Tenant Logging

All significant events are logged to `public.audit_log` with `tenant_id`:

- Authentication events (login, key usage)
- Memory operations (store, recall, consolidate)
- Skill executions (trigger, success, failure, duration)
- Configuration changes
- Errors and exceptions

### 10.2 Admin Visibility

The management console provides:

- Filterable log viewer (by tenant, time range, event type, severity)
- Aggregated metrics dashboard
- Alerting on error rate thresholds

### 10.3 Tenant Visibility

Tenants can view their own logs through the UI — limited to their schema's events. They cannot see other tenants' logs or system-level events.

---

## 11. Deployment Architecture

### 11.1 Target Environment

Oracle Cloud A1 instance (Ampere ARM):
- 4 OCPUs / 24GB RAM (initial; scalable via Oracle A1 Flex)
- Block volume for tenant data
- Tailscale for inference connectivity to Mac Studio

### 11.2 Services

| Service | Type | Notes |
|---------|------|-------|
| Pantheon API | FastAPI application | Shared instance, all tenants |
| PostgreSQL + pgvector | Database | Shared, schema-per-tenant |
| Ollama | Embedding/reranking | Localhost, shared |
| Firecracker | Skill execution | MicroVM pool, on-demand |
| Nginx (optional) | Reverse proxy / TLS | Frontend routing |

### 11.3 Containerization

Containerization is deferred for the initial implementation. The architecture is container-ready (externalized config, shared services, no filesystem coupling in business logic) and can be containerized when operational needs require it.

---

## 12. Migration and Compatibility

This is a new installation, not a migration from the existing single-tenant deployment. The single-tenant codebase on `main` remains unchanged.

The `multitenant` branch diverges architecturally but preserves the core agent logic (memory tiers, conversation flow, extraction pipeline, skill execution model). Code that is backend-agnostic (LLM integration, prompt construction, memory consolidation) should be shared across branches where possible.

---

## 13. Summary of Key Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Database | PostgreSQL + pgvector | Consolidates relational + vector storage; mature multitenancy |
| Tenant isolation | Schema-per-tenant | Clean export/delete, strong isolation, simpler than DB-per-tenant |
| Vector store | Abstracted; pgvector default | Modularity; pgvector avoids a separate service |
| Generative inference | Tenant BYO (OpenAI-compatible) | Lower operational burden; tenant flexibility |
| Embedding/reranking | Host-provided via Ollama | Consistent vector space; included in hosting fee |
| Skill storage | Database (global/tenant/project scope) | Security; no filesystem attack surface; supports marketplace |
| Skill execution | Firecracker microVMs | Strong isolation for untrusted tenant code |
| File storage | Abstracted; local filesystem initially | Defer object storage decision; interface allows future swap |
| Authentication | API keys | Simple; supports mobile/desktop/programmatic access |
| Working memory | Per-conversation | Supports concurrent conversations per tenant |
| Secrets | Encrypted in Postgres | Replaces SQLite vault; centralized |
| Logging | Per-tenant to Postgres | Admin and tenant visibility via UI |
| Containerization | Deferred (architecture is container-ready) | Avoid premature complexity |

---

## 14. Open Questions

- **Object storage migration:** When does latency from object storage become acceptable for project files? Benchmark after initial tenant onboarding.
- **Skill marketplace:** Should tenants be able to publish skills for other tenants to use? Deferred but the DB-backed skill model supports it.
- **Horizontal scaling:** When tenant count exceeds single-instance capacity, how to distribute? Options: read replicas for Postgres, multiple Pantheon API instances behind a load balancer, dedicated Firecracker hosts.
- **Rate limiting strategy:** Per-tenant limits on API calls, memory operations, skill executions, and storage. Specific thresholds TBD based on usage patterns.
- **Tenant billing integration:** Usage metering and billing system. Out of scope for initial implementation.
