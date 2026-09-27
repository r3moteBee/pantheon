# Skill-Discovery Default + Cross-Project Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `'auto'` the new out-of-the-box default for the chat-bar Auto-skill toggle, and persist the user's chosen mode to localStorage so a fresh project inherits the last preference instead of falling back to the old hardcoded `'off'`.

**Architecture:** Two small frontend edits. The Zustand store reads `chat_skill_discovery` from localStorage at module load and writes to it on every set. The mount/project-switch effect in `ChatActions.jsx` drops its hardcoded `'off'` fallback so an unset backend value leaves the user's local preference intact. Per-project backend value still wins when present.

**Tech Stack:** React + Zustand store, no backend changes, no test infrastructure (verification is manual per the spec).

**Spec:** `docs/superpowers/specs/2026-05-09-skill-discovery-default-and-persistence-design.md`

---

## File Structure

| Path | Change |
|---|---|
| `frontend/src/store/index.js` | Read `chat_skill_discovery` from localStorage on init; setter writes to localStorage |
| `frontend/src/components/ChatActions.jsx` | Drop the hardcoded `'off'` fallback in the mount/project-switch effect; only adopt the backend value when one exists |
| `frontend/package.json` | Bump version |

---

## Task 1: Store — localStorage-backed `skillDiscovery`

**Files:**
- Modify: `frontend/src/store/index.js`

The store currently hardcodes `skillDiscovery: 'off'` and the setter only mutates in-memory state. Bootstrap from localStorage with a default of `'auto'`, and have the setter write to localStorage on every change.

- [ ] **Step 1: Read the current store init**

The relevant lines around 38-44 of `frontend/src/store/index.js`:

```js
  // Chat-bar settings (lifted out of Chat.jsx so the unified top bar can
  // render them as icons across tab switches)
  memoryRecall: true,
  setMemoryRecall: (v) => set({ memoryRecall: !!v }),
  contextFocus: 'balanced',           // 'broad' | 'balanced' | 'focused'
  setContextFocus: (v) => set({ contextFocus: v }),
  skillDiscovery: 'off',              // 'off' | 'suggest' | 'auto'
  setSkillDiscovery: (v) => set({ skillDiscovery: v }),
```

The pattern to mirror is `_initialProjectId` (introduced in the active-project persistence ship) — module-load IIFE that reads localStorage with try/catch.

- [ ] **Step 2: Add the module-load reader near the existing `_initialProjectId`**

Find the existing `_initialProjectId` block at the top of `frontend/src/store/index.js` (immediately after `import { create } from 'zustand'`). Add a sibling block right below it:

```js
// Read the persisted skill-discovery mode once at module load. The
// mode is also persisted per-project in the backend vault — see
// ChatActions.jsx — but localStorage carries the user's cross-project
// preference, so a project that has never set the value inherits the
// last choice rather than falling back to a hardcoded default.
const _initialSkillDiscovery = (() => {
  try { return localStorage.getItem('chat_skill_discovery') || 'auto' }
  catch { return 'auto' }
})()
```

- [ ] **Step 3: Replace the in-store init + setter**

Inside the store body, replace:

```js
  skillDiscovery: 'off',              // 'off' | 'suggest' | 'auto'
  setSkillDiscovery: (v) => set({ skillDiscovery: v }),
```

with:

```js
  skillDiscovery: _initialSkillDiscovery,  // 'off' | 'suggest' | 'auto'
  setSkillDiscovery: (v) => {
    try { localStorage.setItem('chat_skill_discovery', v) } catch {}
    set({ skillDiscovery: v })
  },
```

- [ ] **Step 4: Verify the build still succeeds**

Run: `cd /home/pan/pantheon/frontend && VITE_API_URL="" npm run build 2>&1 | tail -10`

Expected: build completes without errors. The pre-existing chunk-size warning is fine.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/store/index.js
git commit -m "$(cat <<'EOF'
frontend/store: localStorage-backed skillDiscovery (default 'auto')

Bootstrap skillDiscovery from chat_skill_discovery on module load and
write through on every set. Default for fresh installs flips from
'off' to 'auto'. Per-project backend value still wins when present
(see ChatActions.jsx); localStorage just carries the cross-project
preference for projects that have never set the toggle.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: ChatActions — drop the hardcoded `'off'` fallback

**Files:**
- Modify: `frontend/src/components/ChatActions.jsx`

