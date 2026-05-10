# Active-Project Persistence Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the active-project pill from reverting to default on refresh/navigation, and auto-sync the pill when a chat session from a different project is resumed.

**Architecture:** Three coordinated changes. Backend's resume endpoint starts returning the session's `project_id` so the frontend can sync. The frontend store reads `active_project_id` from localStorage on init so the very first frame is correct. App.jsx stops fetching the project list and clobbering — Layout.jsx remains the sole loader. Chat.jsx's `resume()` checks the returned `project_id` and switches the active project when it differs.

**Tech Stack:** FastAPI + SQLite (backend conversations API), React + Zustand (frontend store + components), pytest (backend tests). Frontend has no test infrastructure today; verification is manual per the spec's scenario list.

**Spec:** `docs/superpowers/specs/2026-05-09-active-project-persistence-design.md`

---

## File Structure

| Path | Change |
|---|---|
| `backend/api/conversations.py` | Add `project_id` to `get_conversation` and `resume_conversation` responses |
| `backend/tests/integration/test_conversations_endpoint.py` | NEW — verifies the endpoint returns `project_id` |
| `frontend/src/store/index.js` | Initialize `activeProject.id` from localStorage |
| `frontend/src/App.jsx` | Delete two `setActiveProject(projects[0])` calls + the two project-list fetches |
| `frontend/src/components/Chat.jsx` | In `resume()`, sync active project when the returned `project_id` differs |
| `frontend/package.json` | Bump version |

---

## Task 1: Backend — return `project_id` from conversation endpoints

**Files:**
- Modify: `backend/api/conversations.py`
- Test:   `backend/tests/integration/test_conversations_endpoint.py` (new)

The frontend needs `project_id` in the response of `/conversations/{session_id}` and `/conversations/{session_id}/resume` so it can sync the active-project pill when a session is resumed across projects.

- [ ] **Step 1: Create the test file with the bootstrap stanza**

```python
# backend/tests/integration/test_conversations_endpoint.py
"""Integration tests for /conversations endpoints.

Verifies that /conversations/{session_id} and /resume return the
session's owning project_id so the frontend can sync the
active-project pill on session resume.

Run: pytest backend/tests/integration/test_conversations_endpoint.py -v
"""
from __future__ import annotations

import os

os.environ.setdefault("DATA_DIR", "/tmp/pantheon-tests-data")
os.makedirs("/tmp/pantheon-tests-data/db", exist_ok=True)

import pytest
from fastapi.testclient import TestClient
```

- [ ] **Step 2: Write the failing tests**

Append to `backend/tests/integration/test_conversations_endpoint.py`:

```python
@pytest.fixture
def client_with_session():
    """Spin up the FastAPI app, seed one conversation in a non-default project,
    and yield (client, project_id, session_id, message_id)."""
    from main import app
    from memory.episodic import EpisodicMemory
    import asyncio

    project_id = "test-proj-malegis"
    session_id = "sess-test-resume-sync"

    ep = EpisodicMemory()
    asyncio.get_event_loop().run_until_complete(
        ep.save_message(
            project_id=project_id,
            session_id=session_id,
            role="user",
            content="hello from a non-default project",
        )
    )
    client = TestClient(app)
    try:
        yield client, project_id, session_id
    finally:
        # Clean up the seeded session so reruns are deterministic.
        import sqlite3
        with sqlite3.connect(ep.db_path) as conn:
            conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
            conn.commit()


def test_get_conversation_returns_project_id(client_with_session):
    client, project_id, session_id = client_with_session
    r = client.get(f"/api/conversations/{session_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == session_id
    assert body["project_id"] == project_id


def test_resume_returns_project_id(client_with_session):
    client, project_id, session_id = client_with_session
    r = client.post(f"/api/conversations/{session_id}/resume")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["session_id"] == session_id
    assert body["project_id"] == project_id


def test_get_conversation_unknown_session_404(client_with_session):
    client, _, _ = client_with_session
    r = client.get("/api/conversations/does-not-exist-zzz")
    assert r.status_code == 404
```

