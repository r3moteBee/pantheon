# Pantheon frontend review (read-only)

Scope: `frontend/src` (Vite + React 18 + Zustand + Tailwind), cross-checked against `backend/api/*.py`, `backend/main.py`, `backend/utils/self_doc.py`, `docs/USAGE.md`. All paths relative to `/home/user/pantheon/`. Line numbers are from the current tree (version `2026.09.27.H6`). `node_modules` is absent in this checkout, so nothing was built; bundle-size statements are inferred from the import graph.

## Summary table

| ID | Issue | Type | Impact | Effort | Files |
|----|-------|------|--------|--------|-------|
| F1 | Chat top-bar toggles for memory recall / thread focus / persona presence only mutate the Zustand store; nothing persists to the backend, which reads these from the vault. The handlers that do persist live in `Chat.jsx` and are never called. | broken | High | S | `frontend/src/components/ChatActions.jsx:126-142`, `frontend/src/components/Chat.jsx:533-584`, `backend/agent/core.py:338-345`, `backend/api/settings.py:142-155` |
| F2 | Project Settings tab writes `persona / tone_weight / context_focus / skill_discovery` to `project_settings`, but no backend code outside `api/projects.py` reads that table. The whole "Chat defaults" section is write-only. Skill discovery is additionally stored in a *second* place (vault `skill_discovery_{project}`) which is the one chat actually uses. | broken | High | M | `frontend/src/components/chat-tabs/ProjectSettingsPanel.jsx:51-104`, `backend/api/projects.py:405-442`, `backend/api/skills.py:186-197`, `backend/api/chat.py:290,632` |
| F3 | 7 orphaned files (~1,500 lines) never imported: `FileRepository.jsx` (780, patched in the Sept security batch), `TaskMonitor.jsx`-only page is reachable but un-navved (see F5), `SourcesPage.jsx`, `FilesPage.jsx`, `PersonalityPage.jsx`, `chat-tabs/ProjectMcpPanel.jsx`, `chat-tabs/ProjectPersonalityPanel.jsx`. `ProjectMcpPanel` imports `projectMcpApi`, which does not exist in `client.js` — importing it anywhere would break the build. | dead-code | Med | S | see Appendix A |
| F4 | Tasks/jobs UI exists in four places: `TaskMonitor.jsx` (route `/tasks`), `chat-tabs/ProjectTasksPanel.jsx` (chat tab), `Settings.jsx` `GlobalTasksSection` (L697) and `TaskRunsSection` (L1024). Three of them re-implement schedule list + cancel + logs. | duplication | Med | M | `frontend/src/components/TaskMonitor.jsx`, `frontend/src/components/chat-tabs/ProjectTasksPanel.jsx`, `frontend/src/components/Settings.jsx:697-1122` |
| F5 | Routes `/tasks`, `/memory`, `/mcp` are registered but absent from `NAV_ITEMS`; `/mcp` renders the same `MCPConnections` component that the Connections page already hosts in a tab. Reachable only by typing the URL. | duplication | Low | S | `frontend/src/App.jsx:77-83`, `frontend/src/components/Layout.jsx:20-28`, `frontend/src/pages/MCPPage.jsx`, `frontend/src/pages/ConnectionsPage.jsx:4,40` |
| F6 | `ChatTabs.jsx` statically imports `MemoryPage` and `ArtifactsPage`, so the default `/chat` chunk pulls in d3 (via MemoryBrowser→GraphView→ForceGraph) and CodeMirror + 3 language packs (via ArtifactsPage). The `React.lazy` split in `App.jsx` is real but the landing route defeats it for the two heaviest libs. mermaid / jspdf / svg2pdf are correctly dynamic. | inconsistency | Med | S | `frontend/src/components/ChatTabs.jsx:10-11`, `frontend/src/components/ForceGraph.jsx`, `frontend/src/pages/ArtifactsPage.jsx:11-14`, `frontend/src/App.jsx:6-18` |
| F7 | 34 methods in `api/client.js` have no caller; `sourcesApi` is used only by dead `SourcesPage`; `taskRunsApi` is imported by `Settings.jsx` but no method is called; `closeChatSocket` is exported and unused. | dead-code | Low | S | `frontend/src/api/client.js:133-146,172-178,628-631` |
| F8 | Error handling reads `e?.response?.data?.detail` in 19 files, but the axios interceptor already rewrites every rejection into `new Error(detail)`, so that branch is always `undefined`. Coexists with 37 `window.alert/confirm` calls vs ~220 `addNotification` toasts. | inconsistency | Low | S | `frontend/src/api/client.js:18-27`, e.g. `frontend/src/components/SkillEditor.jsx` (16 sites), `frontend/src/components/chat-tabs/ProjectTasksPanel.jsx` (7) |
| F9 | Skill-discovery mode is loaded twice per project switch with conflicting fallbacks: `Chat.jsx` forces `'off'` when the backend has nothing, `ChatActions.jsx` deliberately keeps the localStorage value; `ChatActions` also uses project id fallback `'default-project'` while the other 38 sites use `'default'`. | inconsistency | Low | S | `frontend/src/components/Chat.jsx:526-531`, `frontend/src/components/ChatActions.jsx:45-58,65`, `frontend/src/store/index.js:17-20` |
| F10 | Tailwind classes `gray-850` (23×) and `gray-750` (8×) are used but never defined (`tailwind.config.js` only extends `brand`). They compile to nothing, so those backgrounds silently fall back to whatever is behind them. | broken | Low | S | `frontend/tailwind.config.js:9-18`; e.g. `frontend/src/pages/ArtifactsPage.jsx:1045-1237`, `frontend/src/components/Chat.jsx:154,984` |
| F11 | `CoreEditor.jsx` imports `@codemirror/view`, which is not declared in `package.json` (only present transitively through `@uiw/react-codemirror`). | broken | Low | S | `frontend/src/components/CoreEditor.jsx:18`, `frontend/package.json` |
| F12 | Broken link: job detail's artifact cross-reference renders `href="/artifacts?tab="` — no id, malformed query. `ChatTabs` "Manage projects →" and dead `ProjectMcpPanel` use raw `<a href="/…">` instead of router `Link`, forcing a full reload (drops the module-level chat WebSocket). | broken | Low | S | `frontend/src/components/chat-tabs/ProjectTasksPanel.jsx:468`, `frontend/src/components/ChatTabs.jsx:163-168` |
| F13 | HTML artifact preview is rendered inline with `dangerouslySetInnerHTML` + DOMPurify default profile, while workspace HTML goes through the opaque-origin `SandboxedHtml` iframe. Sanitized, so not exploitable as written, but two different trust models for the same kind of content. | security | Low | S | `frontend/src/pages/ArtifactsPage.jsx:897`, `frontend/src/components/SandboxedHtml.jsx:28-33`, `frontend/src/components/FileRepository.jsx` (dead) |
| F14 | `Chat.jsx` is 1,306 lines / 11 components / 24 `useState` + 33 `useStore` selectors + 7 `useEffect`; WS event handling (L664-744), markdown component factory (L218-279), RouteBadge (L315-352), history drawer (L1125-1224) and save modal (L1226-1305) are all independent and splittable. Markdown rendering is configured 5 different ways across 5 files. | inconsistency | Med | M | `frontend/src/components/Chat.jsx`, `frontend/src/components/markdownComponents.jsx`, `frontend/src/components/PersonalityEditor.jsx:413-420`, `frontend/src/components/chat-tabs/ProjectTasksPanel.jsx:626` |
| F15 | Help content is hand-written JSX at 8 call sites; two of them (task schedule help) overlap each other and `docs/USAGE.md §7`; `docs/USAGE.md` is stale relative to the UI (says project switcher is in the sidebar and search chain is under Settings). Nothing in the frontend fetches `/api/system/self-doc`. | duplication | Low | S | `frontend/src/components/help/*`, `frontend/src/components/TaskMonitor.jsx:241-270`, `frontend/src/components/chat-tabs/ProjectTasksPanel.jsx:158-200`, `docs/USAGE.md:16,95` |
| F16 | Stale scaffold docs shipped in `frontend/`: `BUILD_SUMMARY.txt` references `/sessions/friendly-happy-maxwell/mnt/outputs/…`; `QUICKSTART.md` says `npm install` (CLAUDE.md mandates `npm ci`). | dead-code | Low | S | `frontend/BUILD_SUMMARY.txt`, `frontend/QUICKSTART.md` |

