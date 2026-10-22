import React, { Suspense, lazy, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Link, useSearchParams } from 'react-router-dom'
import {
  MessageSquare, Brain, ListTodo, Github, FolderOpen, Settings,
  ChevronDown, Check,
} from 'lucide-react'
import Chat from './Chat'
import ChatActions from './ChatActions'
// Lazy so d3 / CodeMirror stay out of the /chat landing chunk.
const MemoryPage = lazy(() => import('../pages/MemoryPage'))
const ArtifactsPage = lazy(() => import('../pages/ArtifactsPage'))
import RepoBindingPanel from './chat-tabs/RepoBindingPanel'
import ProjectTasksPanel from './chat-tabs/ProjectTasksPanel'
import ProjectSettingsPanel from './chat-tabs/ProjectSettingsPanel'
import { useStore } from '../store'
import usePageTitle from '../hooks/usePageTitle'
import useConversationPersistence from '../hooks/useConversationPersistence'

export const TABS = [
  { id: 'chat',       label: 'Chat',       icon: MessageSquare },
  { id: 'memory',     label: 'Memory',     icon: Brain },
  { id: 'artifacts',  label: 'Artifacts',  icon: FolderOpen },
  { id: 'repository', label: 'Repository', icon: Github },
  { id: 'tasks',      label: 'Tasks',      icon: ListTodo },
  { id: 'settings',   label: 'Project Settings', icon: Settings },
]
const TAB_IDS = TABS.map((t) => t.id)

// Query parameters that belong to one tab only; dropped when leaving it.
const TAB_PARAMS = ['id', 'scope']

// Short conversation title for document.title: the first user message.
const conversationTitle = (messages) => {
  const first = (messages || []).find((m) => m.role === 'user' && typeof m.content === 'string' && m.content.trim())
  if (!first) return ''
  const line = first.content.trim().split('\n')[0]
  return line.length > 40 ? `${line.slice(0, 39)}…` : line
}

export default function ChatTabs() {
  const [params, setParams] = useSearchParams()
  const rawTab = params.get('tab') || 'chat'
  const tab = TAB_IDS.includes(rawTab) ? rawTab : 'chat'
  const activeProject = useStore((s) => s.activeProject)
  const projectId = activeProject?.id || 'default'
  const chatTitle = useStore((s) => conversationTitle(s.messages))
  const tabsRef = useRef(null)

  useConversationPersistence()

  const tabLabel = TABS.find((t) => t.id === tab)?.label
  usePageTitle(tab === 'chat' ? chatTitle : null, activeProject?.name || (projectId === 'default' ? 'Default Project' : ''), tabLabel)

  const setTab = (id) => {
    setParams((prev) => {
      const p = new URLSearchParams(prev)
      if (id !== tab) TAB_PARAMS.forEach((k) => p.delete(k))
      if (id === 'chat') p.delete('tab')
      else p.set('tab', id)
      return p
    })
  }

  // Keep the active tab visible when the strip scrolls (narrow screens).
  useEffect(() => {
    const el = tabsRef.current?.querySelector('[aria-current="page"]')
    if (el && el.scrollIntoView) el.scrollIntoView({ block: 'nearest', inline: 'nearest' })
  }, [tab])

  return (
    <div className="h-full flex flex-col">
      {/* Unified top bar: project switcher (fixed width) · tabs · chat actions.
          Tabs never move: the switcher has a fixed width and the right-hand
          area only grows into the spacer. On narrow screens the tabs get
          their own scrollable row. */}
      <div data-shell className="border-b border-gray-800 bg-gray-950 px-3 py-1.5 flex flex-wrap sm:flex-nowrap items-center gap-x-3 gap-y-1.5">
        <ProjectPickerPill activeProject={activeProject} />

        <nav
          ref={tabsRef}
          aria-label="Project sections"
          className="order-last sm:order-none basis-full sm:basis-auto flex-1 min-w-0 flex items-center gap-0.5 overflow-x-auto scrollbar-thin"
        >
          {TABS.map((t) => {
            const Icon = t.icon
            const active = tab === t.id
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                aria-current={active ? 'page' : undefined}
                className={`flex flex-shrink-0 items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors whitespace-nowrap ${
                  active ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-900'
                }`}
              >
                <Icon className="w-3 h-3" aria-hidden="true" />
                {t.label}
              </button>
            )
          })}
        </nav>

        {/* Right-hand area: chat-only actions. Same height as the switcher so
            the bar keeps its height on every tab. */}
        <div className="ml-auto flex h-7 flex-shrink-0 items-center">
          {tab === 'chat' && <ChatActions />}
        </div>
      </div>

      {/* Tab body */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {tab === 'chat'       && <Chat />}
        <Suspense fallback={<div className="p-4 text-sm text-gray-400">Loading…</div>}>
          {tab === 'memory'     && <div className="h-full overflow-hidden"><MemoryPage embedded /></div>}
          {/* Not locked to the project: its scope toggle reaches every
              project's artifacts (the old global Artifacts page), and
              ?id=<artifact> deep links work here. */}
          {tab === 'artifacts'  && <ArtifactsPage />}
        </Suspense>
        {tab === 'repository' && <RepoBindingPanel projectId={projectId} />}
        {tab === 'tasks'      && <ProjectTasksPanel projectId={projectId} />}
        {tab === 'settings'   && <ProjectSettingsPanel projectId={projectId} />}
      </div>
    </div>
  )
}


