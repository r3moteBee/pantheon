import React, { useState, useEffect, useRef } from 'react'
import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import {
  MessageSquare, Brain, FolderOpen, User, Settings,
  ListTodo, Briefcase, Menu, X, Bot, ChevronDown, LogOut, Check
} from 'lucide-react'
import { useStore } from '../store'
import { projectsApi } from '../api/client'

function VersionTag() {
  const [version, setVersion] = useState('…')
  useEffect(() => {
    fetch('/api/health').then(r => r.json()).then(d => setVersion(d.version || '?')).catch(() => setVersion('?'))
  }, [])
  return <p className="text-xs text-gray-700 text-center">{version}</p>
}

const NAV_ITEMS = [
  { to: '/chat', icon: MessageSquare, label: 'Chat' },
  { to: '/memory', icon: Brain, label: 'Memory' },
  { to: '/files', icon: FolderOpen, label: 'Files' },
  { to: '/personality', icon: User, label: 'Personality' },
  { to: '/tasks', icon: ListTodo, label: 'Tasks' },
  { to: '/projects', icon: Briefcase, label: 'Projects' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

const DEFAULT_PROJECT = { id: 'default', name: 'Default Project' }

export default function Layout() {
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  const setSidebarOpen = useStore((s) => s.setSidebarOpen)
  const activeProject = useStore((s) => s.activeProject)
  const setActiveProject = useStore((s) => s.setActiveProject)
  const projects = useStore((s) => s.projects)
  const setProjects = useStore((s) => s.setProjects)
  const notifications = useStore((s) => s.notifications)
  const removeNotification = useStore((s) => s.removeNotification)

  const [projectMenuOpen, setProjectMenuOpen] = useState(false)
  const projectMenuRef = useRef(null)

  // Load project list once on mount
  useEffect(() => {
    projectsApi.list().then(res => {
      setProjects(res.data.projects || [])
    }).catch(() => {})
  }, [])

  // Close dropdown when clicking outside
  useEffect(() => {
    const handler = (e) => {
      if (projectMenuRef.current && !projectMenuRef.current.contains(e.target)) {
        setProjectMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  const allProjects = [DEFAULT_PROJECT, ...projects.filter(p => p.id !== 'default')]

  const selectProject = (project) => {
    setActiveProject(project)
    setProjectMenuOpen(false)
  }

  return (
    <div className="flex h-screen bg-gray-950 overflow-hidden">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="fixed inset-0 bg-black/60 z-20 lg:hidden"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      {/* Sidebar */}
      <aside
        className={`
          fixed lg:relative inset-y-0 left-0 z-30
          w-64 bg-gray-900 border-r border-gray-800
          flex flex-col transform transition-transform duration-300
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
        `}
      >
        {/* Logo */}
        <div className="flex items-center gap-3 p-4 border-b border-gray-800">
          <div className="w-8 h-8 bg-brand-600 rounded-lg flex items-center justify-center">
            <Bot className="w-5 h-5 text-white" />
          </div>
          <div>
            <h1 className="font-bold text-white text-sm">Agent Harness</h1>
            <p className="text-xs text-gray-500">AI Agent Framework</p>
          </div>
          <button
            onClick={() => setSidebarOpen(false)}
            className="lg:hidden ml-auto text-gray-400 hover:text-white"
          >
            <X className="w-4 h-4" />
          </button>
        </div>

        {/* Project switcher */}
        <div className="px-3 py-2 border-b border-gray-800" ref={projectMenuRef}>
          <button
            onClick={() => setProjectMenuOpen(o => !o)}
            className="w-full flex items-center gap-2 px-2 py-1.5 rounded-md bg-gray-800 hover:bg-gray-750 text-xs transition-colors group"
          >
            <div className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" />
            <span className="text-gray-300 truncate flex-1 text-left">
              {activeProject?.name || 'Default Project'}
            </span>
            <ChevronDown className={`w-3 h-3 text-gray-500 flex-shrink-0 transition-transform ${projectMenuOpen ? 'rotate-180' : ''}`} />
          </button>

          {projectMenuOpen && (
            <div className="mt-1 rounded-md border border-gray-700 bg-gray-900 shadow-lg overflow-hidden">
              {allProjects.map(project => (
                <button
                  key={project.id}
                  onClick={() => selectProject(project)}
                  className="w-full flex items-center gap-2 px-3 py-2 text-xs hover:bg-gray-800 transition-colors text-left"
                >
                  <Check className={`w-3 h-3 flex-shrink-0 ${activeProject?.id === project.id ? 'text-brand-400' : 'text-transparent'}`} />
                  <span className={`truncate ${activeProject?.id === project.id ? 'text-white font-medium' : 'text-gray-400'}`}>
                    {project.name}
                  </span>
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Navigation */}
        <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => (
            <NavLink
              key={to}
              to={to}
              onClick={() => setSidebarOpen(false)}
              className={({ isActive }) => `
                flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium
                transition-colors duration-150
                ${isActive
                  ? 'bg-brand-600 text-white'
                  : 'text-gray-400 hover:text-white hover:bg-gray-800'
                }
              `}
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              {label}
            </NavLink>
          ))}
        </nav>

        {/* Footer */}
        <div className="p-3 border-t border-gray-800 space-y-2">
          <button
            onClick={() => {
              localStorage.removeItem('auth_token')
              window.dispatchEvent(new Event('auth:logout'))
            }}
            className="w-full flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors"
          >
            <LogOut className="w-3 h-3" />
            Sign out
          </button>
          <VersionTag />
        </div>
      </aside>

      {/* Main content */}
      <div className="flex-1 flex flex-col min-w-0 overflow-hidden">
        {/* Mobile header */}
        <header className="lg:hidden flex items-center gap-3 px-4 py-3 bg-gray-900 border-b border-gray-800">
          <button
            onClick={toggleSidebar}
            className="text-gray-400 hover:text-white"
          >
            <Menu className="w-5 h-5" />
          </button>
          <Bot className="w-5 h-5 text-brand-500" />
          <span className="font-semibold text-white text-sm">Agent Harness</span>
        </header>

        {/* Page content */}
        <main className="flex-1 overflow-hidden">
          <Outlet />
        </main>
      </div>

      {/* Toast notifications */}
      <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
        {notifications.map((n) => (
          <div
            key={n.id}
            className={`
              flex items-center gap-3 px-4 py-3 rounded-lg shadow-lg text-sm max-w-sm
              ${n.type === 'error' ? 'bg-red-900 text-red-100 border border-red-700' : ''}
              ${n.type === 'success' ? 'bg-green-900 text-green-100 border border-green-700' : ''}
              ${!n.type || n.type === 'info' ? 'bg-gray-800 text-gray-100 border border-gray-700' : ''}
            `}
          >
            <span className="flex-1">{n.message}</span>
            <button onClick={() => removeNotification(n.id)} className="opacity-60 hover:opacity-100">
              <X className="w-3 h-3" />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