Security posture overall is good: cookie-only auth, no token in storage or URLs, DOMPurify on every `innerHTML`/`dangerouslySetInnerHTML` except mermaid's own sanitized output, sandboxed iframe for workspace HTML, `noopener` on every `_blank`. Details in §6.

---

## 1. Component inventory and dead code

Import map (who imports each file) is in Appendix A. Summary:

**Orphaned (never imported anywhere, including `App.jsx`):**

| File | Lines | Notes |
|------|-------|-------|
| `src/pages/FilesPage.jsx` | 5 | Route `/files` is a `Navigate` to `/artifacts` (`App.jsx:73`). |
| `src/components/FileRepository.jsx` | 780 | Only consumer was `FilesPage`. Last touched 2026-09-26 in the security batch (#6) — effort spent hardening dead code. `SandboxedHtml` (its child) is still live via `Chat.jsx:2`. |
| `src/pages/SourcesPage.jsx` | 206 | Route `/sources` redirects to `/connections` (`App.jsx:78`). Sole user of `sourcesApi`. |
| `src/pages/PersonalityPage.jsx` | 44 | Route `/personality` redirects to `/settings` (`App.jsx:81`). Wrapped `PersonalityEditor` + `PersonaLibrary`, both live elsewhere. |
| `src/components/chat-tabs/ProjectMcpPanel.jsx` | 62 | Imports `projectMcpApi` from `client.js` — **no such export exists**. Would fail at build if ever imported. Also links `href="/settings"`. |
| `src/components/chat-tabs/ProjectPersonalityPanel.jsx` | 108 | Superseded by `ProjectSettingsPanel` (same `projectSettingsApi` fields). |

All six were last modified 2026-05-30 except `FileRepository`.

**Live but unreachable from nav (see §2):** `pages/TasksPage.jsx` → `TaskMonitor.jsx` (288 lines), `pages/MCPPage.jsx`, `pages/MemoryPage.jsx` (also embedded in ChatTabs, so its component is live).

**Cluster verdicts**

- *Tasks:* `TaskMonitor.jsx` (live at `/tasks`, un-navved; `tasksApi` only, 30 s poll, own create form) vs `chat-tabs/ProjectTasksPanel.jsx` (live, chat tab; `jobsApi` + `tasksApi.listAll`, plan-review drawer, job detail) vs `Settings.jsx` `GlobalTasksSection` L697 (`tasksApi.listAll/getLogs/cancel`) and `TaskRunsSection` L1024 (`jobsApi.list`). `pages/TasksPage.jsx` is a 5-line wrapper. Four surfaces; `ProjectTasksPanel` is the most complete and the only one exposing approve/rerun/retry. `TaskMonitor`, `GlobalTasksSection`, `TaskRunsSection` duplicate its list/cancel/log logic with less capability.
- *Skills:* `Skills.jsx` (live; library tab) + `SkillEditor.jsx`, `SkillImporter.jsx` (imported by Skills), `SkillPicker.jsx` (Chat slash-command popover), `SkillScanDashboard.jsx` (security tab). `pages/SkillsPage.jsx` is the tab shell. No dead members. Overlap: `Settings.jsx` `SkillHubsSection` L454 (registries CRUD) sits under Settings→Skills rather than the Skills page; `SkillSecuritySection` L194 (override password) is separate from SkillScanDashboard — distinct concerns, acceptable.
- *Projects:* `Projects.jsx` (live at `/projects`; create/delete, persona via `personasApi.apply`) vs `chat-tabs/ProjectSettingsPanel.jsx` (live chat tab; rename/delete/export + "chat defaults" via `projectSettingsApi`). Delete-project exists in both (`Projects.jsx:104-110`, `ProjectSettingsPanel.jsx:126-135`). Persona assignment uses two different backend mechanisms: `personasApi.apply` copies soul.md and sets `project.persona_id` (`backend/api/personas.py:200-210`), whereas `ProjectSettingsPanel` writes `project_settings.persona`, which nothing reads (F2).
- *Personality:* `PersonalityEditor.jsx` (live via Settings→Personality, L1123) uses `CoreEditor.jsx` (shared CodeMirror primitive, also used by SkillEditor — good reuse). `chat-tabs/ProjectPersonalityPanel.jsx` dead. `PersonalityEditor` also does persona apply/create (L137, L178), overlapping `PersonaLibrary` and `Projects.jsx`.
- *MCP:* `MCPConnections.jsx` (1,254 lines) is live twice: `ConnectionsPage` tab (`ConnectionsPage.jsx:40`) and `MCPPage` (`/mcp`). `chat-tabs/ProjectMcpPanel.jsx` dead and broken.
- *Memory:* `MemoryBrowser.jsx` live via `MemoryPage` (route + embedded in ChatTabs). `FileRepository.jsx` dead (different storage tier — workspace files — whose UI was dropped when `/files` was redirected to artifacts; the API client and backend routes remain).
- *Settings:* `Settings.jsx` (1,559 lines, 13 section components, 49 `useState`) + `settings/*` (7 files). CLAUDE.md's "already componentized" claim holds for the LLM area only (EndpointList/Card/AddEndpointForm/ModelRouting/RoutingUsage/ChatRouter/RouterTuning). RAG, Security, Secrets, SkillHubs, GlobalTasks, TaskRuns, Sandbox, SystemUpdate sections (~1,100 lines) are still inline in `Settings.jsx`.

## 2. Routing and navigation

`App.jsx:67-84` routes: `/chat`, `/memory`, `/artifacts`, `/skills`, `/mcp`, `/connections`, `/personas`, `/tasks`, `/projects`, `/settings`; redirects `/files→/artifacts`, `/sources→/connections`, `/personality→/settings`.

`Layout.jsx:20-28` nav: Chat, Artifacts, Skills, Personas, Connections, Projects, Settings. **Not in nav:** `/memory`, `/tasks`, `/mcp`. No `Link`/`navigate` to them anywhere in `src` (grep for `to="/tasks"|/memory|/mcp` returned nothing). Memory and Tasks are reachable as ChatTabs tabs (`ChatTabs.jsx:19-26`); MCP via Connections tab.

Lazy loading: all 10 page components use `React.lazy` (`App.jsx:9-18`); `LoginPage` and `Layout` are static (correct). Dynamic imports found: `import('mermaid')` (`Mermaid.jsx:8`), `import('jspdf')` + `import('svg2pdf.js')` (`utils/svgExport.js:74`). **d3 is a static import** (`ForceGraph.jsx`), and **CodeMirror is static** in `ArtifactsPage.jsx:11-14` and `CoreEditor.jsx:17-22`. Because `ChatTabs.jsx:10-11` statically imports `MemoryPage` and `ArtifactsPage`, the `/chat` chunk (the default landing route) contains d3 + CodeMirror + react-markdown. The per-route split is intact for Settings/Skills/etc., but the heaviest libs land on first paint anyway. Not measured (no `node_modules`).

`ChatTabs.jsx:163-168` uses `<a href="/projects">` (full reload) instead of `Link`; a reload discards the module-level WebSocket singleton that `client.js:589` goes out of its way to preserve.

## 3. API client vs backend routes

Method: extracted every `@router.*`/`@app.*` decorator in `backend/api/*.py` + `main.py` (249 routes, all mounted with prefix `/api` per `main.py:290-308`, WS mounted directly at `/ws/chat` by `main.py:313`), and every `api.<verb>(...)`, `fetch(...)`, `new WebSocket(...)` literal in `frontend/src` (232 call sites, all in `client.js` except `Layout.jsx:15` and `LoginPage.jsx:8`). Path params normalized to `{}`.

**Frontend calls with no backend route: none.** `/api/health` exists (`main.py:317`), `/ws/chat` exists (`main.py:313`). The only "phantom" is `projectMcpApi` in dead `ProjectMcpPanel.jsx:3` (no such client group, no such backend route).

**Exported API groups (`client.js`):** `authApi`, `chatApi`, `memoryApi`, `systemApi`, `sourcesApi` (legacy alias of connectionsApi), `connectionsApi`, `projectRepoApi`, `projectSettingsApi`, `taskRunsApi`, `jobsApi`, `conversationsApi`, `artifactsApi`, `filesApi`, `settingsApi`, `llmApi`, `mcpApi`, `skillsApi`, `tasksApi`, `personasApi`, `personalityApi`, `projectsApi`, `messagingApi`, plus `createChatSocket`/`closeChatSocket`.

**Backend routes with no frontend caller** — Appendix B. Two tiers: (B1) 14 routes that no client method touches at all; (B2) 34 routes whose only client method is never invoked by any component. Whether they are agent-only or dead is not determinable from the frontend; the backend agent tools call Python functions directly, not HTTP, so most of these are likely script/curl-only or dead. `/api/system/self-doc` is one of them.

Also noted in passing (backend, not frontend): `POST /api/jobs/{id}/rerun` lives in `api/tasks.py:179` while `POST /api/jobs/{id}/retry` lives in `api/jobs.py:74` — both are used by `ProjectTasksPanel` (L102, L108) with different semantics, but the rerun route is in the wrong module.

## 4. State

`store/index.js` slices: `activeProject` (persisted `localStorage.active_project_id`, L8-11/L27), `sessionId`, `activePersonas`, `historyOpen`, `sidebarCollapsed` (persisted `pantheon_sidebar_collapsed`), chat-bar settings `memoryRecall` / `contextFocus` / `skillDiscovery` (persisted `chat_skill_discovery`) / `personalityWeight`, `messages`, `isStreaming` + `streamingContent`, `currentToolCalls`, `projects`, `settings` (never set anywhere — grep `setSettings(` outside store: 0 hits), `skills` + `activeSkill` (never set), `sidebarOpen`, `notifications`.

Persistence of the three things asked about:
- *Active project:* localStorage id at module load → upgraded to full record by `Layout.jsx:47-58` on mount. Fine.
- *Persona:* not in the store; per-project on the backend (`project.persona_id` via apply). `activePersonas` (group chat) is loaded per session from conversation metadata (`Chat.jsx:504-517`) and saved by `ChatTabs.jsx:242-250`.
- *Model pin:* component-local `useState('auto')` in `Chat.jsx:450`, reset when `sessionId` becomes null; server-side pin lives in process memory (per CLAUDE.md). Not in the store, so switching tabs and back keeps it only because `Chat` stays mounted; navigating away resets it while the server still holds the pin — minor divergence.

Duplication between store and backend (F1/F2/F9): `memoryRecall`, `contextFocus`, `personalityWeight` exist in the store, the vault (`backend/api/settings.py:142-144`) and `project_settings` (`backend/api/projects.py:405-442`); only the vault is read by `agent/core.py:338-345`. `skillDiscovery` is in localStorage, the store, the vault (`skill_discovery_{project}`, read by chat) and `project_settings` (unread).

Local state duplicating the store: `Chat.jsx` and `ChatActions.jsx` each independently load the discovery mode on project switch (`Chat.jsx:527-530` → `'off'` fallback; `ChatActions.jsx:45-58` → keep local). Whichever effect resolves last wins.

Other localStorage keys (per-viewer conveniences, all try/catch-wrapped — fine): `pan_artifacts_folders_collapsed` (`ArtifactsPage.jsx:55-61`), `FolderTree` collapsedKey, `help.*` (HelpDrawer). `client.js:15` removes the legacy `auth_token`.

## 5. Chat.jsx / ChatTabs.jsx

| File | Lines | useState | useEffect | useCallback | useRef | useStore selectors | top-level components |
|------|------:|---------:|----------:|------------:|-------:|-------------------:|---------------------:|
| `Chat.jsx` | 1306 | 24 | 7 | 5 | 5 | 33 | 11 |
| `ChatTabs.jsx` | 315 | 5 | 3 | 0 | 4 | 7 | 3 |

`ChatTabs.jsx` is reasonably sized; its two portal dropdowns (`ProjectPickerPill`, `GroupChannelHeader`) share identical positioning/outside-click boilerplate (L100-118 vs L188-208) that could be one hook.

`Chat.jsx` natural seams, each already self-contained:
- WebSocket event reducer `connectSocket` (L664-744) + send/accept/decline (L748-863) → `useChatSocket()` hook.
- `useMarkdownComponents` (L218-279) + `FilePreview` (L27-110) + `GeneratedImages` (L118-133) → `chatMarkdown.jsx`.
- `ToolCallBlock` (L135-187), `RouteBadge` (L315-352), `MessageActions` (L282-311), `Message` (L354-426) → `Message.jsx`.
- `ChatHistoryDrawer` (L1125-1224) and `SaveToArtifactModal` (L1226-1305) → own files.
- Dead block L533-584 (four persisting handlers + `recallLoading`, see F1).

Markdown rendering is configured differently at every site: `Chat.jsx:404,930` (`remarkGfm` + mermaid + custom `code/img/a`), `Chat.jsx:87` (`remarkGfm` + mermaid, no workspace resolver), `ArtifactsPage.jsx:870` (`remarkGfm` + mermaid), `ProjectTasksPanel.jsx:626` (mermaid, **no** `remarkGfm`), `PersonalityEditor.jsx:413-420` (`remarkGfm`, inline ad-hoc `components`, no mermaid), `FileRepository.jsx:596` (dead; `remarkGfm` only). `markdownComponents.jsx` only supplies the mermaid `pre` override; a single `<Markdown>` wrapper would remove the drift.

## 6. Security

Every sink found by grep (`dangerouslySetInnerHTML|innerHTML|srcDoc|window.open|eval|new Function`):

| Site | Sink | Mitigation | Verdict |
|------|------|-----------|---------|
| `ArtifactsPage.jsx:843` | `innerHTML` (SVG artifact) | `DOMPurify.sanitize(…, {USE_PROFILES:{svg,svgFilters}})` | OK |
| `ArtifactsPage.jsx:897` | `dangerouslySetInnerHTML` (HTML artifact) | `DOMPurify.sanitize` default profile, rendered in app origin | OK but weaker than SandboxedHtml (F13) |
| `Mermaid.jsx:65` | `innerHTML = result.svg` | mermaid `securityLevel: 'strict'` (L20); `htmlLabels:false` | OK — relies on mermaid's sanitizer, not DOMPurify |
| `SandboxedHtml.jsx:28-33` | `srcDoc` | `sandbox="allow-scripts"` (no `allow-same-origin`), `referrerPolicy="no-referrer"`, content fetched via axios | OK. Comment L7-9 still talks about an "Authorization header" and `?token=`; auth is cookie now — stale text only |
| `MCPConnections.jsx:980` | `window.open(url,'_blank','noopener,noreferrer')` | URL comes from backend `authorize_url` | OK |
| `Chat.jsx:59,245`, `ArtifactsPage.jsx:894`, `FileRepository.jsx:714` | `<iframe/embed src>` of `/api/files/view` or artifact preview URL (PDF) | same-origin, cookie auth, server sets content type | OK |
| `Chat.jsx:261,276` | markdown `<img src>`/`<a href>` from agent output | react-markdown 9 default `urlTransform` drops non-http(s)/mailto schemes | OK |

`target="_blank"` links (10 sites) all carry `rel="noopener noreferrer"` or `rel="noreferrer"` (implies noopener).

Auth: cookie-only. `client.js:7-12` uses `withCredentials: true`, no `Authorization` header anywhere in `src`; `client.js:15` deletes the legacy `auth_token` key; no `?token=` in any URL builder (`rawUrl`, `viewUrl`, `downloadUrl`, `exportUrl` are bare paths). `LoginPage.jsx:24-32` receives `token` in the JSON body (backend `auth.py:189` returns it for API clients) but discards it. 401 → `auth:logout` event (`client.js:21-23`).

WebSocket URL: `client.js:609-611` builds `${ws|wss}://${window.location.host}/ws/chat` — same-origin, so the `SameSite=Strict` cookie rides along. Note it ignores `BASE_URL`/`VITE_API_URL` while the HTTP URL builders honor it; consistent only because the documented build uses `VITE_API_URL=""`.

## 7. Consistency — concrete examples worth standardizing

1. **Redundant error extraction.** `client.js:18-27` normalizes every axios error to `Error(detail)`, yet 19 files still write `e?.response?.data?.detail || e.message` (e.g. `SkillEditor.jsx` ×16, `ProjectTasksPanel.jsx` ×7, `ArtifactsPage.jsx` ×6). The left operand is always `undefined`. Pick one: drop the interceptor's rewrap or drop the 70+ dead expressions.
2. **`alert`/`confirm` vs toasts.** 37 native dialog calls (`ProjectTasksPanel.jsx` ×11, `ArtifactsPage.jsx` ×6, `Chat.jsx:1170` `alert('Resume failed…')`) alongside `addNotification` everywhere else. Destructive confirms are legitimate, but `alert` for failures (`ProjectTasksPanel.jsx:117,267,292,307`, `ArtifactsPage.jsx:163-186`) should be toasts.
3. **Project id fallback literal.** `'default'` in 38 places, `'default-project'` in `ChatActions.jsx:45,65`. When `activeProject` is unset, ChatActions persists skill-discovery under a key the chat handler never reads.
4. **Project scope: prop vs store.** `ProjectTasksPanel`, `RepoBindingPanel`, `ProjectSettingsPanel`, `ArtifactsPage(lockedProjectId)` take `projectId` as a prop from `ChatTabs.jsx:83-87`; `MemoryBrowser`, `TaskMonitor`, `Skills`, `Projects` read `useStore(s => s.activeProject)`. Both patterns coexist in the same tab strip.
5. **`fetch` vs axios.** `Layout.jsx:15` uses bare `fetch('/api/health')` while `LoginPage.jsx:8` uses `api.get('/api/health')` for the same version tag; `Chat.jsx:41` bare-fetches `/api/files/view` text. The bare calls skip the 401 interceptor.
6. **Undefined Tailwind shades** (F10): `bg-gray-850`/`hover:bg-gray-750` are used as if they were palette entries.

Inline `style={{}}` is rare (Chat.jsx 7, mostly heights/animation delays; portal positioning in ChatTabs/Tooltip is legitimate). Component naming is consistent PascalCase; file-per-component except `Chat.jsx` and `Settings.jsx`.

## 8. package.json

Dependencies (18): `@codemirror/lang-javascript`, `@codemirror/lang-json`, `@codemirror/lang-markdown`, `@codemirror/lang-python`, `@uiw/react-codemirror`, `axios`, `d3`, `dompurify`, `jspdf`, `lucide-react`, `mermaid`, `react`, `react-dom`, `react-markdown`, `react-router-dom`, `remark-gfm`, `svg2pdf.js`, `zustand`. Dev: `@tailwindcss/typography`, `@types/react*`, `@vitejs/plugin-react`, `autoprefixer`, `postcss`, `tailwindcss`, `vite`.

- **Unused in `src`: none.** Every dependency is imported at least once (`@codemirror/lang-json` only by `CoreEditor.jsx`; `dompurify` only by `ArtifactsPage.jsx`; `@tailwindcss/typography` by `tailwind.config.js`).
- **Undeclared but imported:** `@codemirror/view` (`CoreEditor.jsx:18`) — transitive only (F11).
- **Heavy and static:** `d3` (`ForceGraph.jsx`), `@uiw/react-codemirror` + 4 language packs (`ArtifactsPage.jsx`, `CoreEditor.jsx`), `react-markdown` + `remark-gfm` (5 files). Reach the `/chat` chunk via `ChatTabs` (F6).
- **Heavy and dynamic (correct):** `mermaid`, `jspdf`, `svg2pdf.js`.

## 9. Help system

`help/HelpDrawer.jsx` (65 lines) is a generic collapsible with `localStorage` open-state; content is authored inline at 8 call sites (`SearchProvidersTab.jsx:190`, `MCPConnections.jsx:1157`, `settings/EndpointList.jsx:57`, `Settings.jsx:171`, `ProjectTasksPanel.jsx:158`, `TaskMonitor.jsx:241`, `ArtifactsPage.jsx:340`, `ConnectionsPage.jsx:114`). `help/llmProviders.js` (13 provider presets) and `help/mcpProviders.js` (5 hosted MCP presets) are static tables; the backend has no equivalent list (`self_doc.py` renders live endpoints/routes, `llm_config/known_models.py` holds regex capability guesses), so these are not duplicates of backend data — they are frontend-only presets.

Overlap that does exist:
- `TaskMonitor.jsx:241-270` ("About task schedules": now/delay/interval/cron table + skills-vs-tasks paragraph) and `ProjectTasksPanel.jsx:158-200` ("About schedules and job runs": status table + approve flow) both restate `docs/USAGE.md §7` and CLAUDE.md "Skills vs scheduled tasks". Since `TaskMonitor` is un-navved, the first is effectively unseen.
- `SearchProvidersTab.jsx:190-235` provider table restates `docs/USAGE.md §6 "Search provider chain"`; USAGE.md still says the chain is configured in **Settings → Web Search Provider Chain** (it is Connections → Web search) and `USAGE.md:16` says "Switch projects from the sidebar" (the picker moved to the chat top-bar pill per `Layout.jsx:44-46`).
- `backend/utils/self_doc.py` (`/api/system/self-doc`) is never fetched by the frontend; it is agent-only. No frontend help text is generated from it, so routing/provider facts in the UI cannot drift from it (they are hand-written, and they will).

---

## Appendix A — Component import map

Format: `file ← importers`. **DEAD** = no importer.

```
components/Chat.jsx                        ← ChatTabs
components/ChatActions.jsx                 ← ChatTabs
components/ChatTabs.jsx                    ← pages/ChatPage
components/CoreEditor.jsx                  ← FileRepository(dead), SkillEditor, PersonalityEditor
components/ExportMenu.jsx                  ← Mermaid, pages/ArtifactsPage
components/FileRepository.jsx              ← pages/FilesPage(dead)            → DEAD (transitively)
components/FolderTree.jsx                  ← MoveModal, pages/ArtifactsPage
components/ForceGraph.jsx                  ← GraphView
components/GraphView.jsx                   ← MemoryBrowser
components/Layout.jsx                      ← App
components/MCPConnections.jsx              ← pages/ConnectionsPage, pages/MCPPage
components/MemoryBrowser.jsx               ← pages/MemoryPage
components/Mermaid.jsx                     ← markdownComponents, pages/ArtifactsPage
components/MessagingSettings.jsx           ← Settings
components/MoveModal.jsx                   ← pages/ArtifactsPage
components/PersonaLibrary.jsx              ← pages/PersonasPage, pages/PersonalityPage(dead)
components/PersonalityEditor.jsx           ← Settings, pages/PersonalityPage(dead)
components/ProjectPortability.jsx          ← Projects
components/Projects.jsx                    ← pages/ProjectsPage
components/SandboxedHtml.jsx               ← Chat, FileRepository(dead)
components/SecurityLog.jsx                 ← Settings
components/Settings.jsx                    ← pages/SettingsPage
components/SkillEditor.jsx                 ← Skills
components/SkillImporter.jsx               ← Skills
components/SkillPicker.jsx                 ← Chat
components/SkillScanDashboard.jsx          ← pages/SkillsPage
components/Skills.jsx                      ← pages/SkillsPage
components/TaskMonitor.jsx                 ← pages/TasksPage (route /tasks, not in nav)
components/Tooltip.jsx                     ← ChatActions, Layout, help/InfoTooltip
components/chat-tabs/ProjectMcpPanel.jsx          → DEAD (imports nonexistent projectMcpApi)
components/chat-tabs/ProjectPersonalityPanel.jsx  → DEAD
components/chat-tabs/ProjectSettingsPanel.jsx     ← ChatTabs
components/chat-tabs/ProjectTasksPanel.jsx        ← ChatTabs
components/chat-tabs/RepoBindingPanel.jsx         ← ChatTabs
components/connections/SearchProvidersTab.jsx     ← pages/ConnectionsPage
components/help/HelpDrawer.jsx             ← 8 files (see §9)
components/help/InfoTooltip.jsx            ← settings/* (5), ProjectSettingsPanel, TaskMonitor
components/help/llmProviders.js            ← settings/EndpointList
components/help/mcpProviders.js            ← MCPConnections
components/markdownComponents.jsx          ← Chat, ProjectTasksPanel, pages/ArtifactsPage
components/settings/AddEndpointForm.jsx    ← settings/EndpointList
components/settings/ChatRouter.jsx         ← Settings
components/settings/EndpointCard.jsx       ← settings/EndpointList
components/settings/EndpointList.jsx       ← Settings
components/settings/ModelRouting.jsx       ← Settings
components/settings/RouterTuning.jsx       ← Settings
components/settings/RoutingUsage.jsx       ← Settings
pages/ArtifactsPage.jsx                    ← App (lazy), ChatTabs (static)
pages/ChatPage.jsx                         ← App (lazy)
pages/ConnectionsPage.jsx                  ← App (lazy)
pages/FilesPage.jsx                        → DEAD
pages/LoginPage.jsx                        ← App
pages/MCPPage.jsx                          ← App (lazy; route /mcp, not in nav)
pages/MemoryPage.jsx                       ← App (lazy; route /memory, not in nav), ChatTabs (static)
pages/PersonalityPage.jsx                  → DEAD
pages/PersonasPage.jsx                     ← App (lazy)
pages/ProjectsPage.jsx                     ← App (lazy)
pages/SettingsPage.jsx                     ← App (lazy)
pages/SkillsPage.jsx                       ← App (lazy)
pages/SourcesPage.jsx                      → DEAD
pages/TasksPage.jsx                        ← App (lazy; route /tasks, not in nav)
utils/svgExport.js                         ← ExportMenu
```

## Appendix B — Backend endpoints without a frontend caller

Excluded from this list because they are reached via URL builders or browser redirects, not `api.*` calls: `GET /api/artifacts/{id}/raw` (`artifactsApi.rawUrl`), `GET /api/files/view` and `/download` (`filesApi.viewUrl/downloadUrl`), `GET /api/skills/editor/{name}/export` (`skillsApi.exportUrl`), `GET /api/mcp/oauth/callback` (OAuth redirect), `GET /`, `/health`, `/{full_path}` (SPA shell).

**B1 — no client method at all (14):**

```
GET  /api/artifacts/feed                      api/artifacts.py:187
GET  /api/artifacts/{id}/preview-pdf          api/artifacts.py:361
POST /api/files/convert                       api/files.py:470
POST /api/files/index                         api/files.py:370
GET  /api/files/index-status                  api/files.py:403
GET  /api/mcp/debug/{name}                    api/mcp.py:315
POST /api/mcp/tavily/test-direct              api/mcp.py:255
GET  /api/memory/graph/related/{node_id}      api/memory.py:250
GET  /api/personality                         api/personality.py:76
GET  /api/projects/{id}/export/debug          api/projects.py:287
POST /api/skills/debug-match                  api/skills.py:67
GET  /api/system/self-doc                     api/system.py:170
```

**B2 — client method exists but no component calls it (34):**

```
chatApi.send            POST /api/chat                      api/chat.py:246
chatApi.getHistory      GET  /api/chat/history              api/chat.py:370
chatApi.getSessions     GET  /api/chat/sessions             api/chat.py:383
memoryApi.store         POST /api/memory/store              api/memory.py:45
memoryApi.search        POST /api/memory/search             api/memory.py:58
memoryApi.audit         GET  /api/memory/audit/{tier}       api/memory.py:72
memoryApi.updateNote    PUT  /api/memory/episodic/notes/{id} api/memory.py:110
memoryApi.createGraphNode POST /api/memory/graph/nodes      api/memory.py:196
memoryApi.consolidate   POST /api/memory/consolidate        api/memory.py:353
memoryApi.reembed       POST /api/memory/reembed            api/memory.py:365
memoryApi.embeddingModelStats GET /api/memory/embedding-model-stats api/memory.py:396
connectionsApi.listRepos POST /api/connections/github/repos api/connections.py:254  (sourcesApi.listRepos same route, dead page)
taskRunsApi.list/get/delete/cancel  /api/tasks/runs[/{id}[/cancel]]  api/tasks.py:140-170
jobsApi.create          POST /api/jobs                      api/jobs.py:57
artifactsApi.rename     POST /api/artifacts/{id}/rename     api/artifacts.py:394
artifactsApi.getVersion GET  /api/artifacts/{id}/versions/{n} api/artifacts.py:479
artifactsApi.bulkTags   POST /api/artifacts/bulk/tags       api/artifacts.py:509
artifactsApi.exportAll  GET  /api/artifacts/export-all      api/artifacts.py:535
settingsApi.listModels  GET  /api/settings/models           api/settings.py:273
settingsApi.testConnection GET /api/settings/test-connection api/settings.py:287
settingsApi.restartTelegram POST /api/settings/restart-telegram api/settings.py:328
llmApi.getRoles/setRoles GET/PUT /api/llm/roles             api/llm_endpoints.py:65,75
skillsApi.quarantine    POST /api/skills/{name}/quarantine  api/skills.py:875
skillsApi.listVersionFiles GET /api/skills/editor/{n}/versions/{v}/files api/skills.py:934
skillsApi.readVersionFile  GET /api/skills/editor/{n}/versions/{v}/file  api/skills.py:950
skillsApi.resetAnalytics POST /api/skills/analytics/reset   api/skills.py:652
tasksApi.getAllLogs     GET  /api/tasks/logs/all            api/tasks.py:124
personasApi.get         GET  /api/personas/{id}             api/personas.py:116
personalityApi.status   GET  /api/personality/status        api/personality.py:90
messagingApi.updateMappings PUT /api/messaging/mappings     api/messaging.py:83
```

Also: `filesApi.*` (13 methods) is called only from dead `FileRepository.jsx` except `read` (SandboxedHtml) and `viewUrl` (Chat) — so `GET /api/files`, `PUT /files/write`, `POST /files/upload`, `/upload-multiple`, `DELETE /files`, `/files/mkdir`, `/files/download-zip` have no live UI caller either.

## Appendix C — Frontend calls without a backend route

None. All 230 `api.*`/`fetch` literals resolve to a mounted route. Non-API broken references: `ProjectTasksPanel.jsx:468` `href="/artifacts?tab="` (malformed, no artifact id); `ProjectMcpPanel.jsx:3` `projectMcpApi` (nonexistent export; file is dead).

## Appendix D — Unused / mis-declared dependencies

- Unused: none of the 18 runtime deps or 8 dev deps is unreferenced.
- Undeclared: `@codemirror/view` (imported by `CoreEditor.jsx:18`, resolved transitively).
- Static-but-heavy: `d3`, `@uiw/react-codemirror` + `@codemirror/lang-*`, `react-markdown`/`remark-gfm` (all reach the `/chat` chunk through `ChatTabs.jsx`).
- Dynamic (good): `mermaid`, `jspdf`, `svg2pdf.js`.
