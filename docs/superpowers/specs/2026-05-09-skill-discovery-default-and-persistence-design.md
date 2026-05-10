# Skill-discovery default + cross-project persistence — design

**Date:** 2026-05-09
**Status:** approved (brainstorm)
**Related:** `frontend/src/store/index.js`, `frontend/src/components/ChatActions.jsx`

## Goal

Two coordinated tweaks to the chat-bar's "Auto-skill" toggle:

1. **Change the out-of-the-box default to `'auto'`** (currently `'off'`).
2. **Persist the user's chosen mode across projects** so a fresh
   project that has never had skill-discovery set inherits the user's
   last preference rather than defaulting to `'off'`.

## Today

- The store initializes `skillDiscovery: 'off'` in memory (no
  persistence at the store level).
- The mode is persisted **per project** to the backend vault via
  `skillsApi.setDiscovery(projectId, mode)`.
- On mount and on project switch, `ChatActions.jsx` reads the backend
  value with `skillsApi.getDiscovery(projectId)` and falls back to the
  hardcoded literal `'off'` when the backend has no value for that
  project.
- Net result: switching to a project where you've never used the
  toggle resets you to `'off'` regardless of what you picked elsewhere.

## Design

Three changes, all in two files.

### 1. Store reads + writes localStorage

`frontend/src/store/index.js`:

- Add a module-load IIFE that reads `chat_skill_discovery` from
  localStorage (try/catch wrapped) and defaults to `'auto'` when no
  value is present.
- Use that resolved value as the initial state of `skillDiscovery`.
- The `setSkillDiscovery` setter writes the new value to localStorage
  on every change.

```js
const _initialSkillDiscovery = (() => {
  try { return localStorage.getItem('chat_skill_discovery') || 'auto' }
  catch { return 'auto' }
})()

// ...inside the store:
skillDiscovery: _initialSkillDiscovery,
setSkillDiscovery: (v) => {
  try { localStorage.setItem('chat_skill_discovery', v) } catch {}
  set({ skillDiscovery: v })
},
```

### 2. Mount-effect fallback uses the local value

`frontend/src/components/ChatActions.jsx`:

The existing mount/project-switch effect:

```js
React.useEffect(() => {
  const pid = activeProject?.id || 'default-project'
  let cancelled = false
  skillsApi.getDiscovery(pid).then((res) => {
    if (cancelled) return
    const remote = res?.data?.skill_discovery || 'off'
    if (remote !== skillDiscovery) {
      setSkillDiscovery(remote)
    }
  }).catch(() => { /* offline / no vault — keep local */ })
  return () => { cancelled = true }
}, [activeProject?.id])
```

Two changes:

- Drop the hardcoded `'off'` fallback. When the backend has no value,
  keep the current local value (which after Section 1 is the
  user's localStorage preference, defaulting to `'auto'`).
- The "adopt backend value" branch only fires when the backend
  *actually* has a value AND it differs from local.

The new shape:

```js
React.useEffect(() => {
  const pid = activeProject?.id || 'default-project'
  let cancelled = false
  skillsApi.getDiscovery(pid).then((res) => {
    if (cancelled) return
    const remote = res?.data?.skill_discovery
    if (remote && remote !== skillDiscovery) {
      // Backend has a value for this project — adopt it.
      setSkillDiscovery(remote)
    }
    // No remote value → keep local (the user's last preference,
    // already populated from localStorage on store init).
  }).catch(() => { /* offline / no vault — keep local */ })
  return () => { cancelled = true }
}, [activeProject?.id])
```

### 3. Existing setter call sites unchanged

The `cycleSkillDiscovery` handler in `ChatActions.jsx` already calls
`setSkillDiscovery(next)` and then `skillsApi.setDiscovery(pid, next)`.
Section 1 augments the setter to also write localStorage, so no
changes to the cycle handler are needed.

## Behavior matrix

| Scenario | Old behavior | New behavior |
|---|---|---|
| Brand-new install, no backend value | `'off'` | `'auto'` |
| User sets `'auto'` in project X, opens project Y (no backend value) | `'off'` | `'auto'` (inherits from local) |
| User sets `'auto'` in project X, opens project Y where they previously set `'off'` | `'off'` (backend wins) | `'off'` (backend wins) |
| User sets `'suggest'`, refreshes browser | `'off'` | `'suggest'` (localStorage restore) |
| Backend offline | initial value, no errors | initial value (localStorage), no errors |

## Files touched

| File | Change |
|---|---|
| `frontend/src/store/index.js` | localStorage-bootstrapped init + setter writes localStorage |
| `frontend/src/components/ChatActions.jsx` | Mount-effect fallback drops `'off'` literal; only adopts backend value when present |
| `frontend/package.json` | Bump version |

## Verification (manual)

Frontend has no test infrastructure today. Walk these scenarios after
deploy:

1. Fresh install (clear `chat_skill_discovery` from localStorage,
   ensure no backend value for the active project): pill shows
   `'auto'`.
2. Cycle to `'suggest'`. Refresh browser. Pill still shows
   `'suggest'`.
3. Cycle to `'off'`. Switch to a project that has never had
   skill-discovery set. Pill should show `'off'` (your local
   preference), not the old `'off'` default.
4. Cycle to `'auto'` in project X. Switch to project Y where you
   previously set `'suggest'`. Pill shows `'suggest'` (backend value
   for Y still wins).
5. Open dev console, set
   `localStorage.setItem('chat_skill_discovery', 'suggest')`,
   refresh. Pill shows `'suggest'`.

## Out of scope

- The other three chat-bar settings (`memoryRecall`, `contextFocus`,
  `personalityWeight`) have the same in-memory-only character. Same
  treatment could apply to them — defer until requested.
- Migration of existing `'off'` values for users who never deliberately
  picked off (vs. the hardcoded default). Not safe to do automatically;
  any existing localStorage or backend-set value is treated as a
  deliberate choice.
- Per-project default override (e.g. "this project always wants `'off'`
  regardless of my preference"). Already supported via the per-project
  backend value — no UI change needed.
