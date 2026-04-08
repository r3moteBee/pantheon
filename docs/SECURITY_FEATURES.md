# Pantheon Security Features

## Overview

Pantheon implements defense-in-depth security for its Skills system. Security is enforced at three stages: **pre-installation** (scanning), **runtime** (sandboxing), and **post-hoc** (audit logging). This document covers all implemented security features as of 2026-04-06.

---

## 1. Security Scanner (`backend/skills/scanner.py`)

Every imported or user-installed skill passes through a three-layer scan pipeline before it can be enabled.

### Layer 1: Static Analysis (fast, deterministic)

Runs immediately on file contents with no external dependencies.

**File type controls:**
- Allowed extensions: `.json`, `.md`, `.txt`, `.py`, `.js`, `.ts`, `.sh`, `.bash`, `.yaml`, `.yml`, `.toml`, `.html`, `.css`, `.csv`, `.jinja`, `.jinja2`
- Blocked extensions (always rejected): `.exe`, `.dll`, `.so`, `.dylib`, `.bin`, `.msi`, `.dmg`, `.app`, `.com`, `.bat`, `.cmd`, `.ps1`, `.vbs`, `.wsh`, `.scr`, `.jar`, `.class`, `.war`, `.pyc`, `.pyo`, `.wasm`

**Size limits:**
- Max total skill size: 10 MB
- Max single file: 500 KB
- Max file count: 50

**Dangerous pattern detection** (regex matching in `.py`, `.js`, `.ts`, `.sh`, `.bash` files):

| Pattern | Severity | Rationale |
|---------|----------|-----------|
| `os.system()` | Critical | Shell injection vector |
| `subprocess` with `shell=True` | Critical | Shell injection vector |
| `eval()` / `exec()` | Critical | Arbitrary code execution |
| `compile()` | Warning | Dynamic code generation |
| `requests` / `httpx` / `urllib` / `socket` | Info–Warning | Network access |
| `paramiko` | Warning | SSH remote access |
| `shutil.rmtree` | Warning | Recursive deletion |
| `os.environ` / `os.getenv()` | Warning | Environment variable access |
| Hardcoded credential patterns | Critical | Embedded secrets |
| `base64.b64decode` | Warning | Possible obfuscation |
| `__import__()` / `getattr(__*)` | Warning | Reflective/dynamic access |
| `curl` / `wget` | Info | Network access |
| `rm -rf /` | Critical | Destructive command |
| `chmod 777` | Warning | Overly permissive |

**Manifest validation:** Checks for required fields (`name`, `description`).

### Layer 2: Capability Analysis (medium, heuristic)

Compares the skill's **declared** capabilities in `skill.json` against what the code **actually does**.

- Detects undeclared capabilities: network, file_write, file_read, subprocess, env_access
- Flags declared-but-unused capabilities (informational)
- Validates Pantheon memory permission alignment (e.g., skill declares `writes: ["semantic"]` but permissions only grant `"r"` or `"none"` for semantic tier)

### Layer 3: AI Review (slower, semantic)

Uses the configured LLM provider to semantically analyse skill scripts.

**Review scope:**
1. Malicious intent — data exfiltration, backdoors, credential theft, unauthorised access
2. Capability mismatch — scripts doing things the manifest doesn't declare
3. Instructions vs code — does `instructions.md` accurately describe what scripts do?
4. Overall risk rating: low / medium / high / critical

**Implementation:** Sends a structured prompt with the manifest, instructions snippet (truncated to 3K chars), and script contents (truncated to 8K chars per file) to the LLM. Parses a JSON response with findings. Gracefully degrades if the LLM returns invalid JSON or is unavailable.

### Risk Scoring

Findings are weighted to produce a 0.0–1.0 risk score:

| Severity | Weight per finding |
|----------|-------------------|
| Info | 0.02 |
| Warning | 0.10 |
| Critical | 0.35 |

**Pass/fail criteria:** A scan fails if it has **any critical finding** OR the risk score is ≥ 0.5.

---

## 2. Scan Gates and Enforcement

### Scan-before-enable

Non-bundled skills **must** have a passing scan result before `enable_for_project()` allows them to be enabled. Attempting to enable a skill without a scan or with a failed scan returns HTTP 403 with the reason.

