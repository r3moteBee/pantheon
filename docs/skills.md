# Skills

A skill is a reusable recipe: a folder containing `skill.json` and `instructions.md`. When a skill is active, its instructions go into the agent's system prompt. **Skills are prompt context, not code.** Pantheon has no skill executor. Nothing runs scripts that ship inside a skill folder; the agent only reads `instructions.md`. Any code the agent runs goes through its normal tools (`code_execute`/`run_command`, which are gated by host exec; see [security.md](security.md)).

Skills are different from **scheduled tasks**. A task is a one-shot or recurring job that may *bind* a skill through `skill_name` (see [jobs.md](jobs.md)).

## Where skills live

| Location | Kind | Notes |
|---|---|---|
| `<repo>/skills/<name>/` | Bundled | Trusted and read-only in the editor. `is_bundled` is set by the loader from this directory, never read from `skill.json`. |
| `data/skills/<name>/` | User-installed | Created by import, the editor, or the `create_skill` agent tool |
| `data/skills/.quarantine/` | Quarantined | Not loaded |
| `data/skills/.scan_results/<name>.json` | Scan cache | Tagged with a content hash. Any file change invalidates it. |
| `data/skills/.versions/<name>/<stamp>/` | Edit history | Snapshot taken before every editor write |

A user skill with the same name as a bundled skill is refused at load time and logged as `skill.name_collision_blocked`.

## `skill.json`

`SkillManifest` (`skills/models.py`) accepts unknown fields (`extra="allow"`), so manifests from external hubs load unchanged.

```json
{
  "name": "content-ingest-graph",
  "description": "Ingest a list of sources and link topics",
  "version": "1.0.0",
  "triggers": ["ingest these videos"],
  "tags": ["ingest", "graph"],
  "chains": ["summarize-conversation"],
  "requires_mcp": ["mcp_SubDownload_search_youtube"],
  "parameters": [{"name": "urls", "type": "string", "required": true}],
  "pantheon": {
    "model_class": "agent",
    "schedulable": {"enabled": true, "default_cron": "0 7 * * *"},
    "memory": {"reads": ["semantic"], "writes": []},
    "permissions": {"network_domains": [], "file_paths": [], "vault_secrets": [],
                    "memory_tiers": {"semantic": "r", "episodic": "none", "graph": "none"}}
  }
}
```

- `triggers`, `tags` and `description` drive auto-discovery.
- `chains` pulls other skills' instructions into the context (at most 2 levels deep and 4 skills).
- `requires_mcp` is an extra top-level field listing MCP tool names. Scheduled runs check it.
- `pantheon.model_class` feeds the chat router.
- `pantheon.permissions` is **declarative only**. The scanner compares it against the skill's contents, but nothing enforces it at runtime.

## Invocation

- **Explicit:** a message starting with `/slug` (`resolver.resolve_explicit`). `/foo_bar` and `/foo-bar` both resolve. Scan-blocked skills are skipped.
- **Auto-discovery:** `resolver.resolve_auto` uses keyword scoring, not embeddings. A full trigger phrase scores +3, two or more non-stopword trigger words +1.5, a tag +1, the name +2, and three or more description words up to +2.5. The top match must score at least 3.0. The mode is stored per project in vault key `skill_discovery_<project>` (`GET/PUT /api/skills/discovery/{project_id}?mode=`):
  - `off` (default): no discovery.
  - `suggest`: the WebSocket chat sends a `skill_suggestion` for the user to accept or decline. Telegram shows buttons. REST chat treats `suggest` as `auto`.
  - `auto`: the skill activates and the chat emits `skill_active`.
- **Scheduled:** `create_task(skill_name=…)` is validated when scheduled. At run time the `autonomous_task` handler fails fast if the skill is missing, scan-blocked, or its `requires_mcp` tools aren't registered in the MCP manager.
- **Bots:** the same explicit and auto flow runs in every messaging adapter.

Enablement is per project. A skill is enabled unless its project is in `disabled_projects` or it is `scan_blocked`: a non-bundled skill with a failed scan and no override. Usage counters (explicit, auto, scheduled, suggestion accepted or declined) go to `data/skill_analytics.json` (`skills/analytics.py`).

## Modules (`backend/skills/`)

| Module | Role |
|---|---|
| `registry.py` | `SkillRegistry`: loads both directories, keeps per-project enable/disable state, persists scan results by content hash, runs the enable-time scan gate, provides `scan_summary()` |
| `resolver.py` | `resolve_explicit`, `resolve_auto`, `build_skill_context` (instructions + chains) |
| `editor.py` | File tree CRUD confined to user skills. `create_blank_skill` backs the `create_skill` tool. LLM helpers: scaffold, improve instructions, optimize triggers, lint/AI-lint. `test_skill_against_message` is a dry-run match, not an execution. |
| `importer.py` | Hub adapters (`skill_md`, `github`, `clawhub`, `local` upload, plus configured generic registries). Safe zip/tar extraction, scan on import, auto-quarantine on failure. |
| `scanner.py` | Three-layer security scan (see [security.md](security.md#skill-scanner-and-gates)) |
| `exporter.py` | Packages a user skill as `.tar.gz` (skipping `.versions` and caches) |
| `publisher.py` | Submits a skill that passed its scan to a registry's `/skills/submit`. If the registry is read-only, it stages the archive under `data/skill_submissions/`. |
| `versioning.py` | Snapshot, list, preview and restore of past skill versions. Restore snapshots the current state first. |
| `registries_config.py` | Configured registries in `data/skill_registries.json`. Bearer tokens live in the vault as `skill_registry:<id>`. |
| `models.py` | `SkillManifest`, `LoadedSkill`, `ScanResult`, `SkillDiscoveryMode` (`off/suggest/auto`), and related types |

External registries implement the [skill registry protocol](skill-registry-protocol.md). MCP server registries are a separate system ([mcp-registry-protocol.md](mcp-registry-protocol.md)).

## API (`backend/api/skills.py`, under `/api`)

| Group | Routes |
|---|---|
| Registry | `GET /skills`, `GET /skills/{name}`, `POST /skills/reload`, `DELETE /skills/{name}`, `PUT /skills/{name}/toggle`, `POST /skills/debug-match` |
| Discovery | `GET/PUT /skills/discovery/{project_id}` |
| Hubs and import | `GET /skills/hubs`, `POST /skills/search-hub`, `POST /skills/import` (`{source, hub, ai_review}`), `POST /skills/import/upload`, `GET/POST/PUT/DELETE /skills/registries[/{id}]` |
| Editor | `/skills/editor/…`: blank, scaffold, improve, optimize-triggers, lint, ai-lint, file CRUD and rename, test, versions (list/files/file/restore), export, publish |
| Security | `POST /skills/scan/all`, `GET /skills/scan/summary`, `POST/GET /skills/{name}/scan`, `GET /skills/quarantine/list`, `POST /skills/{name}/quarantine`, `POST /skills/{name}/unquarantine`, `GET /skills/security/override-status` |
| Analytics | `GET /skills/analytics`, `POST /skills/analytics/reset` |

Import always runs the scanner. Layer 3 (the AI review) is skipped only with `ai_review: false`. Enabling a non-bundled skill that has no scan, or failed its scan, returns 403. A forced enable (`force_enable` + `override_password`) is covered in [security.md](security.md#skill-scanner-and-gates).
