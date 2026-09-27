# Help system + LLM config first application — design

**Date:** 2026-05-10
**Status:** approved (brainstorm)
**Related:** `frontend/src/components/Tooltip.jsx`, `frontend/src/components/settings/`, `frontend/src/components/ChatActions.jsx`

## Goal

Make Pantheon's configuration UI self-explanatory enough that a new
user can complete LLM setup without reading external docs. This is
the first ship of a multi-part UX effort:

1. Build two reusable help primitives (`InfoTooltip`, `HelpDrawer`)
   so all future help-text additions across the app share the same
   visual + interaction patterns.
2. Apply them to the highest-pain surfaces — LLM endpoints + role
   mapping, the four cryptic chat-bar icons, the project-settings
   chat-defaults dropdowns, and the scheduled-tasks dropdown.

The remaining 11 configuration surfaces (Personality editor, Personas
library, Skills, MCP, Connections, Sources, Projects, Web Search
providers, Skill Editor, etc.) are explicitly out of scope for this
ship; each gets its own focused application of the same primitives.

## Today

A `Tooltip` primitive already exists at
`frontend/src/components/Tooltip.jsx` — it wraps children with a
hover trigger and renders a label through a portal so it escapes
overflow ancestors. It's used by the four chat-bar icons in
`ChatActions.jsx`, but the labels there are procedural ("Auto-skill:
auto. Cycle: off → suggest → auto") rather than explanatory.

No `?` icon convention exists. No collapsible help drawer exists. The
LLM-endpoints form is a stack of unlabelled inputs with placeholder
text; users must look up provider URLs themselves.

## Design

### 1. Reusable primitives

Two new components under `frontend/src/components/help/`. Both render
gray-on-dark to stay subtle.

#### `InfoTooltip`

`frontend/src/components/help/InfoTooltip.jsx`. Wraps the existing
`Tooltip` primitive with a `HelpCircle` icon trigger from
`lucide-react` (already a dependency). Used inline next to a form
label or section heading.

```jsx
<label>
  Base URL <InfoTooltip text="The root URL the API responds at — usually ends in /v1." />
</label>
```

API:

| Prop | Type | Notes |
|---|---|---|
| `text` | string (required) | The tooltip body. One sentence. |
| `placement` | `'top' \| 'bottom' \| 'left' \| 'right'` | Default `'top'`. Forwarded to `Tooltip`. |
| `size` | number | Default `14` (px). Forwarded to the lucide icon. |

Implementation: a `<Tooltip label={text} placement={placement}>`
wrapping a `<button>` that contains the `<HelpCircle>` icon. Button
type is `"button"` (no implicit form-submit), classes
`"text-gray-500 hover:text-gray-300 inline-flex items-center"`.

#### `HelpDrawer`

`frontend/src/components/help/HelpDrawer.jsx`. A collapsible inline
panel with a header bar (chevron + `?` icon + title). Clicking the
header toggles open/closed. Body renders arbitrary children — used
for tables, paragraphs, code snippets, external links.

```jsx
<HelpDrawer title="Common LLM providers" storageKey="help.llm-providers">
  <ProvidersTable />
</HelpDrawer>
```

API:

| Prop | Type | Notes |
|---|---|---|
| `title` | string (required) | Header text. |
| `children` | node | Body content. |
| `defaultOpen` | bool | Default `false`. |
| `storageKey` | string | Optional. When set, open/closed state persists to `localStorage[storageKey]`. Without it, drawer resets to `defaultOpen` on each mount. |

Visual: a single rounded-border container; header is a clickable
button row spanning the full width; body slides into view via Tailwind's
`max-h-0`/`max-h-[1000px]` transition. No portal — the drawer lives
inline in the page flow above the form it documents.

When `storageKey` is set: read the value on mount, default to
`defaultOpen` when absent or unparseable, write `'true'`/`'false'` on
toggle. Wrap reads/writes in try/catch (matching the established
pattern from `_initialProjectId` and `_initialSkillDiscovery` in
`store/index.js`).

### 2. LLM-config application

#### Provider data table

`frontend/src/components/help/llmProviders.js` exports a static array:

```js
export const LLM_PROVIDERS = [
  { name: 'OpenAI',          base_url: 'https://api.openai.com/v1',
    api_type: 'openai',     signup_url: 'https://platform.openai.com/api-keys',
    signup_label: 'platform.openai.com' },
  { name: 'Anthropic',       base_url: 'https://api.anthropic.com/v1',
    api_type: 'anthropic',  signup_url: 'https://console.anthropic.com/settings/keys',
    signup_label: 'console.anthropic.com' },
  { name: 'Google Gemini',   base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/',
    api_type: 'openai',     signup_url: 'https://aistudio.google.com/apikey',
    signup_label: 'aistudio.google.com' },
  { name: 'OpenRouter',      base_url: 'https://openrouter.ai/api/v1',
    api_type: 'openai',     signup_url: 'https://openrouter.ai/keys',
    signup_label: 'openrouter.ai/keys' },
  { name: 'Abacus.ai (RouteLLM)', base_url: 'https://routellm.abacus.ai/v1',
    api_type: 'openai',     signup_url: 'https://abacus.ai/app/apikeys',
    signup_label: 'abacus.ai' },
  { name: 'Groq',            base_url: 'https://api.groq.com/openai/v1',
    api_type: 'openai',     signup_url: 'https://console.groq.com/keys',
    signup_label: 'console.groq.com' },
  { name: 'Together AI',     base_url: 'https://api.together.xyz/v1',
    api_type: 'openai',     signup_url: 'https://api.together.xyz/settings/api-keys',
    signup_label: 'api.together.xyz' },
  { name: 'Fireworks',       base_url: 'https://api.fireworks.ai/inference/v1',
    api_type: 'openai',     signup_url: 'https://fireworks.ai/api-keys',
    signup_label: 'fireworks.ai' },
  { name: 'DeepInfra',       base_url: 'https://api.deepinfra.com/v1/openai',
    api_type: 'openai',     signup_url: 'https://deepinfra.com/dash/api_keys',
    signup_label: 'deepinfra.com' },
  { name: 'xAI Grok',        base_url: 'https://api.x.ai/v1',
    api_type: 'openai',     signup_url: 'https://console.x.ai',
    signup_label: 'console.x.ai' },
  { name: 'Mistral',         base_url: 'https://api.mistral.ai/v1',
    api_type: 'openai',     signup_url: 'https://console.mistral.ai/api-keys/',
    signup_label: 'console.mistral.ai' },
  { name: 'Ollama (local)',  base_url: 'http://localhost:11434/v1',
    api_type: 'ollama',     signup_url: 'https://ollama.com',
    signup_label: 'ollama.com', signup_note: 'No key — install locally' },
  { name: 'LM Studio (local)', base_url: 'http://localhost:1234/v1',
    api_type: 'openai',     signup_url: 'https://lmstudio.ai',
    signup_label: 'lmstudio.ai', signup_note: 'No key — install locally' },
]
```

Static const, easy to maintain. No backend round-trip needed.

#### LLM help drawer

In `frontend/src/components/settings/EndpointList.jsx`, mount above
the existing `<AddEndpointForm>`:

```jsx
<HelpDrawer title="Common LLM providers" storageKey="help.llm-providers">
  <table>...renders LLM_PROVIDERS as a table...</table>
</HelpDrawer>
<AddEndpointForm onCreated={...} prefill={prefill} setPrefill={setPrefill} />
```

Each row in the table:
- Provider name
- `<code>` block with the base URL (truncates with `text-xs font-mono`
  on narrow viewports)
- `<code>` block with the api_type
- Signup link rendered as `<a target="_blank" rel="noopener noreferrer">`
- A small **"Use this"** button at the row end that calls
  `setPrefill({ name, base_url, api_type })`

`AddEndpointForm` accepts a new `prefill` prop. When it changes,
`useEffect` populates the form's `name`, `base_url`, and `api_type`
fields (leaving `api_key` empty for the user to paste). Also scrolls
the form into view (`scrollIntoView({ behavior: 'smooth' })`) so the
user sees their click took effect even if the drawer is below the
fold.

The `name` field gets populated from the provider name passed through
`slugify` (already present in `frontend/src/utils/`, or use a small
inline `name.toLowerCase().replace(/[^a-z0-9]+/g, '-')`).

#### Per-field tooltips on AddEndpointForm

Add `<InfoTooltip>` next to each field label:

| Field | Hint text |
|---|---|
| Name | "A short label for this endpoint (e.g. `openai`, `local-ollama`). Used internally; you won't see it in chat." |
| API type | "Pick the protocol the endpoint speaks. Most cloud providers and OpenRouter speak `openai`. Anthropic uses its own. Use `ollama` for local Ollama." |
| Base URL | "The root URL the API responds at — usually ends in `/v1`. See the help drawer above for common values." |
| API key | "Stored encrypted in the local vault and never leaves your machine." |

#### Per-role tooltips on RoleMapping

Add `<InfoTooltip>` next to each role label in
`frontend/src/components/settings/RoleMappingRow.jsx`:

| Role | Hint text |
|---|---|
| chat | "The main agent loop — every chat message goes through this model." |
| prefill | "Cheap helper for short structured tasks (titles, tags, summaries). Falls back to chat if unset." |
| vision | "Image-aware completions when an artifact has visuals. Falls back to chat if unset." |
| embed | "Embeddings model for semantic memory + similarity. No fallback — must be set or semantic search is disabled." |
| rerank | "Re-orders semantic-search results. Falls back to embed-only ranking if unset." |

### 3. Other surface applications

#### Chat-bar settings (`ChatActions.jsx`)

Replace the four existing `label=` strings on the `Tooltip`-wrapped
icons. Each new label leads with a one-sentence WHAT-it-does, then
the procedural cycle or toggle hint:

| Icon | Old label | New label |
|---|---|---|
| Memory recall | (existing toggle copy) | `Memory recall: <ON/OFF>. The agent searches past chats and indexed files for relevant context before answering. Click to toggle.` |
| Context focus | `Thread focus: <X>. Cycle: broad → balanced → focused` | `Thread focus: <X>. How tightly the agent stays on the current message vs. the wider conversation. Cycle: broad → balanced → focused.` |
| Auto-skill | `Auto-skill: <X>. Cycle: off → suggest → auto` | `Auto-skill: <X>. Whether the agent auto-loads matching skills (off = manual /skill only; suggest = ask first; auto = load silently). Cycle: off → suggest → auto.` |
| Persona presence | `Persona presence: <X>. Cycle: minimal → balanced → strong` | `Persona presence: <X>. How strongly the persona's tone colors responses. Cycle: minimal → balanced → strong.` |

Pure copy edit; no structural change.

#### Project Settings panel (`chat-tabs/ProjectSettingsPanel.jsx`)

Three dropdowns (`tone_weight`, `context_focus`, `skill_discovery`)
get an `<InfoTooltip>` next to each label. Hint copy is the
WHAT-it-does prefix from the chat-bar table above (drop the "Cycle:"
suffix since these are dropdowns not cyclers).

#### Scheduled tasks dropdown (`TaskMonitor.jsx`)

Single `<InfoTooltip>` next to the "Schedule" label:

> "Pick a preset to run once now, after a delay, on an interval, or via cron. `cron` syntax is standard 5-field (min hour day month weekday) — e.g. `0 9 * * *` = 9am daily."

## Files touched

| Path | Change |
|---|---|
| `frontend/src/components/help/InfoTooltip.jsx` | NEW — `?` icon wrapping `Tooltip` |
| `frontend/src/components/help/HelpDrawer.jsx` | NEW — collapsible panel with localStorage-backed open state |
| `frontend/src/components/help/llmProviders.js` | NEW — 13-row static provider table |
| `frontend/src/components/settings/EndpointList.jsx` | Mount `<HelpDrawer>` above `<AddEndpointForm>`; thread `prefill` state |
| `frontend/src/components/settings/AddEndpointForm.jsx` | Accept `prefill` prop; per-field `<InfoTooltip>`s |
| `frontend/src/components/settings/RoleMappingRow.jsx` | Per-role `<InfoTooltip>`s |
| `frontend/src/components/ChatActions.jsx` | Replace four tooltip label strings |
| `frontend/src/components/chat-tabs/ProjectSettingsPanel.jsx` | Three `<InfoTooltip>`s next to chat-default dropdowns |
| `frontend/src/components/TaskMonitor.jsx` | One `<InfoTooltip>` next to the Schedule label |
| `frontend/package.json` | Bump version |

## Verification (manual — frontend has no test infrastructure)

1. Open Settings → LLM. The "Common LLM providers" drawer is visible
   above the AddEndpointForm, collapsed by default. Header reads
   "Common LLM providers" with a `?` icon and a chevron.
2. Click the drawer header. It expands; the table shows 13 rows. Each
   has a "Use this" button.
3. Click "Use this" on the OpenAI row. The form below scrolls into
   view; Name field shows "openai", Base URL shows
   `https://api.openai.com/v1`, API type shows "openai". API key
   remains empty.
4. Hover the `?` icon next to "Base URL" in the form. Tooltip appears
   within ~200 ms with the configured text.
5. Refresh the page. The drawer is still collapsed (or open if you
   left it open in step 2) — the localStorage persistence is working.
6. Open the chat view. Hover each of the four chat-bar icons (memory,
   focus, skill, persona). Each shows a multi-sentence label whose
   first sentence describes what the setting does.
7. Open the project settings tab in chat. The three chat-default
   dropdowns each have a `?` icon next to their label; hovering shows
   the same WHAT-it-does copy minus the cycle hint.
8. Open Tasks. The Schedule dropdown's label has a `?` icon; hovering
   shows the cron-syntax hint.
9. Resize the browser to mobile width. The provider drawer table
   wraps or scrolls horizontally without breaking the surrounding
   layout. (Acceptable: horizontal-scroll inside the drawer.)

## Out of scope

- **The other 11 configuration surfaces** (Personality, Personas,
  Skills, MCP, Connections, Sources, Projects, Web Search providers,
  Skill Editor, GitHub Sources, Project MCP panel). Each gets its own
  focused ship using the primitives from Section 1.
- **Translating help copy** — English only, matches the rest of the app.
- **Visual companion / animation polish** beyond the basic
  open/closed transition. The drawer animates with Tailwind's
  built-in transition classes; no custom motion library.
- **Test infrastructure for the frontend.** Verification stays manual
  this ship. Adding Vitest is its own ship.
- **A backend "providers catalog" API.** The provider list is a
  static frontend const; a future ship can move it server-side if a
  hosted-provider directory ever materializes.

## Maintenance notes

- `LLM_PROVIDERS` lives in one file. Adding a new provider is a
  10-line append; no other file needs to change.
- The `storageKey` convention for `HelpDrawer` should follow
  `help.<surface-name>` (e.g. `help.llm-providers`,
  `help.scheduled-tasks-cron`). This namespaces drawer state so
  future help drawers don't collide.
- Help copy lives inline next to the component it documents. No
  central translation file (we're English-only). When future ships
  add help, they add copy at the same site as the field.
