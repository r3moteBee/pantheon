# Active-project persistence + session-load coupling — design

**Date:** 2026-05-09
**Status:** approved (brainstorm)
**Related:** `frontend/src/store/index.js`, `frontend/src/App.jsx`, `frontend/src/components/Layout.jsx`, `backend/api/conversations.py`

## Goal

Stop the active-project pill from silently reverting to the default
project on page refresh and across navigation. When a chat session is
resumed (whether by clicking history, deep-linking, or via a posted
task-completion message), the active project should switch to match
that session's project so the pill never lies about what the user is
looking at.

## Symptoms (today)

- **Refreshing the page** resets the active project to whatever
  `projects[0]` happens to be (almost always the seeded `default`
  project), even when the user explicitly picked another project.
- **Navigating between Settings / Projects / Chat** sometimes shows the
  same revert. (Actually a downstream effect — Layout doesn't unmount
  across nav, but a user perceives "after I move around it forgets.")
- **Loading a chat session whose history belongs to a different
  project** leaves the pill on the prior project — the user sees X's
  messages with Y in the pill.

## Root cause

Two effects race on every authenticated load:

1. `App.jsx` (lines 62 and 72) fetches the project list and
   unconditionally calls `setActiveProject(projects[0])` — the seeded
   default project, since it sorts first.
2. `Layout.jsx` (lines 45-57) fetches the project list and tries to
   restore from `localStorage["active_project_id"]`, falling back to
   `projects[0]` only when nothing is stored.

Both effects fire near-simultaneously on every load. Whichever HTTP
response arrives last wins. App.jsx tends to win because the parent
component renders (and starts its fetch) first, but timing is not
guaranteed.

The store's initial state also hardcodes
`activeProject: { id: 'default', name: 'Default Project' }`, so for the
first frame after every refresh the project is `default` regardless of
the user's prior choice. Any project-scoped fetch fired during that
first frame hits the wrong project.

`/conversations/{session_id}` and `/conversations/{session_id}/resume`
do not return the session's `project_id`, so the frontend has no way
to know whether a resumed session belongs to a different project.

## Design

Three changes, in three layers.

### 1. Single source of truth for active project

**Store (`frontend/src/store/index.js`)** — read localStorage on init
so the very first frame uses the correct id:

```js
const _initialProjectId = (() => {
  try { return localStorage.getItem('active_project_id') || 'default' }
  catch { return 'default' }
})()

activeProject: { id: _initialProjectId, name: '' },
```

The `name` is intentionally empty until `Layout.jsx`'s effect populates
the projects list and a downstream effect upgrades the activeProject
record with its full data (existing `setActiveProject` calls already
do this — Layout's effect, ProjectSettingsPanel's edit hook, etc.). A
brief empty-name flash in the pill is acceptable; an incorrect
project-id is not.

**App.jsx** — delete both `setActiveProject(projects[0])` calls
(lines 62 and 72). App.jsx is a routing shell and should not own
project state. The two project-list fetches in App.jsx
(`handleLogin`, the `useEffect([authState])`) can also go entirely —
Layout.jsx fetches the list once on mount, which is sufficient.

**Layout.jsx** — no change. Its existing effect at lines 45-57 is the
right pattern: fetch list, restore from localStorage, fall back to
`projects[0]` only when nothing is stored. After App.jsx is cleaned
up, this effect runs unopposed.

### 2. Session-resume syncs active project

**Backend (`backend/api/conversations.py`)** — add `project_id` to the
response of both `get_conversation` and `resume_conversation`. The
session's owning project is in the `conversations` table:

```python
with sqlite3.connect(ep.db_path) as conn:
    row = conn.execute(
        "SELECT project_id FROM conversations WHERE session_id = ?",
        (session_id,),
    ).fetchone()
session_project_id = row[0] if row else None

return {
    "session_id": session_id,
    "messages": messages,
    "message_count": len(messages),
    "project_id": session_project_id,
}
```

Apply the same shape to `get_conversation` (`/conversations/{session_id}`).
The existing `project_id` query parameter on these endpoints is now
purely advisory — kept for backward compatibility, ignored when looking
up the session.

