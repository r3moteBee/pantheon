# Pantheon Skills System — Implementation Plan

## Executive Summary

Add a first-class Skills system to Pantheon: a **Skills Library** for importing, scanning, and managing reusable agent capabilities, and a **Skill Editor** with AI-assisted authoring. Skills extend what the agent can do without modifying core code — think of them as installable "recipes" the agent can follow when the right situation arises.

---

## 1. What Is a Skill in Pantheon?

A skill is a self-contained bundle that teaches the agent *how* to do something. It differs from the existing hardcoded tools (which are fixed Python functions) by being user-authored, dynamically loaded, and editable at runtime.

Each skill is a directory:

```
web-scraper/
├── skill.json            # Metadata, triggers, parameters, security manifest
├── instructions.md       # Natural-language procedure the agent follows
├── scripts/              # Optional helper scripts (Python, bash, JS)
│   └── scrape.py
├── references/           # Reference docs loaded on demand
│   └── api_guide.md
└── assets/               # Templates, icons, sample files
    └── template.html
```

### skill.json Schema

```json
{
  "name": "web-scraper",
  "version": "1.0.0",
  "description": "Scrape structured data from web pages...",
  "author": "brent",
  "license": "MIT",
  "triggers": [
    "scrape a website", "extract data from a page",
    "pull content from URL"
  ],
  "parameters": [
    { "name": "url", "type": "string", "required": true, "description": "Target URL" },
    { "name": "selector", "type": "string", "required": false, "description": "CSS selector" }
  ],
  "capabilities_required": ["network", "file_write"],
  "dependencies": { "python": ["httpx", "beautifulsoup4"] },
  "tags": ["web", "scraping", "data-extraction"],
  "source_hub": null,
  "security_scan": null,

  "pantheon": {
    "memory": {
      "reads": ["semantic", "episodic"],
      "writes": ["semantic"],
      "auto_store": true
    },
    "project_aware": true,
    "schedulable": {
      "enabled": true,
      "default_cron": null,
      "description": "Scrape target on a schedule and store results"
    },
    "permissions": {
      "network_domains": ["*.example.com"],
      "file_paths": ["workspace/scraped/**"],
      "vault_secrets": ["SCRAPER_API_KEY"],
      "memory_tiers": { "semantic": "rw", "episodic": "r", "graph": "none" }
    },
    "telemetry": {
      "track_usage": true,
      "auto_disable_threshold": 5
    }
  }
}
```

### Pantheon Extensions (the `pantheon` block)

The top-level fields (`name`, `description`, `triggers`, `parameters`, `capabilities_required`, `dependencies`, `tags`) follow standard conventions compatible with SKILL.md, MCP, and ClawHub imports. The `pantheon` block is entirely optional — imported skills work without it, and Pantheon adds sensible defaults. But skills authored in Pantheon can unlock capabilities no other host provides:

#### Memory Integration

```json
"memory": {
  "reads": ["semantic", "episodic"],
  "writes": ["semantic"],
  "auto_store": true
}
```

Skills can declare which memory tiers they consume and produce. This is the single biggest differentiator — a "weekly report" skill can read episodic memory to summarize recent conversations, a "research" skill can write findings to semantic memory for future recall, and a "relationship mapper" skill can build entities in graph memory.

`auto_store: true` means the skill's outputs are automatically fed through the existing extraction pipeline (the same one that runs after `consolidate_memory`), so knowledge gained during skill execution flows into the memory system without the skill author needing to handle it.

When the scanner evaluates an imported skill, it checks whether the skill's scripts actually access memory APIs. If a skill declares `"writes": ["semantic"]` but its code also touches graph memory, that's a finding.

#### Project Context Awareness

```json
"project_aware": true
```

When enabled, the skill resolver passes project context to the skill at execution time: the active personality profile, workspace file listing, project metadata, and recent episodic memory. This lets a single "code review" skill behave differently for a Python project vs a React project without the author writing separate skills. The skill's instructions.md can reference `{{project.personality}}`, `{{project.files}}`, and `{{project.recent_context}}` as template variables that get populated at runtime.

#### Autonomous Scheduling

```json
"schedulable": {
  "enabled": true,
  "default_cron": "0 9 * * 1",
  "description": "Scrape target every Monday at 9am and store results"
}
```

Skills that declare themselves as schedulable appear in the task scheduler UI with a one-click "Schedule This" button. The agent can also schedule them via the existing `create_task` tool. When a scheduled skill fires, it runs autonomously (using `run_autonomous` in agent core) with the skill's instructions injected. Results can be pushed to Telegram via the existing integration.

No other skill format supports this. SKILL.md and MCP skills are purely reactive — they respond to user prompts. Pantheon skills can be proactive.

#### Granular Permissions

```json
"permissions": {
  "network_domains": ["*.example.com", "api.github.com"],
  "file_paths": ["workspace/scraped/**"],
  "vault_secrets": ["SCRAPER_API_KEY"],
  "memory_tiers": { "semantic": "rw", "episodic": "r", "graph": "none" }
}
```

Standard skill formats use coarse capability flags (`"network"`, `"file_write"`). Pantheon's permission model is fine-grained:

- **network_domains**: Allowlist of domains the skill's scripts can reach. The executor's subprocess proxy blocks all other outbound connections. This prevents a "web scraper" skill from phoning home to an attacker's server.
- **file_paths**: Glob patterns relative to the project workspace. Scripts can only read/write within these paths.
- **vault_secrets**: Named secrets from Pantheon's encrypted vault that the skill is allowed to access. A skill that needs an API key declares it here; the user approves on install. Other vault entries remain invisible to the skill.
- **memory_tiers**: Per-tier read/write/none permissions. A skill that only needs to search semantic memory can't silently write to episodic memory to poison the conversation history.

The scanner validates that declared permissions match actual code behavior. Undeclared access attempts are blocked at runtime and logged.

#### Skill Telemetry

```json
"telemetry": {
  "track_usage": true,
  "auto_disable_threshold": 5,
  "effectiveness_review": {
    "enabled": true,
    "review_after_n": 10,
    "store_executions": true
  }
}
```

Pantheon tracks per-skill metrics across three tiers:

**Tier 1 — Mechanical Metrics (automatic, zero-cost)**
- **Activation count**: How often the resolver matches this skill to a user message.
- **Completion rate**: Did the agent follow the skill's instructions to the end, or bail out partway? Tracked by monitoring whether the agent's response includes the skill's expected output patterns vs abandoning the skill flow.
- **Execution cost**: Tokens consumed, tool calls made, wall-clock time. Stored per invocation so you can spot skills that are getting more expensive over time.
- **False positive rate**: The skill activated but the user's intent didn't match. Detected when the agent abandons the skill mid-execution, or when the user immediately redirects ("no, I meant...").
- **False negative rate**: The user explicitly invoked a skill the resolver didn't suggest, or manually re-triggered after the resolver skipped it. Tracked by comparing resolver suggestions vs actual skill usage.

