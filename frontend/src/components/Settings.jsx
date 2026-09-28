import React, { useState } from 'react'
import { RefreshCw, MessageCircle, Shield, Cpu, Key, Library, Clock, Server, User as UserIcon } from 'lucide-react'
import MessagingSettings from './MessagingSettings'
import LlmSettings from './settings/LlmSettings'
import RagSettings from './settings/RagSettings'
import ChannelsHelp from './settings/ChannelsHelp'
import PersonalitySettings from './settings/PersonalitySettings'
import SkillHubsSettings from './settings/SkillHubsSettings'
import TasksSettings from './settings/TasksSettings'
import SecuritySettings from './settings/SecuritySettings'
import SecretsSettings from './settings/SecretsSettings'
import SystemUpdate from './settings/SystemUpdate'

export default function Settings() {
  const [tab, setTab] = useState('llms')

  const tabs = [
    { id: 'llms', label: 'LLMs', icon: Cpu },
    { id: 'rag', label: 'RAG Settings', icon: Server },
    { id: 'channels', label: 'Channels', icon: MessageCircle },
    { id: 'personality', label: 'Personality', icon: UserIcon },
    { id: 'skills', label: 'Skills', icon: Library },
    { id: 'tasks', label: 'Tasks', icon: Clock },
    { id: 'security', label: 'Security', icon: Shield },
    { id: 'secrets', label: 'Secrets', icon: Key },
    { id: 'update', label: 'System Update', icon: RefreshCw },
  ]

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center border-b border-gray-800 px-6 pt-4">
        {tabs.map((t) => {
          const Icon = t.icon
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-4 py-2 text-xs font-medium border-b-2 transition-colors ${
                tab === t.id
                  ? 'border-brand-400 text-brand-300'
                  : 'border-transparent text-gray-500 hover:text-gray-300'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          )
        })}
      </div>
      <div className="flex-1 overflow-hidden">
        {tab === 'llms' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-2xl mx-auto p-6 space-y-8">
              <LlmSettings />
            </div>
          </div>
        )}
        {tab === 'rag' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-2xl mx-auto p-6 space-y-8">
              <RagSettings />
            </div>
          </div>
        )}
        {tab === 'channels' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-2xl mx-auto p-6 space-y-8">
              <ChannelsHelp />
              <MessagingSettings />
            </div>
          </div>
        )}
        {tab === 'skills' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-2xl mx-auto p-6 space-y-8">
              <SkillHubsSettings />
            </div>
          </div>
        )}
        {tab === 'tasks' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-3xl mx-auto p-6 space-y-8">
              <TasksSettings />
            </div>
          </div>
        )}
        {tab === 'personality' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-3xl mx-auto p-6">
              <PersonalitySettings />
            </div>
          </div>
        )}
        {tab === 'security' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-2xl mx-auto p-6 space-y-8">
              <SecuritySettings />
            </div>
          </div>
        )}
        {tab === 'secrets' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-2xl mx-auto p-6 space-y-8">
              <SecretsSettings />
            </div>
          </div>
        )}
        {tab === 'update' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-2xl mx-auto p-6 space-y-8">
              <SystemUpdate />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