Bundled skills (shipped in the `skills/` directory of the repo) are trusted and exempt from the scan gate.

### Scan persistence with content hashing

Scan results are persisted to `data/skills/.scan_results/{skill_name}.json` as JSON, tagged with a SHA-256 hash of the skill's content. When the registry loads, it checks the stored hash against the current content — if any file in the skill directory has changed, the cached scan result is invalidated and the skill must be re-scanned. This means scan results survive server restarts but automatically expire when skill files are modified.

### Security override

For cases where a legitimate skill fails the scan (e.g., a known-safe script that uses `eval()`), an override mechanism exists:

1. The admin sets a security override password in Settings, stored in the encrypted vault as `skill_security_override_password`
2. The frontend sends the password with a force-enable request
3. The backend verifies the password against the vault value
4. If correct, the skill is enabled despite the failed scan
5. The override is logged as a `skill.override_used` security event (WARNING level)
6. Failed override attempts are logged as `skill.override_failed`

**API:** `PUT /api/skills/{name}/toggle` with body `{ "enabled": true, "project_id": "...", "force": true, "override_password": "..." }`

---

## 3. Quarantine System

Skills that fail the security scan are automatically moved to a quarantine directory (`data/skills/.quarantine/`).

### Quarantine flow

- **Auto-quarantine:** When a per-skill scan fails, the skill is moved from `data/skills/` to `data/skills/.quarantine/` and disabled. Logged as `skill.quarantined` event.
- **Manual quarantine:** `POST /api/skills/{name}/quarantine` — manually quarantine a skill. Logged.
- **Restore (unquarantine):** `POST /api/skills/{name}/unquarantine` — moves the skill back to `data/skills/` and reloads the registry. Checks for name collision with bundled skills (returns 409 Conflict if the name collides). Logged as `skill.unquarantined`.
- **List quarantined:** `GET /api/skills/quarantine/list` — returns all quarantined skills with metadata.

### Bundled skill protection

Bundled skills live in the repo directory and cannot be physically moved to quarantine. If a bundled skill fails a scan, it is flagged in the registry but not quarantined.

---

## 4. Anti-Spoofing Protections

### Name masquerade prevention

The registry maintains a `_bundled_names` set tracking all bundled skill names. User-installed skills with names that collide with bundled skills are blocked at load time with a logged warning (`skill.name_collision_blocked`). The `is_bundled_name()` check also prevents restoring a quarantined skill if its name collides with a bundled skill.

### Bundled flag spoofing prevention

The `is_bundled` flag on a loaded skill is set **exclusively** by the registry loader based on the source directory (`skills/` = bundled, `data/skills/` = user-installed). The flag is never read from `skill.json` content, preventing imported skills from claiming bundled status to bypass scan gates.

---

## 5. Runtime Sandbox (`backend/skills/executor.py`)

Even after passing the scanner, skill scripts execute under strict runtime constraints.

### Subprocess isolation

Scripts are **never** imported into the backend Python process. They always run as subprocesses via `asyncio.create_subprocess_exec()` — no `shell=True`, preventing shell injection.

### Environment filtering

Only a safe allowlist of environment variables is passed to the subprocess:
`PATH`, `HOME`, `USER`, `LANG`, `LC_ALL`, `PYTHONPATH`, `NODE_PATH`, `TERM`

Additional variables are only passed if they match vault secrets the skill has declared in its `permissions.vault_secrets` list.

### Path traversal prevention

Before execution, the script path is resolved and validated to ensure it is inside the skill directory. Attempted traversal (e.g., `../../etc/passwd`) is blocked and logged as a `skill.path_traversal_blocked` CRITICAL event.

### Execution limits

| Limit | Default | Maximum |
|-------|---------|---------|
| Timeout | 30 seconds | 300 seconds |
| Output size | — | 512 KB (stdout + stderr truncated) |

On timeout, the process is killed and the event is logged.

### Interpreter detection

The executor determines the interpreter from the file extension (`.py` → `python3`, `.js` → `node`, `.ts` → `npx tsx`, `.sh` → `bash`). No user-controlled interpreter selection.

---

## 6. Security Audit Log (`backend/security_log.py`)

All security-relevant events are written to `data/logs/security.log` as structured JSON lines (one JSON object per line). Events are also emitted to stdout via the standard Python logging system.

### Event Categories

