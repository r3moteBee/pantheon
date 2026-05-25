# Pantheon Frontend

React + Vite + Tailwind UI for the Pantheon agent harness. Single-page app served by the FastAPI backend in normal use; runs standalone on the Vite dev server when iterating on UI code.

## Pages

| Path | Page | What it does |
|---|---|---|
| `/chat` | **Chat** | Real-time streaming chat with the agent; tool-call visualization; per-project sessions; in-context tabs (MCP, Personality, Tasks, Settings, Repo binding) |
| `/artifacts` | **Artifacts** | Folder-tree view of saved artifacts. Inline previews for markdown / HTML / PDF / Office / images / SVG / Mermaid diagrams. Version history. Drag-and-drop reorganization. Export Mermaid + SVG as SVG / PNG / PDF. |
| `/memory` | **Memory** | Browse + search the episodic, semantic, graph, and archival tiers. Inspect graph nodes + edges via the force-graph view. |
| `/files` | **Files** | Per-project workspace file explorer (ephemeral scratch — not the same as Artifacts). Upload / download / preview / edit. |
| `/sources` | **Sources** | Source-adapter ingestion UI — drop URLs or video IDs, picks the right adapter, runs the full ingest pipeline. |
| `/tasks` | **Tasks** | Autonomous task scheduler — `now`, `delay:N`, `interval:N`, cron. Per-job logs, rerun, cancel. |
| `/skills` | **Skills** | Skill library — installed skills, security scan dashboard, AI-assisted editor, hub imports. |
| `/mcp` | **MCP Connections** | Manage MCP servers. API-key or OAuth 2.1 (PKCE + DCR + OIDC fallback). Per-tool enable/disable. Tavily usage tracking. Inline edit. |
| `/personas` | **Personas** | Persona library — reusable personality templates. |
| `/personality` | **Personality** | Per-project `soul.md` / `agent.md` editor with live markdown preview. |
| `/projects` | **Projects** | Project CRUD. Each project gets isolated memory, workspace, and personality. |
| `/connections` | **Connections** | GitHub PAT bindings + other integration credentials. |
| `/settings` | **Settings** | Named LLM endpoints + role mapping (chat / prefill / vision / embed / rerank), security log, system settings. |

The Chat tab bar (`chat-tabs/`) surfaces project-scoped panels inline: MCP, Personality, Tasks, Settings, Repo binding — no page navigation needed while chatting.

## Project structure

```
frontend/
├── src/
│   ├── api/client.js              Axios wrappers + WebSocket helper
│   │                              (chatApi, memoryApi, llmApi, mcpApi, artifactsApi,
│   │                               skillsApi, tasksApi, projectsApi, …)
│   ├── store/index.js             Zustand store
│   ├── pages/                     One file per top-level route (14 pages)
│   ├── components/
│   │   ├── Chat.jsx, ChatTabs.jsx, ChatActions.jsx
│   │   ├── Layout.jsx             App shell + sidebar
│   │   ├── MCPConnections.jsx     MCP connection cards + Add/Edit forms
│   │   ├── Mermaid.jsx, ExportMenu.jsx
│   │   ├── ForceGraph.jsx, GraphView.jsx   Graph memory visualization
│   │   ├── Skills.jsx, SkillEditor.jsx, SkillImporter.jsx, SkillScanDashboard.jsx
│   │   ├── settings/              EndpointCard, AddEndpointForm, EndpointList,
│   │   │                          RoleMapping, RoleMappingRow
│   │   ├── chat-tabs/             ProjectMcpPanel, ProjectPersonalityPanel,
│   │   │                          ProjectSettingsPanel, ProjectTasksPanel,
│   │   │                          RepoBindingPanel
│   │   ├── connections/, help/    GitHub PAT UI + in-app help
│   │   └── (plus tools, modals, editors)
│   ├── utils/svgExport.js         SVG → SVG/PNG/PDF download helpers
│   ├── App.jsx                    Router
│   ├── main.jsx                   React root
│   └── index.css                  Tailwind base + custom scrollbar
├── tailwind.config.js             Uses @tailwindcss/typography for prose
├── vite.config.js
├── package.json                   "version" field drives backend version
└── Dockerfile
```

## Install + run

### Dev server (hot reload)

```bash
cd frontend
npm install
npm run dev          # http://localhost:5173 — proxies /api + /ws to :8000
```

The backend must be running at `http://localhost:8000` for the dev server to work (proxy is configured in `vite.config.js`).

### Production build

```bash
npm run build        # writes frontend/dist/
```

The FastAPI backend serves `frontend/dist/` directly — no separate static server needed in production. Running `./start.sh` from the repo root assumes a built `dist/` exists.

### Docker

The frontend builds into the backend container in Docker mode (see `docker-compose.yml`). The standalone frontend Dockerfile is kept but unused by default.

## API integration

All API calls go through `src/api/client.js`. Major namespaces:

```js
import {
  chatApi, memoryApi, filesApi, settingsApi, tasksApi,
  personalityApi, projectsApi, artifactsApi, mcpApi,
  skillsApi, llmApi, connectionsApi
} from './api/client'

await chatApi.send(message, sessionId, projectId)
await mcpApi.startOauth(connectionName)
await artifactsApi.list({ projectId, path: 'NBJ/' })
```

WebSocket chat streaming:

```js
import { createChatSocket } from './api/client'

const socket = createChatSocket((event) => {
  switch (event.type) {
    case 'text_delta':   /* append to message */ break
    case 'tool_call':    /* display tool execution */ break
    case 'tool_result':  /* render result */ break
    case 'done':         /* message complete */ break
  }
})
socket.send(JSON.stringify({ message, session_id, project_id }))
```

Bearer-token auth is added by an Axios interceptor based on `localStorage.auth_token`. For endpoints hit by bare HTML tags (`<a href download>`, `<img src>`), URLs append `?token=…` instead — see `artifactsApi.rawUrl` and `filesApi.downloadUrl`.

## State management

Zustand store at `src/store/index.js`. Major slices: `activeProject`, `sessionId`, `messages`, `isStreaming`, `streamingContent`, `currentToolCalls`, `projects`, `notifications`, `sidebarOpen`.

```js
const messages = useStore((s) => s.messages)
const addMessage = useStore((s) => s.addMessage)
const activeProject = useStore((s) => s.activeProject)
```

## Styling

Tailwind v3 with `@tailwindcss/typography` for `prose` rendering in chat / artifact previews. Dark theme is the default (Pantheon does not have a light theme). `.scrollbar-thin` utility for narrow scrollbars.

## Versioning

`frontend/package.json` `"version"` field (format `YYYY.MM.DD.HXX`) is the **single source of truth for the whole project**. The backend reads it at startup and surfaces it at `GET /api/health`. Bump it on every push.

## Common tasks

**Add a new page:**
1. Create `src/pages/MyPage.jsx`
2. Add route in `src/App.jsx`
3. Add sidebar nav entry in `src/components/Layout.jsx`

**Add a new API method:**
1. Add it to the right namespace in `src/api/client.js`
2. Call from components with `try/catch` and surface errors via `useStore.addNotification`

**Inspect MCP traffic:**
- DevTools → Network → filter `/mcp/` for HTTP calls
- Backend logs (`tail -f ~/pantheon/backend.log | grep MCP`) for the full JSON-RPC + headers

## License

Part of the Pantheon project — MIT.
