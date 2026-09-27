# Pantheon — Personas, Personality layer, Bundled skills: usefulness assessment

Read-only review of `/home/user/pantheon` (no files modified). All claims cite code. "tok" = chars/4 estimate.

---

## 0. Headline findings

1. **A persona is nothing but a soul.md preset.** `POST /personas/{id}/apply/{project}` copies the persona's `soul` string over the project's `personality/soul.md` override and stamps `persona_id` into `projects.json` (`backend/api/personas.py:192-226`). Nothing else changes: no tools, model, temperature, memory access, router class or skills. The persona JSON has exactly 10 fields — `id, name, tagline, description, icon, traits, best_for, is_default, is_bundled, soul` — and the only field that reaches the LLM is `soul` (plus `name`/`icon` as a chat prefix in group mode, `chat.py:735-748`).
2. **Three disconnected persona mechanisms exist**, and one of them is dead:
   - (a) *Project persona* — apply → project soul.md override (`personas.py:192`, `Projects.jsx:117-127`, `PersonalityEditor.jsx:131-140`). This is the only one that reliably changes the prompt.
   - (b) *Group chat personas* — `active_personas` list in conversation metadata; **only does anything when 2+ personas are selected** (`chat.py:711`). One selected persona is silently ignored (falls into the `else` at `chat.py:823`). In group mode each persona replaces soul.md **and drops agent.md entirely** (`prompts.py:43-45`), up to 3 round-robin turns triggered by `@id` mentions (`chat.py:733-820`).
   - (c) *Project-settings "Default persona" + "Tone weight"* — written to `phase_g.db.project_settings` (`projects.py:406-447`), **read by no runtime code**. `core.py:338-339` reads `personality_weight` and `context_focus` from the *global* vault; `chat.py:632` reads `skill_discovery_{project}` from the vault. The two panels that edit this table (`ProjectSettingsPanel.jsx:200-235`, `ProjectPersonalityPanel.jsx`) are writing to a dead store. `ProjectPersonalityPanel.jsx` additionally has no import site anywhere in `frontend/src` (dead component) and offers tone values `focused/balanced/broad` (`:5`) that don't match the backend's `minimal/balanced/strong` (`prompts.py:13-31`).
3. **Default-persona side effect.** New projects default to `pan` (`Projects.jsx:11`) and `apply` runs on create (`:22-24`), so every project gets a **project-level soul.md override** that is a strict subset of global soul.md (pan.json is a verbatim prefix of soul.md minus the 598-char `## Key Commitments` block — verified by script). Consequence: editing the *global* Identity in Settings → Personality has no effect on any project created through the UI, because `get_full_personality` prefers the override (`agent/personality.py:88-91`). This is the single most confusing behaviour in the feature.
4. **CLAUDE.md is wrong about `data/`.** `.gitignore:47-51` ignores `data/*` but whitelists `!data/personality/*.md`; `git ls-files data/` shows `data/personality/{soul,agent}.md` tracked and byte-identical to `backend/data/personality/*` (diff = none). `.gitignore:93` also lists `backend/data/`, yet `git ls-files backend/data` shows migrations, personas and personality tracked (force-added). Two identical copies of soul/agent are in git; only `backend/data/personality` is the real template (`agent/personality.py:12`).
5. **Bundled skills are thin prompt wrappers** (1.7–3.2 KB of markdown each) with a 47-line manifest of which most fields are never read (`auto_store`, `telemetry`, `evolution`, `schedulable.*`, `permissions.network_domains/file_paths` — 0 runtime readers outside the scanner/models). Only 3 of 10 are relevant to thematic/vendor research (`web-research`, `knowledge-capture`, `summarize-conversation`), and several triggers will misfire on ordinary chat once auto-discovery is turned on.

---

## (a) Persona comparison table