#### Authentication

| Event | Level | Fields |
|-------|-------|--------|
| `auth.login_success` | INFO | `ip` |
| `auth.login_failure` | WARNING | `ip`, `reason` |

#### Skill Scanning

| Event | Level | Fields |
|-------|-------|--------|
| `skill.scan_passed` | INFO | `skill`, `risk`, `findings` |
| `skill.scan_failed` | WARNING | `skill`, `risk`, `findings` |
| `skill.scan_all` | INFO | `passed`, `failed`, `errors` |

#### Skill Enable/Disable

| Event | Level | Fields |
|-------|-------|--------|
| `skill.enabled` | INFO | `skill`, `project` |
| `skill.disabled` | INFO | `skill`, `project` |
| `skill.override_used` | WARNING | `skill`, `project` |
| `skill.override_failed` | WARNING | `skill`, `reason` |

#### Quarantine

| Event | Level | Fields |
|-------|-------|--------|
| `skill.quarantined` | WARNING | `skill`, `reason` |
| `skill.unquarantined` | INFO | `skill` |

#### Skill Deletion

| Event | Level | Fields |
|-------|-------|--------|
| `skill.deleted` | INFO/WARNING | `skill`, `is_bundled` |

#### Anti-Spoofing

| Event | Level | Fields |
|-------|-------|--------|
| `skill.name_collision_blocked` | WARNING | `skill`, `reason` |

#### Executor Sandbox

| Event | Level | Fields |
|-------|-------|--------|
| `skill.execution_start` | INFO | `skill`, `script` |
| `skill.execution_timeout` | WARNING | `skill`, `script`, `timeout` |
| `skill.execution_failed` | WARNING | `skill`, `script`, `exit_code` |
| `skill.path_traversal_blocked` | CRITICAL | `skill`, `path` |

#### Vault / Secrets

| Event | Level | Fields |
|-------|-------|--------|
| `vault.secret_set` | INFO | `key` |
| `vault.secret_deleted` | INFO | `key` |

#### Settings

| Event | Level | Fields |
|-------|-------|--------|
| `settings.updated` | INFO | `changed_keys` |

### Log Format

Each line is a JSON object with at minimum:

```json
{
  "ts": "2026-04-06T12:34:56.789Z",
  "event": "skill.scan_failed",
  "level": "WARNING",
  "skill": "example-skill",
  "risk": 0.85,
  "findings": 12
}
```

### Log Location

`{data_dir}/logs/security.log` — the file handler is lazily initialized on the first security event. The log directory is created automatically if it doesn't exist.

---

## 7. Frontend Security UI

### Security Dashboard (`SkillScanDashboard.jsx`)

Accessible via the **Security** tab on the Skills page. Shows:
- Summary cards: total skills, passed, failed, unscanned counts
- Skills table sorted by severity with scan status badges
- Quarantine section with restore buttons
- Bulk "Scan All" action

### Per-Skill Scan UI

Each skill card in the Library tab shows a `ScanBadge` component indicating scan status (clean / warnings / failed / unscanned). Each card has a "Scan" button to trigger a per-skill scan, and an expandable `ScanResults` panel showing individual findings color-coded by severity.

### API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/skills/scan/all` | POST | Bulk scan all registered skills |
| `/api/skills/scan/summary` | GET | Dashboard summary data |
| `/api/skills/{name}/scan` | POST | Scan a specific skill |
| `/api/skills/{name}/scan` | GET | Get stored scan results |
| `/api/skills/quarantine/list` | GET | List quarantined skills |
| `/api/skills/{name}/quarantine` | POST | Manually quarantine |
| `/api/skills/{name}/unquarantine` | POST | Restore from quarantine |
| `/api/skills/security/override-status` | GET | Check if override password is configured |

---

## 8. Known Gaps (Planned for Future Phases)

These security features are declared in the `skill.json` schema but not yet enforced at runtime:

1. **Network domain restrictions** (`permissions.network_domains`) — the manifest declares allowed domains but the subprocess proxy to enforce them is not yet implemented
2. **Memory tier permission enforcement** (`permissions.memory_tiers`) — declared in the model but not enforced during skill execution
3. **File path sandboxing** (`permissions.file_paths`) — workspace glob patterns are declared but not enforced beyond the basic path traversal check in the executor
