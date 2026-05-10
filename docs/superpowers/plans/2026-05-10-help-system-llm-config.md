# Help System + LLM-Config First Application Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build two reusable help primitives (`InfoTooltip`, `HelpDrawer`) and apply them to the LLM endpoints/role-mapping UI plus the four cryptic chat-bar icons, the project-settings chat-defaults panel, and the scheduled-tasks dropdown.

**Architecture:** Two new components under `frontend/src/components/help/` reuse the existing `Tooltip` primitive at `frontend/src/components/Tooltip.jsx`. A static const exports a 13-row provider catalog. The LLM `EndpointList` mounts a collapsible `<HelpDrawer>` above `<AddEndpointForm>` whose rows can pre-fill the form via a `prefill` prop. Per-field hints across multiple surfaces use `<InfoTooltip>` next to existing labels. No backend changes.

**Tech Stack:** React + Tailwind, lucide-react (already a dep, provides `HelpCircle` + `ChevronDown`/`ChevronRight`), no test infrastructure on the frontend (verification is manual per the spec).

**Spec:** `docs/superpowers/specs/2026-05-10-help-system-llm-config-design.md`

---

## File Structure

| Path | Change |
|---|---|
| `frontend/src/components/help/InfoTooltip.jsx` | NEW — `?` icon wrapping the existing `Tooltip` |
| `frontend/src/components/help/HelpDrawer.jsx` | NEW — collapsible inline panel with optional localStorage persistence |
| `frontend/src/components/help/llmProviders.js` | NEW — static 13-row provider catalog |
| `frontend/src/components/settings/EndpointList.jsx` | Mount `<HelpDrawer>` above the form; thread `prefill` state |
| `frontend/src/components/settings/AddEndpointForm.jsx` | Accept `prefill` prop; per-field `<InfoTooltip>`s |
| `frontend/src/components/settings/RoleMappingRow.jsx` | Per-role `<InfoTooltip>` next to label |
| `frontend/src/components/ChatActions.jsx` | Replace four tooltip label strings with WHAT-it-does prefixes |
| `frontend/src/components/chat-tabs/ProjectSettingsPanel.jsx` | Extend `Field` to accept `tooltip` prop; pass hints to three dropdowns |
| `frontend/src/components/TaskMonitor.jsx` | Add label + `<InfoTooltip>` above the schedule `<select>` |
| `frontend/package.json` | Bump version |

---

## Task 1: Build the `InfoTooltip` primitive

**Files:**
- Create: `frontend/src/components/help/InfoTooltip.jsx`

A tiny `?` icon that wraps the existing `Tooltip` to deliver a one-sentence hint on hover. Used inline next to form labels.

- [ ] **Step 1: Create the file**

```jsx
// frontend/src/components/help/InfoTooltip.jsx
import React from 'react'
import { HelpCircle } from 'lucide-react'
import Tooltip from '../Tooltip'

/**
 * Inline `?` icon that shows a one-sentence hint on hover. Use next
 * to a form label or section heading for short clarifications. For
 * richer content (paragraphs, tables, links) use HelpDrawer instead.
 *
 * Wraps the existing Tooltip primitive so hint copy renders through
 * a portal and escapes overflow ancestors.
 */
export default function InfoTooltip({ text, placement = 'top', size = 14 }) {
  if (!text) return null
  return (
    <Tooltip label={text} placement={placement}>
      <button
        type="button"
        aria-label={text}
        className="text-gray-500 hover:text-gray-300 inline-flex items-center align-middle ml-1"
      >
        <HelpCircle width={size} height={size} aria-hidden="true" />
      </button>
    </Tooltip>
  )
}
```

- [ ] **Step 2: Verify the build**

Run: `cd /home/pan/pantheon/frontend && VITE_API_URL="" npm run build 2>&1 | tail -10`

Expected: build completes without errors.

- [ ] **Step 3: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/components/help/InfoTooltip.jsx
git commit -m "$(cat <<'EOF'
frontend/help: add InfoTooltip primitive

Tiny ? icon (lucide HelpCircle) that wraps the existing Tooltip to
deliver a one-sentence hint on hover. Used inline next to form
labels for short clarifications. Subsequent commits apply it across
the LLM-endpoints form, role mapping, project-settings dropdowns,
and the tasks dropdown.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Build the `HelpDrawer` primitive

**Files:**
- Create: `frontend/src/components/help/HelpDrawer.jsx`

