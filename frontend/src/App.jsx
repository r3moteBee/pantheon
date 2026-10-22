import React, { lazy, useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate, useLocation } from 'react-router-dom'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import { authApi } from './api/client'

// Pages are code-split: each route's bundle (CodeMirror, d3, mermaid…)
// loads on first visit instead of all up front. Suspense lives in Layout.
const ChatPage = lazy(() => import('./pages/ChatPage'))
const MemoryPage = lazy(() => import('./pages/MemoryPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const SkillsPage = lazy(() => import('./pages/SkillsPage'))
const ConnectionsPage = lazy(() => import('./pages/ConnectionsPage'))
const ProjectsPage = lazy(() => import('./pages/ProjectsPage'))

// Redirect that keeps the query string (plus `extra` params), so old
// bookmarks like /artifacts?id=<artifact> still land on the same thing.
function RedirectWithQuery({ to, extra = {} }) {
  const { search, hash } = useLocation()
  const params = new URLSearchParams(search)
  for (const [k, v] of Object.entries(extra)) params.set(k, v)
  const qs = params.toString()
  return <Navigate to={`${to}${qs ? `?${qs}` : ''}${hash}`} replace />
}

export default function App() {
  // null = checking, false = needs login, true = authenticated
  const [authState, setAuthState] = useState(null)

  const initAuth = async () => {
    try {
      const { auth_required } = await authApi.config()
      if (!auth_required) {
        setAuthState(true)
        return
      }
      try {
        await authApi.session()
        setAuthState(true)
      } catch {
        setAuthState(false)
      }
    } catch {
      // Can't reach backend yet — show login as safe fallback
      setAuthState(false)
    }
  }

  useEffect(() => {
    initAuth()

    // Listen for 401 responses (from the axios interceptor)
    const handleLogout = () => setAuthState(false)
    window.addEventListener('auth:logout', handleLogout)
    return () => window.removeEventListener('auth:logout', handleLogout)
  }, [])

  const handleLogin = (token) => {
    setAuthState(true)
  }

  // Checking auth
  if (authState === null) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <div className="w-6 h-6 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  // Not authenticated
  if (authState === false) {
    return <LoginPage onLogin={handleLogin} />
  }

  // Authenticated
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<ChatPage />} />
          <Route path="memory" element={<MemoryPage />} />
          {/* The project tabs live under /chat?tab=…; /workspace is an alias.
              Global artifacts = the "All" scope of the project Artifacts tab. */}
          <Route path="workspace" element={<RedirectWithQuery to="/chat" />} />
          <Route path="files" element={<RedirectWithQuery to="/chat" extra={{ tab: 'artifacts' }} />} />
          <Route path="artifacts" element={<RedirectWithQuery to="/chat" extra={{ tab: 'artifacts' }} />} />
          <Route path="skills" element={<SkillsPage />} />
          <Route path="mcp" element={<Navigate to="/connections" replace />} />
          <Route path="sources" element={<Navigate to="/connections" replace />} />
          <Route path="connections" element={<ConnectionsPage />} />
          <Route path="personas" element={<Navigate to="/settings?section=personality" replace />} />
          <Route path="personality" element={<Navigate to="/settings?section=personality" replace />} />
          <Route path="tasks" element={<Navigate to="/settings?section=tasks" replace />} />
          <Route path="projects" element={<ProjectsPage />} />
          <Route path="settings" element={<SettingsPage />} />
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
