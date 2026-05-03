import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL || ''

export const api = axios.create({
  baseURL: BASE_URL,
  timeout: 120000,
  headers: { 'Content-Type': 'application/json' },
})

// Request interceptor — attach auth token if present
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('auth_token')
  if (token && token !== 'no-auth') {
    config.headers['Authorization'] = `Bearer ${token}`
  }
  return config
})

// Response interceptor — handle errors and 401 redirects
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      localStorage.removeItem('auth_token')
      window.dispatchEvent(new Event('auth:logout'))
    }
    const message = error.response?.data?.detail || error.message || 'Request failed'
    return Promise.reject(new Error(message))
  }
)

// Auth API
export const authApi = {
  config: () => api.get('/api/auth/config').then((r) => r.data),
  login: (password) =>
    api.post('/api/auth/login', { password }),
}

// Chat API
export const chatApi = {
  send: (message, sessionId, projectId) =>
    api.post('/api/chat', { message, session_id: sessionId, project_id: projectId, stream: false }),
  getHistory: (sessionId, projectId, limit = 50) =>
    api.get('/api/chat/history', { params: { session_id: sessionId, project_id: projectId, limit } }),
  getSessions: (projectId, limit = 20) =>
    api.get('/api/chat/sessions', { params: { project_id: projectId, limit } }),
  attachFile: (file, projectId) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post('/api/chat/attach', formData, {
      params: { project_id: projectId },
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
}

// Memory API
export const memoryApi = {
  store: (content, tier, projectId, metadata = {}) =>
    api.post('/api/memory/store', { content, tier, project_id: projectId, metadata }),
  search: (query, projectId, tiers = ['semantic', 'episodic'], limit = 10) =>
    api.post('/api/memory/search', { query, project_id: projectId, tiers, limit }),
  audit: (tier, projectId) =>
    api.get(`/api/memory/audit/${tier}`, { params: { project_id: projectId } }),
  listSemantic: (projectId, limit = 50, offset = 0) =>
    api.get('/api/memory/semantic', { params: { project_id: projectId, limit, offset } }),
  deleteSemantic: (docId, projectId) =>
    api.delete(`/api/memory/semantic/${docId}`, { params: { project_id: projectId } }),
  listNotes: (projectId) =>
    api.get('/api/memory/episodic/notes', { params: { project_id: projectId } }),
  listMessages: (projectId, limit = 50) =>
    api.get('/api/memory/episodic/messages', { params: { project_id: projectId, limit } }),
  updateNote: (noteId, content) =>
    api.put(`/api/memory/episodic/notes/${noteId}`, { content }),
  deleteNote: (noteId) =>
    api.delete(`/api/memory/episodic/notes/${noteId}`),
  deleteMessage: (messageId) =>
    api.delete(`/api/memory/episodic/messages/${messageId}`),
  listGraphNodes: (projectId, nodeType) =>
    api.get('/api/memory/graph/nodes', { params: { project_id: projectId, node_type: nodeType } }),
  listGraphEdges: (projectId) =>
    api.get('/api/memory/graph/edges', { params: { project_id: projectId } }),
  createGraphNode: (nodeType, label, projectId, metadata = {}) =>
    api.post('/api/memory/graph/nodes', { node_type: nodeType, label, project_id: projectId, metadata }),
  createGraphEdge: (labelA, labelB, relationship, projectId) =>
    api.post('/api/memory/graph/edges', { label_a: labelA, label_b: labelB, relationship, project_id: projectId }),
  deleteGraphNode: (nodeId, projectId) =>
    api.delete(`/api/memory/graph/nodes/${nodeId}`, { params: { project_id: projectId } }),
  deleteGraphEdge: (edgeId, projectId) =>
    api.delete(`/api/memory/graph/edges/${edgeId}`, { params: { project_id: projectId } }),
  listArchivalNotes: (projectId) =>
    api.get('/api/memory/archival/notes', { params: { project_id: projectId } }),
  readArchivalNote: (filename, projectId) =>
    api.get(`/api/memory/archival/notes/${encodeURIComponent(filename)}`, { params: { project_id: projectId } }),
  createArchivalNote: (content, projectId) =>
    api.post('/api/memory/archival/notes', { content }, { params: { project_id: projectId } }),
  deleteArchivalNote: (filename, projectId) =>
    api.delete(`/api/memory/archival/notes/${encodeURIComponent(filename)}`, { params: { project_id: projectId } }),
  getArchivalSummary: (projectId) =>
    api.get('/api/memory/archival/summary', { params: { project_id: projectId } }),
  updateArchivalSummary: (content, projectId) =>
    api.put('/api/memory/archival/summary', { content }, { params: { project_id: projectId } }),
  consolidate: (projectId, sessionId) =>
    api.post('/api/memory/consolidate', null, { params: { project_id: projectId, session_id: sessionId } }),
  graphFull: (projectId, type, limit = 500) =>
    api.get('/api/memory/graph/full', { params: { project_id: projectId, type, limit } }),
  graphPath: (projectId, from, to, opts = {}) =>
    api.get('/api/memory/graph/path', {
      params: { project_id: projectId, from, to, k: opts.k || 1, weighted: opts.weighted ? true : undefined },
    }),
  reembed: (projectId) =>
    api.post('/api/memory/reembed', null, { params: { project_id: projectId } }),
  embeddingModelStats: (projectId) =>
    api.get('/api/memory/embedding-model-stats', { params: { project_id: projectId } }),
}

export const systemApi = {
  sandboxHealth: () => api.get('/api/system/sandbox'),
}

export const sourcesApi = {
  // Legacy aliases — kept for any existing call sites; new code should
  // use connectionsApi.
  listGitHub: (projectId) =>
    api.get('/api/connections/github', { params: { project_id: projectId } }),
  listRepos: (token) =>
    api.get('/api/connections/github/repos', { params: { token } }),
  createGitHub: (projectId, token, repo, defaultBranch) =>
    api.post('/api/connections/github', {
      project_id: projectId, token, repo, default_branch: defaultBranch,
    }),
  deleteGitHub: (id) => api.delete(`/api/connections/github/${id}`),
}

export const connectionsApi = {
  list: () => api.get('/api/connections/github'),
  listRepos: (token) =>
    api.get('/api/connections/github/repos', { params: { token } }),
  create: ({ token, repo, default_branch }) =>
    api.post('/api/connections/github', { token, repo, default_branch }),
  delete: (id) => api.delete(`/api/connections/github/${id}`),
  // Live calls keyed off a stored connection (no token in URL)
  listConnectionRepos: (id) =>
    api.get(`/api/connections/github/${id}/repos`),
  listConnectionBranches: (id, owner, repo) =>
    api.get(`/api/connections/github/${id}/branches`, { params: { owner, repo } }),
}

export const projectRepoApi = {
  get: (projectId) => api.get(`/api/projects/${projectId}/repo`),
  bind: (projectId, body) => api.post(`/api/projects/${projectId}/repo`, body),
  unbind: (projectId) => api.delete(`/api/projects/${projectId}/repo`),
}

export const projectMcpApi = {
  list: (projectId) => api.get(`/api/projects/${projectId}/mcp`),
  set: (projectId, serverId, enabled) =>
    api.post(`/api/projects/${projectId}/mcp/${serverId}`, { enabled }),
}

export const projectSettingsApi = {
  get: (projectId) => api.get(`/api/projects/${projectId}/settings`),
  update: (projectId, body) => api.put(`/api/projects/${projectId}/settings`, body),
}

export const taskRunsApi = {
  list: (params = {}) => api.get('/api/tasks/runs', { params }),
  get: (id) => api.get(`/api/tasks/runs/${id}`),
  delete: (id) => api.delete(`/api/tasks/runs/${id}`),
  cancel: (id) => api.post(`/api/tasks/runs/${id}/cancel`),
}

export const conversationsApi = {
  list: (projectId, limit = 50) =>
    api.get('/api/conversations', { params: { project_id: projectId, limit } }),
  get: (sessionId, projectId) =>
    api.get(`/api/conversations/${sessionId}`, { params: { project_id: projectId } }),
  resume: (sessionId, projectId) =>
    api.post(`/api/conversations/${sessionId}/resume`, null, { params: { project_id: projectId } }),
  delete: (sessionId) => api.delete(`/api/conversations/${sessionId}`),
  saveAsArtifact: (sessionId, projectId, body = {}) =>
    api.post(`/api/conversations/${sessionId}/save-as-artifact`, body, { params: { project_id: projectId } }),
}

export const artifactsApi = {
  list: (projectId, opts = {}) =>
    api.get('/api/artifacts', { params: { project_id: projectId, ...opts } }),
  folders: (projectId) =>
    api.get('/api/artifacts/folders', { params: { project_id: projectId } }),
  tags: (projectId) =>
    api.get('/api/artifacts/tags', { params: { project_id: projectId } }),
  get: (id) => api.get(`/api/artifacts/${id}`),
  rawUrl: (id) => `/api/artifacts/${id}/raw`,
  preview: (id) => api.get(`/api/artifacts/${id}/preview`),
  create: ({ project_id, path, content, content_type = 'text/markdown', title, tags, source }) =>
    api.post('/api/artifacts', { project_id, path, content, content_type, title, tags, source }),
  upload: (file, { project_id = 'default', path, title, tags } = {}) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('project_id', project_id)
    fd.append('path', path || file.name)
    if (title) fd.append('title', title)
    if (tags) fd.append('tags', JSON.stringify(tags))
    return api.post('/api/artifacts/upload', fd, { headers: { 'Content-Type': 'multipart/form-data' } })
  },
  update: (id, body) => api.patch(`/api/artifacts/${id}`, body),
  rename: (id, new_path) => api.post(`/api/artifacts/${id}/rename`, { new_path }),
  pin: (id, pinned) => api.post(`/api/artifacts/${id}/pin`, { pinned }),
  delete: (id) => api.delete(`/api/artifacts/${id}`),
  restore: (id) => api.post(`/api/artifacts/${id}/restore`),
  versions: (id) => api.get(`/api/artifacts/${id}/versions`),
  getVersion: (id, n) => api.get(`/api/artifacts/${id}/versions/${n}`),
  diff: (id, a, b) => api.get(`/api/artifacts/${id}/diff`, { params: { a, b } }),
  restoreVersion: (id, n) => api.post(`/api/artifacts/${id}/versions/${n}/restore`),
  bulkTags: (ids, tags, add = true) => api.post('/api/artifacts/bulk/tags', { ids, tags, add }),
  bulkDelete: (ids) => api.post('/api/artifacts/bulk/delete', { ids }),
  bulkExport: (ids) =>
    api.post('/api/artifacts/bulk/export', { ids }, { responseType: 'blob' }),
  exportAll: (projectId) =>
    api.get('/api/artifacts/export-all', {
      params: { project_id: projectId }, responseType: 'blob',
    }),
}

// Files API
export const filesApi = {
  list: (projectId, path = '') =>
    api.get('/api/files', { params: { project_id: projectId, path } }),
  read: (path, projectId) =>
    api.get('/api/files/read', { params: { path, project_id: projectId } }),
  write: (path, content, projectId) =>
    api.put('/api/files/write', { content }, { params: { path, project_id: projectId } }),
  upload: (file, projectId, path = '') => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post('/api/files/upload', formData, {
      params: { project_id: projectId, path },
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  uploadMultiple: (files, projectId, path = '') => {
    const formData = new FormData()
    for (const file of files) {
      formData.append('files', file)
    }
    return api.post('/api/files/upload-multiple', formData, {
      params: { project_id: projectId, path },
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  delete: (path, projectId) =>
    api.delete('/api/files', { params: { path, project_id: projectId } }),
  mkdir: (path, projectId) =>
    api.post('/api/files/mkdir', null, { params: { path, project_id: projectId } }),
  downloadZip: (paths, projectId) =>
    api.post('/api/files/download-zip', { paths }, {
      params: { project_id: projectId },
      responseType: 'blob',
    }),
  downloadUrl: (path, projectId) => {
    const token = localStorage.getItem('auth_token')
    const tokenParam = token && token !== 'no-auth' ? `&token=${encodeURIComponent(token)}` : ''
    return `${BASE_URL}/api/files/download?path=${encodeURIComponent(path)}&project_id=${encodeURIComponent(projectId)}${tokenParam}`
  },
  viewUrl: (path, projectId) => {
    const token = localStorage.getItem('auth_token')
    const tokenParam = token && token !== 'no-auth' ? `&token=${encodeURIComponent(token)}` : ''
    return `${BASE_URL}/api/files/view?path=${encodeURIComponent(path)}&project_id=${encodeURIComponent(projectId)}${tokenParam}`
  },
}

// Settings API
export const settingsApi = {
  get: () => api.get('/api/settings'),
  update: (data) => api.put('/api/settings', data),
  listModels: () => api.get('/api/settings/models'),
  testConnection: () => api.get('/api/settings/test-connection'),
  listSecrets: () => api.get('/api/secrets'),
  setSecret: (key, value) => api.put(`/api/secrets/${key}`, { value }),
  deleteSecret: (key) => api.delete(`/api/secrets/${key}`),
  restartTelegram: () => api.post('/api/settings/restart-telegram'),
  getSecurityLog: (limit = 200, offset = 0) =>
    api.get('/api/settings/security-log', { params: { limit, offset } }),
  clearSecurityLog: () => api.delete('/api/settings/security-log'),
  // Web search provider chain
  getSearchProviders: () => api.get('/api/settings/search/providers'),
  setSearchProviders: (providers) => api.put('/api/settings/search/providers', { providers }),
  resetSearchProvider: (name, period = 'daily') =>
    api.post(`/api/settings/search/providers/${encodeURIComponent(name)}/reset`, null, { params: { period } }),
  testSearchChain: (query = 'test query') =>
    api.post('/api/settings/search/test', null, { params: { query } }),
}

// MCP Connections API
export const mcpApi = {
  listConnections: () => api.get('/api/mcp/connections'),
  addConnection: (name, url, apiKey, headers = {}, enabled = true) =>
    api.post('/api/mcp/connections', { name, url, api_key: apiKey, headers, enabled }),
  updateConnection: (name, data) => api.put(`/api/mcp/connections/${name}`, data),
  removeConnection: (name) => api.delete(`/api/mcp/connections/${name}`),
  testConnection: (name) => api.post(`/api/mcp/connections/${name}/test`),
  reconnect: (name) => api.post(`/api/mcp/connections/${name}/reconnect`),
  listTools: () => api.get('/api/mcp/tools'),
  toggleTool: (connectionName, toolName, excluded) =>
    api.put(`/api/mcp/connections/${connectionName}/tools`, { tool_name: toolName, excluded }),
  // Tavily credit management
  getTavilyUsage: () => api.get('/api/mcp/tavily/usage'),
  setTavilyThresholds: (dailyLimit, monthlyLimit) =>
    api.put('/api/mcp/tavily/thresholds', { daily_limit: dailyLimit, monthly_limit: monthlyLimit }),
  resetTavilyDaily: () => api.post('/api/mcp/tavily/reset-daily'),
  resetTavilyMonthly: () => api.post('/api/mcp/tavily/reset-monthly'),
}

// Skills API
export const skillsApi = {
  list: (projectId, { enabledOnly = false } = {}) =>
    api.get('/api/skills', {
      params: {
        project_id: projectId,
        include_disabled: !enabledOnly,
      },
    }),
  get: (skillName) => api.get(`/api/skills/${skillName}`),
  toggle: (skillName, projectId, enabled, { forceEnable, overridePassword } = {}) =>
    api.put(`/api/skills/${skillName}/toggle`, {
      project_id: projectId,
      enabled,
      ...(forceEnable && { force_enable: true, override_password: overridePassword }),
    }),
  overrideStatus: () => api.get('/api/skills/security/override-status'),
  reload: () => api.post('/api/skills/reload'),
  delete: (skillName) => api.delete(`/api/skills/${skillName}`),
  scan: (skillName, aiReview = true) =>
    api.post(`/api/skills/${skillName}/scan`, null, { params: { ai_review: aiReview } }),
  getScan: (skillName) => api.get(`/api/skills/${skillName}/scan`),
  scanAll: (aiReview = false) =>
    api.post('/api/skills/scan/all', null, { params: { ai_review: aiReview } }),
  scanSummary: () => api.get('/api/skills/scan/summary'),
  quarantine: (skillName) => api.post(`/api/skills/${skillName}/quarantine`),
  listQuarantined: () => api.get('/api/skills/quarantine/list'),
  unquarantine: (skillName) => api.post(`/api/skills/${skillName}/unquarantine`),
  getDiscovery: (projectId) => api.get(`/api/skills/discovery/${projectId}`),
  setDiscovery: (projectId, mode) =>
    api.put(`/api/skills/discovery/${projectId}`, null, { params: { mode } }),

  // AI-Assisted Editor (Phase 4)
  createBlank: (name, description = '') =>
    api.post('/api/skills/editor/blank', { name, description }),
  listFiles: (skillName) =>
    api.get(`/api/skills/editor/${skillName}/files`),
  readFile: (skillName, path) =>
    api.get(`/api/skills/editor/${skillName}/file`, { params: { path } }),
  writeFile: (skillName, path, content) =>
    api.put(`/api/skills/editor/${skillName}/file`, { content }, { params: { path } }),
  deleteFile: (skillName, path) =>
    api.delete(`/api/skills/editor/${skillName}/file`, { params: { path } }),
  scaffold: (brief, { nameHint = null, materialize = false } = {}) =>
    api.post('/api/skills/editor/scaffold', { brief, name_hint: nameHint, materialize }),
  improve: (instructions, { goal = null, skillName = null } = {}) =>
    api.post('/api/skills/editor/improve', { instructions, goal, skill_name: skillName }),
  optimizeTriggers: (description, instructions, currentTriggers = []) =>
    api.post('/api/skills/editor/optimize-triggers', {
      description, instructions, current_triggers: currentTriggers,
    }),
  lint: (manifestJson, instructions) =>
    api.post('/api/skills/editor/lint', { manifest_json: manifestJson, instructions }),
  aiLint: (manifestJson, instructions) =>
    api.post('/api/skills/editor/ai-lint', { manifest_json: manifestJson, instructions }),
  createFile: (skillName, path, content = '') =>
    api.post(`/api/skills/editor/${skillName}/file/new`, { path, content }),
  renameFile: (skillName, oldPath, newPath) =>
    api.post(`/api/skills/editor/${skillName}/file/rename`, { old_path: oldPath, new_path: newPath }),

  // Phase 5: versioning, sharing, analytics, publishing
  listVersions: (skillName) =>
    api.get(`/api/skills/editor/${skillName}/versions`),
  listVersionFiles: (skillName, versionId) =>
    api.get(`/api/skills/editor/${skillName}/versions/${versionId}/files`),
  readVersionFile: (skillName, versionId, path) =>
    api.get(`/api/skills/editor/${skillName}/versions/${versionId}/file`, { params: { path } }),
  restoreVersion: (skillName, versionId) =>
    api.post(`/api/skills/editor/${skillName}/versions/${versionId}/restore`),
  exportUrl: (skillName) => `/api/skills/editor/${skillName}/export`,
  getAnalytics: () => api.get('/api/skills/analytics'),
  resetAnalytics: (skill = null) =>
    api.post('/api/skills/analytics/reset', null, { params: skill ? { skill } : {} }),
  publishSkill: (skillName, registryId, note = '') =>
    api.post(`/api/skills/editor/${skillName}/publish`, { registry_id: registryId, note }),
  testSkill: (skillName, message) =>
    api.post(`/api/skills/editor/${skillName}/test`, { message }),

  // Skill registry hubs (admin)
  listRegistries: () => api.get('/api/skills/registries'),
  createRegistry: (payload) => api.post('/api/skills/registries', payload),
  updateRegistry: (id, payload) => api.put(`/api/skills/registries/${id}`, payload),
  deleteRegistry: (id) => api.delete(`/api/skills/registries/${id}`),

  // Import
  listHubs: () => api.get('/api/skills/hubs'),
  searchHub: (query, hub = null) =>
    api.post('/api/skills/search-hub', null, {
      params: { query, ...(hub && { hub }) },
    }),
  importSkill: (source, hub = 'local', aiReview = true) =>
    api.post('/api/skills/import', { source, hub, ai_review: aiReview }),
  importUpload: (file, aiReview = true) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post('/api/skills/import/upload', formData, {
      params: { ai_review: aiReview },
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
}

// Tasks API
export const tasksApi = {
  list: (projectId) => api.get('/api/tasks', { params: { project_id: projectId } }),
  listAll: () => api.get('/api/tasks/all'),
  create: (name, description, schedule, projectId) =>
    api.post('/api/tasks', { name, description, schedule, project_id: projectId }),
  cancel: (taskId) => api.delete(`/api/tasks/${taskId}`),
  getLogs: (taskId, projectId) =>
    api.get(`/api/tasks/${taskId}/logs`, { params: { project_id: projectId } }),
  getAllLogs: (projectId) =>
    api.get('/api/tasks/logs/all', { params: { project_id: projectId } }),
}

// Personas API
export const personasApi = {
  list: () => api.get('/api/personas').then((r) => r.data),
  get: (personaId) => api.get(`/api/personas/${personaId}`).then((r) => r.data),
  create: (data) => api.post('/api/personas', data).then((r) => r.data),
  update: (personaId, data) => api.put(`/api/personas/${personaId}`, data).then((r) => r.data),
  delete: (personaId) => api.delete(`/api/personas/${personaId}`).then((r) => r.data),
  apply: (personaId, projectId) =>
    api.post(`/api/personas/${personaId}/apply/${projectId}`).then((r) => r.data),
}

// Personality API
export const personalityApi = {
  getSoul: (projectId) => api.get('/api/personality/soul', { params: { project_id: projectId } }),
  updateSoul: (content, projectId) =>
    api.put('/api/personality/soul', { content }, { params: { project_id: projectId } }),
  getAgent: (projectId) => api.get('/api/personality/agent', { params: { project_id: projectId } }),
  updateAgent: (content, projectId) =>
    api.put('/api/personality/agent', { content }, { params: { project_id: projectId } }),
  status: () => api.get('/api/personality/status'),
}

// Projects API
export const projectsApi = {
  list: () => api.get('/api/projects'),
  create: (name, description, id) => api.post('/api/projects', { name, description, id }),
  get: (projectId) => api.get(`/api/projects/${projectId}`),
  update: (projectId, name, description) =>
    api.put(`/api/projects/${projectId}`, { name, description }),
  delete: (projectId) => api.delete(`/api/projects/${projectId}`),

  // Export / Import
  exportProject: (projectId, components = null) =>
    api.post(`/api/projects/${projectId}/export`, { components }, { responseType: 'blob' }),
  exportPreview: (projectId, components = null) =>
    api.post(`/api/projects/${projectId}/export/preview`, { components }),
  importProject: (file, { targetId = null, targetName = null, components = null, overwrite = false } = {}) => {
    const formData = new FormData()
    formData.append('file', file)
    const params = {}
    if (targetId) params.target_id = targetId
    if (targetName) params.target_name = targetName
    if (components) params.components = components.join(',')
    if (overwrite) params.overwrite = true
    return api.post('/api/projects/import', formData, {
      params,
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  scanImport: (file) => {
    const formData = new FormData()
    formData.append('file', file)
    return api.post('/api/projects/import/scan', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
}

// WebSocket helper
// ── Persistent chat WebSocket ──────────────────────────────────────────────
// Kept as a module-level singleton so page navigation within the SPA doesn't
// tear it down while the agent is still processing a long-running tool call.

let _chatSocket = null
let _pingInterval = null
const PING_INTERVAL_MS = 15_000 // 15s keepalive

function _cleanupSocket() {
  if (_pingInterval) { clearInterval(_pingInterval); _pingInterval = null }
  _chatSocket = null
}

export function createChatSocket(onMessage, onClose) {
  // Reuse existing open socket
  if (_chatSocket?.readyState === WebSocket.OPEN) {
    // Re-bind handlers (component may have remounted after navigation)
    _chatSocket.onmessage = (event) => {
      try { onMessage(JSON.parse(event.data)) } catch (e) { console.error('WS parse error:', e) }
    }
    _chatSocket.onclose = (event) => {
      _cleanupSocket()
      if (event.code !== 1000 && event.code !== 1001 && onClose) onClose(event.code, event.reason)
    }
    return _chatSocket
  }

  // Close stale socket if any
  if (_chatSocket) { try { _chatSocket.close() } catch {} }
  _cleanupSocket()

  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = localStorage.getItem('auth_token') || ''
  const tokenParam = token && token !== 'no-auth' ? `?token=${encodeURIComponent(token)}` : ''
  const wsUrl = `${proto}//${window.location.host}/ws/chat${tokenParam}`
  const socket = new WebSocket(wsUrl)

  socket.onmessage = (event) => {
    try { onMessage(JSON.parse(event.data)) } catch (e) { console.error('WS parse error:', e) }
  }
  socket.onerror = (err) => console.error('WebSocket error:', err)
  socket.onclose = (event) => {
    _cleanupSocket()
    if (event.code !== 1000 && event.code !== 1001 && onClose) onClose(event.code, event.reason)
  }

  // Start keepalive pings once connected
  socket.onopen = () => {
    _pingInterval = setInterval(() => {
      if (socket.readyState === WebSocket.OPEN) {
        socket.send(JSON.stringify({ type: 'ping' }))
      }
    }, PING_INTERVAL_MS)
  }

  _chatSocket = socket
  return socket
}

export function closeChatSocket() {
  if (_chatSocket) { try { _chatSocket.close(1000) } catch {} }
  _cleanupSocket()
}
