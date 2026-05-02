import React, { useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useSearchParams } from 'react-router-dom'
import {
  MessageSquare, Brain, ListTodo, Github, FolderOpen, Settings,
  ChevronDown, Check,
} from 'lucide-react'
import Chat from './Chat'
import ChatActions from './ChatActions'
import MemoryPage from '../pages/MemoryPage'
import ArtifactsPage from '../pages/ArtifactsPage'
import RepoBindingPanel from './chat-tabs/RepoBindingPanel'
import ProjectTasksPanel from './chat-tabs/ProjectTasksPanel'
import ProjectSettingsPanel from './chat-tabs/ProjectSettingsPanel'
import { useStore } from '../store'

const TABS = [
  { id: 'chat',       label: 'Chat',       icon: MessageSquare },
  { id: 'memory',     label: 'Memory',     icon: Brain },
  { id: 'artifacts',  label: 'Artifacts',  icon: FolderOpen },
  { id: 'repository', label: 'Repository', icon: Github },
  { id: 'tasks',      label: 'Tasks',      icon: ListTodo },
  { id: 'settings',   label: 'Project Settings', icon: Settings },
]

export default function ChatTabs() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') || 'chat'
  const activeProject = useStore((s) => s.activeProject)
  const projectId = activeProject?.id || 'default'
  const sessionId = useStore((s) => s.sessionId)

  const setTab = (id) => {
    if (id === 'chat') params.delete('tab')
    else              params.set('tab', id)
    setParams(params, { replace: false })
  }

  // Per-tab right-side action area. Add cases here as other tabs need
  // contextual icons (e.g. Memory could host refresh/type-filter).
  const RightActions = () => {
    if (tab === 'chat') return <ChatActions />
    return null
  }

  return (
    <div className="h-full flex flex-col">
      {/* Unified top bar: project (far left) · tabs · spacer · actions (right) */}
      <div className="border-b border-gray-800 bg-gray-950 px-3 py-1.5 flex items-center gap-3 overflow-x-auto">
        {/* Project pill — clickable, opens project picker dropdown */}
        <ProjectPickerPill activeProject={activeProject} sessionId={sessionId} tab={tab} />

        <div className="flex items-center gap-0.5">
          {TABS.map((t) => {
            const Icon = t.icon
            const active = tab === t.id
            return (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                className={`flex items-center gap-1.5 px-2.5 py-1 text-xs rounded-md transition-colors whitespace-nowrap ${
                  active ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-900'
                }`}
              >
                <Icon className="w-3 h-3" />
                {t.label}
              </button>
            )
          })}
        </div>

        <span className="ml-auto" />
        <RightActions />
      </div>

      {/* Tab body */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {tab === 'chat'       && <Chat />}
        {tab === 'memory'     && <div className="h-full overflow-hidden"><MemoryPage embedded /></div>}
        {tab === 'artifacts'  && <ArtifactsPage lockedProjectId={projectId} />}
        {tab === 'repository' && <RepoBindingPanel projectId={projectId} />}
        {tab === 'tasks'      && <ProjectTasksPanel projectId={projectId} />}
        {tab === 'settings'   && <ProjectSettingsPanel projectId={projectId} />}
      </div>
    </div>
  )
}


function ProjectPickerPill({ activeProject, sessionId, tab }) {
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

  return (
    <>
      <button
        ref={triggerRef}
        onClick={() => setOpen(!open)}
        className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-gray-900 border border-gray-800 hover:bg-gray-800 hover:border-gray-700 transition-colors whitespace-nowrap"
        title="Switch project"
      >
        <Brain className="w-3 h-3 text-brand-400" />
        <span className="text-xs font-medium text-gray-200">
          {activeProject?.name || 'Default Project'}
        </span>
        {sessionId && tab === 'chat' && (
          <span className="text-[10px] text-gray-600 font-mono ml-1">
            · {sessionId.slice(0, 8)}
          </span>
        )}
        <ChevronDown className="w-3 h-3 text-gray-500 ml-0.5" />
      </button>
      {open && createPortal(
        <div
          ref={menuRef}
          className="rounded-md bg-gray-900 border border-gray-700 shadow-xl py-1 min-w-[12rem]"
          style={{ position: 'fixed', left: coords.left, top: coords.top, zIndex: 9999 }}
        >
          <div className="px-2 py-1 text-[10px] uppercase tracking-wide text-gray-500">
            Projects
          </div>
          {projects.length === 0 && (
            <div className="px-2 py-1 text-xs text-gray-500 italic">No projects</div>
          )}
          {projects.map((p) => (
            <button
              key={p.id}
              onClick={() => pick(p)}
              className="w-full text-left px-2 py-1.5 text-xs flex items-center gap-2 hover:bg-gray-800"
            >
              <Check className={`w-3 h-3 ${activeProject?.id === p.id ? 'text-brand-400' : 'text-transparent'}`} />
              <span className={activeProject?.id === p.id ? 'text-white font-medium' : 'text-gray-300'}>
                {p.name}
              </span>
            </button>
          ))}
          <div className="border-t border-gray-800 mt-1 pt-1">
            <a
              href="/projects"
              className="block px-2 py-1.5 text-xs text-gray-400 hover:text-gray-200 hover:bg-gray-800"
            >
              Manage projects →
            </a>
          </div>
        </div>,
        document.body
      )}
    </>
  )
}

