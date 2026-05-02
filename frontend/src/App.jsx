import React, { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import ChatPage from './pages/ChatPage'
import MemoryPage from './pages/MemoryPage'
import FilesPage from './pages/FilesPage'
import ArtifactsPage from './pages/ArtifactsPage'
import PersonalityPage from './pages/PersonalityPage'
import SettingsPage from './pages/SettingsPage'
import SkillsPage from './pages/SkillsPage'
import MCPPage from './pages/MCPPage'
import SourcesPage from './pages/SourcesPage'
import ConnectionsPage from './pages/ConnectionsPage'
import PersonasPage from './pages/PersonasPage'
import TasksPage from './pages/TasksPage'
import ProjectsPage from './pages/ProjectsPage'
import LoginPage from './pages/LoginPage'
import { useStore } from './store'
import { projectsApi, authApi } from './api/client'

export default function App() {
  const setProjects = useStore((s) => s.setProjects)
  const setActiveProject = useStore((s) => s.setActiveProject)

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
    // Load projects now that we are authenticated
    projectsApi.list().then((res) => {
      const projects = res.data.projects || []
      setProjects(projects)
      if (projects.length > 0) setActiveProject(projects[0])
    }).catch(console.error)
  }

  // Load projects on first authenticated render
  useEffect(() => {
    if (authState === true) {
      projectsApi.list().then((res) => {
        const projects = res.data.projects || []
        setProjects(projects)
        if (projects.length > 0) setActiveProject(projects[0])
      }).catch(console.error)
    }
  }, [authState])

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