A collapsible inline panel for richer help content (tables, paragraphs, links). Header bar is a clickable button that toggles open/closed; optional `storageKey` persists the open state to localStorage so a user who closes it doesn't see it pop back open every load.

- [ ] **Step 1: Create the file**

```jsx
// frontend/src/components/help/HelpDrawer.jsx
import React, { useState } from 'react'
import { HelpCircle, ChevronDown, ChevronRight } from 'lucide-react'

/**
 * Collapsible inline help panel. Header bar is a clickable button
 * that toggles open/closed. Body renders arbitrary children — used
 * for tables, paragraphs, code snippets, external links.
 *
 * Optional storageKey: when set, open state persists to
 * localStorage[storageKey]. Without it, the drawer resets to
 * defaultOpen on each mount.
 *
 * Convention for storageKey: `help.<surface-name>` so namespaces
 * don't collide (e.g. `help.llm-providers`).
 */
function _readPersisted(storageKey, defaultOpen) {
  if (!storageKey) return defaultOpen
  try {
    const v = localStorage.getItem(storageKey)
    if (v === 'true') return true
    if (v === 'false') return false
    return defaultOpen
  } catch {
    return defaultOpen
  }
}

export default function HelpDrawer({
  title,
  children,
  defaultOpen = false,
  storageKey,
}) {
  const [open, setOpen] = useState(() => _readPersisted(storageKey, defaultOpen))

  const toggle = () => {
    const next = !open
    setOpen(next)
    if (storageKey) {
      try { localStorage.setItem(storageKey, String(next)) } catch {}
    }
  }

  const Chevron = open ? ChevronDown : ChevronRight

  return (
    <div className="border border-gray-700 rounded-md bg-gray-900/30 overflow-hidden">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-300 hover:bg-gray-800/50"
      >
        <Chevron className="w-4 h-4 text-gray-500" aria-hidden="true" />
        <HelpCircle className="w-4 h-4 text-gray-500" aria-hidden="true" />
        <span className="flex-1 text-left">{title}</span>
      </button>
      {open && (
        <div className="px-3 py-3 border-t border-gray-800">
          {children}
        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verify the build**

Run: `cd /home/pan/pantheon/frontend && VITE_API_URL="" npm run build 2>&1 | tail -10`

Expected: build completes without errors.

- [ ] **Step 3: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/components/help/HelpDrawer.jsx
git commit -m "$(cat <<'EOF'
frontend/help: add HelpDrawer primitive

Collapsible inline panel for richer help content (tables, paragraphs,
links). Header is a clickable button row; body renders arbitrary
children. Optional storageKey persists the open state to localStorage
so a user who closes it doesn't see it pop back open every load.
Convention: help.<surface-name> for the storageKey to namespace state.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Provider catalog + LLM `HelpDrawer` mount

**Files:**
- Create: `frontend/src/components/help/llmProviders.js`
- Modify: `frontend/src/components/settings/EndpointList.jsx`

A static 13-row catalog of common LLM providers (URL, api_type, signup link). Mounted as a collapsible drawer above the AddEndpointForm with a "Use this" button per row that pre-fills the form via a `prefill` prop. Form prefill wiring lands in Task 4 — this task introduces the state and the visual.

- [ ] **Step 1: Create the provider catalog**

```js
// frontend/src/components/help/llmProviders.js
//
// Common LLM endpoint URLs surfaced in the Settings → LLM help drawer.
// Adding a provider: append a row. Removing: drop the row. No backend
// round-trip; this is intentionally a static const so it's trivial to
// maintain and ships in the bundle without a fetch.

