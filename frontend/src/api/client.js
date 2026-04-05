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
  downloadUrl: (path, projectId) => {
    const token = localStorage.getItem('auth_token')
    const tokenParam = token && token !== 'no-auth' ? `&token=${encodeURIComponent(token)}` : ''
    return `${BASE_URL}/api/files/download?path=${encodeURIComponent(path)}&project_id=${encodeURIComponent(projectId)}${tokenParam}`
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
}

// Tasks API
export const tasksApi = {
  list: (projectId) => api.get('/api/tasks', { params: { project_id: projectId } }),
  create: (name, description, schedule, projectId) =>
    api.post('/api/tasks', { name, description, schedule, project_id: projectId }),
  cancel: (taskId) => api.delete(`/api/tasks/${taskId}`),
  getLogs: (taskId, projectId) =>
    api.get(`/api/tasks/${taskId}/logs`, { params: { project_id: projectId } }),
  getAllLogs: (projectId) =>
    api.get('/api/tasks/logs/all', { params: { project_id: projectId } }),
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
}

// WebSocket helper
export function createChatSocket(onMessage, onClose) {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const token = localStorage.getItem('auth_token') || ''
  const tokenParam = token && token !== 'no-auth' ? `?token=${encodeURIComponent(token)}` : ''
  const wsUrl = `${proto}//${window.location.host}/ws/chat${tokenParam}`
  const socket = new WebSocket(wsUrl)
  socket.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data)
      onMessage(data)
    } catch (e) {
      console.error('WS parse error:', e)
    }
  }
  socket.onerror = (err) => console.error('WebSocket error:', err)
  socket.onclose = (event) => {
    // Codes 1000 (normal) and 1001 (going away) are expected — anything else is unexpected
    if (event.code !== 1000 && event.code !== 1001 && onClose) {
      onClose(event.code, event.reason)
    }
  }
  return socket
}
