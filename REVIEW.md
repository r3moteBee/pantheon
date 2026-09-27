# Reviewer guide

Start here if you're reviewing Pantheon for the first time.

1. **[README.md](README.md)** — what Pantheon is and how to install it.
2. **[CLAUDE.md](CLAUDE.md)** — the working reference: architecture, conventions and gotchas. Read its **Design rationale** section before recommending architectural changes; those calls are deliberate.
3. **[backend/README.md](backend/README.md)** — backend layout and routers.
4. Subsystems:
   - [docs/tools.md](docs/tools.md) (generated agent tool list)
   - [docs/jobs.md](docs/jobs.md)
   - [docs/skills.md](docs/skills.md)
   - [docs/messaging.md](docs/messaging.md)
   - [docs/security.md](docs/security.md)
   - [backend/sources/SOURCE_ADAPTERS.md](backend/sources/SOURCE_ADAPTERS.md)
   - [backend/memory/README.md](backend/memory/README.md)
5. **[docs/USAGE.md](docs/USAGE.md)** — how a user drives the agent.

Pantheon is **single-user and single-process** by design: APScheduler and the job worker run inside the FastAPI process. Recommendations built on multi-tenant patterns (queue/worker split, per-tenant isolation, request-scoped DB pools) don't apply here.

Ask before touching:
- **`data/`** holds runtime user data (memory, projects, vault). Never delete it during a review.
- **The vault scheme** (`backend/secrets/vault.py`, PBKDF2 plus a per-vault salt). Changing the parameters without a migration makes every stored secret unreadable. `scripts/rotate_vault_key.py` is the supported way to change the key.

Run the tests:

```bash
cd backend && ../.venv/bin/python -m pytest tests/integration/ -q
```

`docs/archive/` holds old plans and specs. They record past intent, not current behavior.
