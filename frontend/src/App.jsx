import React, { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import ChatPage from './pages/ChatPage'
import MemoryPage from './pages/MemoryPage'
import FilesPage from './pages/FilesPage'
import PersonalityPage from './pages/PersonalityPage'
import SettingsPage from './pages/SettingsPage'
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
  // Auth configuration from the backend
  const [authConfig, setAuthConfig] = useState(null)

  const initAuth = async () => {
    try {
      // Check for OIDC callback token in URL
      const params = new URLSearchParams(window.location.search)
      const urlToken = params.get('token')
      const authError = params.get('auth_error')

      // Clean URL params
      if (urlToken || authError) {
        window.history.replaceState({}, '', window.location.pathname)
      }

      if (urlToken) {
        localStorage.setItem('auth_token', urlToken)
        setAuthState(true)
        return
      }

      const config = await authApi.config()
      setAuthConfig(config)

      if (!config.auth_required) {
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
      setAuthState(false)
    }
  }

  useEffect(() => {
    initAuth()

    const handleLogout = () => {
      localStorage.removeItem('auth_token')
      setAuthState(false)
    }
    window.addEventListener('auth:logout', handleLogout)
    return () => window.removeEventListener('auth:logout', handleLogout)
  }, [])

  const handleLogin = (token) => {
    setAuthState(true)
    projectsApi.list().then((res) => {
      const projects = res.data.projects || []
      setProjects(projects)
      if (projects.length > 0) setActiveProject(projects[0])
    }).catch(console.error)
  }

  useEffect(() => {
    if (authState === true) {
      projectsApi.list().then((res) => {
        const projects = res.data.projects || []
        setProjects(projects)
        if (projects.length > 0) setActiveProject(projects[0])
      }).catch(console.error)
    }
  }, [authState])

  if (authState === null) {
    return (
      <div className="min-h-screen bg-gray-950 flex items-center justify-center">
        <div className="w-6 h-6 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" />
      </div>
    )
  }

  if (authState === false) {
    return <LoginPage onLogin={handleLogin} authConfig={authConfig} />
  }

  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/chat" replace />} />
          <Route path="chat" element={<ChatPage />} />
          <Route path="memory" element={<MemoryPage />} />
          <Route path="files" element={<FilesPage />} />
          <Route path="personality" element={<PersonalityPage />} />
          <Route path="tasks" element={<TasksPage />} />
          <Route path="projects" element={<ProjectsPage />} />
          <Route path="settings" element={<SettingsPage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  )
}
