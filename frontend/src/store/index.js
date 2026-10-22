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


// Last open conversation per project (UI review #3). localStorage with an
// in-memory mirror so a blocked/private storage still works within the tab.
const _convMirror = new Map()
const _convKey = (projectId) => `pantheon_conv_${projectId || 'default'}`
export function readProjectConversation(projectId) {
  try {
    const v = localStorage.getItem(_convKey(projectId))
    if (v) return v
  } catch {}
  return _convMirror.get(_convKey(projectId)) || null
}
export function writeProjectConversation(projectId, sessionId) {
  const k = _convKey(projectId)
  if (sessionId) _convMirror.set(k, sessionId)
  else _convMirror.delete(k)
  try {
    if (sessionId) localStorage.setItem(k, sessionId)
    else localStorage.removeItem(k)
  } catch {}
}


export const useStore = create((set, get) => ({
  // Active project
  activeProject: { id: _initialProjectId, name: '' },
  setActiveProject: (project) => {
    try { if (project?.id) localStorage.setItem('active_project_id', project.id) } catch {}
    set({ activeProject: project })
  },

  // Active session. Every change is remembered as the active project's
  // last conversation, so reloads and project switches can reopen it.
  sessionId: null,
  setSessionId: (id) => {
    writeProjectConversation(get().activeProject?.id, id || null)
    set({ sessionId: id })
  },

  historyOpen: false,
  setHistoryOpen: (v) => set({ historyOpen: !!v }),

  // Collapsible sidebar (desktop, persisted)
  sidebarCollapsed: typeof localStorage !== 'undefined' && localStorage.getItem('pantheon_sidebar_collapsed') === 'true',
  toggleSidebarCollapsed: () => set((state) => {
    const next = !state.sidebarCollapsed
    try { localStorage.setItem('pantheon_sidebar_collapsed', String(next)) } catch {}
    return { sidebarCollapsed: next }
  }),

  // Chat-bar settings (lifted out of Chat.jsx so the unified top bar can
  // render them as icons across tab switches)
  memoryRecall: true,
  setMemoryRecall: (v) => set({ memoryRecall: !!v }),
  contextFocus: 'balanced',           // 'broad' | 'balanced' | 'focused'
  setContextFocus: (v) => set({ contextFocus: v }),
  // Loaded per project from /api/projects/{id}/settings (effective values).
  skillDiscovery: 'off',              // 'off' | 'suggest' | 'auto'
  setSkillDiscovery: (v) => set({ skillDiscovery: v }),
  personalityWeight: 'balanced',      // 'minimal' | 'balanced' | 'strong'
  setPersonalityWeight: (v) => set({ personalityWeight: v }),

  // Chat messages (current session display)
  messages: [],
  addMessage: (msg) => set((state) => ({ messages: [...state.messages, msg] })),
  clearMessages: () => set({ messages: [] }),
  setMessages: (messages) => set({ messages }),

  // Streaming state
  isStreaming: false,
  setIsStreaming: (v) => set({ isStreaming: v }),
  streamingContent: '',
  setStreamingContent: (v) => set({ streamingContent: v }),
  appendStreamingContent: (v) => set((state) => ({ streamingContent: state.streamingContent + v })),

  // Tool calls in current response
  currentToolCalls: [],
  addToolCall: (tc) => set((state) => ({ currentToolCalls: [...state.currentToolCalls, tc] })),
  clearToolCalls: () => set({ currentToolCalls: [] }),

  // Projects
  projects: [],
  setProjects: (projects) => set({ projects }),

  // Sidebar open/closed (mobile)
  sidebarOpen: false,
  setSidebarOpen: (v) => set({ sidebarOpen: v }),
  toggleSidebar: () => set((state) => ({ sidebarOpen: !state.sidebarOpen })),

  // Notifications/toasts
  notifications: [],
  addNotification: (n) => {
    const id = Date.now()
    set((state) => ({ notifications: [...state.notifications, { ...n, id }] }))
    setTimeout(() => {
      set((state) => ({ notifications: state.notifications.filter((x) => x.id !== id) }))
    }, 4000)
  },
  removeNotification: (id) =>
    set((state) => ({ notifications: state.notifications.filter((n) => n.id !== id) })),
}))