export const LLM_PROVIDERS = [
  { name: 'OpenAI',
    base_url: 'https://api.openai.com/v1',
    api_type: 'openai',
    signup_url: 'https://platform.openai.com/api-keys',
    signup_label: 'platform.openai.com' },
  { name: 'Anthropic',
    base_url: 'https://api.anthropic.com/v1',
    api_type: 'anthropic',
    signup_url: 'https://console.anthropic.com/settings/keys',
    signup_label: 'console.anthropic.com' },
  { name: 'Google Gemini',
    base_url: 'https://generativelanguage.googleapis.com/v1beta/openai/',
    api_type: 'openai',
    signup_url: 'https://aistudio.google.com/apikey',
    signup_label: 'aistudio.google.com' },
  { name: 'OpenRouter',
    base_url: 'https://openrouter.ai/api/v1',
    api_type: 'openai',
    signup_url: 'https://openrouter.ai/keys',
    signup_label: 'openrouter.ai/keys' },
  { name: 'Abacus.ai (RouteLLM)',
    base_url: 'https://routellm.abacus.ai/v1',
    api_type: 'openai',
    signup_url: 'https://abacus.ai/app/apikeys',
    signup_label: 'abacus.ai' },
  { name: 'Groq',
    base_url: 'https://api.groq.com/openai/v1',
    api_type: 'openai',
    signup_url: 'https://console.groq.com/keys',
    signup_label: 'console.groq.com' },
  { name: 'Together AI',
    base_url: 'https://api.together.xyz/v1',
    api_type: 'openai',
    signup_url: 'https://api.together.xyz/settings/api-keys',
    signup_label: 'api.together.xyz' },
  { name: 'Fireworks',
    base_url: 'https://api.fireworks.ai/inference/v1',
    api_type: 'openai',
    signup_url: 'https://fireworks.ai/api-keys',
    signup_label: 'fireworks.ai' },
  { name: 'DeepInfra',
    base_url: 'https://api.deepinfra.com/v1/openai',
    api_type: 'openai',
    signup_url: 'https://deepinfra.com/dash/api_keys',
    signup_label: 'deepinfra.com' },
  { name: 'xAI Grok',
    base_url: 'https://api.x.ai/v1',
    api_type: 'openai',
    signup_url: 'https://console.x.ai',
    signup_label: 'console.x.ai' },
  { name: 'Mistral',
    base_url: 'https://api.mistral.ai/v1',
    api_type: 'openai',
    signup_url: 'https://console.mistral.ai/api-keys/',
    signup_label: 'console.mistral.ai' },
  { name: 'Ollama (local)',
    base_url: 'http://localhost:11434/v1',
    api_type: 'ollama',
    signup_url: 'https://ollama.com',
    signup_label: 'ollama.com',
    signup_note: 'No key — install locally' },
  { name: 'LM Studio (local)',
    base_url: 'http://localhost:1234/v1',
    api_type: 'openai',
    signup_url: 'https://lmstudio.ai',
    signup_label: 'lmstudio.ai',
    signup_note: 'No key — install locally' },
]

/** Slugify a provider name into an endpoint-name suggestion. */
export function providerNameToSlug(name) {
  return (name || '').toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}
```

- [ ] **Step 2: Update `EndpointList.jsx` to mount the drawer + thread prefill state**

Replace `frontend/src/components/settings/EndpointList.jsx` entirely:

```jsx
import { useEffect, useState } from 'react'
import { llmApi } from '../../api/client'
import EndpointCard from './EndpointCard'
import AddEndpointForm from './AddEndpointForm'
import HelpDrawer from '../help/HelpDrawer'
import { LLM_PROVIDERS, providerNameToSlug } from '../help/llmProviders'