Source: `backend/data/personas/*.json`. Every persona uses the identical skeleton: `# The Soul of <N>` → "You are <N>, a …. Your purpose is to …" → `## Core Identity` with exactly 4 bold trait paragraphs → `## Working Style` → closing line "You are <N>: …" (9/9 match on every structural check). Word-level pairwise SequenceMatcher mean 0.10 (the wording is distinct), but 33–43 % of each persona's word tokens appear in ≥6 of the 9 (the shared "you never / you always / your purpose" register). Souls are 1.56–1.69 KB (~390–420 tok) except Pan at 2.8 KB (~700 tok).

| id | Role (tagline / best_for) | Unique behavioural content (what actually differs) | Tool / model / temp / memory diffs | Fields never read by code |
|---|---|---|---|---|
| pan (default) | Shepherd of Data / general purpose, data stewardship | Vigilance, stewardship, honest accounting, "tend before you expand", proactive memory, ask clarifying questions | none | `traits`, `best_for`, `tagline`, `description`, `is_default` (UI-only; `is_default` is never consulted by backend — default comes from `Projects.jsx:11` hard-coding `'pan'`) |
| apollo | Communicator & Creator / writing, docs | Ask about audience/purpose first; draft-revise-polish; adapt register | none | same |
| artemis | Guardian & Protector / security audits | Think like an adversary; audit inputs/auth/data flows; classify severity | none | same |
| athena | Strategist & Architect / architecture, code review | Plan before build; present options with trade-offs; "never rush" | none | same |
| hephaestus | Builder & Craftsman / coding, debugging | "Write code first, then explain"; test as you go; pragmatic shortcuts | none | same |
| hermes | Scout & Connector / research, competitive analysis | "Start searching immediately rather than over-planning"; cite sources; flag confidence levels; concise | none | same |
| mnemosyne | Chronicler & Historian / knowledge mgmt, notes | Summaries with decisions/action items; reference past discussions; "should I record this?" | none | same |
| prometheus | Teacher & Mentor / tutoring, explanation | Ask what learner knows; progressive disclosure; check understanding | none | same |
| zeus | Executive & Decider / prioritisation, exec summaries | "Say what needs to be said without padding"; "I recommend X because Y"; don't hedge | none | same |

Contradictions between personas (matter only in group mode, where several are active in one conversation): Athena "never rush into action / plan before you build" vs Hermes "start searching immediately rather than over-planning" vs Hephaestus "write code first, then explain"; Zeus "don't hedge" vs Pan/soul.md "never speculate beyond your evidence… flag uncertainty".

Persona fields the API accepts but code never uses at runtime: `traits`, `best_for`, `tagline`, `description`, `icon` (icon only used as group-chat prefix), `is_default`, `created_at/updated_at`. `is_bundled` is overwritten by the loader (`personas.py:39,53`).

