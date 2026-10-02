import axios from 'axios'

const BASE_URL = import.meta.env.VITE_API_URL || ''

// Auth rides on the HttpOnly session cookie set by /api/auth/login — script
// never sees the token and it never goes into URLs.
export const api = axios.create({
  baseURL: BASE_URL,
  timeout: 120000,
  withCredentials: true,
  headers: { 'Content-Type': 'application/json' },
})

// Earlier builds kept a (non-expiring) token in localStorage; drop it.
try { localStorage.removeItem('auth_token') } catch { /* storage unavailable */ }

// Response interceptor — handle errors and 401 redirects
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401 && !error.config?.url?.includes('/api/auth/')) {
      window.dispatchEvent(new Event('auth:logout'))
    }
    // One place turns an API failure into a readable Error: callers use
    // err.message (FastAPI's detail may be a string, a validation list or
    // an object) and err.status.
    const detail = error.response?.data?.detail
    let message
    if (typeof detail === 'string') message = detail
    else if (Array.isArray(detail)) message = detail.map((d) => d?.msg || JSON.stringify(d)).join('; ')
    else if (detail) message = detail.message || JSON.stringify(detail)
    else message = error.message || 'Request failed'
    const err = new Error(message)
    err.status = error.response?.status
    err.data = error.response?.data
    err.response = error.response
    return Promise.reject(err)
  }
)

// Auth API
export const authApi = {
  config: () => api.get('/api/auth/config').then((r) => r.data),
  login: (password) =>
    api.post('/api/auth/login', { password }),
  // Resolves when the session cookie is valid; rejects on 401.
  session: () => api.get('/api/auth/session').then((r) => r.data),
  logout: () => api.post('/api/auth/logout').catch(() => {}),
}

