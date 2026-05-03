import React, { useState, useEffect, useRef } from 'react'
import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import {
  MessageSquare, FolderOpen, User, Settings,
  Briefcase, Menu, X, Bot, LogOut, Zap, Github, Users,
  PanelLeftClose, PanelLeftOpen,
} from 'lucide-react'
import { useStore } from '../store'
import { projectsApi } from '../api/client'
import Tooltip from './Tooltip'

function VersionTag() {
  const [version, setVersion] = useState('…')
  useEffect(() => {
    fetch('/api/health').then(r => r.json()).then(d => setVersion(d.version || '?')).catch(() => setVersion('?'))
  }, [])
  return <p className="text-xs text-gray-700 text-center">{version}</p>
}

const NAV_ITEMS = [
  { to: '/chat', icon: MessageSquare, label: 'Chat' },
  { to: '/artifacts', icon: FolderOpen, label: 'Artifacts' },
  { to: '/skills', icon: Zap, label: 'Skills' },
  { to: '/personas', icon: Users, label: 'Personas' },
  { to: '/connections', icon: Github, label: 'Connections' },
  { to: '/projects', icon: Briefcase, label: 'Projects' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

export default function Layout() {
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const setSidebarOpen = useStore((s) => s.setSidebarOpen)
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  const collapsed = useStore((s) => s.sidebarCollapsed)
  const toggleCollapsed = useStore((s) => s.toggleSidebarCollapsed)
  const setProjects = useStore((s) => s.setProjects)
  const setActiveProject = useStore((s) => s.setActiveProject)
  const notifications = useStore((s) => s.notifications)
  const removeNotification = useStore((s) => s.removeNotification)
  const navigate = useNavigate()

  // Load projects once at mount so the chat-bar pill picker has its list.
  // The sidebar itself no longer renders a project picker — the chat
  // top-bar pill is the picker now.
  useEffect(() => {
    projectsApi.list().then((res) => {
      const all = res.data?.projects || []
      setProjects(all)
      if (all.length > 0) {
        // Restore last-active or fall back to first
        const stored = localStorage.getItem('active_project_id')
        const restored = stored && all.find((p) => p.id === stored)
        setActiveProject(restored || all[0])
      }
    }).catch(() => {})
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const sidebarWidth = collapsed ? 'w-16' : 'w-60'

  return (
    <div className="flex h-screen bg-gray-950 text-gray-100">
      {/* Mobile overlay */}
      {sidebarOpen && (
        <div
          className="lg:hidden fixed inset-0 z-40 bg-black/60"
          onClick={() => setSidebarOpen(false)}
        />
      )}

      <aside
        className={`
          fixed lg:relative z-50 lg:z-auto
          ${sidebarWidth} h-full bg-gray-900 border-r border-gray-800
          flex flex-col transition-[width] duration-150
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0'}
        `}
      >
        {/* Logo / brand */}
        <div className={`flex items-center gap-3 px-4 py-3 ${collapsed ? 'justify-center px-2' : ''}`}>
          <div className="w-7 h-7 bg-brand-600 rounded-lg flex items-center justify-center flex-shrink-0">
            <Bot className="w-4 h-4 text-white" />
          </div>
          {!collapsed && (
            <div className="min-w-0">
              <h1 className="font-bold text-white text-sm leading-tight">Pantheon</h1>
              <p className="text-[10px] text-gray-500 leading-tight truncate">AI Agent Framework</p>
            </div>
          )}
          {!collapsed && (
            <button
              onClick={() => setSidebarOpen(false)}
              className="lg:hidden ml-auto text-gray-400 hover:text-white"
              aria-label="Close sidebar"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        {/* Navigation */}
        <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
          {NAV_ITEMS.map(({ to, icon: Icon, label }) => {
            const link = (
              <NavLink
                key={to}
                to={to}
                onClick={() => setSidebarOpen(false)}
                className={({ isActive }) => `
                  flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium
                  transition-colors duration-150
                  ${collapsed ? 'justify-center' : ''}
                  ${isActive
                    ? 'bg-brand-600 text-white'
                    : 'text-gray-400 hover:text-white hover:bg-gray-800'
                  }
                `}
              >
                <Icon className="w-4 h-4 flex-shrink-0" />
                {!collapsed && label}
              </NavLink>
            )
            return collapsed ? (
              <Tooltip key={to} label={label} placement="right">{link}</Tooltip>
            ) : link
          })}
        </nav>

        {/* Footer */}
        <div className="p-3 space-y-1">
          {/* Collapse toggle (desktop only) */}
          <button
            onClick={toggleCollapsed}
            className={`hidden lg:flex w-full items-center gap-2 px-3 py-1.5 rounded-lg text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors ${collapsed ? 'justify-center' : ''}`}
            title={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
            aria-label={collapsed ? 'Expand sidebar' : 'Collapse sidebar'}
          >
            {collapsed ? (
              <PanelLeftOpen className="w-3.5 h-3.5" />
            ) : (
              <>
                <PanelLeftClose className="w-3 h-3" />
                <span>Collapse</span>
              </>
            )}
          </button>

          <button
            onClick={() => {
              localStorage.removeItem('auth_token')
              window.dispatchEvent(new Event('auth:logout'))
            }}
            title={collapsed ? 'Sign out' : undefined}
            className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors ${collapsed ? 'justify-center' : ''}`}
          >
            <LogOut className="w-3 h-3 flex-shrink-0" />
            {!collapsed && 'Sign out'}
          </button>
          {!collapsed && <VersionTag />}
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
          <span className="font-semibold text-white text-sm">Pantheon</span>
        </header>

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