- [ ] **Step 3: Run the new tests — expected to fail**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_conversations_endpoint.py -v`

Expected: `test_get_conversation_returns_project_id` and `test_resume_returns_project_id` both FAIL with `KeyError: 'project_id'` (or assertion error showing the key missing).

- [ ] **Step 4: Read the current `get_conversation` and `resume_conversation` handlers**

The current code is in `backend/api/conversations.py`:

```python
@router.get("/conversations/{session_id}")
async def get_conversation(
    session_id: str,
    project_id: str = Query("default"),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    ep = EpisodicMemory()
    messages = await ep.get_history(session_id=session_id, limit=limit)
    if not messages:
        # Still return session metadata if it exists
        with sqlite3.connect(ep.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM conversations WHERE session_id = ?", (session_id,)
            ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="conversation not found")
    return {
        "session_id": session_id,
        "messages": messages,
        "count": len(messages),
    }


@router.post("/conversations/{session_id}/resume")
async def resume_conversation(
    session_id: str,
    project_id: str = Query("default"),
) -> dict[str, Any]:
    """Returns the rehydrated conversation context.

    The frontend uses this to load the message history into the chat
    pane and continue the same session. AgentCore.from_session() is
    used by the WebSocket handler when subsequent messages arrive
    with the same session_id.
    """
    ep = EpisodicMemory()
    messages = await ep.get_history(session_id=session_id, limit=500)
    if not messages:
        raise HTTPException(status_code=404, detail="no messages for session")
    return {
        "session_id": session_id,
        "messages": messages,
        "message_count": len(messages),
    }
```

- [ ] **Step 5: Add a small helper that looks up the session's project**

Insert this helper near the top of `backend/api/conversations.py` (right after `router = APIRouter()`):

```python
def _lookup_session_project_id(ep: EpisodicMemory, session_id: str) -> str | None:
    """Return the owning project_id for a session, or None if the
    session row is missing. Read from the conversations table; messages
    also carry project_id but the conversation row is the canonical
    home."""
    with sqlite3.connect(ep.db_path) as conn:
        row = conn.execute(
            "SELECT project_id FROM conversations WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    return row[0] if row else None
```

- [ ] **Step 6: Update `get_conversation` to include `project_id`**

Replace the function body:

```python
@router.get("/conversations/{session_id}")
async def get_conversation(
    session_id: str,
    project_id: str = Query("default"),
    limit: int = Query(500, ge=1, le=2000),
) -> dict[str, Any]:
    ep = EpisodicMemory()
    messages = await ep.get_history(session_id=session_id, limit=limit)
    session_project_id = _lookup_session_project_id(ep, session_id)
    if not messages and session_project_id is None:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {
        "session_id": session_id,
        "messages": messages,
        "count": len(messages),
        "project_id": session_project_id,
    }
```

(Note: the previous body issued a second sqlite read inside the `if not messages` branch to check existence; the helper folds that into one read on every call, simplifying the flow.)

- [ ] **Step 7: Update `resume_conversation` to include `project_id`**

Replace the function body:

```python
@router.post("/conversations/{session_id}/resume")
async def resume_conversation(
    session_id: str,
    project_id: str = Query("default"),
) -> dict[str, Any]:
    """Returns the rehydrated conversation context.

    The frontend uses this to load the message history into the chat
    pane and continue the same session. AgentCore.from_session() is
    used by the WebSocket handler when subsequent messages arrive
    with the same session_id. Also returns the session's owning
    project_id so the frontend can sync the active-project pill when
    a user resumes a session from a different project than the one
    currently active.
    """
    ep = EpisodicMemory()
    messages = await ep.get_history(session_id=session_id, limit=500)
    if not messages:
        raise HTTPException(status_code=404, detail="no messages for session")
    session_project_id = _lookup_session_project_id(ep, session_id)
    return {
        "session_id": session_id,
        "messages": messages,
        "message_count": len(messages),
        "project_id": session_project_id,
    }
```

- [ ] **Step 8: Run the tests — expected to pass**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/test_conversations_endpoint.py -v`

Expected: 3 tests pass.

- [ ] **Step 9: Run the full integration suite to confirm no regressions**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ -v 2>&1 | tail -5`

Expected: previously-passing tests still pass; new test count is 3 above the prior baseline.

- [ ] **Step 10: Commit**

```bash
cd /home/pan/pantheon
git add backend/api/conversations.py backend/tests/integration/test_conversations_endpoint.py
git commit -m "$(cat <<'EOF'
conversations: return project_id from get + resume responses

Frontend needs the session's owning project_id so it can sync the
active-project pill when a user resumes a session from a different
project. Adds _lookup_session_project_id helper, includes the field
in both /conversations/{session_id} and /resume payloads, and adds an
integration test that seeds a session in a non-default project and
asserts the field round-trips.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Frontend store — initialize `activeProject` from localStorage

**Files:**
- Modify: `frontend/src/store/index.js`

The store currently hardcodes `activeProject: { id: 'default', name: 'Default Project' }` as initial state. This is the root of the "first-frame is wrong" bug. Read the persisted id on init so any project-scoped fetch fired during the first render targets the correct project.

- [ ] **Step 1: Read the current store init**

Lines 1-9 of `frontend/src/store/index.js` currently are:

```js
import { create } from 'zustand'

export const useStore = create((set, get) => ({
  // Active project
  activeProject: { id: 'default', name: 'Default Project' },
  setActiveProject: (project) => {
    try { if (project?.id) localStorage.setItem('active_project_id', project.id) } catch {}
    set({ activeProject: project })
  },
```

- [ ] **Step 2: Replace with the localStorage-bootstrapped version**

Edit `frontend/src/store/index.js`:

```js
import { create } from 'zustand'

// Read the persisted active-project id once at module load. Layout.jsx
// fetches the project list on mount and upgrades activeProject with the
// full record (including name); until then, the id alone is enough for
// any project-scoped fetch to target the right project from the very
// first frame.
const _initialProjectId = (() => {
  try { return localStorage.getItem('active_project_id') || 'default' }
  catch { return 'default' }
})()

export const useStore = create((set, get) => ({
  // Active project
  activeProject: { id: _initialProjectId, name: '' },
  setActiveProject: (project) => {
    try { if (project?.id) localStorage.setItem('active_project_id', project.id) } catch {}
    set({ activeProject: project })
  },
```

- [ ] **Step 3: Verify the store still parses**

Run: `cd /home/pan/pantheon/frontend && node -e "import('./src/store/index.js').then(m => console.log('ok:', !!m.useStore))" 2>&1 | tail -5`

If node ESM import isn't set up for the frontend, skip this step — the build verification in Task 5 catches syntax errors.

- [ ] **Step 4: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/store/index.js
git commit -m "$(cat <<'EOF'
frontend/store: initialize activeProject id from localStorage

The store previously hardcoded activeProject to {id:'default',...} at
init, so the first frame after every refresh used the wrong project
and any project-scoped fetch fired during that frame hit the default
project. Read active_project_id at module load instead. Layout.jsx's
mount-effect upgrades the record with the full project name once
the project list arrives.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: App.jsx — stop fetching the project list

**Files:**
- Modify: `frontend/src/App.jsx`

App.jsx currently fetches the project list in two places (`handleLogin` and a `useEffect([authState])`) and unconditionally calls `setActiveProject(projects[0])` after each fetch — the seeded `default` project. This races with Layout.jsx's localStorage-restore on every load. Layout.jsx is the right owner of project-list loading, so App.jsx's two fetches need to go away entirely.

- [ ] **Step 1: Read the current App.jsx project-fetching code**

Lines around 19-75 of `frontend/src/App.jsx`. The relevant sections to remove:

```js
import { projectsApi, authApi } from './api/client'

export default function App() {
  const setProjects = useStore((s) => s.setProjects)
  const setActiveProject = useStore((s) => s.setActiveProject)
  // ...

  const handleLogin = (token) => {
    setAuthState(true)
    // Load projects now that we are authenticated
    projectsApi.list().then((res) => {
      const projects = res.data.projects || []
      setProjects(projects)
      if (projects.length > 0) setActiveProject(projects[0])
    }).catch(console.error)
  }

  // Load projects on first authenticated render
  useEffect(() => {
    if (authState === true) {
      projectsApi.list().then((res) => {
        const projects = res.data.projects || []
        setProjects(projects)
        if (projects.length > 0) setActiveProject(projects[0])
      }).catch(console.error)
    }
  }, [authState])
```

- [ ] **Step 2: Remove the two `setActiveProject` callers and the fetches that feed them**

Edit `frontend/src/App.jsx`. Replace the import + the two fetch sites:

```js
// Replace this import line:
import { projectsApi, authApi } from './api/client'

// With this (drop projectsApi):
import { authApi } from './api/client'
```

Remove the unused `setProjects` and `setActiveProject` selectors:

```js
// Delete these two lines from the component body:
const setProjects = useStore((s) => s.setProjects)
const setActiveProject = useStore((s) => s.setActiveProject)
```

Replace `handleLogin` (drop the project fetch):

```js
const handleLogin = (token) => {
  setAuthState(true)
}
```

Delete the entire `useEffect(() => { if (authState === true) { projectsApi.list()... } }, [authState])` block.

- [ ] **Step 3: Verify the file still parses**

Run: `cd /home/pan/pantheon/frontend && node --input-type=module -e "import('./src/App.jsx').catch(e => { console.error(e.message); process.exit(1) }); console.log('ok')" 2>&1 | tail -5`

If JSX-via-node import isn't workable, skip — Task 5's `npm run build` will catch any syntax issue.

- [ ] **Step 4: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/App.jsx
git commit -m "$(cat <<'EOF'
frontend/App: drop project-list fetches that clobbered active project

App.jsx fetched the project list in two places (handleLogin and a
useEffect on authState) and then unconditionally setActiveProject
(projects[0]), racing Layout.jsx's localStorage-restore. Layout.jsx
already owns the project-list fetch and respects localStorage, so
App.jsx's copies are pure clobber. Remove them and the now-unused
imports/selectors. After this commit, the only writers of
activeProject on initial load are Layout.jsx (restore) and the user.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Chat.jsx — sync active project when resuming a foreign session

**Files:**
- Modify: `frontend/src/components/Chat.jsx`

`resume()` at line 997-1009 currently loads the session's messages and sets `sessionId` but ignores the project the session belongs to. With Task 1 in place, the response now carries `project_id`. Sync the active project when it differs from the current pill.

- [ ] **Step 1: Read the current `resume()` body**

The block to modify is around lines 980-1010:

```js
function HistoryPanel() {
  const open = useStore((s) => s.historyOpen)
  const setOpen = useStore((s) => s.setHistoryOpen)
  const projectId = useStore((s) => s.activeProject?.id || 'default')
  const setSessionId = useStore((s) => s.setSessionId)
  const setMessages = useStore((s) => s.setMessages)
  const clearMessages = useStore((s) => s.clearMessages)
  // ...
  const resume = async (sessionId) => {
    try {
      const res = await conversationsApi.resume(sessionId, projectId)
      const msgs = (res.data.messages || []).map((m) => ({
        role: m.role,
        content: m.content,
        timestamp: m.timestamp,
      }))
      clearMessages()
      setMessages(msgs)
      setSessionId(sessionId)
      setOpen(false)
    } catch (e) {
```

- [ ] **Step 2: Add the new selectors and the sync block**

Edit `frontend/src/components/Chat.jsx`. Inside `HistoryPanel`, add two more selectors (next to the existing `setSessionId` etc.):

```js
const projects = useStore((s) => s.projects)
const setActiveProject = useStore((s) => s.setActiveProject)
```

Then update `resume()` so that, after the messages load, it switches the active project if the response says the session belongs to a different one:

```js
const resume = async (sessionId) => {
  try {
    const res = await conversationsApi.resume(sessionId, projectId)
    const msgs = (res.data.messages || []).map((m) => ({
      role: m.role,
      content: m.content,
      timestamp: m.timestamp,
    }))
    clearMessages()
    setMessages(msgs)
    setSessionId(sessionId)

    // Sync active project to match the session's project so the
    // pill never disagrees with the chat content. If the session's
    // project no longer exists in the loaded list, leave the pill
    // alone — chat content still loads.
    const sessionProjectId = res.data?.project_id
    if (sessionProjectId && sessionProjectId !== projectId) {
      const target = projects.find((p) => p.id === sessionProjectId)
      if (target) setActiveProject(target)
    }

    setOpen(false)
  } catch (e) {
    // ... existing catch unchanged
```

(The `catch` block stays as it was — only the `try` body changes.)

- [ ] **Step 3: Commit**

```bash
cd /home/pan/pantheon
git add frontend/src/components/Chat.jsx
git commit -m "$(cat <<'EOF'
frontend/Chat: sync active project on session resume

When resuming a chat session whose project differs from the active
pill, switch the pill to match the session's project. Backend now
returns project_id in the resume payload (companion change). Skips
the switch silently when the session's project no longer exists in
the loaded list — chat content still loads.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Version bump + manual verification

**Files:**
- Modify: `frontend/package.json`

Bump the single source of truth so `/api/health` reports a new build.

- [ ] **Step 1: Read the current version**

```bash
grep '"version"' /home/pan/pantheon/frontend/package.json
```

Expected: `"version": "2026.05.08.H2",` (or a later H suffix from a same-day ship). Today's date is 2026-05-09, so the new version is `"2026.05.09.H1"` (or the next H if H1 is already used today).

- [ ] **Step 2: Bump the version**

Edit `frontend/package.json`. Change only the `"version"` field:

```json
  "version": "2026.05.09.H1",
```

- [ ] **Step 3: Build the frontend**

Run: `cd /home/pan/pantheon/frontend && VITE_API_URL="" npm run build 2>&1 | tail -10`

Expected: build completes without errors. If the build fails, the error usually points at the broken file from Tasks 2-4 — fix and rerun.

- [ ] **Step 4: Run the full backend integration suite once more**

Run: `cd /home/pan/pantheon/backend && ~/pantheon/.venv/bin/python -m pytest tests/integration/ -v 2>&1 | tail -5`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
cd /home/pan/pantheon
git add frontend/package.json
git commit -m "$(cat <<'EOF'
bump version to 2026.05.09.H1 — active-project persistence ship

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 6: Hand-off — manual verification scenarios**

The frontend has no automated test infrastructure today; the spec's verification scenarios must be walked manually after the user redeploys. Tell the user the rebuild command and the six scenarios:

```
Rebuild:
  cd ~/pantheon
  ./stop.sh && pkill -f "uvicorn main:app" 2>/dev/null
  find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
  ./start.sh && sleep 3 && curl -s http://localhost:8000/api/health

Verification (open the UI in a browser; data-tools panel for localStorage):

  1. Pick project X in the pill. Refresh (Cmd-R). Pill still shows X.
     Project-scoped panels (artifacts, tasks, files) show X's data.
  2. Pick project X. Click Settings → Projects → Chat. Pill still
     shows X across all three navigations.
  3. Pick project X. In dev console:
       localStorage.setItem('active_project_id', 'Y')
     Refresh. Pill shows Y.
  4. With X active, resume a session created in Y (open history while
     in Y, copy the session id, switch to X, then trigger resume of
     that id — easiest path: open the chat history panel and click
     a Y-only session). Pill switches to Y. Chat shows Y's history.
  5. With X active, resume a session whose project Y has been
     deleted. Pill stays at X. Chat content still loads.
  6. Fresh install (clear localStorage entirely): pill shows the
     seeded `default` project. No console errors.
```

The user runs the deploy commands themselves; do not ssh.