**Tier 2 — Conversational Signals (automatic, heuristic)**

After a skill executes, Pantheon analyzes the next 2-3 turns of conversation using the existing extraction pipeline:

- **Positive signals**: User says "thanks", "perfect", "great"; user builds on the result with follow-up questions; user shares the output externally (detected via file download or copy).
- **Negative signals**: User immediately retries with rephrased input; user explicitly corrects the output ("no, change X to Y"); user abandons the conversation thread.
- **Neutral**: User moves to an unrelated topic (skill may have been fine but not remarkable).

These signals produce a rolling **satisfaction score** (0.0–1.0) stored in the skill's telemetry record. The score weights recent invocations more heavily than old ones, so a skill that improves over time (after trigger tuning or instruction edits) reflects that.

**Tier 3 — Periodic AI Review (opt-in, deeper)**

When `effectiveness_review.enabled` is true and the skill has accumulated `review_after_n` executions, Pantheon runs an automated review using the agent's LLM:

1. Pull the last N execution records from episodic memory (input message, skill instructions used, agent output, user response).
2. Ask the LLM to evaluate: "Given these executions, is this skill consistently producing good results? Are there patterns in what works vs what doesn't? Should the triggers, instructions, or parameters be adjusted?"
3. Store the review as a structured report attached to the skill, visible in the library UI.
4. If the review identifies actionable improvements, surface them as suggestions in the editor: "The AI review suggests adding a step for edge case X — 3 of the last 10 executions stumbled on it."

This closes the loop: skills don't just run — they learn from how they're used, and surface improvement suggestions back to the author.

**Auto-Disable and Alerting**

`auto_disable_threshold` is the number of consecutive false positives before the skill is automatically disabled with a notification to the user. This prevents poorly-triggered skills from degrading the experience. The satisfaction score also feeds into this: a skill whose score drops below 0.3 over 10+ invocations gets flagged (not auto-disabled, but highlighted in the library with a "needs attention" badge).

The user can review all telemetry in the library UI — a per-skill dashboard showing activation history, satisfaction trend, cost trend, and AI review reports — and decide whether to tune triggers, edit instructions, or disable.

#### Skill Evolution (per-skill opt-in)

```json
"evolution": {
  "enabled": false,
  "locked": false,
  "mode": "propose",
  "require_approval": true,
  "max_version_depth": 10,
  "evolve_scope": ["instructions", "triggers", "parameters"]
}
```

**Evolution is off by default and must be explicitly enabled per skill.** This is a deliberate design choice — many skills need to execute exactly as defined, every time. A compliance workflow, a data processing pipeline with validated steps, or any skill where predictability matters more than optimization should remain static. The user decides which skills are candidates for evolution and which are not.

Two levels of protection:

- `"enabled": false` (default) — Evolution is off. The agent executes the skill as written. No deviation tracking, no proposals. The user can turn it on later through the library UI or editor.
- `"locked": true` — Evolution is permanently disabled for this skill. The toggle is hidden in the UI to prevent accidental enabling. Unlocking requires editing `skill.json` directly — an intentional friction point. Use this for skills where exact execution is a hard requirement (compliance, security procedures, regulated workflows).

When `evolution.enabled` is true, skills become living documents that improve through use. The agent actively monitors its own execution against the skill's instructions and proposes improvements when it discovers better approaches.

**How it works:**

1. **Deviation Detection** — During skill execution, the agent's working memory tracks which parts of `instructions.md` it followed literally, which parts it adapted, and which parts it skipped entirely. After execution completes, a lightweight comparison runs: "Did I deviate from the skill's instructions? Was the deviation intentional and beneficial?"

2. **Improvement Proposal** — If the agent deviated and the outcome was positive (user satisfaction signal from Tier 2), it drafts a proposed change. This isn't a raw diff — it's a structured proposal:

   ```json
   {
     "skill": "web-scraper",
     "version": "1.0.0",
     "proposed_version": "1.1.0",
     "trigger": "Agent found more effective approach during execution",
     "changes": [
       {
         "section": "instructions.md#step-3",
         "type": "refinement",
         "original": "Use CSS selectors to extract the target elements...",
         "proposed": "First check if the page provides a JSON-LD structured data block (faster and more reliable than CSS selectors). Fall back to CSS selectors only if structured data is absent...",
         "rationale": "In 4 of the last 6 executions, the target page had structured data available. Checking for it first reduced execution time by ~40% and produced cleaner output.",
         "evidence": ["exec-2024-04-01-a", "exec-2024-04-02-b", "exec-2024-04-03-a"]
       }
     ],
     "telemetry_context": {
       "executions_since_last_evolution": 6,
       "satisfaction_before": 0.72,
       "projected_satisfaction": 0.85
     }
   }
   ```

3. **User Approval** — When `require_approval` is true (the default and strongly recommended), the agent presents the proposal in conversation: "I've been running the web-scraper skill and noticed a pattern — most target pages have structured data I can use instead of CSS selectors. This would make the skill faster and more reliable. Want me to update it?" The user sees the before/after diff and approves, rejects, or modifies.

4. **Version Control** — Every evolution creates a new version. The skill directory maintains a `versions/` subdirectory with snapshots:

   ```
   web-scraper/
   ├── skill.json
   ├── instructions.md          # current (v1.2.0)
   └── versions/
       ├── 1.0.0/
       │   ├── instructions.md
       │   └── changelog.md     # "Initial version"
       ├── 1.1.0/
       │   ├── instructions.md
       │   └── changelog.md     # "Added JSON-LD check before CSS selectors"
       └── 1.2.0/
           └── changelog.md     # "Refined error handling for rate-limited pages"
   ```

   `max_version_depth` controls how many old versions to keep (oldest are pruned). Rollback is one click in the library UI.

5. **Validation** — After an evolution is applied, the telemetry system tracks whether the change actually helped. If satisfaction drops after an evolution, the system flags it: "The last update to web-scraper may have caused a regression — satisfaction dropped from 0.72 to 0.58. Would you like to roll back?" This prevents runaway degradation from well-intentioned but wrong improvements.

**Evolution modes:**

| Mode | Behavior |
|------|----------|
| `propose` | Agent drafts proposals but never applies them without user approval. Safest option. |
| `auto_minor` | Agent can auto-apply minor refinements (wording clarity, trigger additions) but proposes structural changes for approval. Good for mature skills. |
| `auto` | Agent can auto-apply all changes. Only recommended for personal utility skills with high execution volume where manual approval is a bottleneck. Telemetry validation still catches regressions. |