export default function EndpointList({ onChange }) {
  const [endpoints, setEndpoints] = useState([])
  const [loading, setLoading] = useState(true)
  const [prefill, setPrefill] = useState(null)

  const refresh = async () => {
    setLoading(true)
    try {
      setEndpoints(await llmApi.listEndpoints())
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  const handleChange = () => {
    refresh()
    onChange?.()
  }

  const useProvider = (p) => {
    setPrefill({
      name: providerNameToSlug(p.name),
      base_url: p.base_url,
      api_type: p.api_type,
    })
  }

  return (
    <section className='space-y-3'>
      <header className='flex items-center justify-between'>
        <h3 className='text-sm font-semibold text-gray-200'>Endpoints</h3>
        <span className='text-xs text-gray-500'>
          {loading ? '…' : `${endpoints.length} configured`}
        </span>
      </header>
      <div className='space-y-2'>
        {endpoints.map((e) => (
          <EndpointCard key={e.name} endpoint={e} onChange={handleChange} />
        ))}
        {!loading && endpoints.length === 0 && (
          <div className='text-xs text-gray-500 italic'>
            No endpoints yet. Add one below.
          </div>
        )}
      </div>
      <HelpDrawer title='Common LLM providers' storageKey='help.llm-providers'>
        <p className='text-xs text-gray-400 mb-3'>
          Click <strong>Use this</strong> to pre-fill the form below. You'll
          still need to paste an API key from the provider's signup page.
        </p>
        <div className='overflow-x-auto'>
          <table className='w-full text-xs'>
            <thead className='text-gray-500'>
              <tr>
                <th className='text-left font-normal pb-1 pr-3'>Provider</th>
                <th className='text-left font-normal pb-1 pr-3'>Base URL</th>
                <th className='text-left font-normal pb-1 pr-3'>API type</th>
                <th className='text-left font-normal pb-1 pr-3'>Get a key</th>
                <th className='pb-1'></th>
              </tr>
            </thead>
            <tbody className='text-gray-300'>
              {LLM_PROVIDERS.map((p) => (
                <tr key={p.name} className='border-t border-gray-800'>
                  <td className='py-1.5 pr-3'>{p.name}</td>
                  <td className='py-1.5 pr-3'>
                    <code className='font-mono text-[11px] text-gray-400 break-all'>{p.base_url}</code>
                  </td>
                  <td className='py-1.5 pr-3'>
                    <code className='font-mono text-[11px] text-gray-400'>{p.api_type}</code>
                  </td>
                  <td className='py-1.5 pr-3'>
                    <a
                      href={p.signup_url}
                      target='_blank'
                      rel='noopener noreferrer'
                      className='text-brand-400 hover:underline'
                    >
                      {p.signup_label}
                    </a>
                    {p.signup_note && (
                      <span className='text-gray-500 ml-1'>· {p.signup_note}</span>
                    )}
                  </td>
                  <td className='py-1.5'>
                    <button
                      type='button'
                      onClick={() => useProvider(p)}
                      className='text-[11px] px-2 py-0.5 rounded bg-gray-700 hover:bg-gray-600'
                    >
                      Use this
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </HelpDrawer>
      <AddEndpointForm
        onSaved={handleChange}
        prefill={prefill}
        onPrefillConsumed={() => setPrefill(null)}
      />
    </section>
  )
}
```

- [ ] **Step 3: Verify the build**

Run: `cd /home/pan/pantheon/frontend && VITE_API_URL="" npm run build 2>&1 | tail -10`

Expected: build completes without errors. (`AddEndpointForm` doesn't yet consume `prefill` / `onPrefillConsumed` — that lands in Task 4. The unused props are harmless until then.)

- [ ] **Step 4: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/components/help/llmProviders.js frontend/src/components/settings/EndpointList.jsx
git commit -m "$(cat <<'EOF'
frontend/settings: add LLM provider help drawer above EndpointList

13-row static catalog of common LLM providers (URL, api_type, signup
link), surfaced via HelpDrawer above the AddEndpointForm. Each row
has a "Use this" button that sets prefill state. AddEndpointForm
will consume the prefill prop in the next commit.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: AddEndpointForm — consume `prefill` + per-field `InfoTooltip`s

**Files:**
- Modify: `frontend/src/components/settings/AddEndpointForm.jsx`

Wire `prefill` so the "Use this" buttons from Task 3 actually pre-populate Name / Base URL / API type. Add a `?` icon next to each field label.

- [ ] **Step 1: Replace the file**

Replace `frontend/src/components/settings/AddEndpointForm.jsx` entirely:

```jsx
import { useEffect, useRef, useState } from 'react'
import { llmApi } from '../../api/client'
import InfoTooltip from '../help/InfoTooltip'

const API_TYPES = [
  { value: 'openai', label: 'OpenAI / OpenAI-compatible', placeholder: 'https://api.openai.com/v1' },
  { value: 'anthropic', label: 'Anthropic', placeholder: 'https://api.anthropic.com' },
  { value: 'ollama', label: 'Ollama', placeholder: 'http://localhost:11434/v1' },
  { value: 'custom', label: 'Custom', placeholder: 'https://your.endpoint/v1' },
]

const HINTS = {
  name: "A short label for this endpoint (e.g. openai, local-ollama). Used internally; you won't see it in chat.",
  api_type: "Pick the protocol the endpoint speaks. Most cloud providers and OpenRouter speak openai. Anthropic uses its own. Use ollama for local Ollama.",
  base_url: "The root URL the API responds at — usually ends in /v1. See the help drawer above for common values.",
  api_key: "Stored encrypted in the local vault and never leaves your machine.",
}

export default function AddEndpointForm({ onSaved, prefill, onPrefillConsumed }) {
  const [name, setName] = useState('')
  const [apiType, setApiType] = useState('openai')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [testResult, setTestResult] = useState(null)
  const formRef = useRef(null)

  const apiTypeMeta = API_TYPES.find((t) => t.value === apiType)

  // Consume prefill from the help drawer's "Use this" buttons. We
  // overwrite the three known fields, leave api_key alone (the user
  // pastes that), and scroll the form into view so they see the change
  // even when the drawer is below the fold.
  useEffect(() => {
    if (!prefill) return
    if (prefill.name) setName(prefill.name)
    if (prefill.api_type) setApiType(prefill.api_type)
    if (prefill.base_url) setBaseUrl(prefill.base_url)
    setTestResult(null)
    setError('')
    if (formRef.current) {
      formRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
    onPrefillConsumed?.()
  }, [prefill, onPrefillConsumed])

  const handleTest = async () => {
    setError('')
    setTestResult(null)
    setBusy(true)
    try {
      const r = await llmApi.probe({
        base_url: baseUrl, api_type: apiType, api_key: apiKey,
      })
      setTestResult(r)
    } catch (e) {
      setError(String(e?.response?.data?.detail || e?.message || e))
    } finally {
      setBusy(false)
    }
  }

  const handleSave = async (e) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await llmApi.saveEndpoint({
        name, base_url: baseUrl, api_type: apiType, api_key: apiKey || null,
      })
      setName('')
      setBaseUrl('')
      setApiKey('')
      setTestResult(null)
      onSaved?.()
    } catch (e) {
      setError(String(e?.response?.data?.detail || e?.message || e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      ref={formRef}
      onSubmit={handleSave}
      className='border border-gray-700 rounded-md p-3 bg-gray-900/30 space-y-3'
    >
      <div className='grid grid-cols-1 md:grid-cols-2 gap-3'>
        <div>
          <label className='block text-xs text-gray-400 mb-1'>
            Name <InfoTooltip text={HINTS.name} />
          </label>
          <input
            type='text'
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder='e.g. local-ollama'
            required
            className='w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm'
          />
        </div>
        <div>
          <label className='block text-xs text-gray-400 mb-1'>
            API type <InfoTooltip text={HINTS.api_type} />
          </label>
          <select
            value={apiType}
            onChange={(e) => setApiType(e.target.value)}
            className='w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm'
          >
            {API_TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label className='block text-xs text-gray-400 mb-1'>
          Base URL <InfoTooltip text={HINTS.base_url} />
        </label>
        <input
          type='url'
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder={apiTypeMeta?.placeholder || ''}
          required
          className='w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm'
        />
      </div>
      <div>
        <label className='block text-xs text-gray-400 mb-1'>
          API key <InfoTooltip text={HINTS.api_key} />
        </label>
        <div className='flex gap-2'>
          <input
            type={showKey ? 'text' : 'password'}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder='(leave blank for Ollama / open endpoints)'
            className='flex-1 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm'
          />
          <button
            type='button'
            onClick={() => setShowKey((v) => !v)}
            className='text-xs px-2 py-1 rounded bg-gray-700'
          >
            {showKey ? 'Hide' : 'Show'}
          </button>
        </div>
      </div>
      <div className='flex items-center gap-2'>
        <button
          type='button'
          onClick={handleTest}
          disabled={busy || !baseUrl}
          className='text-xs px-3 py-1 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-50'
        >
          {busy ? 'Testing…' : 'Test'}
        </button>
        <button
          type='submit'
          disabled={busy || !name || !baseUrl}
          className='text-xs px-3 py-1 rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50'
        >
          Save endpoint
        </button>
        {testResult && (
          <span className={`text-xs ${testResult.ok ? 'text-emerald-300' : 'text-red-300'}`}>
            {testResult.ok
              ? `OK — ${testResult.models.length} models`
              : `Failed: ${testResult.error}`}
          </span>
        )}
        {error && <span className='text-xs text-red-300'>{error}</span>}
      </div>
    </form>
  )
}
```

- [ ] **Step 2: Verify the build**

Run: `cd /home/pan/pantheon/frontend && VITE_API_URL="" npm run build 2>&1 | tail -10`

Expected: build completes without errors.

- [ ] **Step 3: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/components/settings/AddEndpointForm.jsx
git commit -m "$(cat <<'EOF'
frontend/settings: AddEndpointForm consumes prefill + per-field hints

Wire the prefill prop so the help-drawer "Use this" buttons populate
Name / API type / Base URL (api_key left for the user). Scroll the
form into view on prefill so the change is visible when the drawer
is above the fold. Add ? icon InfoTooltips next to each of the four
field labels with concrete one-sentence hints.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: RoleMappingRow — per-role `InfoTooltip`

**Files:**
- Modify: `frontend/src/components/settings/RoleMappingRow.jsx`

Each row already takes `label` (e.g. "chat") and `description` (e.g. "Main agent loop"). Add an `InfoTooltip` next to the label whose copy is a one-sentence WHAT-it-does — richer than the existing inline `description`.

- [ ] **Step 1: Edit the file**

The lookup table can be a const inside the file. Replace `frontend/src/components/settings/RoleMappingRow.jsx`:

```jsx
import { useEffect, useState } from 'react'
import { llmApi } from '../../api/client'
import InfoTooltip from '../help/InfoTooltip'

const ROLE_HINTS = {
  chat: 'The main agent loop — every chat message goes through this model.',
  prefill: 'Cheap helper for short structured tasks (titles, tags, summaries). Falls back to chat if unset.',
  vision: 'Image-aware completions when an artifact has visuals. Falls back to chat if unset.',
  embed: 'Embeddings model for semantic memory + similarity. No fallback — must be set or semantic search is disabled.',
  rerank: 'Re-orders semantic-search results. Falls back to embed-only ranking if unset.',
}

export default function RoleMappingRow({
  role, label, description, endpoints, value, onChange,
}) {
  const [models, setModels] = useState([])
  const [probing, setProbing] = useState(false)
  const [probeError, setProbeError] = useState('')

  const selectedEndpoint = value?.endpoint || ''
  const selectedModel = value?.model || ''
  const hint = ROLE_HINTS[role]

  useEffect(() => {
    setModels([])
    setProbeError('')
  }, [selectedEndpoint])

  const fetchModels = async () => {
    if (!selectedEndpoint) return
    setProbing(true)
    setProbeError('')
    try {
      const r = await llmApi.probe({ endpoint_name: selectedEndpoint })
      if (r.ok) {
        setModels(r.models)
      } else {
        setProbeError(r.error || 'probe failed')
      }
    } finally {
      setProbing(false)
    }
  }

  return (
    <div className='grid grid-cols-12 gap-2 items-center py-2 border-b border-gray-800'>
      <div className='col-span-3'>
        <div className='text-sm text-gray-200'>
          {label}
          {hint && <InfoTooltip text={hint} />}
        </div>
        <div className='text-xs text-gray-500'>{description}</div>
      </div>
      <div className='col-span-4'>
        <select
          value={selectedEndpoint}
          onChange={(e) => onChange({ endpoint: e.target.value, model: '' })}
          className='w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm'
        >
          <option value=''>— unassigned —</option>
          {endpoints.map((ep) => (
            <option key={ep.name} value={ep.name}>{ep.name}</option>
          ))}
        </select>
      </div>
      <div className='col-span-5 flex gap-2'>
        {models.length > 0 ? (
          <select
            value={selectedModel}
            onChange={(e) => onChange({ endpoint: selectedEndpoint, model: e.target.value })}
            className='flex-1 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm'
          >
            <option value=''>— pick a model —</option>
            {models.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        ) : (
          <input
            type='text'
            value={selectedModel}
            onChange={(e) => onChange({ endpoint: selectedEndpoint, model: e.target.value })}
            placeholder='model id'
            disabled={!selectedEndpoint}
            className='flex-1 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm disabled:opacity-50'
          />
        )}
        <button
          type='button'
          onClick={fetchModels}
          disabled={!selectedEndpoint || probing}
          className='text-xs px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-50'
        >
          {probing ? '…' : 'Fetch'}
        </button>
      </div>
      {probeError && (
        <div className='col-span-12 text-xs text-red-300 pl-3'>{probeError}</div>
      )}
    </div>
  )
}
```

- [ ] **Step 2: Verify the build**

Run: `cd /home/pan/pantheon/frontend && VITE_API_URL="" npm run build 2>&1 | tail -10`

Expected: build completes without errors.

- [ ] **Step 3: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/components/settings/RoleMappingRow.jsx
git commit -m "$(cat <<'EOF'
frontend/settings: per-role InfoTooltip on RoleMappingRow

Adds a ? icon next to each role label (chat / prefill / vision /
embed / rerank) whose hint copy is a richer WHAT-it-does plus
fallback semantics — distinct from the brief inline description that
already renders below the label.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Other surface applications

**Files:**
- Modify: `frontend/src/components/ChatActions.jsx`
- Modify: `frontend/src/components/chat-tabs/ProjectSettingsPanel.jsx`
- Modify: `frontend/src/components/TaskMonitor.jsx`

Three small applications: rewrite the four chat-bar tooltip strings, extend `Field` in ProjectSettingsPanel to accept a tooltip prop and pass hints to the three chat-default dropdowns, and add a labelled tooltip above the schedule dropdown in TaskMonitor.

- [ ] **Step 1: Rewrite the four chat-bar `IconButton` labels**

Edit `frontend/src/components/ChatActions.jsx` lines 119-145. Replace the four `IconButton` blocks (Sparkles / Target / Wand2 / UserCircle) with:

```jsx
      <IconButton
        icon={Sparkles}
        label={memoryRecall
          ? 'Memory recall: ON. The agent searches past chats and indexed files for relevant context before answering. Click to toggle.'
          : 'Memory recall: OFF. The agent searches past chats and indexed files for relevant context before answering. Click to toggle.'}
        active={memoryRecall}
        activeColor="text-brand-400"
        onClick={() => setMemoryRecall(!memoryRecall)}
      />
      <IconButton
        icon={Target}
        label={`Thread focus: ${contextFocus}. How tightly the agent stays on the current message vs. the wider conversation. Cycle: broad → balanced → focused.`}
        toneClass={focusTone}
        onClick={() => cycle(contextFocus, ['broad', 'balanced', 'focused'], setContextFocus)}
      />
      <IconButton
        icon={Wand2}
        label={`Auto-skill: ${skillDiscovery}. Whether the agent auto-loads matching skills (off = manual /skill only; suggest = ask first; auto = load silently). Cycle: off → suggest → auto.`}
        toneClass={skillTone}
        onClick={cycleSkillDiscovery}
      />
      <IconButton
        icon={UserCircle}
        label={`Persona presence: ${personalityWeight}. How strongly the persona's tone colors responses. Cycle: minimal → balanced → strong.`}
        toneClass={personaTone}
        onClick={() => cycle(personalityWeight, ['minimal', 'balanced', 'strong'], setPersonalityWeight)}
      />
```

(Pure copy edits — `IconButton` already wraps in `Tooltip` with the `label` string.)

- [ ] **Step 2: Extend `Field` in `ProjectSettingsPanel.jsx` to accept a `tooltip` prop**

Find the `Field` component near the bottom of `frontend/src/components/chat-tabs/ProjectSettingsPanel.jsx` (~line 292). Replace it with:

```jsx
function Field({ label, tooltip, children }) {
  return (
    <div>
      <label className="block text-xs text-gray-400 mb-1">
        {label}
        {tooltip && <InfoTooltip text={tooltip} />}
      </label>
      {children}
    </div>
  )
}
```

Add the import at the top of the same file (next to the other component imports):

```jsx
import InfoTooltip from '../help/InfoTooltip'
```

Then update the three dropdown call sites near line 211-238 to pass `tooltip` props:

```jsx
            <Field
              label="Tone weight"
              tooltip="How strongly the persona's tone colors responses. Use minimal for matter-of-fact answers, strong for fully in-character."
            >
              <select
                value={chatDefaults.tone_weight}
                onChange={(e) => updateChat('tone_weight', e.target.value)}
                className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-800 text-sm"
              >
                {TONES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
            <Field
              label="Context focus"
              tooltip="How tightly the agent stays on the current message vs. the wider conversation."
            >
              <select
                value={chatDefaults.context_focus}
                onChange={(e) => updateChat('context_focus', e.target.value)}
                className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-800 text-sm"
              >
                {TONES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
            <Field
              label="Skill discovery"
              tooltip="Whether the agent auto-loads matching skills: off = manual /skill only; suggest = ask first; auto = load silently."
            >
              <select
                value={chatDefaults.skill_discovery}
                onChange={(e) => updateChat('skill_discovery', e.target.value)}
                className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-800 text-sm"
              >
                {SKILLS.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </Field>
```

- [ ] **Step 3: Add a labelled tooltip above the schedule dropdown in `TaskMonitor.jsx`**

The current schedule `<select>` lives at lines 46-57 with no `<label>`. Add an InfoTooltip import at the top of the file (next to other imports):

```jsx
import InfoTooltip from './help/InfoTooltip'
```

Then wrap the `<select>` with a label/tooltip pair. Replace the existing `<select>` block (lines 46-57) with:

```jsx
      <div>
        <label className='block text-xs text-gray-400 mb-1'>
          Schedule
          <InfoTooltip text="Pick a preset to run once now, on a daily/weekly/monthly cadence, or every N hours. cron syntax is standard 5-field (min hour day month weekday) — e.g. 0 9 * * * = 9am daily." />
        </label>
        <select
          value={schedule}
          onChange={(e) => setSchedule(e.target.value)}
          className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
        >
          <option value="now">Run Now (once)</option>
          <option value="0 9 * * *">Daily at 9am</option>
          <option value="0 9 * * 1">Weekly (Monday 9am)</option>
          <option value="0 9 1 * *">Monthly (1st at 9am)</option>
          <option value="interval:60">Every Hour</option>
          <option value="interval:360">Every 6 Hours</option>
        </select>
      </div>
```

- [ ] **Step 4: Verify the build**

Run: `cd /home/pan/pantheon/frontend && VITE_API_URL="" npm run build 2>&1 | tail -10`

Expected: build completes without errors.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/components/ChatActions.jsx frontend/src/components/chat-tabs/ProjectSettingsPanel.jsx frontend/src/components/TaskMonitor.jsx
git commit -m "$(cat <<'EOF'
frontend: apply InfoTooltips to chat-bar / project-settings / tasks

- ChatActions: rewrite the four cryptic icon labels (memory recall,
  context focus, auto-skill, persona presence) so each leads with a
  one-sentence WHAT-it-does and keeps the existing toggle/cycle hint.
- ProjectSettingsPanel: extend Field to accept a tooltip prop; pass
  hints to the three chat-default dropdowns (tone_weight,
  context_focus, skill_discovery).
- TaskMonitor: wrap the schedule <select> with a labelled InfoTooltip
  explaining the presets and cron syntax.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Version bump + manual verification handoff

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Read the current version**

```bash
grep '"version"' /home/pan/pantheon/frontend/package.json
```

Expected: `"version": "2026.05.09.H2",`. Bump to `"2026.05.10.H1"` (today's first ship).

- [ ] **Step 2: Bump the version**

Edit `frontend/package.json`. Change ONLY the `"version"` field to `2026.05.10.H1`.

- [ ] **Step 3: Build the frontend**

Run: `cd /home/pan/pantheon/frontend && VITE_API_URL="" npm run build 2>&1 | tail -10`

Expected: build completes without errors.

- [ ] **Step 4: Run the backend integration suite as a sanity check**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ 2>&1 | tail -3`

Expected: 145 passed, 5 skipped (the existing baseline — no backend changes in this ship).

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add frontend/package.json
git commit -m "$(cat <<'EOF'
bump version to 2026.05.10.H1 — help system + LLM config

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Hand-off — manual verification scenarios**

Tell the user the rebuild command and the nine scenarios from the spec:

```
Rebuild:
  cd ~/pantheon
  ./stop.sh && pkill -f "uvicorn main:app" 2>/dev/null
  find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
  cd frontend && VITE_API_URL="" npm run build && cd ..
  ./start.sh && sleep 3 && curl -s http://localhost:8000/api/health

Verification (open the UI in a browser):

  1. Open Settings → LLM. The "Common LLM providers" drawer is
     visible above the AddEndpointForm, collapsed by default. Header
     shows a chevron + ? icon + the title.
  2. Click the drawer header. It expands; the table shows 13 rows.
     Each has a "Use this" button.
  3. Click "Use this" on the OpenAI row. The form below scrolls into
     view; Name shows "openai", Base URL shows
     https://api.openai.com/v1, API type shows "openai". API key
     remains empty.
  4. Hover the ? icon next to "Base URL" in the form. Tooltip appears
     within ~200ms with the configured hint text.
  5. Refresh the page. The drawer's open/closed state persists from
     step 2.
  6. Open the chat view. Hover each of the four chat-bar icons
     (memory, focus, skill, persona). Each shows a multi-sentence
     label whose first sentence describes what the setting does.
  7. Open the project settings tab in chat. The three chat-default
     dropdowns each have a ? icon next to their label; hovering
     shows the configured hint.
  8. Open Tasks. The Schedule dropdown now has a "Schedule" label
     above it with a ? icon; hovering shows the cron-syntax hint.
  9. Resize the browser to mobile width. The provider drawer table
     wraps or scrolls horizontally without breaking the surrounding
     layout.

The user runs the deploy commands themselves; do not ssh.
```
