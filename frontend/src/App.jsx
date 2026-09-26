import React, { lazy, useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import LoginPage from './pages/LoginPage'
import { authApi } from './api/client'

// Pages are code-split: each route's bundle (CodeMirror, d3, mermaid…)
// loads on first visit instead of all up front. Suspense lives in Layout.
const ChatPage = lazy(() => import('./pages/ChatPage'))
const MemoryPage = lazy(() => import('./pages/MemoryPage'))
const ArtifactsPage = lazy(() => import('./pages/ArtifactsPage'))
const SettingsPage = lazy(() => import('./pages/SettingsPage'))
const SkillsPage = lazy(() => import('./pages/SkillsPage'))
const MCPPage = lazy(() => import('./pages/MCPPage'))
const ConnectionsPage = lazy(() => import('./pages/ConnectionsPage'))
const PersonasPage = lazy(() => import('./pages/PersonasPage'))
const TasksPage = lazy(() => import('./pages/TasksPage'))
const ProjectsPage = lazy(() => import('./pages/ProjectsPage'))

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
      const token = localStorage.getItem('auth_token')
      if (token) {
        setAuthState(true)
      } else {
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
          <Route path="files" element={<Navigate to="/artifacts" replace />} />
          <Route path="artifacts" element={<ArtifactsPage />} />
          <Route path="skills" element={<SkillsPage />} />
          <Route path="mcp" element={<MCPPage />} />
          <Route path="sources" element={<Navigate to="/connections" replace />} />
          <Route path="connections" element={<ConnectionsPage />} />
          <Route path="personas" element={<PersonasPage />} />
          <Route path="personality" element={<Navigate to="/settings" replace />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="projects" element={<ProjectsPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
