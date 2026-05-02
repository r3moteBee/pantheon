import React from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  MessageSquare, Brain, ListTodo, Github, FolderOpen, Settings,
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
        {/* Project name pill — anchored far left, always visible */}
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-gray-900 border border-gray-800 whitespace-nowrap">
          <Brain className="w-3 h-3 text-brand-400" />
          <span className="text-xs font-medium text-gray-200">
            {activeProject?.name || 'Default Project'}
          </span>
          {sessionId && tab === 'chat' && (
            <span className="text-[10px] text-gray-600 font-mono ml-1">
              · {sessionId.slice(0, 8)}
            </span>
          )}
        </div>

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