**Overlap with the research use-case:** for "thematic/vendor research over time", the relevant personas are Hermes (research/synthesis) and Mnemosyne (memory/continuity) — and both sets of instructions are *already* in soul.md (Seeker's Drive, Proactive Memory) and agent.md ("start every knowledge question with recall()", "cite recalled context"). Apollo/Artemis/Athena/Hephaestus/Prometheus/Zeus describe coding-assistant / tutoring / exec roles that map to nothing in the adapter/ingest/graph workflow.

---

## (b) Runtime data flow: persona → prompt

### Path 1 — project persona (the one that works)
1. UI: `Projects.jsx:117-127` (card dropdown) / `Projects.jsx:22-24` (create, defaults to `'pan'`) / `PersonalityEditor.jsx:131-140` ("apply persona" in Settings → Personality with a project scope) → `personasApi.apply` (`api/client.js:503-504`).
2. `POST /api/personas/{id}/apply/{project}` → `backend/api/personas.py:192-211` → `save_soul(soul_content, project_id)` → writes `data/projects/<project>/personality/soul.md` (`agent/personality.py:95-102`); `_update_project_persona` stamps `persona_id` into `data/db/projects.json` (`personas.py:214-226`). `persona_id` is read back only by `Projects.jsx:138` for display.
3. Every chat turn: `AgentCore.chat` → `build_system_prompt(project_id=…, personality_weight=<global vault>, custom_soul=None)` (`core.py:381-388`) → `get_full_personality(project_id)` (`prompts.py:47`) → project `soul.md` override wins over global (`agent/personality.py:84-92`).
4. Same path for jobs and bots: `autonomous_task.py:259-266`, `scheduled_job.py:84`, `coding_task.py:83`, `iteration_loop.py:259/314/347`, `messaging/adapters/{discord,telegram,slack,matrix,mattermost}.py` all build `AgentCore(project_id=…)` without `custom_soul`, so **jobs and bots inherit the project's applied persona via the soul.md override** — there is no explicit persona parameter for them.

### Path 2 — group chat personas (WS only)
1. `ChatTabs.jsx:216-230` toggles checkboxes → `conversationsApi.updateMetadata(session, {active_personas})` (`api/conversations.py:101`) and `Chat.jsx:797` sends `active_personas` on each WS message; `chat.py:686-697` upserts it into the `conversations` table.
2. `chat.py:711 if len(active_personas) > 1:` → for up to 3 turns, `_find_persona(p_id)` → `agent.custom_soul = p_soul` (`chat.py:766`) → `build_system_prompt(custom_soul=…)` sets `agent_config = ""` (`prompts.py:43-45`), i.e. **agent.md's memory/tool guidance is dropped for group turns**. Responses saved with `metadata={"persona_id", "persona_name"}` (`chat.py:805`). `@id` mentions in the reply hand off to the next persona (`chat.py:810-815`).
3. REST `/api/chat` has no group path at all (`chat.py:275-320` only resolves skills).
4. Persona selection here is **per conversation** (conversations metadata); path 1 is **per project** (file on disk); there is no global default other than the hard-coded `'pan'` in `Projects.jsx:11`.

### Path 3 — project_settings.persona (dead)
`PUT /projects/{id}/settings` (`projects.py:421-447`) stores `persona, tone_weight, context_focus, skill_discovery` in `phase_g.db`. Repo-wide grep: no reader of `tone_weight` or `project_settings.persona` outside `projects.py`; the live values come from vault keys `personality_weight`, `context_focus` (`core.py:338-339`, `api/settings.py:143-144`) and `skill_discovery_{project}` (`chat.py:290,632`, `api/skills.py:186`). The `ProjectSettingsPanel` hint "Drives the per-message toggles in the chat header" (`ProjectSettingsPanel.jsx:199`) is false — `Chat.jsx:520-530` reads `settingsApi.get()` (global) and `skillsApi.getDiscovery(pid)`.

### Interactions
- **Router / model_class:** none. `llm_config/router.py:256` reads `skill.manifest.pantheon.model_class`; `persona` appears nowhere in router.py or provider.py.
- **Skills:** none. Skill context is appended as `## Additional Context` after soul+agent (`prompts.py:153,159`); the skill's `{{project.personality}}` placeholder (`skills/code-review/instructions.md:153`) is never rendered (0 hits in backend).
- **Memory extraction** has to defend against personas: `memory/extraction.py:47-48` tells the extractor to ignore "persona (Zeus, Athena, etc.)" names so they don't become graph entities.
- **Export/import** carry the project `personality/` dir (`project_export.py:392`, `project_import.py:456-477`), so an applied persona travels with a project zip.
- **No persona → ** `get_full_personality` falls back to global `data/personality/soul.md`, then bundled template, then a one-liner (`agent/personality.py:25-46`). Global soul.md **is Pan** (verbatim + Key Commitments), so "no persona" and "Pan" are the same identity.

---

## (c) System-prompt assembly, in order (`prompts.py:155-316`, then `core.py:391-407`)

| # | Block | Source | Static size (approx) | Condition |
|---|---|---|---|---|
| 1 | Personality scope prefix | `prompts.py:13-31`, weight from global vault `personality_weight` (`core.py:338`) | ~220 chars / 55 tok | always |
| 2 | soul.md (global or project override, or persona `custom_soul`) | `agent/personality.py:84-92` | 3.4 KB / ~850 tok (Pan). Persona souls 1.6 KB / ~400 tok | always |
| 3 | `---` + agent.md | same; **empty when `custom_soul` set** (`prompts.py:45`) | 8.5 KB / ~2,140 tok | always except group-persona turns |
| 4 | `## Active Project` + storage-layout rules | `prompts.py:57-95` | ~1.2 KB / ~300 tok | when project_id |
| 5 | `## Repository work protocol` | `prompts.py:101-134` | ~1.2 KB / ~300 tok | only when project bound to a repo |
| 6 | `## Corpus Context` (recalled memories) | `prompts.py:136-151`; budget `recall_token_budget=4000` (`config.py:112`) | dynamic, ≤ ~4 K tok | when recall returns hits |
| 7 | `## Additional Context` = skill context (`## Active Skill: …` + instructions.md + chained skills) | `prompts.py:153`; `resolver.py:163-239` | 1.7–3.2 KB per skill / 430–800 tok | when a skill is active |
| 8 | `---` + `## Self-reference conventions` | `prompts.py:163-164` | 470 chars / ~120 tok | always |
| 9 | `## Persistence boundary — Pantheon vs MCP save tools` | `prompts.py:166-193` | 1.36 KB / ~340 tok | always |
| 10 | `## Storage layers — artifacts vs workspace files` | `prompts.py:195-220` | 1.35 KB / ~340 tok | always |
| 11 | `## Tool selection — scan before you decline` | `prompts.py:222-250` | 1.53 KB / ~380 tok | always |
| 12 | `## Skills vs scheduled tasks` | `prompts.py:252-288` | 1.9 KB / ~480 tok | always |
| 13 | `## Scheduled task approval flow` | `prompts.py:290-314` | 2.0 KB / ~500 tok | always |
| 14 | `Current time:` | `prompts.py:316` | tiny | always |
| 15 | `RECENT BACKGROUND JOB ACTIVITY` (≤5 jobs, 24 h) | `core.py:31-93, 393-395` | ~0.3–1 KB | when jobs exist |
| 16 | `## Available skills (installed in this project)` | `core.py:95-142, 405-407` | ~3.8 KB / ~950 tok with the 10 bundled skills | when any skill is enabled (always, by default) |

**Static floor for an ordinary project chat turn with no skill active, no repo, no recall:** blocks 1–4 + 8–14 + 16 ≈ 3.4 + 8.5 + 1.2 + 8.6 + 3.8 KB ≈ **25.5 KB ≈ 6,400 tokens** of fixed text, of which soul.md + agent.md are ~12 KB (~47 %) and the available-skills block is ~3.8 KB (~15 %) — and the skills block is dominated by 7 skills irrelevant to research.

### Redundancy / contradictions between layers
- **"Who am I" is stated up to four times:** persona soul (or soul.md) → agent.md `## Overview` ("You are an AI agent with five tiers of memory…") → agent.md `## Summary` ("You are an autonomous agent with real capabilities…") → skill instructions' own framing. Group mode adds a fifth (persona prefix) and removes agent.md.
- **Memory guidance duplicated 3×:** soul.md "Proactive Memory / Know the Flock"; agent.md `## Memory Architecture` + `## Decision Framework` + `### remember() and recall()`; `knowledge-capture` skill's tier rules. agent.md also documents an "Archival Memory" tier that CLAUDE.md says is mostly unused, and a "Working Memory… last 10-20 exchanges" figure that isn't enforced anywhere in code.
- **Storage/workspace guidance contradicts itself:** agent.md `## File Workspace` (lines 77-89) tells the agent to *organise reports in the workspace* ("reports/, analysis/…", "store outputs in predictable locations") while prompts.py block 10 says workspace is *scratch, ephemeral, not indexed* and "ALWAYS save [to artifacts]". The bundled `daily-digest` skill also says "store it as a file in the workspace" (instructions.md step 4) — the exact anti-pattern CLAUDE.md warns against.
- **Tool names that don't exist:** prompts.py:233 tells the model to use `web_fetch`; TOOL_SCHEMAS has `web_search` only (no `web_fetch` anywhere in tools.py). agent.md:106/152 says escalate "via Telegram" — `send_telegram` exists but only if the bot is configured.
- **agent.md `## Performance and Limitations`** hard-codes "web searches timeout after 15 s; LLM calls after 120 s; 50 iterations" — none of these numbers are sourced from config and will drift.
- **Tone contradictions:** Pan/soul.md "ask clarifying questions… when ambiguous" and block 11 "ONLY ask for clarification when genuinely ambiguous" are compatible, but Zeus ("don't hedge") and Hermes ("first good answer beats a perfect late one") conflict with soul.md's Key Commitments ("never speculate beyond evidence", "transparent about uncertainty") — and in project mode the persona *replaces* soul.md, so those commitments vanish for any project not on Pan.
- **Scope prefix vs persona:** `_PERSONALITY_SCOPES["balanced"]` says "avoid inserting personal identity into analysis" while every persona's soul opens with a 3-paragraph identity monologue; at `minimal` the prefix tells the model to ignore most of what follows, which makes ~400–850 tokens of soul dead weight.

---

## (d) Bundled skills (`/home/user/pantheon/skills/*`, loaded by `skills/registry.py:34,68-75` — repo-root `skills/` is the bundled dir; user skills in `data/skills/`, cannot shadow bundled names)

Tool existence checked against TOOL_SCHEMAS names in `backend/agent/tools.py` (has: `web_search, recall, remember, create_graph_node, link_concepts, read_file, write_file, list_workspace_files, save_to_artifact, code_execute, run_command, send_telegram, create_task, …`; **no** `web_fetch`). None of the 10 declares `requires_mcp` (the field isn't even in `SkillManifest`, it survives via `extra="allow"` and is read only by `autonomous_task.py:198-204`).

| slug | Purpose | Deps exist? | Substantive? | Overlaps with | Trigger misfire risk (auto/suggest mode; fires at score ≥ 3.0 = one full-phrase trigger, `resolver.py:105-110`, `chat.py:643`) |
|---|---|---|---|---|---|
| autoresearch | Wizard → runs `python utils/autoresearch.py` via `code_execute` | `code_execute` is host-exec gated (`tools.py:28-31`); refused in background jobs and when `AGENT_HOST_EXEC=never`, yet manifest says `schedulable: true`. `utils/autoresearch.py` exists (16.7 KB) | Yes — only skill with real machinery behind it | none | Low: "karpathy loop", "run autoresearch" are specific |
| code-review | Systematic review checklist | `read_file` ok; `{{project.personality}}`/`{{project.files}}` placeholders never rendered | Medium — good checklist, but generic LLM behaviour | Athena/Artemis personas; `start_coding_task` | Low-med: "code review" fine; "check for bugs" fine |
| daily-digest | Summarise recent episodic + workspace activity | uses `list_workspace_files`, `send_telegram` (optional) | Thin; tells agent to store digest in workspace (contradicts artifact rule) | `summarize-conversation`, Mnemosyne, `_build_recent_jobs_block` | **High**: "status update", "recent activity", "project summary", "catch me up" are ordinary phrases in a research chat ("give me a status update on the NBJ ingest") |
| draft-message | Email/Slack/announcement tone guide | none needed | Thin prompt wrapper | Apollo persona | **High**: bare trigger `"announce"` and `"help me write"` match any "help me write a query / a summary…", and "announce" substring-matches "announcement" — exactly the vocabulary of the `blog/announcement` adapter and vendor-announcement research |
| explain-code | Plain-language code walkthrough | `read_file` | Thin | Prometheus persona | **High**: `"what does this do"`, `"help me understand"`, `"what's happening here"`, `"walk me through"` fire on non-code questions ("help me understand this vendor's pricing") |
| knowledge-capture | Store facts/decisions/relationships to semantic + graph | `remember`, `create_graph_node`, `link_concepts` all exist | Medium — sensible tiering guidance | agent.md memory rules; `remember` tool; `save_last_response` | Med: "remember this", "don't forget that" are natural — but firing here is *desired*; low harm |
| summarize-conversation | Recap w/ decisions + action items | none | Thin | daily-digest; Mnemosyne; prompts.py `save_last_response` guidance | **High**: `"give me a summary"`, `"meeting notes"` fire when user asks to summarise a *transcript/artifact*, not the conversation; skill then steers toward episodic recap |
| task-breakdown | Phases/steps/effort estimates | none | Thin | Athena/Zeus personas; `create_task` confusion risk (block 12 exists because of this) | Med: `"how should I approach"`, `"plan this out"` common; `"break this down"` clashes with "break down this vendor's product line" |
| weather | Web-search a forecast, emoji format | `web_search` exists; declares `network_domains: ["*"]` (unread) | Thin, and off-domain for a research harness | none | Med: `"forecast for"` will match "revenue forecast for 2027", "market forecast for GLP-1" — a real hazard for vendor research; `"how hot is it"` harmless |
| web-research | 2–6 searches → structured, sourced summary | `web_search` exists | Medium — reasonable procedure; storing findings to semantic via auto_store is **not implemented** (`auto_store` has 0 readers) | Hermes persona; `ingest_source` (the real research pipeline, which this skill never mentions) | **High**: `"investigate"`, `"look into"`, `"find out about"`, `"compare options for"` are the everyday verbs of thematic research; firing steers the agent to raw web_search instead of `recall`/`ingest_source`, contradicting agent.md's "check memory first" |

Manifest fields with **zero** runtime readers (besides scanner/models): `pantheon.memory.auto_store`, `pantheon.telemetry.*`, `pantheon.evolution.*`, `pantheon.schedulable.*` (only surfaced in `to_summary` for a UI badge), `permissions.network_domains`, `permissions.file_paths`, `dependencies`, `source_hub`, `license`. `capabilities_required` is used only by the scanner (`skills/scanner.py:235`) and importer. `permissions.vault_secrets` is read by `skills/executor.py:53`, which runs *scripts* — none of the 10 bundled skills ship a script, so for bundled skills the executor path is unused.

Resolver notes (`skills/resolver.py`): stopword filtering (`:27-40`) protects against `what/the/this`, but full-phrase containment (`:107`) is a plain substring test, so `"announce"`⊂"announcement", `"forecast for"`⊂"revenue forecast for", `"investigate"`⊂"investigated". Default discovery mode is `off` (`chat.py:290,632`), so today none of this fires unless the user flips it — but the frontend store defaults `chat_skill_discovery` to `'auto'` in localStorage (`store/index.js:19`), which is a UI/backend default mismatch worth noting.

---

## (e) Frontend surface

| Surface | Route / location | Status |
|---|---|---|
| Personas page (`PersonasPage.jsx` → `PersonaLibrary.jsx`, 343 lines) | `/personas` nav item (`Layout.jsx:24`) | Live. CRUD + Clone. **Cannot apply** a persona from here (no apply button — grep shows only Edit/Delete/Clone). |
| Settings → Personality tab (`Settings.jsx:1135,1456` → `PersonalityEditor.jsx`, 532 lines) | `/settings` | Live. Global or per-project soul/agent editor, "apply persona", "Save as Persona", "copy global to project". This is the real control surface. |
| `PersonalityPage.jsx` (imports PersonalityEditor + PersonaLibrary) | `/personality` → `Navigate to /settings` (`App.jsx:85`) | **Dead** (unreachable route). |
| Project tab → Project Settings (`ProjectSettingsPanel.jsx:200-235`) "Default persona / Tone weight / Context focus / Skill discovery" | Chat → project tabs (`ChatTabs.jsx:24,85`) | Live UI, **dead backend** (writes `phase_g.db.project_settings`, no reader). Hint text is false. |
| `ProjectPersonalityPanel.jsx` (108 lines) | none | **Dead** (no import site; wrong tone enum). |
| Projects page (`Projects.jsx`) persona dropdown on create + per-card | `/projects` | Live; third place to apply a persona. |
| Chat header "Personality: minimal/balanced/strong" cycle (`Chat.jsx:551-560`) | chat | Live; global vault `personality_weight`. |
| Chat tabs "Group" persona checklist (`ChatTabs.jsx:186-300`) | chat | Live but only meaningful with ≥2 checked. |
| Skills page (`SkillsPage.jsx` → `Skills.jsx` 634 + `SkillScanDashboard.jsx` 436) | `/skills` | Live: library + security scan tabs. |
| `SkillEditor.jsx` (1,250 lines: multi-file editor, test, AI improve, trigger optimiser, versions, publish) + `SkillImporter.jsx` (451) | modal from Skills.jsx | Live; substantial. |
| `SkillPicker.jsx` (118) | chat `/` autocomplete | Live; the good UX. |
| Settings → Skills tab (`Settings.jsx:1508` → `SkillHubsSection`) | `/settings` | Live; hub registries only — distinct from /skills, but a user has to know that. |

Duplication: persona *application* exists in 3 places (Projects page, Settings→Personality, project Settings tab — the last one being dead); persona *browsing* in 2 (Personas page, PersonalityEditor's picker); skill config in 2 pages (/skills and Settings→Skills). Two dead components (`PersonalityPage.jsx`, `ProjectPersonalityPanel.jsx`) and one dead table (`project_settings`).

---

## (f) Verdict

**For a single user doing thematic/vendor research, the persona system as built adds close to zero behavioural value and a measurable amount of confusion:**

- The only lever is soul.md text. Global soul.md *is* Pan. Eight alternative souls are ~400-token identity essays whose actionable content (Hermes: cite sources / flag confidence; Mnemosyne: surface past context) is already in soul.md + agent.md. The other six describe coding, tutoring, security-audit and exec roles that never intersect with `ingest_source`/`recall`/graph work.
- Applying any non-Pan persona **removes** the Key Commitments block (no fabrication, flag uncertainty) — the part of soul.md most relevant to research integrity.
- Defaulting `pan` on project create silently shadows global soul.md edits for every project.
- Group-persona mode drops agent.md, so the agent loses its memory/tool guidance exactly when running multiple voices; it also spends 3× the tokens per user message. It is a demo feature, not a research feature.
- `project_settings.persona/tone_weight` and two panels are dead; `_PERSONALITY_SCOPES` at `minimal` tells the model to ignore the soul it just paid ~850 tokens for.

**Skills:** `SkillEditor`/`SkillPicker`/scanner/registry are solid infrastructure and worth keeping — the `/slug` mechanism and per-project enable/disable are the right primitives, and user-authored research skills (e.g. the `content-ingest-graph` mentioned in CLAUDE.md) are where the value is. The **bundled ten** are generic assistant recipes: `autoresearch` is real but is a coding-optimiser tool; `weather`, `draft-message`, `explain-code`, `code-review`, `task-breakdown` are off-domain for this harness; `daily-digest`/`summarize-conversation` overlap each other and the recent-jobs block; `web-research` actively steers away from the corpus-first workflow. Their triggers — `"investigate"`, `"look into"`, `"forecast for"`, `"announce"`, `"status update"`, `"give me a summary"`, `"help me understand"` — are the vocabulary of vendor research and will misfire the moment auto-discovery is enabled. They also cost ~950 tokens per turn in the available-skills block whether or not they're ever invoked.

### Streamlining options (all preserve flexibility)

**Option 1 — Collapse personas into "soul presets" inside the Personality editor; delete the Personas page, group mode, and dead settings.**
- Keep `backend/data/personas/*.json` as read-only *templates* offered by a "Start from preset…" dropdown in `PersonalityEditor.jsx` (which already has apply + Save-as-Persona). Remove `/personas` nav, `PersonaLibrary.jsx`, `ChatTabs` group checklist, `chat.py:685-820` group loop, `project_settings.persona/tone_weight` columns + both panels, `PersonalityPage.jsx`, `ProjectPersonalityPanel.jsx`. Stop auto-applying `pan` on project create (new projects inherit global until the user explicitly overrides).
- Pros: one place to edit identity; global edits propagate again; ~600 lines of frontend + ~140 lines of backend removed; no behavioural loss for single-user research; presets remain cloneable/editable so a "vendor-analyst" soul is still one click away.
- Cons: loses the multi-voice demo; anyone relying on `active_personas` metadata in old conversations sees only the saved prefixes (already persisted in message content, so history is unaffected).

**Option 2 — Make persona a real project-level setting with actual effects (if the feature is to stay).**
- Move persona binding out of "copy soul.md into a file" into `project_settings.persona` (already exists, currently dead) and have `build_system_prompt` resolve it at runtime: soul = persona.soul **appended to** (not replacing) the global Key Commitments; optionally let a persona carry `model_class` (router hint, like skills already do at `router.py:256`), `default_skills` and `tone`. Delete the group loop or gate it behind the same setting.
- Pros: personas become the "role" abstraction the UI already implies (dropdown per project, honoured by chat, jobs and bots uniformly); no more file-copy drift; the currently-dead table and panel get a purpose; adding `model_class` lets "Hermes" run on a fast model and "Athena" on frontier — a difference that's actually observable.
- Cons: real work (~1–2 days): prompt resolution change, migration of existing `projects/<id>/personality/soul.md` overrides, removal of `personas.py:apply` semantics, tests. Still ~400 tokens/turn of identity prose unless the souls are also trimmed.

**Option 3 — Minimal hygiene, no product change.**
- Fix the foot-guns only: don't default `pan` on create (`Projects.jsx:11`), make `apply` append Key Commitments, drop `agent_config = ""` when `custom_soul` is set (`prompts.py:45`), delete the two dead components and the false hint, correct `web_fetch` → `web_search` in `prompts.py:233`, reconcile agent.md `## File Workspace` with the artifact rule, and untrack the duplicate `data/personality/*.md` (or update CLAUDE.md to say it's tracked on purpose).
- For skills: move `weather`, `draft-message`, `explain-code`, `code-review`, `task-breakdown`, `autoresearch` out of `skills/` into an `examples/` folder (or ship them disabled by default via `.skill_state.json`), keep `web-research` (rewritten to `recall` first, then `ingest_source` for anything worth keeping, then `web_search`), `knowledge-capture`, and one of `daily-digest`/`summarize-conversation`. Tighten triggers to multi-word phrases and drop bare verbs (`investigate`, `announce`, `look into`, `forecast for`). This alone cuts the always-on skills block from ~950 to ~300 tokens.
- Pros: an afternoon of work, no UX removal, immediate token and correctness win.
- Cons: leaves three apply surfaces and the conceptual overlap in place; the Personas page remains decorative.

**Recommendation:** Option 1 for personas (the feature's honest shape is "soul.md presets"), plus the skills half of Option 3. If you want personas to earn their nav slot later, Option 2 is the structurally correct path — but only build it once there's a concrete need for a per-project difference beyond prose (e.g. routing a project to a different model), because today nothing a persona does can't be done by editing the project's soul.md directly.
