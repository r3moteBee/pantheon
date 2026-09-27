import React from 'react'
import {
  History, Save, Plus, Sparkles, Target, Wand2, UserCircle,
} from 'lucide-react'
import { useStore } from '../store'
import { conversationsApi, projectSettingsApi } from '../api/client'
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

  const projectId = activeProject?.id || 'default'

  // These are per-project chat settings (with global fallback) that the
  // backend reads on every turn — load the effective values on project
  // switch and persist every toggle, or the buttons change nothing.
  React.useEffect(() => {
    let cancelled = false
    projectSettingsApi.get(projectId).then((res) => {
      if (cancelled) return
      const eff = res?.data?.effective || {}
      setMemoryRecall(eff.memory_recall !== false)
      setContextFocus(eff.context_focus || 'balanced')
      setPersonalityWeight(eff.tone_weight || 'balanced')
      setSkillDiscovery(eff.skill_discovery || 'off')
    }).catch(() => { /* keep current values */ })
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])

  const persist = async (knob, value, setter, previous) => {
    setter(value)
    try {
      await projectSettingsApi.update(projectId, { [knob]: value })
    } catch (e) {
      setter(previous)
      addNotification({ type: 'error', message: `Couldn't save ${knob.replace('_', ' ')}: ${e.message}` })
    }
  }

  const next = (current, options) => options[(options.indexOf(current) + 1) % options.length]

  const cycleSkillDiscovery = () =>
    persist('skill_discovery', next(skillDiscovery, ['off', 'suggest', 'auto']), setSkillDiscovery, skillDiscovery)

  const onSaveChat = async () => {
    if (!sessionId) return
    try {
      const res = await conversationsApi.saveAsArtifact(sessionId, projectId)
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
          ? 'Memory recall: ON. The agent searches past chats and indexed files for relevant context before answering. Click to toggle for this project.'
          : 'Memory recall: OFF. The agent searches past chats and indexed files for relevant context before answering. Click to toggle for this project.'}
        active={memoryRecall}
        activeColor="text-brand-400"
        onClick={() => persist('memory_recall', !memoryRecall, setMemoryRecall, memoryRecall)}
      />
      <IconButton
        icon={Target}
        label={`Thread focus: ${contextFocus}. How tightly the agent stays on the current message vs. the wider conversation. Cycle: broad → balanced → focused.`}
        toneClass={focusTone}
        onClick={() => persist('context_focus', next(contextFocus, ['broad', 'balanced', 'focused']), setContextFocus, contextFocus)}
      />
      <IconButton
        icon={Wand2}
        label={`Auto-skill: ${skillDiscovery}. Whether the agent auto-loads matching skills (off = manual /skill only; suggest = ask first; auto = load silently). Cycle: off → suggest → auto.`}
        toneClass={skillTone}
        onClick={cycleSkillDiscovery}
      />
      <IconButton
        icon={UserCircle}
        label={`Persona presence: ${personalityWeight}. How strongly the persona's tone colors responses. Cycle: minimal → balanced → strong.`}
        toneClass={personaTone}
        onClick={() => persist('tone_weight', next(personalityWeight, ['minimal', 'balanced', 'strong']), setPersonalityWeight, personalityWeight)}
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
