import React from 'react'
import { Outlet, NavLink, useNavigate } from 'react-router-dom'
import {
  MessageSquare, Brain, FolderOpen, User, Settings,
  ListTodo, Briefcase, Menu, X, Bot, ChevronDown, LogOut
} from 'lucide-react'
import { useStore } from '../store'

const NAV_ITEMS = [
  { to: '/chat', icon: MessageSquare, label: 'Chat' },
  { to: '/memory', icon: Brain, label: 'Memory' },
  { to: '/files', icon: FolderOpen, label: 'Files' },
  { to: '/personality', icon: User, label: 'Personality' },
  { to: '/tasks', icon: ListTodo, label: 'Tasks' },
  { to: '/projects', icon: Briefcase, label: 'Projects' },
  { to: '/settings', icon: Settings, label: 'Settings' },
]

export default function Layout() {
  const sidebarOpen = useStore((s) => s.sidebarOpen)
  const toggleSidebar = useStore((s) => s.toggleSidebar)
  const setSidebarOpen = useStore((s) => s.setSidebarOpen)
  const activeProject = useStore((s) => s.activeProject)
  const notifications = useStore((s) => s.notifications)
  const removeNotification = useStore((s) => s.removeNotification)

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

        {/* Active project indicator */}
        <div className="px-3 py-2 border-b border-gray-800">
          <div className="flex items-center gap-2 px-2 py-1.5 rounded-md bg-gray-800 text-xs">
            <div className="w-2 h-2 rounded-full bg-green-400 flex-shrink-0" />
            <span className="text-gray-300 truncate">{activeProject?.name || 'Default Project'}</span>
          </div>
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
          <p className="text-xs text-gray-700 text-center">2026-04-01-03</p>
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
