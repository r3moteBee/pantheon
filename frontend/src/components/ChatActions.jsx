import React from 'react'
import {
  History, Save, Plus, Sparkles, Target, Wand2, UserCircle,
} from 'lucide-react'
import { useStore } from '../store'
import { conversationsApi } from '../api/client'
import Tooltip from './Tooltip'

/**
 * Icon-only contextual action bar for the Chat tab. Lives in the unified
 * top bar (rendered by ChatTabs). Uses the global store for settings so
 * the active state survives tab switches.
 */
export default function ChatActions() {
  const sessionId = useStore((s) => s.sessionId)
  const setSessionId = useStore((s) => s.setSessionId)
  const clearMessages = useStore((s) => s.clearMessages)
  const setHistoryOpen = useStore((s) => s.setHistoryOpen)
  const activeProject = useStore((s) => s.activeProject)
  const addNotification = useStore((s) => s.addNotification)

  const memoryRecall = useStore((s) => s.memoryRecall)
  const setMemoryRecall = useStore((s) => s.setMemoryRecall)
  const contextFocus = useStore((s) => s.contextFocus)
  const setContextFocus = useStore((s) => s.setContextFocus)
  const skillDiscovery = useStore((s) => s.skillDiscovery)
  const setSkillDiscovery = useStore((s) => s.setSkillDiscovery)
  const personalityWeight = useStore((s) => s.personalityWeight)
  const setPersonalityWeight = useStore((s) => s.setPersonalityWeight)

  const cycle = (current, options, setter) => {
    const i = options.indexOf(current)
    setter(options[(i + 1) % options.length])
  }

  const onSaveChat = async () => {
    if (!sessionId) return
    try {
      const res = await conversationsApi.saveAsArtifact(sessionId, activeProject?.id || 'default')
      addNotification({ type: 'success', message: `Saved chat to artifact: ${res.data.path}` })
    } catch (e) {
      addNotification({ type: 'error', message: 'Save failed: ' + (e?.response?.data?.detail || e.message) })
    }
  }

  const focusTone =
    contextFocus === 'focused' ? 'text-amber-400'
    : contextFocus === 'broad' ? 'text-gray-500'
    : 'text-gray-400'

  const skillTone =
    skillDiscovery === 'auto' ? 'text-emerald-400'
    : skillDiscovery === 'suggest' ? 'text-amber-400'
    : 'text-gray-500'

  const personaTone =
    personalityWeight === 'strong' ? 'text-purple-400'
    : personalityWeight === 'minimal' ? 'text-gray-500'
    : 'text-gray-400'

  return (
    <div className="flex items-center gap-0.5">
      <IconButton
        icon={History}
        label="Chat history"
        onClick={() => setHistoryOpen(true)}
      />
      <IconButton
        icon={Save}
        label="Save chat as artifact"
        onClick={onSaveChat}
        disabled={!sessionId}
      />
      <IconButton
        icon={Plus}
        label="New conversation"
        onClick={() => { setSessionId(null); clearMessages() }}
      />
      <Divider />
      <IconButton
        icon={Sparkles}
        label={memoryRecall
          ? 'Memory recall: ON — click to disable'
          : 'Memory recall: OFF — click to enable'}
        active={memoryRecall}
        activeColor="text-brand-400"
        onClick={() => setMemoryRecall(!memoryRecall)}
      />
      <IconButton
        icon={Target}
        label={`Thread focus: ${contextFocus}. Cycle: broad → balanced → focused`}
        toneClass={focusTone}
        onClick={() => cycle(contextFocus, ['broad', 'balanced', 'focused'], setContextFocus)}
      />
      <IconButton
        icon={Wand2}
        label={`Auto-skill: ${skillDiscovery}. Cycle: off → suggest → auto`}
        toneClass={skillTone}
        onClick={() => cycle(skillDiscovery, ['off', 'suggest', 'auto'], setSkillDiscovery)}
      />
      <IconButton
        icon={UserCircle}
        label={`Persona presence: ${personalityWeight}. Cycle: minimal → balanced → strong`}
        toneClass={personaTone}
        onClick={() => cycle(personalityWeight, ['minimal', 'balanced', 'strong'], setPersonalityWeight)}
      />
    </div>
  )
}


function IconButton({ icon: Icon, label, onClick, disabled, active, activeColor = 'text-brand-400', toneClass }) {
  const color = toneClass || (active ? activeColor : 'text-gray-400 hover:text-gray-200')
  return (
    <Tooltip label={label}>
      <button
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        className={`p-1.5 rounded-md transition-colors ${color} ${
          disabled ? 'opacity-40 cursor-not-allowed' : 'hover:bg-gray-800'
        } ${active ? 'bg-brand-950' : ''}`}
      >
        <Icon className="w-4 h-4" />
      </button>
    </Tooltip>
  )
}

function Divider() {
  return <span className="mx-1 h-4 w-px bg-gray-800" />
}