**What can evolve:**

The `evolve_scope` array controls what the agent is allowed to propose changes to:

- `"instructions"` — The core procedure in `instructions.md`. Most common evolution target.
- `"triggers"` — Trigger phrases in `skill.json`. The agent notices when users phrase requests in ways the triggers don't cover and proposes additions.
- `"parameters"` — Parameter definitions. The agent notices when it frequently needs an input the skill doesn't declare and proposes adding it.
- `"scripts"` — Helper scripts. More sensitive — script changes go through the security scanner before approval. Not included by default.
- `"permissions"` — The `pantheon.permissions` block. Never auto-applied regardless of mode. Permission escalation always requires explicit user approval.

**What can't evolve (hard constraints):**

- The skill's `name` and core identity never change through evolution.
- Permission escalation (requesting new vault secrets, new network domains, new memory tiers) always requires explicit approval, even in `auto` mode.
- Script modifications always re-trigger the security scanner.
- Evolution proposals are rejected if the proposed change would break backward compatibility with the skill's declared parameters (existing callers shouldn't break).

**Where this connects to Tier 3 reviews:**

The periodic AI review (Tier 3 telemetry) and skill evolution are complementary. The review identifies patterns across many executions and suggests improvements at a high level ("this skill struggles with edge case X"). Evolution captures real-time insights from individual executions ("I just found a better way to do step 3"). Both feed proposals into the same approval flow in the UI.

Over time, a skill imported from ClawHub with bare-bones instructions can evolve into a Pantheon-native skill with rich memory integration, optimized triggers, and battle-tested instructions — all driven by actual usage rather than manual authoring.

### Compatibility Strategy

The two-layer approach keeps imports clean:

| Scenario | What happens |
|----------|-------------|
| Import SKILL.md from SkillsMP | Frontmatter → top-level fields. No `pantheon` block. Defaults apply (no memory access, not schedulable, broad sandbox). |
| Import MCP tool from Smithery | Tool schema → `parameters` + `capabilities_required`. Adapter generates `instructions.md` from tool description. No `pantheon` block. |
| Import from ClawHub | OpenClaw format → normalized. No `pantheon` block. Full scanner pipeline mandatory. |
| Author in Pantheon editor | AI assistant suggests appropriate `pantheon` extensions based on what the skill does. Memory integration, scheduling, and permissions are configured through the editor UI, not hand-written JSON. |
| Export from Pantheon | Graduated export — see Export Strategy below. |

Imported skills get progressively enhanced: install first with defaults, then the user (or AI assistant) can add `pantheon` extensions to unlock deeper integration. The editor makes this easy — "This skill reads conversation history. Would you like to give it access to episodic memory?"

### Export Strategy

Exporting a Pantheon skill is not a binary strip-or-block decision. The exporter analyzes how deeply the skill depends on Pantheon-specific features and presents the user with a clear report before proceeding.

#### Dependency Analysis

When the user clicks "Export," the exporter scans the skill for Pantheon extension usage:

1. **Scan `skill.json`** — catalog every field in the `pantheon` block (memory tiers, permissions, scheduling, evolution, telemetry).
2. **Scan `instructions.md`** — detect references to Pantheon-specific capabilities: template variables (`{{project.personality}}`, `{{project.files}}`), memory operations ("recall from episodic memory," "store in semantic memory"), scheduling language, etc.
3. **Scan scripts** — check for Pantheon API calls (memory endpoints, vault access, project context).
4. **Classify each dependency** as either **supplementary** (skill works without it, just loses a feature) or **structural** (instructions break without it).

#### Export Modes

| Mode | When to use | What happens |
|------|-------------|-------------|
| **Pantheon-to-Pantheon** | Sharing with another Pantheon instance | Full export including `pantheon` block, versions, telemetry config. Everything transfers. |
| **Portable (clean)** | Skill has no structural Pantheon dependencies | Strip `pantheon` block. Output a standard SKILL.md bundle. The exporter notes which supplementary features were removed ("Memory auto-store and telemetry will not be available on the target platform"). |
| **Portable (adapted)** | Skill has structural Pantheon dependencies | The exporter flags the specific instructions/steps that won't work and offers AI-assisted rewriting. Example: "Step 2 says 'Recall all episodic memories from the last 7 days.' This depends on Pantheon's memory system. Want me to rewrite this step for a platform without memory access?" The AI rewrites those steps to be self-contained (e.g., replacing memory recall with a prompt to the user: "Please provide a summary of recent context"). The user reviews and approves the adapted version before export. |

#### Export Report

Before any export is finalized, the user sees a report:

```
Export Analysis: weekly-report v1.3.0
─────────────────────────────────────

Pantheon features used:
  ✓ memory.reads: [episodic, semantic]     → STRUCTURAL — instructions reference memory recall in steps 1, 3
  ✓ memory.writes: [semantic]              → supplementary — auto_store, skill works without it
  ✓ schedulable                            → supplementary — removed (target platform handles scheduling)
  ✓ project_aware                          → STRUCTURAL — step 2 uses {{project.personality}}
  ✓ evolution                              → supplementary — removed
  ✓ permissions.vault_secrets              → supplementary — removed (no vault on target)

Export mode: Portable (adapted)
  → 2 steps need rewriting (steps 1, 2)
  → AI has prepared adapted versions — review below

[Review Adaptations]  [Export as Pantheon Bundle]  [Cancel]
```

This gives the user full visibility. They can choose to export the adapted version, export the full Pantheon bundle for another Pantheon instance, or cancel and keep the skill Pantheon-only.

#### Skills That Shouldn't Export

Some skills are so deeply integrated with Pantheon that adaptation would strip away their core value — a "memory consolidation" skill that exists solely to reorganize episodic memory, for example. In these cases, the exporter shows the analysis but recommends against portable export: "This skill's entire purpose depends on Pantheon's memory system. Exporting a portable version would require rewriting 90% of the instructions. Consider exporting as a Pantheon bundle instead."

The user can still force a portable export if they want — Pantheon doesn't block it — but the recommendation is clear.

### How Skills Differ from Existing Tools

| Aspect | Current Tools | Skills |
|--------|--------------|--------|
| Defined in | Hardcoded `tools.py` | User-authored directories |
| Added by | Code change + redeploy | Import or create at runtime |
| Instructions | Python function body | Natural-language markdown + optional scripts |
| Trigger | LLM sees JSON schema | LLM matches description/triggers to user intent |
| Editable | No (requires dev) | Yes, via UI editor |
| Sandboxed | Runs in backend process | Runs in restricted subprocess |

