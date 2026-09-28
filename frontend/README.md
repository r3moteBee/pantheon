# Pantheon Frontend

React + Vite + Tailwind UI for the Pantheon agent harness. In normal use the FastAPI backend serves the built `dist/`; run the Vite dev server when iterating on UI code.

## Dev server

```bash
cd frontend
npm ci
npm run dev          # http://localhost:5173 — proxies /api and /ws to :8000 (vite.config.js)
```

The backend must be running on `http://localhost:8000`. Change the proxy target in `vite.config.js` if yours is elsewhere.

## Production build

```bash
npm ci
VITE_API_URL="" npm run build     # writes frontend/dist/
npm run preview                   # optional: serve dist/ locally
```

`VITE_API_URL=""` keeps API calls same-origin (the backend serves `dist/`). `./start.sh` refuses to start without a built `dist/`. In Docker mode `frontend/Dockerfile` builds `dist/` into an nginx image that the root `nginx` service fronts on port 80.

Use `npm ci`, not `npm install`: it installs exactly what `package-lock.json` pins. Whenever you change a dependency or bump `"version"`, run `npm install --package-lock-only` and commit the lockfile, or `npm ci` will fail.

## Versioning

`"version"` in `package.json` (format `YYYY.MM.DD.HXX`) is the single source of truth for the whole project. The backend reads it at startup and returns it from `GET /api/health`. Bump it on every push.

## Auth

Login (`POST /api/auth/login`, `pages/LoginPage.jsx`) sets an HttpOnly `pantheon_session` cookie. The Axios instance in `src/api/client.js` uses `withCredentials: true`, so `fetch`, `<img src>`, `<a download>` and the chat WebSocket all authenticate via the cookie. The frontend never sees or stores the token: there is no `localStorage.auth_token` (old copies are removed on load) and URLs never carry `?token=`.

## Routes

Pages are in `src/pages/`, routed in `src/App.jsx`:

| Path | Page |
|---|---|
| `/chat` | Streaming chat. Tabs (`ChatTabs.jsx`): Chat, Memory, Artifacts, Repository, Tasks, Project Settings (`components/chat-tabs/`) |
| `/memory` | Browse/search episodic, semantic, graph and archival memory; force-graph view |
| `/artifacts` | Artifact tree with previews, version history, export (`/files` redirects here) |
| `/skills` | Skill library, security scan, editor, importer |
| `/mcp` | Redirects to Connections → MCP servers |
| `/connections` | MCP servers, GitHub, web-search providers (`/sources` redirects here) |
| `/personas` | Redirects to Settings → Personality (presets) |
| `/projects` | Project CRUD, import/export |
| `/settings` | LLMs, RAG, Channels, Personality, Skills, Tasks, Security, Secrets, System Update (`/personality` and `/tasks` redirect here) |

## Structure

```
src/
├── api/client.js         Axios wrappers per resource (chatApi, llmApi, mcpApi, artifactsApi, …) + createChatSocket()
├── store/index.js        Zustand store
├── pages/                One file per route (lazy-loaded; LoginPage is eager)
├── components/
│   ├── Chat.jsx, ChatTabs.jsx, Layout.jsx, Settings.jsx, MCPConnections.jsx, Skills.jsx, …
│   ├── Markdown.jsx      The one markdown renderer (remark-gfm + mermaid); pass `components` to override
│   ├── chat/             Chat.jsx's parts: useChatSocket, useChatAttachments, Message, ToolCallBlock,
│   │                     ChatComposer, ChatHistoryDrawer, SaveToArtifactModal
│   ├── settings/         One file per Settings tab (LLM endpoints/routing/tuning, RAG, skill hubs,
│   │                     tasks + job runs, security, sandbox, secrets, system update)
│   ├── chat-tabs/        ProjectTasksPanel, ProjectSettingsPanel, RepoBindingPanel
│   ├── connections/      SearchProvidersTab
│   ├── help/             Help drawer, tooltips, provider presets
│   └── SandboxedHtml.jsx Untrusted HTML rendering (sandboxed iframe, no allow-same-origin)
└── utils/svgExport.js    SVG → SVG/PNG/PDF export
```

**Settings → LLMs** renders the `components/settings/` stack: endpoints (add, Probe for models), **Model routing** (ordered models per task class, capability badges, profile editor), **Chat router** (per-turn class selection), **Model usage**, and **Routing tuning**. All of it talks to `/api/llm/*` via `llmApi`. In chat, `/model <class|auto>` or the input's model picker pins a class for the session.

## Lazy loading

Route pages (except LoginPage) are `React.lazy` imports, and heavy libraries load on demand: `mermaid` (`components/Mermaid.jsx`), `jspdf` + `svg2pdf.js` (`utils/svgExport.js`). Keep new heavy dependencies behind a dynamic `import()`.

## Conventions

- All HTTP goes through `src/api/client.js`; surface errors with the store's `addNotification`.
- Anything rendered via `innerHTML` / `dangerouslySetInnerHTML` goes through DOMPurify first; workspace HTML uses `SandboxedHtml`.
- Styling is Tailwind v3 with `@tailwindcss/typography` for `prose` in chat and artifact previews; the UI is dark-themed.
- New page: add `src/pages/MyPage.jsx`, a lazy route in `src/App.jsx`, and a nav entry in `src/components/Layout.jsx`.

## Troubleshooting

- **API calls fail on :5173** — backend not running on :8000, or the `vite.config.js` proxy points elsewhere.
- **Chat does nothing** — check the `/ws/chat` WebSocket in DevTools; in Settings → LLMs, Probe the endpoint (an error means a bad base URL or key) and make sure the `agent` class has a route.