function ProjectPickerPill({ activeProject }) {
  const triggerRef = useRef(null)
  const menuRef = useRef(null)
  const [open, setOpen] = useState(false)
  const [coords, setCoords] = useState({ left: 0, top: 0 })
  const projects = useStore((s) => s.projects) || []
  const setActiveProject = useStore((s) => s.setActiveProject)

  // Position the menu beneath the trigger, anchored to its bounding rect.
  useLayoutEffect(() => {
    if (!open || !triggerRef.current) return
    const r = triggerRef.current.getBoundingClientRect()
    setCoords({ left: r.left, top: r.bottom + 4 })
  }, [open])

  // Close on outside click. Trigger and portal'd menu both count as inside.
  React.useEffect(() => {
    if (!open) return
    const onDoc = (e) => {
      const t = e.target
      if (triggerRef.current && triggerRef.current.contains(t)) return
      if (menuRef.current && menuRef.current.contains(t)) return
      setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  const pick = (p) => { setActiveProject(p); setOpen(false) }
  const name = activeProject?.name || (activeProject?.id === 'default' ? 'Default Project' : 'Loading…')

  return (
    <>
      <button
        ref={triggerRef}
        onClick={() => setOpen(!open)}
        className="flex h-7 w-36 sm:w-52 flex-shrink-0 items-center gap-1.5 px-2 rounded-md bg-gray-900 border border-gray-800 hover:bg-gray-800 hover:border-gray-700 transition-colors whitespace-nowrap"
        title={`Project: ${name} (click to switch)`}
        aria-label={`Project: ${name}. Switch project`}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <Brain className="w-3 h-3 flex-shrink-0 text-brand-400" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate text-left text-xs font-medium text-gray-200">
          {name}
        </span>
        <ChevronDown className="w-3 h-3 flex-shrink-0 text-gray-400" aria-hidden="true" />
      </button>
      {open && createPortal(
        <div
          ref={menuRef}
          role="menu"
          className="rounded-md bg-gray-900 border border-gray-700 shadow-xl py-1 min-w-[13rem] max-w-[min(24rem,calc(100vw-1.5rem))] max-h-[70vh] overflow-y-auto scrollbar-thin"
          style={{ position: 'fixed', left: coords.left, top: coords.top, zIndex: 9999 }}
        >
          <div className="px-2 py-1 text-xs uppercase tracking-wide text-gray-400">
            Projects
          </div>
          {projects.length === 0 && (
            <div className="px-2 py-1 text-xs text-gray-400 italic">No projects</div>
          )}
          {projects.map((p) => (
            <button
              key={p.id}
              role="menuitemradio"
              aria-checked={activeProject?.id === p.id}
              onClick={() => pick(p)}
              title={p.name}
              className="w-full text-left px-2 py-1.5 text-xs flex items-center gap-2 hover:bg-gray-800"
            >
              <Check className={`w-3 h-3 flex-shrink-0 ${activeProject?.id === p.id ? 'text-brand-400' : 'text-transparent'}`} aria-hidden="true" />
              <span className={`truncate ${activeProject?.id === p.id ? 'text-white font-medium' : 'text-gray-300'}`}>
                {p.name}
              </span>
            </button>
          ))}
          <div className="border-t border-gray-800 mt-1 pt-1">
            <Link
              to="/projects"
              onClick={() => setOpen(false)}
              className="block px-2 py-1.5 text-xs text-gray-400 hover:text-gray-200 hover:bg-gray-800"
            >
              Manage projects →
            </Link>
          </div>
        </div>,
        document.body
      )}
    </>
  )
}