The mount/project-switch effect at lines 39-52 currently overwrites the local value with `'off'` whenever the backend has no value for the active project. After Task 1, the local value carries the user's cross-project preference, so this overwrite undoes the new behavior. Change the fallback so it only adopts the backend value when one actually exists.

- [ ] **Step 1: Read the current mount-effect**

Lines 36-52 of `frontend/src/components/ChatActions.jsx`:

```js
  // Persist skill-discovery mode to backend and rehydrate on project switch.
  // The frontend store alone is insufficient — the chat handlers read this
  // from the backend vault, so a UI-only toggle has no effect.
  React.useEffect(() => {
    const pid = activeProject?.id || 'default-project'
    let cancelled = false
    skillsApi.getDiscovery(pid).then((res) => {
      if (cancelled) return
      const remote = res?.data?.skill_discovery || 'off'
      if (remote !== skillDiscovery) {
        // Adopt backend value on mount / project switch (don't overwrite it).
        setSkillDiscovery(remote)
      }
    }).catch(() => { /* offline / no vault — keep local */ })
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProject?.id])
```

- [ ] **Step 2: Replace the effect**

Edit `frontend/src/components/ChatActions.jsx`:

```js
  // Persist skill-discovery mode to backend and rehydrate on project switch.
  // The frontend store alone is insufficient — the chat handlers read this
  // from the backend vault, so a UI-only toggle has no effect.
  //
  // When the backend has no value for the active project, keep the
  // current local value (the user's cross-project preference, populated
  // from localStorage on store init). Only the explicit backend value
  // overrides the local preference.
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
      // No remote value → keep local; nothing to do.
    }).catch(() => { /* offline / no vault — keep local */ })
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeProject?.id])
```

(The only logical change: drop the `|| 'off'` literal and add a truthy guard before adopting `remote`.)

- [ ] **Step 3: Verify the build still succeeds**

Run: `cd /home/pan/pantheon/frontend && VITE_API_URL="" npm run build 2>&1 | tail -10`

Expected: build completes without errors.

- [ ] **Step 4: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/components/ChatActions.jsx
git commit -m "$(cat <<'EOF'
frontend/ChatActions: keep local skillDiscovery when backend is unset

The mount/project-switch effect previously fell back to a hardcoded
'off' literal when the backend had no value for the active project,
which clobbered the user's cross-project preference (now in
localStorage per the companion store change). Drop the literal and
guard the adopt branch on a truthy remote value so unset projects
keep the local default.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Version bump + manual verification handoff

**Files:**
- Modify: `frontend/package.json`

- [ ] **Step 1: Read the current version**

```bash
grep '"version"' /home/pan/pantheon/frontend/package.json
```

Expected: `"version": "2026.05.09.H1",`. Bump to `"2026.05.09.H2"` (today's second ship).

- [ ] **Step 2: Bump the version**

Edit `frontend/package.json`. Change ONLY the `"version"` field to `2026.05.09.H2`.

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
bump version to 2026.05.09.H2 — skill-discovery default + persistence

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Hand-off — manual verification scenarios**

Tell the user the rebuild command and the five scenarios from the spec:

```
Rebuild:
  cd ~/pantheon
  ./stop.sh && pkill -f "uvicorn main:app" 2>/dev/null
  find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
  cd frontend && VITE_API_URL="" npm run build && cd ..
  ./start.sh && sleep 3 && curl -s http://localhost:8000/api/health

Verification (open the UI in a browser; data-tools panel for localStorage):

  1. Fresh install (clear chat_skill_discovery from localStorage AND
     ensure no backend value for the active project): pill shows
     'auto'.
  2. Cycle to 'suggest'. Refresh browser. Pill still shows 'suggest'.
  3. Cycle to 'off'. Switch to a project that has never had
     skill-discovery set. Pill shows 'off' (your local preference),
     not the old 'off' default coming from the literal.
  4. Cycle to 'auto' in project X. Switch to project Y where you
     previously set 'suggest' via the UI. Pill shows 'suggest'
     (backend value for Y still wins).
  5. Open dev console:
       localStorage.setItem('chat_skill_discovery', 'suggest')
     Refresh. Pill shows 'suggest'.

Note: scenarios #3 and #1 produce the same visible pill state ('off')
unless localStorage is empty for #1. The behavioral difference is
where the value comes from — verify by reading
localStorage.getItem('chat_skill_discovery') in the dev console
between scenarios.
```

The user runs the deploy commands themselves; do not ssh.
