import { create } from 'zustand'

export const useStore = create((set, get) => ({
  // Active project
  activeProject: { id: 'default', name: 'Default Project' },
  setActiveProject: (project) => set({ activeProject: project }),

  // Active session
  sessionId: null,
  setSessionId: (id) => set({ sessionId: id }),

  historyOpen: false,
  setHistoryOpen: (v) => set({ historyOpen: !!v }),

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

  // Settings
  settings: null,
  setSettings: (s) => set({ settings: s }),

  // Skills
  skills: [],
  setSkills: (skills) => set({ skills }),
  activeSkill: null,
  setActiveSkill: (skill) => set({ activeSkill: skill }),
  skillDiscovery: 'off',
  setSkillDiscovery: (mode) => set({ skillDiscovery: mode }),

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