---

## 2. Architecture

### 2.1 Backend Components

```
backend/
├── skills/
│   ├── models.py          # Pydantic models: SkillManifest, ScanResult, etc.
│   ├── registry.py        # Load/list/enable/disable skills from disk
│   ├── resolver.py        # Match user intent → relevant skills (embedding + keyword)
│   ├── executor.py        # Run a skill's scripts in a sandboxed subprocess
│   ├── scanner.py         # Security analysis pipeline
│   ├── importer.py        # Fetch from hubs, unpack, scan, install
│   └── editor.py          # AI-assisted skill authoring helpers
├── api/
│   └── skills.py          # REST endpoints for CRUD, import, scan, test
```

### 2.2 Data Storage

```
data/
└── skills/
    ├── _index.json         # Skill registry index (name, version, enabled, scan status)
    ├── web-scraper/        # Installed skill directories
    ├── report-writer/
    └── .quarantine/        # Skills that failed security scan
```

### 2.3 Frontend Components

```
frontend/src/
├── pages/
│   └── Skills.jsx          # Main skills page (library + editor tabs)
├── components/
│   ├── SkillLibrary.jsx    # Browse, search, enable/disable installed skills
│   ├── SkillImporter.jsx   # Import from hub URL or upload .tar.gz
│   ├── SkillScanner.jsx    # Security scan results display
│   ├── SkillEditor.jsx     # Monaco-based editor with AI assist panel
│   ├── SkillTester.jsx     # Test a skill with sample prompts
│   └── SkillPicker.jsx     # Autocomplete dropdown for "/" invocations in chat
```

The chat input (`Chat.jsx`) is extended with:
- `/` prefix detection → opens `SkillPicker` autocomplete with installed skill names
- **Auto-Skill** toggle in the chat header bar alongside Personality and Focus controls

### 2.4 Skill Invocation Modes

Skills can be invoked two ways: **explicitly** by the user, or **automatically** by the agent. The user controls which mode is active via a chat UI toggle.

#### Explicit Invocation

The user calls a skill by name using a `/` prefix in the chat input:

```
/web-scraper https://example.com/products
/weekly-report
/code-review backend/api/chat.py
```

This always works regardless of toggle state. The agent receives the skill's instructions and follows them for that turn. The chat input provides autocomplete for installed skill names (filtered to skills enabled for the active project).

If the user types `/` alone, a skill picker appears showing all available skills with descriptions, grouped by tag. This doubles as a discovery mechanism — users can browse what's installed without leaving the chat.

#### Auto-Discovery (toggle-controlled)

When the **Auto-Skill** toggle is on, the resolver runs on every user message, checking whether any enabled skill matches the user's intent. If it finds a match, the skill's instructions are injected into the agent's context for that turn. The agent can also mention which skill it's using: *"I'm using the web-scraper skill to extract this data."*

When the toggle is off, the resolver is completely bypassed. The agent uses only its built-in tools and any skills the user explicitly invokes with `/`. This gives the user full certainty about when skills are and aren't influencing the agent's behavior.

#### Chat UI Toggle

The toggle sits alongside the existing Personality Weight and Context Focus controls in the chat header:

```
[Personality: balanced ▾] [Focus: balanced ▾] [Auto-Skill: off ▾]
```

Three states, cycled on click (matching the existing toggle UX pattern):

| State | Behavior |
|-------|----------|
| **Off** | No auto-discovery. Skills only fire via explicit `/` invocation. Default for new projects. |
| **Suggest** | Resolver runs and the agent mentions relevant skills ("I could use the web-scraper skill for this — want me to?") but doesn't activate them without confirmation. Good for learning what skills are available. |
| **Auto** | Resolver runs and the agent uses matching skills automatically. Best for users with a tuned skill library who trust their trigger definitions. |

The toggle state is stored per-project (via the existing project settings mechanism), so a research project can have auto-discovery on while a compliance project keeps it off.

#### Backend Integration

The agent's tool loop in `core.py` currently iterates on hardcoded `TOOL_SCHEMAS`. The integration point depends on invocation mode:

**Explicit (`/skill-name`):**
1. Chat endpoint detects the `/` prefix, looks up the skill in the registry.
2. Skill instructions are prepended to the system prompt for that turn.
3. If the skill has callable scripts, they're added to `TOOL_SCHEMAS` for that turn.
4. The user's message (minus the `/skill-name` prefix) is passed to the agent.

**Auto-discovery (when toggle is on):**
1. Before the LLM call, the **Skill Resolver** checks the user message against enabled skills using embedding similarity on descriptions + keyword matching on triggers.
2. For **Suggest** mode: matched skills are noted in the system prompt as available but the agent is instructed to ask before using them.
3. For **Auto** mode: matched skill instructions are injected directly into the system prompt (similar to how memory recall works today).
4. If a skill includes callable scripts, they're exposed as additional tool schemas for that turn.

Script execution always goes through `executor.py` which runs in a subprocess with restricted capabilities.

This approach avoids changing the core LLM loop — skills are "soft tools" that work through prompt augmentation, with optional "hard tool" script endpoints. The toggle simply controls whether the resolver runs automatically or only responds to explicit invocations.

#### API Support

```
POST /api/chat  →  { "message": "/web-scraper https://example.com", ... }
```

The chat endpoint parses skill invocations from the message. For API consumers who don't use the web UI, explicit invocation via `/` in the message body works the same way. Auto-discovery is controlled by a `skill_discovery` field in the project settings API:

```
PATCH /api/projects/:id  →  { "settings": { "skill_discovery": "off" | "suggest" | "auto" } }
```

---

## 3. Skills Library

### 3.1 Hub Import Flow

```
User pastes hub URL or searches
       │
       ▼
┌─────────────────┐
│  Fetch Manifest  │  ← Pull skill.json from hub API
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Download Bundle │  ← .tar.gz or git clone
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Security Scan   │  ← Static analysis + AI review
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
  PASS      FAIL
    │         │
    ▼         ▼
 Install   Quarantine
 & Index   + Show Report
```

### 3.2 Supported Hubs

Build adapter interfaces for the major registries:

| Hub | Format | Adapter Strategy |
|-----|--------|-----------------|
| Smithery (MCP) | MCP server packages | Parse MCP tool defs → convert to skill.json |
| ClawHub (OpenClaw) | Proprietary ClawHub format | Parse OpenClaw skill def → skill.json. **High-risk source** — 1,184 confirmed malicious skills found in audit; scanner Layer 3 (AI review) is mandatory for ClawHub imports |
| SkillsMP / SkillsLLM | SKILL.md bundles | Parse frontmatter → skill.json, copy instructions.md |
| GitHub repos | Varies | Clone, detect format, convert |
| Local upload | .tar.gz / .zip | Unpack and validate |