**Frontend** — when the resume response comes back, sync if the
returned `project_id` differs from the active one and the project
exists in the loaded list:

```js
const res = await conversationsApi.resume(sessionId, activeProject?.id || 'default')
const sessionProjectId = res.data?.project_id
if (sessionProjectId && sessionProjectId !== activeProject?.id) {
  const target = projects.find((p) => p.id === sessionProjectId)
  if (target) setActiveProject(target)
}
```

There is currently exactly one consumer: `Chat.jsx:999` calls
`conversationsApi.resume(sessionId, projectId)` from the history-panel
"resume" button. That's the one site to add the sync block to. No
other component renders session messages from `conversationsApi.get`
or `conversationsApi.resume` today.

**Edge cases:**
- Session's project_id refers to a project that no longer exists in
  the loaded list (e.g. project was deleted): `target` is undefined,
  no switch happens, chat content still loads. Acceptable.
- Session's `project_id` is `NULL` (legacy data from before project_id
  was tracked, or a session where it was never set): same handling —
  skip the switch.
- Session's project is found but matches the active project: no-op.

### 3. Audit other writers

After change 1, the writers of `setActiveProject` reduce to:

| Caller | Trigger | Notes |
|---|---|---|
| `Layout.jsx:53` | Mount-effect: restore from localStorage | Owner of initial-load logic |
| `ChatTabs.jsx:118` | User picks a project in the pill picker | User-initiated |
| `Projects.jsx:258` | User picks a project on the Projects page | User-initiated |
| `ProjectSettingsPanel.jsx:99` | User renames active project; refresh record | Maintains name after edit |
| `ProjectSettingsPanel.jsx:136` | Active project deleted; fall back to `default` | Necessary fallback |
| `Chat.jsx` resume code | New: sync to session's project on resume | Per Section 2 |

No other callers exist after the App.jsx deletion. Each remaining
caller has a clear, user-correlated trigger.

## Verification (manual — frontend has no test infrastructure)

The implementer should walk these scenarios and confirm each:

1. Pick project X in the pill picker. Refresh the page (Cmd-R). The
   pill still shows X. Project-scoped panels (artifacts, tasks, files)
   show X's data.
2. Pick project X. Click Settings → Projects → Chat. The pill still
   shows X across all three navigations.
3. Pick project X. In the browser dev console, run
   `localStorage.setItem('active_project_id', 'Y')`. Refresh. The pill
   shows Y.
4. With project X active, resume a session that was created in project
   Y (open the chat history list while in Y, copy a session id, switch
   to X, then trigger resume of that id — e.g. via a posted
   task-completion message that includes the session id). The pill
   switches to Y; chat shows Y's history.
5. With project X active, resume a session whose project Y has been
   deleted. The pill stays on X. Chat content still loads.
6. Fresh install (clear localStorage entirely): pill shows the seeded
   `default` project. No console errors.

## Out of scope (explicit)

- **Persisting the current sessionId across refreshes.** Today,
  sessionId is in-memory only; refreshing always starts a fresh chat.
  That is intentional and orthogonal to this design.
- **Adding a "viewing history from project Y, sending to project X"
  banner.** The user picked the auto-switch behavior; we don't need
  a divergence warning.
- **Per-project sidebar nav state** (e.g. remembering which artifacts
  filter was active per project). Out of scope.
- **Test infrastructure for the frontend.** No tests exist today; this
  spec relies on manual verification. Adding a Vitest harness is its
  own ship.

## Files touched

| File | Change |
|---|---|
| `frontend/src/store/index.js` | Init `activeProject.id` from localStorage |
| `frontend/src/App.jsx` | Delete two `setActiveProject` calls + two project-list fetches |
| `frontend/src/components/Layout.jsx` | No change (already correct) |
| `frontend/src/components/Chat.jsx` | On resume, sync active project to session's project |
| `backend/api/conversations.py` | Add `project_id` to `get_conversation` + `resume_conversation` responses |
| `frontend/package.json` | Bump version (per Pantheon convention) |
