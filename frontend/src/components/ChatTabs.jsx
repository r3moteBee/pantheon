import React, { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { MessageSquare, Brain, ListTodo, Github, User as UserIcon, Plug } from 'lucide-react'
import Chat from './Chat'
import MemoryPage from '../pages/MemoryPage'
import RepoBindingPanel from './chat-tabs/RepoBindingPanel'
import ProjectTasksPanel from './chat-tabs/ProjectTasksPanel'
import ProjectPersonalityPanel from './chat-tabs/ProjectPersonalityPanel'
import ProjectMcpPanel from './chat-tabs/ProjectMcpPanel'
import { useStore } from '../store'

const TABS = [
  { id: 'chat',        label: 'Chat',        icon: MessageSquare },
  { id: 'memory',      label: 'Memory',      icon: Brain },
  { id: 'tasks',       label: 'Tasks',       icon: ListTodo },
  { id: 'repository',  label: 'Repository',  icon: Github },
  { id: 'personality', label: 'Personality', icon: UserIcon },
  { id: 'mcp',         label: 'MCP',         icon: Plug },
]

export default function ChatTabs() {
  const [params, setParams] = useSearchParams()
  const tab = params.get('tab') || 'chat'
  const activeProject = useStore((s) => s.activeProject)
  const projectId = activeProject?.id || 'default'

  const setTab = (id) => {
    if (id === 'chat') {
      params.delete('tab')
    } else {
      params.set('tab', id)
    }
    setParams(params, { replace: false })
  }

  return (
    <div className="h-full flex flex-col">
      {/* Tab strip */}
      <div className="border-b border-gray-800 bg-gray-950 px-3 py-1 flex items-center gap-0.5 overflow-x-auto">
        {TABS.map((t) => {
          const Icon = t.icon
          const active = tab === t.id
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-md transition-colors whitespace-nowrap ${
                active
                  ? 'bg-gray-800 text-white'
                  : 'text-gray-400 hover:text-gray-200 hover:bg-gray-900'
              }`}
            >
              <Icon className="w-3 h-3" />
              {t.label}
            </button>
          )
        })}
      </div>

      {/* Tab body */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {tab === 'chat'        && <Chat />}
        {tab === 'memory'      && <div className="h-full overflow-hidden"><MemoryPage embedded /></div>}
        {tab === 'tasks'       && <ProjectTasksPanel projectId={projectId} />}
        {tab === 'repository'  && <RepoBindingPanel projectId={projectId} />}
        {tab === 'personality' && <ProjectPersonalityPanel projectId={projectId} />}
        {tab === 'mcp'         && <ProjectMcpPanel projectId={projectId} />}
      </div>
    </div>
  )
}