Each adapter implements:
```python
class HubAdapter(ABC):
    async def search(self, query: str) -> list[HubResult]
    async def fetch(self, identifier: str) -> Path  # returns temp dir
    def detect_format(self, path: Path) -> SkillFormat
    def normalize(self, path: Path) -> SkillManifest
```

### 3.3 Library UI

The library view shows installed skills as cards with:

- Name, description, author, version, tags
- Enable/disable toggle
- Security scan badge (passed / warning / failed / unscanned)
- Last used timestamp
- Quick actions: Edit, Test, Delete, Re-scan

Search/filter by tags, scan status, or free text. An "Import" button opens the importer modal.

---

## 4. Security Scanner

This is the critical differentiator. Every imported skill goes through a multi-layer scan before activation.

### 4.1 Scan Pipeline

```
Layer 1: Static Analysis (fast, deterministic)
├── File type whitelist (reject binaries, executables)
├── Script pattern matching (detect shell injection, network calls, file ops)
├── Dependency audit (check packages against known-vulnerability DBs)
├── Size/complexity limits (reject suspiciously large bundles)
└── Manifest validation (required fields, sane values)

Layer 2: Capability Analysis (medium, heuristic)
├── Map declared capabilities_required vs actual code behavior
├── Detect undeclared network access, file system access, env var reads
├── Flag credential/secret patterns (API keys, tokens, passwords)
└── Check for obfuscated code (base64 blobs, eval(), exec())

Layer 3: AI Review (slower, semantic)
├── LLM analyzes each script file for malicious intent
├── Cross-references instructions.md claims vs script behavior
├── Generates human-readable risk assessment
└── Assigns risk score: low / medium / high / critical
```

### 4.2 Scan Result Model

```python
class ScanResult(BaseModel):
    skill_name: str
    scan_timestamp: datetime
    overall_risk: Literal["low", "medium", "high", "critical"]
    passed: bool
    layers: dict  # detailed results per layer
    findings: list[Finding]  # individual issues found
    ai_summary: str  # human-readable assessment
    recommendations: list[str]
```

### 4.3 Capability Sandboxing at Runtime

Even after passing the scan, skills run with restricted capabilities:

- **Network**: Only if `capabilities_required` includes `"network"`, and only to user-approved domains
- **File system**: Confined to the project's workspace directory
- **Subprocess**: Scripts run via `subprocess.run()` with timeout, memory limit, and no shell expansion
- **Secrets**: Skills cannot access the vault unless explicitly granted per-skill
- **No eval/exec**: Python scripts are run as subprocesses, never imported into the backend process

---

## 5. Skill Editor with AI Assistance

### 5.1 Editor UI

Split-pane layout:

```
┌─────────────────────────────────────────────────┐
│  [Skills Library]  [Skill Editor]  [Test]        │
├────────────────────────┬────────────────────────┤
│                        │                        │
│   File Tree            │   AI Assistant Panel   │
│   ├── skill.json       │                        │
│   ├── instructions.md  │   "What kind of skill  │
│   ├── scripts/         │    do you want to      │
│   └── references/      │    create?"            │
│                        │                        │
│   ─── Editor ───────── │   [Suggestions]        │
│   (Monaco/CodeMirror)  │   [Trigger Optimizer]  │
│                        │   [Security Check]     │
│                        │   [Test Runner]        │
│                        │                        │
├────────────────────────┴────────────────────────┤
│  Status: Saved │ Scan: Passed │ Last Test: OK    │
└─────────────────────────────────────────────────┘
```

### 5.2 AI Assistant Features

The assistant panel uses the agent's own LLM to help with skill authoring:

1. **Skill Wizard**: Conversational flow to scaffold a new skill from a description.
   - "I want a skill that generates weekly project reports"
   - AI asks clarifying questions, then generates skill.json + instructions.md + any needed scripts

2. **Instruction Improver**: Paste or edit instructions.md, and the AI suggests:
   - Clearer step ordering
   - Edge cases to handle
   - Better trigger phrases for discoverability

