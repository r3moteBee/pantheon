import React, { useState, useEffect } from 'react'
import { Outlet, NavLink } from 'react-router-dom'
import {
  LayoutDashboard, Settings,
  Briefcase, Menu, X, Bot, LogOut, Zap, Plug,
  PanelLeftClose, PanelLeftOpen,
} from 'lucide-react'
import { useStore } from '../store'
import { authApi, projectsApi } from '../api/client'
import Tooltip from './Tooltip'

function VersionTag() {
  const [version, setVersion] = useState('…')
  useEffect(() => {
    fetch('/api/health').then(r => r.json()).then(d => setVersion(d.version || '?')).catch(() => setVersion('?'))
  }, [])
  return <p className="text-xs text-gray-400 text-center" title="Pantheon version">{version}</p>
}

// Navigation IA (UI review #14): "Workspace" is the active project (its
// Chat / Memory / Artifacts / Repository / Tasks / Project Settings tabs,
// all under /chat); the rest applies to every project. The old global
// Artifacts page is the "All" scope of the project Artifacts tab.
const WORKSPACE_ITEM = { to: '/chat', icon: LayoutDashboard, label: 'Workspace' }
const GLOBAL_ITEMS = [
  { to: '/projects', icon: Briefcase, label: 'Projects' },
  { to: '/skills', icon: Zap, label: 'Skills' },
  { to: '/connections', icon: Plug, label: 'Connections' },
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
  const activeProject = useStore((s) => s.activeProject)
  const projectName = activeProject?.name || (activeProject?.id === 'default' ? 'Default Project' : '')
  const notifications = useStore((s) => s.notifications)
  const removeNotification = useStore((s) => s.removeNotification)

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
          ${sidebarOpen ? 'translate-x-0' : '-translate-x-full lg:translate-x-0 max-lg:invisible'}
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
              <p className="text-xs text-gray-400 leading-tight truncate">AI Agent Framework</p>
            </div>
          )}
          {!collapsed && (
            <button
              onClick={() => setSidebarOpen(false)}
              className="lg:hidden ml-auto text-gray-400 hover:text-white"
              aria-label="Close menu"
              title="Close menu"
            >
              <X className="w-4 h-4" />
            </button>
          )}
        </div>

        {/* Navigation */}
        <nav aria-label="Main" className="flex-1 p-3 overflow-y-auto">
          <NavItem item={WORKSPACE_ITEM} collapsed={collapsed} sublabel={projectName}
            onNavigate={() => setSidebarOpen(false)} />
          {collapsed ? (
            <div className="my-3 border-t border-gray-800" role="separator" />
          ) : (
            <p className="mt-5 mb-1.5 px-3 text-xs font-medium uppercase tracking-wide text-gray-400">
              Global
            </p>
          )}
          <div className="space-y-1">
            {GLOBAL_ITEMS.map((item) => (
              <NavItem key={item.to} item={item} collapsed={collapsed} onNavigate={() => setSidebarOpen(false)} />
            ))}
          </div>
        </nav>

        {/* Footer */}
        <div className="p-3 space-y-1">
          {/* Collapse toggle (desktop only) */}
          <button
            onClick={toggleCollapsed}
            className={`hidden lg:flex w-full items-center gap-2 px-3 py-1.5 rounded-lg text-xs text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors whitespace-nowrap ${collapsed ? 'justify-center' : ''}`}
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
            onClick={async () => {
              await authApi.logout()
              window.dispatchEvent(new Event('auth:logout'))
            }}
            title={collapsed ? 'Sign out' : undefined}
            aria-label="Sign out"
            className={`w-full flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors whitespace-nowrap ${collapsed ? 'justify-center' : ''}`}
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
            aria-label="Open menu"
            title="Open menu"
            aria-expanded={sidebarOpen}
          >
            <Menu className="w-5 h-5" />
          </button>
          <Bot className="w-5 h-5 text-brand-500" />
          <span className="font-semibold text-white text-sm">Pantheon</span>
        </header>

        <main className="flex-1 overflow-hidden">
          <React.Suspense fallback={<div className="flex-1 flex items-center justify-center"><div className="w-6 h-6 border-2 border-brand-500 border-t-transparent rounded-full animate-spin" /></div>}>
            <Outlet />
          </React.Suspense>
        </main>
      </div>

      {/* Toast notifications */}
      <div className="fixed bottom-4 right-4 left-4 sm:left-auto z-50 flex flex-col items-end gap-2" role="status" aria-live="polite">
        {notifications.map((n) => (
          <div
            key={n.id}
            className={`
              flex items-center gap-3 px-4 py-3 rounded-lg shadow-lg text-sm max-w-sm break-words
              ${n.type === 'error' ? 'bg-red-900 text-red-100 border border-red-700' : ''}
              ${n.type === 'success' ? 'bg-green-900 text-green-100 border border-green-700' : ''}
              ${!n.type || n.type === 'info' ? 'bg-gray-800 text-gray-100 border border-gray-700' : ''}
            `}
          >
            <span className="flex-1">{n.message}</span>
            <button onClick={() => removeNotification(n.id)} className="opacity-70 hover:opacity-100"
              aria-label="Dismiss notification" title="Dismiss">
              <X className="w-3 h-3" />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}


function NavItem({ item, collapsed, sublabel, onNavigate }) {
  const { to, icon: Icon, label } = item
  const link = (
    <NavLink
      to={to}
      onClick={onNavigate}
      aria-label={collapsed ? (sublabel ? `${label}: ${sublabel}` : label) : undefined}
      className={({ isActive }) => `
        flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium
        transition-colors duration-150 whitespace-nowrap
        ${collapsed ? 'justify-center' : ''}
        ${isActive
          ? 'bg-brand-700 text-white'
          : 'text-gray-400 hover:text-white hover:bg-gray-800'
        }
      `}
    >
      {({ isActive }) => (
        <>
          <Icon className="w-4 h-4 flex-shrink-0" aria-hidden="true" />
          {!collapsed && (
            <span className="min-w-0 flex-1">
              <span className="block leading-tight">{label}</span>
              {sublabel && (
                <span className={`block truncate text-xs font-normal leading-tight mt-0.5 ${isActive ? 'text-brand-100' : 'text-gray-400'}`}
                  title={sublabel}>
                  {sublabel}
                </span>
              )}
            </span>
          )}
        </>
      )}
    </NavLink>
  )
  return collapsed ? (
    <Tooltip label={sublabel ? `${label}: ${sublabel}` : label} placement="right">{link}</Tooltip>
  ) : link
}
