import React from 'react'
import { Navigate } from 'react-router-dom'
import { RefreshCw, MessageCircle, Shield, Cpu, Key, Clock, Server, User as UserIcon, UserRound } from 'lucide-react'
import MessagingSettings from './MessagingSettings'
import LlmSettings from './settings/LlmSettings'
import RagSettings from './settings/RagSettings'
import ChannelsHelp from './settings/ChannelsHelp'
import PersonalitySettings from './settings/PersonalitySettings'
import AboutYouSettings from './settings/AboutYouSettings'
import TasksSettings from './settings/TasksSettings'
import SecuritySettings from './settings/SecuritySettings'
import SecretsSettings from './settings/SecretsSettings'
import SystemUpdate from './settings/SystemUpdate'
import TabStrip from './TabStrip'
import { useUrlParam } from '../hooks/useUrlState'
import usePageTitle from '../hooks/usePageTitle'

const TABS = [
  { id: 'llms', label: 'LLMs', icon: Cpu },
  { id: 'rag', label: 'RAG Settings', icon: Server },
  { id: 'channels', label: 'Channels', icon: MessageCircle },
  { id: 'about', label: 'About you', icon: UserRound },
  { id: 'personality', label: 'Personality', icon: UserIcon },
  { id: 'tasks', label: 'Tasks', icon: Clock },
  // Sandbox status + security audit log. Not to be confused with the skill
  // scans under Skills -> Security.
  { id: 'security', label: 'Sandbox & Audit', icon: Shield },
  { id: 'secrets', label: 'Secrets', icon: Key },
  { id: 'update', label: 'System Update', icon: RefreshCw },
]

// Every section shares one content column (UI review #36: was 672/768 px).
function Section({ children, spaced = true }) {
  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className={`max-w-[860px] mx-auto p-4 sm:p-6 ${spaced ? 'space-y-8' : ''}`}>
        {children}
      </div>
    </div>
  )
}

export default function Settings() {
  // ?section=<id> keeps the open section across reloads (UI review #15);
  // the older ?tab=<id> links (e.g. /personas -> Personality) still work.
  // "skills" (registry hubs) moved to Skills -> Sources.
  const [rawTab] = useUrlParam('section', 'llms', { aliases: ['tab'] })
  const [tab, setTab] = useUrlParam('section', 'llms', {
    aliases: ['tab'], allowed: TABS.map((t) => t.id),
  })
  const current = TABS.find((t) => t.id === tab)
  usePageTitle(current?.label, 'Settings')

  if (rawTab === 'skills') return <Navigate to="/skills?section=sources" replace />

  return (
    <div className="h-full flex flex-col">
      <TabStrip
        tabs={TABS}
        value={tab}
        onChange={setTab}
        label="Settings sections"
        className="border-b border-gray-800 px-2 sm:px-6 pt-4"
      />
      <div className="flex-1 overflow-hidden">
        {tab === 'llms' && <Section><LlmSettings /></Section>}
        {tab === 'rag' && <Section><RagSettings /></Section>}
        {tab === 'channels' && <Section><ChannelsHelp /><MessagingSettings /></Section>}
        {tab === 'tasks' && <Section><TasksSettings /></Section>}
        {tab === 'about' && <Section spaced={false}><AboutYouSettings /></Section>}
        {tab === 'personality' && <Section spaced={false}><PersonalitySettings /></Section>}
        {tab === 'security' && <Section><SecuritySettings /></Section>}
        {tab === 'secrets' && <Section><SecretsSettings /></Section>}
        {tab === 'update' && <Section><SystemUpdate /></Section>}
      </div>
    </div>
  )
}