3. **Trigger Optimizer**: Given a skill description, generate and test trigger phrases to ensure the agent reliably activates the skill when appropriate (and doesn't activate it when inappropriate).

4. **Script Generator**: Describe what a helper script should do, and the AI writes it with appropriate error handling and security constraints.

5. **Live Security Feedback**: As you edit, the scanner runs in the background and flags issues inline (like a linter).

### 5.3 Skill Testing

The "Test" tab lets you:

- Enter a sample user message
- See which skills the resolver would match
- Run the skill end-to-end in a sandboxed context
- View the agent's response with skill-augmented instructions
- Compare with/without skill results

---

## 6. API Endpoints

```
GET    /api/skills                    # List all installed skills
GET    /api/skills/:name              # Get skill details + scan results
POST   /api/skills                    # Create new skill (from editor)
PUT    /api/skills/:name              # Update skill
DELETE /api/skills/:name              # Remove skill
PATCH  /api/skills/:name/toggle       # Enable/disable

POST   /api/skills/import             # Import from hub URL or upload
POST   /api/skills/:name/scan         # Run security scan
GET    /api/skills/:name/scan         # Get scan results

POST   /api/skills/search-hub         # Search a hub for skills
GET    /api/skills/hubs               # List configured hub sources

POST   /api/skills/:name/test         # Test skill with sample prompt
POST   /api/skills/resolve            # Given a message, return matching skills

POST   /api/skills/ai/scaffold        # AI generates skill from description
POST   /api/skills/ai/improve         # AI improves instructions.md
POST   /api/skills/ai/triggers        # AI generates trigger phrases
```

---

## 7. Skill Discovery for Autonomous Tasks

The Auto-Skill toggle in the chat UI governs interactive sessions where the user is present to course-correct. Autonomous tasks are different — they run unattended, potentially on a schedule, with no user in the loop. Skill discovery for these tasks needs its own explicit decision point.

### Task Creation Prompt

When a task is created — whether through the Tasks UI, the `create_task` tool, or by scheduling a skill — the agent prompts the user to choose a skill discovery policy for that task:

**In chat (via `create_task` tool):**
> "I'm setting up the task 'Generate weekly project report' to run every Monday at 9am. Should this task be allowed to use skills from your library automatically, or should it only use skills you specify?"

**In the Tasks UI:**
The task creation form includes a **Skill Access** dropdown:

| Option | Behavior |
|--------|----------|
| **None** | Task runs with built-in tools only. No skills. Safest for simple, predictable tasks. |
| **Specified only** | User selects specific skills the task is allowed to use. The task can only activate those skills, nothing else. Best for production workflows. |
| **Auto-discover** | Task can use any enabled skill from the project's library if the resolver finds a match. Most flexible, but the task may behave differently as skills are added/removed. |

Default is **None** — the user must actively opt in to skill access for autonomous tasks.

### Per-Task Skill Allow List

When **Specified only** is selected, a skill picker appears showing all skills enabled for the project. The user checks the ones this task should have access to. This produces a task-level allow list stored in the task definition:

```json
{
  "task_id": "abc-123",
  "description": "Generate weekly project report",
  "schedule": "0 9 * * 1",
  "skill_policy": "specified",
  "allowed_skills": ["weekly-report", "data-summarizer"],
  "skill_discovery": false
}
```

For **Auto-discover** mode:

```json
{
  "task_id": "abc-123",
  "skill_policy": "auto",
  "allowed_skills": [],
  "skill_discovery": true
}
```

### Why This Matters

Interactive chat and autonomous tasks have fundamentally different risk profiles. In chat, the user sees what the agent is doing and can redirect. An autonomous task that unexpectedly picks up a new skill (because someone imported it into the library yesterday) could produce surprising results, miss its objective, or consume resources the user didn't anticipate.

The per-task policy makes this explicit: "This task uses *these* skills, and only these skills." It also creates a clear audit trail — when reviewing task execution history, you can see exactly which skills were available and which were activated.

### Integration with Existing Task Scheduler

The existing `create_task` tool in `agent/tools.py` and the Tasks API get extended with:

```
POST /api/tasks  →  { ..., "skill_policy": "specified", "allowed_skills": ["weekly-report"] }
```

The `run_autonomous` method in `agent/core.py` checks the task's `skill_policy` before invoking the resolver. If the policy is `none`, the resolver is skipped entirely. If `specified`, only the listed skills are candidates. If `auto`, the full resolver runs against enabled project skills.

---

## 8. Implementation Phases

### Phase 1: Foundation (2-3 weeks)
**Goal: Skills load, work with the agent, and are usable from chat**

Backend:
- [x] `backend/skills/models.py` — Pydantic models for SkillManifest, ScanResult ✅ Full two-layer schema with PantheonExtensions, SkillDiscoveryMode enum, ProjectSkillSettings
- [x] `backend/skills/registry.py` — Load skills from `skills/` (bundled) and `data/skills/` (user-installed), maintain index ✅ Singleton registry with per-project enable/disable, state persistence to `.skill_state.json`
- [x] `backend/skills/resolver.py` — Basic keyword + embedding matching ✅ Keyword scoring with trigger/tag/name/description matching. Embedding matching deferred to Phase 2 (noted in code)
- [x] Integrate resolver into `agent/core.py` — inject matched skill instructions into system prompt ✅ `AgentCore` accepts `skill_context` and `active_skill_name`, injects via `extra_context` param
- [x] `backend/api/skills.py` — CRUD endpoints ✅ GET list, GET detail, PUT toggle, POST reload, GET/PUT discovery mode
- [x] Extend `backend/api/chat.py` — parse `/skill-name` prefix from messages, resolve to skill, inject into agent context ✅ Full integration: explicit `/` invocation, auto-discovery with suggest/auto modes, `skill_active` and `skill_suggestion` WebSocket events
- [x] Add `skill_discovery` to project settings model (`off` / `suggest` / `auto`, default `off`) ✅ Stored via vault per-project, `SkillDiscoveryMode` enum in models, API endpoints in skills.py

Frontend:
- [x] `frontend/pages/Skills.jsx` — Basic library view with enable/disable per project ✅ SkillCard components with expand/collapse, trigger/parameter/instruction detail view, per-project toggle, reload button
- [x] `frontend/components/SkillPicker.jsx` — Autocomplete dropdown triggered by `/` in chat input ✅ Full autocomplete with keyboard nav (↑↓/Tab/Enter/Esc), tag display, query filtering
- [x] Extend `Chat.jsx` — `/` prefix detection opens SkillPicker; show skill name badge when a skill is active in a response ✅ `/` detection triggers SkillPicker, `activeSkillBadge` state shown in UI, `skill_suggestion` notifications
- [x] Add **Auto-Skill** toggle to chat header bar (alongside Personality and Focus toggles), cycling off → suggest → auto ✅ Click-to-cycle toggle with off/suggest/auto states, color-coded (brand for auto, amber for suggest)
- [x] Store Auto-Skill toggle state per project via project settings API ✅ Uses `skillsApi.getDiscovery`/`setDiscovery` backed by per-project vault storage

Bundled Starter Skills (in `skills/` at repo root — shipped with Pantheon):
- [x] `web-research` — Search the web and produce a structured summary with sources
- [x] `code-review` — Review code files for bugs, security, performance, and style (project-aware)
- [x] `summarize-conversation` — Distill conversation into key points, decisions, and action items (reads episodic memory)
- [x] `explain-code` — Explain code in plain language adapted to user's level (project-aware)
- [x] `task-breakdown` — Break complex goals into actionable steps with effort estimates (reads/writes semantic memory)
- [x] `knowledge-capture` — Extract and store key facts and relationships into memory (writes semantic + graph memory)
- [x] `daily-digest` — Summarize recent project activity from memory and workspace (schedulable, reads episodic + semantic)
- [x] `draft-message` — Write professional emails, Slack messages, and announcements (reads semantic memory for context)

These 8 skills cover the key feature dimensions: simple instruction-only skills, project-aware skills, memory-reading skills, memory-writing skills, and a schedulable skill. They provide immediate test coverage for the Skills.jsx library page, `/` invocation in chat, and the Auto-Skill resolver.

Bonus skill (not in original plan):
- [x] `weather` — Weather lookup skill (10 triggers, extra test coverage for resolver)

Additional infrastructure delivered (not in Phase 1 plan but supports skills):
- [x] `backend/api/mcp.py` — Full MCP connections API (list, add, update, remove, tool toggle, test)
- [x] `backend/mcp_client/` — MCP client with `manager.py`, `client.py`, Tavily credit tracking
- [x] `frontend/src/pages/MCPPage.jsx` + `MCPConnections.jsx` — MCP management UI
- [x] `frontend/src/api/client.js` — `skillsApi` with all CRUD + discovery endpoints

---

### Phase 1 Completion Assessment (2026-04-05)

**Status: ✅ PHASE 1 COMPLETE — Ready to proceed to Phase 2**

All 12 backend + frontend checklist items are implemented and all 8 planned bundled skills (plus 1 bonus) are shipped. The implementation matches the plan spec closely:

| Category | Planned | Done | Notes |
|----------|---------|------|-------|
| Backend modules | 7 | 7 | Resolver uses keyword matching only (embedding deferred as noted) |
| Frontend components | 5 | 5 | Full SkillPicker, library, chat integration, toggle |
| Bundled skills | 8 | 9 | +1 bonus `weather` skill |
| MCP integration | — | ✅ | Not in Phase 1 plan but delivered (supports Phase 3 hub imports) |

**Minor gaps / items to carry forward:**
1. Embedding-based resolver matching (noted as Phase 2 in resolver.py) — keyword matching works but embedding would improve auto-discovery accuracy
2. No `DELETE /api/skills/:name` endpoint yet — only toggle enable/disable exists
3. No `POST /api/skills` (create) or `PUT /api/skills/:name` (update) endpoints — these are needed for Phase 4 (editor) but not Phase 1
4. `skill_discovery` stored in vault rather than project settings model — functional but should migrate to `projects.json` for consistency

---

### Phase 2: Security Scanner (1-2 weeks)
**Goal: Skills are scanned before activation**

- [x] `backend/skills/scanner.py` — Layer 1 (static) + Layer 2 (capability) analysis ✅ Pattern matching for dangerous code (eval/exec/shell injection/network/env access/obfuscation), file type whitelist/blocklist, size limits, manifest validation, capability mismatch detection
- [x] AI review integration (Layer 3) using existing LLM provider ✅ LLM-based semantic analysis of scripts, cross-references instructions vs code behaviour, returns structured JSON findings with risk assessment
- [x] Quarantine flow for failed scans ✅ Auto-quarantine on scan failure, `POST /api/skills/:name/quarantine`, `GET /api/skills/quarantine/list`, `POST /api/skills/:name/unquarantine`, `.quarantine/` directory in data/skills/
- [x] Scan results display in frontend ✅ ScanBadge component (clean/warnings/failed/unscanned), ScanResults panel with severity-colored findings, scan button on each skill card, per-skill scan trigger
- [x] Runtime sandboxing in `backend/skills/executor.py` ✅ Subprocess execution (never imported), env filtering (allowlist only), path traversal prevention, interpreter detection, timeout/output limits, no shell=True

**Security Hardening (pre-Phase 3 gate):**
- [x] Scan-before-enable gate ✅ Non-bundled skills must have a passing scan before `enable_for_project()` allows enabling. Returns 403 with reason if scan is missing or failed.
- [x] Scan persistence with content hashing ✅ Scan results persisted to `.scan_results/` as JSON, tagged with SHA-256 content hash. Auto-invalidated when any skill file changes. Survives registry reloads.
- [x] Scan-all + centralized dashboard ✅ `POST /api/skills/scan/all` bulk scan endpoint, `GET /api/skills/scan/summary` for dashboard data. New `SkillScanDashboard.jsx` with summary count cards (total/passed/failed/unscanned), skills table sorted by severity, quarantine section with restore button. Tabbed SkillsPage (Library | Security).
- [x] Name masquerade prevention ✅ `_bundled_names` set in registry tracks all bundled skill names. User-installed skills with colliding names are blocked at load time with logged warning. `is_bundled_name()` check on unquarantine prevents restoring over a bundled skill (409 Conflict).
- [x] Bundled flag spoofing prevention ✅ `is_bundled` flag set exclusively by the loader based on source directory — never read from `skill.json` content. Prevents imported skills from claiming bundled status to bypass scan gates.

### Phase 2 Completion Assessment (2026-04-06)

**Status: ✅ PHASE 2 COMPLETE — Ready to proceed to Phase 3**

All 5 core checklist items plus 5 security hardening items are implemented. The scanner is operational with full three-layer analysis, quarantine flow, and a dedicated Security tab in the frontend.

| Category | Planned | Done | Notes |
|----------|---------|------|-------|
| Scanner layers | 3 | 3 | Static, Capability, AI Review all operational |
| Quarantine flow | 1 | 1 | Auto-quarantine on failure, manual quarantine/restore, bundled skill protection |
| Frontend security UI | 1 | 2 | ScanBadge per-skill + SkillScanDashboard with summary cards and severity table |
| Runtime sandbox | 1 | 1 | Subprocess-only execution, env filtering, path traversal prevention, timeout/output limits |
| Hardening extras | 5 | 5 | Scan gate, persistence with hashing, dashboard, name masquerade prevention, bundled flag spoofing prevention |
| Security audit log | — | ✅ | Not in Phase 2 plan but delivered: `security_log.py` with JSON-structured event logging |
| Security override | — | ✅ | Force-enable with vault-stored password, logged as override event |

**Key deliverables beyond plan:**
1. `backend/security_log.py` — Centralized structured security audit log (`data/logs/security.log`) with typed event methods for auth, scanning, quarantine, execution, vault, and settings events
2. Security override password flow — Vault-stored password for force-enabling skills that failed scan, with full audit logging
3. Risk scores displayed as percentages in the frontend

**Minor gaps / items to carry forward:**
1. Network domain enforcement at runtime (permissions.network_domains) — declared in manifest model but subprocess proxy not yet implemented
2. Memory tier permission enforcement at runtime — declared in model but not enforced during skill execution
3. File path restriction enforcement at runtime — workspace globs declared but not enforced beyond path traversal check

---

### Phase 3: Hub Import (1-2 weeks)
**Goal: Import skills from external hubs**

- [x] Hub adapter interface ✅ `backend/skills/importer.py` — HubAdapter ABC with fetch/normalize/detect_format contract and pluggable `_ADAPTERS` registry
- [x] SKILL.md format adapter (SkillsMP/SkillsLLM compatibility) ✅ SkillMdAdapter with YAML frontmatter parsing, fallback simple parser, auto-generates skill.json + instructions.md
- [x] GitHub repo adapter ✅ GitHubAdapter with repo search (agent-skill/llm-skill topic filter), zip download with safe extraction, auto-format detection (Pantheon/SKILL.md/README fallback)
- [x] Local upload (.tar.gz/.zip) ✅ LocalUploadAdapter with zip/tar.gz/tar extraction, zip-slip and tar-slip protection, symlink rejection, auto-flatten single subdirectory, format detection delegation
- [x] `SkillImporter.jsx` — Import modal with hub search ✅ Tabbed modal (Search Hubs / GitHub / Upload), drag-and-drop file upload, search result cards, AI review toggle, import result banners
- [x] Auto-scan on import ✅ Importer orchestrator runs full scan pipeline (Layer 1-3) on every import, auto-quarantines failed scans, reloads registry after install
- [x] Import API endpoints ✅ GET /skills/hubs, POST /skills/search-hub, POST /skills/import, POST /skills/import/upload (multipart), plus skillsApi client methods
- [x] Frontend integration ✅ Import button in Skills Library header, modal overlay, skill list auto-refresh on import

**Scope change:** Smithery / MCP adapter was removed from Phase 3. Smithery is an MCP server registry, not a skill hub — conflating the two bypassed proper MCP lifecycle management and gave false assurance from the skill scanner. MCP server discovery now lives in a separate MCP connector subsystem with its own generic registry protocol (see `docs/mcp-registry-protocol.md`).

### Phase 3 Completion Assessment (2026-04-07)

**Status: ✅ PHASE 3 COMPLETE**

All revised Phase 3 items (minus Smithery, which moved to the MCP roadmap) are implemented, audited, and hardened.

| Category | Planned | Done | Notes |
|----------|---------|------|-------|
| Hub adapter interface | 1 | 1 | Pluggable ABC + `_ADAPTERS` registry |
| Format adapters | 3 | 3 | SKILL.md, GitHub, Local Upload (Smithery removed — see MCP roadmap) |
| Frontend importer | 1 | 1 | `SkillImporter.jsx` wired into Skills library |
| Auto-scan on import | 1 | 1 | Unified orchestrator — runs for every adapter path |
| API routes | 1 | 4 | `/skills/hubs`, `/skills/search-hub`, `/skills/import`, `/skills/import/upload` |

**Security hardening during audit:**
1. Fixed zip-slip in `GitHubAdapter.fetch()` — previously used unchecked `ZipFile.extractall()`
2. Fixed zip-slip in `LocalUploadAdapter.fetch()` — tar path was checked but zip was not
3. Added symlink rejection and absolute-path rejection in both archive extractors via shared `_safe_extract_zip` / `_safe_extract_tar` helpers
4. Dropped stale `topic:mcp-server` filter from the GitHub search query

**Minor gaps / items to carry forward:**
1. GitHub adapter only supports public repos (no PAT auth). Acceptable for v1; add if enterprise users need private-repo imports.
2. GitHub adapter only tries `main` then `master` branches; doesn't honor repo's default branch. Low priority.
3. Import endpoint doesn't surface AI-review progress in real time — user waits on a spinner. Could stream via SSE in Phase 5.

### Phase 4: AI-Assisted Editor (1-2 weeks)
**Goal: Create and iterate on skills in the browser**

- [ ] `SkillEditor.jsx` — Split-pane editor with file tree
- [ ] AI scaffold endpoint (generate skill from description)
- [ ] AI improve endpoint (refine instructions)
- [ ] AI trigger optimizer
- [ ] `SkillTester.jsx` — Test skills with sample messages
- [ ] Live security linting during editing

### Phase 5: Polish & Advanced (ongoing)
- [ ] Skill versioning and rollback
- [ ] Skill sharing (export as .tar.gz with manifest)
- [ ] Usage analytics (which skills fire, how often, success rate)
- [ ] Skill chaining (one skill can invoke another)
- [ ] Community hub contribution (publish back to registries)

---

## 9. Key Design Decisions

### Why prompt injection over new tool schemas?

Skills work primarily by injecting instructions into the system prompt rather than registering new OpenAI-compatible tool schemas. This is simpler and more flexible — the agent gets natural-language guidance it can adapt to context, rather than rigid function signatures. Scripts are exposed as tool schemas only when the skill genuinely needs callable endpoints (e.g., a web scraper that needs to return structured data).

### Why not MCP servers?

MCP is powerful but heavy for this use case. Each MCP server is a separate running process with its own transport layer. Pantheon skills are lighter — they're instructions + optional scripts, loaded on demand. However, the Smithery adapter can *convert* MCP tool definitions into Pantheon skills, getting the best of both ecosystems.

### Why quarantine instead of block?

Users should be able to inspect and override scan results. A "quarantined" skill is visible but disabled, with a clear explanation of what was flagged. The user can review the findings and manually approve if they trust the source. This balances security with user autonomy.

---

## 10. Security Threat Model

| Threat | Mitigation |
|--------|-----------|
| Malicious scripts (data exfil, backdoors) | 3-layer scan pipeline + subprocess sandbox |
| Obfuscated code hiding intent | Static detection of eval/exec/base64 + AI semantic review |
| Supply chain attacks (compromised hub) | Scan on every import, re-scan on update |
| Prompt injection via instructions.md | Instructions are injected as system context, not user input; LLM treats them as operator guidance |
| Resource exhaustion (infinite loops, memory bombs) | Subprocess timeout + memory limits |
| Credential theft | Skills can't access vault; env vars are filtered |
| Skill impersonation (fake "official" skill) | Author + source tracking; scan badges in UI |

---

## 11. Resolved Design Decisions

1. **Skill scope**: Skills are installed globally and enabled per-project. When Auto-Skill is on and the resolver matches an installed-but-not-enabled skill, the agent can suggest enabling it for the current project: "I found a skill called 'code-review' that looks relevant here, but it's not enabled for this project. Want me to turn it on?" This keeps the library centralized while giving each project explicit control over which skills are active.

2. **Automatic skill discovery**: Controlled by the per-project Auto-Skill toggle (off / suggest / auto). When off, skills only fire via explicit `/` invocation. Resolved in Section 2.4.

3. **Skill marketplace contribution**: Yes — the graduated export strategy (Section 3.3) already handles this. Pantheon-to-Pantheon bundles preserve all extensions; portable exports adapt or strip Pantheon-specific features with AI assistance. Publishing back to hubs is a Phase 5+ feature that will use the portable export path.

4. **Versioning strategy**: Date-based versioning (`2026-04-05`, `2026-04-05.1` for multiple versions in a day). Semantic versioning implies a precision about breaking changes that doesn't match how skills actually evolve — especially with the evolution system making incremental tweaks. Date-based is simpler, honest, and sorts naturally.

5. **Hub updates**: Never auto-applied. When a hub has a newer version of an installed skill, the library UI shows an "Update available" badge. Clicking it fetches the new version and runs the full security scanner pipeline before installation. The user reviews the scan results and approves or rejects.

   **Interaction with local evolutions**: If the user has evolved a skill locally since the original import, a hub update creates a conflict. The update flow handles this:
   - Show a diff: hub changes vs local evolutions since the original import.
   - Three options: **Accept hub update** (discards local evolutions — previous version preserved in version history for rollback), **Keep local** (dismiss the update notification), or **Merge** (AI-assisted — the agent analyzes both change sets and proposes a merged version that incorporates the hub's improvements while preserving local evolutions that don't conflict).
   - The merged version goes through the security scanner before activation, same as any other change.