// Chat API
export const chatApi = {
  /**
   * Upload a file as a chat attachment. Backend stores it in ArtifactStore
   * under chat-attachments/YYYY-MM-DD/ and, for images, enqueues a
   * background image_extraction job.
   *
   * Response shape:
   *   { status, artifact_id, path, content_type, size, filename,
   *     indexing, extraction_job_id? }
   */
  attachFile: (file, projectId, sessionId = null) => {
    const formData = new FormData()
    formData.append('file', file)
    const params = { project_id: projectId }
    if (sessionId) params.session_id = sessionId
    return api.post('/api/chat/attach', formData, {
      params,
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
}

// Memory API
export const memoryApi = {
  listSemantic: (projectId, limit = 50, offset = 0) =>
    api.get('/api/memory/semantic', { params: { project_id: projectId, limit, offset } }),
  deleteSemantic: (docId, projectId) =>
    api.delete(`/api/memory/semantic/${docId}`, { params: { project_id: projectId } }),
  listNotes: (projectId) =>
    api.get('/api/memory/episodic/notes', { params: { project_id: projectId } }),
  listMessages: (projectId, limit = 50) =>
    api.get('/api/memory/episodic/messages', { params: { project_id: projectId, limit } }),
  deleteNote: (noteId) =>
    api.delete(`/api/memory/episodic/notes/${noteId}`),
  deleteMessage: (messageId) =>
    api.delete(`/api/memory/episodic/messages/${messageId}`),
  createGraphEdge: (labelA, labelB, relationship, projectId) =>
    api.post('/api/memory/graph/edges', { label_a: labelA, label_b: labelB, relationship, project_id: projectId }),
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
  graphFull: (projectId, type, limit = 500) =>
    api.get('/api/memory/graph/full', { params: { project_id: projectId, type, limit } }),
  graphPath: (projectId, from, to, opts = {}) =>
    api.get('/api/memory/graph/path', {
      params: { project_id: projectId, from, to, k: opts.k || 1, weighted: opts.weighted ? true : undefined },
    }),
}

export const systemApi = {
  sandboxHealth: () => api.get('/api/system/sandbox'),
  checkUpdate: () => api.get('/api/system/update/check'),
  executeUpdate: (payload) => api.post('/api/system/update/execute', payload),
}

export const connectionsApi = {
  list: () => api.get('/api/connections/github'),
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

export const projectSettingsApi = {
  get: (projectId) => api.get(`/api/projects/${projectId}/settings`),
  update: (projectId, body) => api.put(`/api/projects/${projectId}/settings`, body),
}

// Unified jobs API
export const jobsApi = {
  list: (params = {}) => api.get('/api/jobs', { params }),
  get: (id) => api.get(`/api/jobs/${id}`),
  cancel: (id) => api.post(`/api/jobs/${id}/cancel`),
  rerun: (id) => api.post(`/api/jobs/${id}/rerun`),
  delete: (id) => api.delete(`/api/jobs/${id}`),
}

export const conversationsApi = {
  list: (projectId, limit = 50) =>
    api.get('/api/conversations', { params: { project_id: projectId, limit } }),
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
  rawUrl: (id) => {
    return `${BASE_URL}/api/artifacts/${id}/raw`
  },
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
  move: (id, dest_folder, { dest_project_id = null, mode = 'move' } = {}) =>
    api.post(`/api/artifacts/${id}/move`, { dest_folder, dest_project_id, mode }),
  moveBulk: (ids, dest_folder, { dest_project_id = null, mode = 'move' } = {}) =>
    api.post('/api/artifacts/bulk/move', { ids, dest_folder, dest_project_id, mode }),
  pin: (id, pinned) => api.post(`/api/artifacts/${id}/pin`, { pinned }),
  delete: (id) => api.delete(`/api/artifacts/${id}`),
  versions: (id) => api.get(`/api/artifacts/${id}/versions`),
  diff: (id, a, b) => api.get(`/api/artifacts/${id}/diff`, { params: { a, b } }),
  restoreVersion: (id, n) => api.post(`/api/artifacts/${id}/versions/${n}/restore`),
  bulkDelete: (ids) => api.post('/api/artifacts/bulk/delete', { ids }),
  bulkExport: (ids) =>
    api.post('/api/artifacts/bulk/export', { ids }, { responseType: 'blob' }),
}

// Files API
export const filesApi = {
  read: (path, projectId) =>
    api.get('/api/files/read', { params: { path, project_id: projectId } }),
  viewUrl: (path, projectId) => {
    return `${BASE_URL}/api/files/view?path=${encodeURIComponent(path)}&project_id=${encodeURIComponent(projectId)}`
  },
}

// Settings API
export const settingsApi = {
  get: () => api.get('/api/settings'),
  update: (data) => api.put('/api/settings', data),
  listSecrets: () => api.get('/api/secrets'),
  setSecret: (key, value) => api.put(`/api/secrets/${key}`, { value }),
  deleteSecret: (key) => api.delete(`/api/secrets/${key}`),
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

// LLM endpoints, task-class routing, model profiles + usage
export const llmApi = {
  listEndpoints: () =>
    api.get('/api/llm/endpoints').then((r) => r.data.endpoints),
  saveEndpoint: (payload) =>
    api.post('/api/llm/endpoints', payload).then((r) => r.data),
  deleteEndpoint: (name) =>
    api.delete(`/api/llm/endpoints/${encodeURIComponent(name)}`).then((r) => r.data),
  probe: (payload) =>
    api.post('/api/llm/probe', payload).then((r) => r.data),
  taskClasses: () =>
    api.get('/api/llm/task-classes').then((r) => r.data.task_classes),
  getRoutes: () => api.get('/api/llm/routes').then((r) => r.data),
  setRoutes: (routes) => api.put('/api/llm/routes', { routes }).then((r) => r.data),
  getProfile: (endpoint, model) =>
    api.get('/api/llm/profile', { params: { endpoint, model } }).then((r) => r.data),
  saveProfile: (endpoint, model, profile) =>
    api.put('/api/llm/profiles', { profiles: { [`${endpoint}/${model}`]: profile } }).then((r) => r.data),
  resetProfile: (endpoint, model) =>
    api.delete('/api/llm/profiles', { params: { endpoint, model } }).then((r) => r.data),
  usage: (hours = 24) => api.get('/api/llm/usage', { params: { hours } }).then((r) => r.data),
  getRouter: () => api.get('/api/llm/router').then((r) => r.data),
  setRouter: (patch) => api.put('/api/llm/router', patch).then((r) => r.data),
  routerDecisions: (hours = 24) =>
    api.get('/api/llm/router/decisions', { params: { hours } }).then((r) => r.data),
  routerFeedback: (decisionId, rating) =>
    api.post('/api/llm/router/feedback', { decision_id: decisionId, rating }).then((r) => r.data),
  routerTuning: (hours = 168) =>
    api.get('/api/llm/router/tuning', { params: { hours } }).then((r) => r.data),
  routerSimulate: (patch, days = 14) =>
    api.post('/api/llm/router/simulate', { patch, days }).then((r) => r.data),
  routerApply: (id, hours = 168) =>
    api.post('/api/llm/router/apply', { id, hours }).then((r) => r.data),
}

// MCP Connections API
export const mcpApi = {
  listConnections: () => api.get('/api/mcp/connections'),
  addConnection: (name, url, apiKey, headers = {}, enabled = true, authType = 'api_key') =>
    api.post('/api/mcp/connections', { name, url, api_key: apiKey, headers, enabled, auth_type: authType }),
  updateConnection: (name, data) => api.put(`/api/mcp/connections/${name}`, data),
  removeConnection: (name) => api.delete(`/api/mcp/connections/${name}`),
  testConnection: (name) => api.post(`/api/mcp/connections/${name}/test`),
  reconnect: (name) => api.post(`/api/mcp/connections/${name}/reconnect`),
  startOauth: (name) => api.post(`/api/mcp/connections/${name}/start-oauth`),
  revokeOauth: (name) => api.post(`/api/mcp/connections/${name}/revoke-oauth`),
  listTools: () => api.get('/api/mcp/tools'),
  toggleTool: (connectionName, toolName, excluded) =>
    api.put(`/api/mcp/connections/${connectionName}/tools`, { tool_name: toolName, excluded }),
  // Per-connection call budget (every connection is metered)
  getBudget: (name) => api.get(`/api/mcp/connections/${encodeURIComponent(name)}/budget`),
  setBudget: (name, body) => api.put(`/api/mcp/connections/${encodeURIComponent(name)}/budget`, body),
  resetBudget: (name, period) => api.post(`/api/mcp/connections/${encodeURIComponent(name)}/budget/reset`, { period }),
  scanPorts: () => api.post('/api/mcp/scan'),
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
  listQuarantined: () => api.get('/api/skills/quarantine/list'),
  unquarantine: (skillName) => api.post(`/api/skills/${skillName}/unquarantine`),

  // AI-assisted editor
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

  // Versioning, analytics, publishing
  listVersions: (skillName) =>
    api.get(`/api/skills/editor/${skillName}/versions`),
  restoreVersion: (skillName, versionId) =>
    api.post(`/api/skills/editor/${skillName}/versions/${versionId}/restore`),
  exportUrl: (skillName) => `/api/skills/editor/${skillName}/export`,
  getAnalytics: () => api.get('/api/skills/analytics'),
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
  listAll: () => api.get('/api/tasks/all'),
  cancel: (taskId) => api.delete(`/api/tasks/${taskId}`),
  runNow: (taskId) => api.post(`/api/tasks/${taskId}/run-now`),
  approve: (taskId) => api.post(`/api/tasks/${taskId}/approve`),
  updatePlan: (taskId, plan) => api.patch(`/api/tasks/${taskId}/plan`, { plan }),
  getLogs: (taskId, projectId) =>
    api.get(`/api/tasks/${taskId}/logs`, { params: { project_id: projectId } }),
}

// Personas API
export const personasApi = {
  list: () => api.get('/api/personas').then((r) => r.data),
  create: (data) => api.post('/api/personas', data).then((r) => r.data),
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
  // Project: drop its override (follow global). Global: restore bundled files.
  reset: (projectId) => api.post('/api/personality/reset', null, { params: { project_id: projectId } }),
}

// Projects API
export const projectsApi = {
  list: () => api.get('/api/projects'),
  create: (name, description, id) => api.post('/api/projects', { name, description, id }),
  get: (projectId) => api.get(`/api/projects/${projectId}`),
  update: (projectId, name, description) =>
    api.put(`/api/projects/${projectId}`, { name, description }),
  delete: (projectId) => api.delete(`/api/projects/${projectId}`),
  deletePreview: (projectId) => api.get(`/api/projects/${projectId}/delete-preview`),

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

// Messaging Gateway API
export const messagingApi = {
  status: () => api.get('/api/messaging/status'),
  restartAdapter: (name) => api.post(`/api/messaging/${name}/restart`),
  getChannels: () => api.get('/api/messaging/channels'),
  getMappings: () => api.get('/api/messaging/mappings'),
  setMapping: (channelId, projectId) =>
    api.put(`/api/messaging/mappings/${encodeURIComponent(channelId)}`, { project_id: projectId }),
  removeMapping: (channelId) =>
    api.delete(`/api/messaging/mappings/${encodeURIComponent(channelId)}`),
  getDefaultProject: () => api.get('/api/messaging/default-project'),
  setDefaultProject: (projectId) =>
    api.put('/api/messaging/default-project', { project_id: projectId }),
}

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
  // Same-origin handshake carries the session cookie.
  const wsUrl = `${proto}//${window.location.host}/ws/chat`
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
