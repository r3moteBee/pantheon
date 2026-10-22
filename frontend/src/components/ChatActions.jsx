import React from 'react'
import { createPortal } from 'react-dom'
import { Link } from 'react-router-dom'
import { History, Save, Plus, SlidersHorizontal, ChevronDown } from 'lucide-react'
import { useStore } from '../store'
import { conversationsApi, projectSettingsApi } from '../api/client'
import Tooltip from './Tooltip'

// The four per-project chat settings. The names and help text are the
// same ones Project Settings uses (UI review #8): one name per setting.
export const CHAT_SETTINGS = {
  memory_recall: {
    label: 'Memory recall',
    help: 'Search past chats, artifacts and the graph for relevant context before each reply.',
    options: [['on', 'on'], ['off', 'off']],
  },
  context_focus: {
    label: 'Context focus',
    help: 'How tightly the agent stays on the current message vs. the wider conversation.',
    options: [['broad', 'broad'], ['balanced', 'balanced'], ['focused', 'focused']],
  },
  tone_weight: {
    label: 'Persona strength',
    help: "How strongly the persona's tone colors replies: minimal for matter-of-fact answers, strong for fully in character.",
    options: [['minimal', 'minimal'], ['balanced', 'balanced'], ['strong', 'strong']],
  },
  skill_discovery: {
    label: 'Skill discovery',
    help: 'Whether the agent loads matching skills by itself: off = only when you type /skill; suggest = ask first; auto = load silently.',
    options: [['off', 'off'], ['suggest', 'suggest'], ['auto', 'auto']],
  },
}

/**
 * Contextual action bar for the Chat tab, in the unified top bar (rendered
 * by ChatTabs): history, save, new chat, and one "Chat options" popover
 * that shows and changes the per-project chat settings. Settings live in
 * the global store so the values survive tab switches.
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
  // switch and persist every change, or the controls change nothing.
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

  const values = {
    memory_recall: memoryRecall ? 'on' : 'off',
    context_focus: contextFocus,
    tone_weight: personalityWeight,
    skill_discovery: skillDiscovery,
  }
  const setters = {
    memory_recall: (v) => setMemoryRecall(v === 'on'),
    context_focus: setContextFocus,
    tone_weight: setPersonalityWeight,
    skill_discovery: setSkillDiscovery,
  }

  const persist = async (knob, value) => {
    const previous = values[knob]
    if (value === previous) return
    setters[knob](value)
    try {
      await projectSettingsApi.update(projectId, { [knob]: knob === 'memory_recall' ? value === 'on' : value })
    } catch (e) {
      setters[knob](previous)
      addNotification({ type: 'error', message: `Couldn't save ${CHAT_SETTINGS[knob].label.toLowerCase()}: ${e.message}` })
    }
  }

  const onSaveChat = async () => {
    if (!sessionId) return
    try {
      const res = await conversationsApi.saveAsArtifact(sessionId, projectId)
      addNotification({ type: 'success', message: `Saved chat to artifact: ${res.data.path}` })
    } catch (e) {
      addNotification({ type: 'error', message: 'Save failed: ' + (e.message) })
    }
  }

  return (
    <div className="flex items-center gap-0.5">
      <IconButton
        icon={History}
        label="Chat history"
        onClick={() => setHistoryOpen(true)}
      />
      <IconButton
        icon={Save}
        label={sessionId ? 'Save chat as artifact' : 'Save chat as artifact (send a message first)'}
        onClick={onSaveChat}
        disabled={!sessionId}
      />
      <IconButton
        icon={Plus}
        label="New conversation"
        onClick={() => { setSessionId(null); clearMessages() }}
      />
      <Divider />
      <ChatOptions values={values} onChange={persist} />
    </div>
  )
}

function ChatOptions({ values, onChange }) {
  const [open, setOpen] = React.useState(false)
  const [coords, setCoords] = React.useState({ left: 0, top: 0 })
  const btnRef = React.useRef(null)
  const panelRef = React.useRef(null)

  const summary = Object.entries(CHAT_SETTINGS).map(([k, s]) => `${s.label} ${values[k]}`).join(', ')

  const place = React.useCallback(() => {
    const r = btnRef.current?.getBoundingClientRect()
    if (!r) return
    const width = Math.min(320, window.innerWidth - 16)
    setCoords({ left: Math.max(8, Math.min(r.right - width, window.innerWidth - width - 8)), top: r.bottom + 6, width })
  }, [])

  React.useLayoutEffect(() => { if (open) place() }, [open, place])

  React.useEffect(() => {
    if (!open) return
    const onDown = (e) => {
      if (panelRef.current?.contains(e.target) || btnRef.current?.contains(e.target)) return
      setOpen(false)
    }
    const onKey = (e) => { if (e.key === 'Escape') { setOpen(false); btnRef.current?.focus() } }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    window.addEventListener('resize', place)
    // Move focus into the panel so keyboard users land on the controls.
    setTimeout(() => panelRef.current?.querySelector('[aria-checked="true"]')?.focus(), 0)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
      window.removeEventListener('resize', place)
    }
  }, [open, place])

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label={`Chat options: ${summary}`}
        title={`Chat options: ${summary}`}
        className={`flex items-center gap-1 rounded-md px-1.5 py-1 text-xs whitespace-nowrap transition-colors ${
          open ? 'bg-gray-800 text-white' : 'text-gray-300 hover:text-white hover:bg-gray-800'
        }`}
      >
        <SlidersHorizontal className="w-4 h-4" aria-hidden="true" />
        <span className="hidden md:inline">Options</span>
        {/* The settings that differ from the defaults, at a glance. */}
        <span className="hidden xl:inline text-gray-400">
          {values.memory_recall === 'off' && '· recall off '}
          {values.context_focus !== 'balanced' && `· ${values.context_focus} `}
          {values.tone_weight !== 'balanced' && `· persona ${values.tone_weight} `}
          {values.skill_discovery !== 'off' && `· skills ${values.skill_discovery}`}
        </span>
        <ChevronDown className="w-3 h-3" aria-hidden="true" />
      </button>
      {open && createPortal(
        <div
          ref={panelRef}
          role="dialog"
          aria-label="Chat options"
          className="rounded-lg bg-gray-900 border border-gray-700 shadow-xl p-3 space-y-3"
          style={{ position: 'fixed', left: coords.left, top: coords.top, width: coords.width, zIndex: 9999 }}
        >
          <div className="text-sm font-semibold text-gray-100">Chat options</div>
          {Object.entries(CHAT_SETTINGS).map(([key, s]) => (
            <div key={key}>
              <div id={`chatopt-${key}`} className="text-xs font-medium text-gray-200">{s.label}</div>
              <p className="text-xs text-gray-400 mb-1.5">{s.help}</p>
              <div role="radiogroup" aria-labelledby={`chatopt-${key}`} className="flex rounded-md border border-gray-700 overflow-hidden">
                {s.options.map(([value, label]) => {
                  const on = values[key] === value
                  return (
                    <button
                      key={value}
                      type="button"
                      role="radio"
                      aria-checked={on}
                      onClick={() => onChange(key, value)}
                      className={`flex-1 px-2 py-1 text-xs whitespace-nowrap border-r border-gray-700 last:border-r-0 transition-colors ${
                        on ? 'bg-brand-700 text-white font-medium' : 'bg-gray-850 text-gray-300 hover:bg-gray-800 hover:text-white'
                      }`}
                    >
                      {label}
                    </button>
                  )
                })}
              </div>
            </div>
          ))}
          <p className="text-xs text-gray-400 border-t border-gray-800 pt-2">
            Saved for this project right away.{' '}
            <Link to="/chat?tab=settings" onClick={() => setOpen(false)} className="text-brand-300 hover:text-brand-200 underline">
              Project Settings
            </Link>{' '}
            can set them back to the global value.
          </p>
        </div>,
        document.body,
      )}
    </>
  )
}

function IconButton({ icon: Icon, label, onClick, disabled }) {
  return (
    <Tooltip label={label}>
      <button
        type="button"
        onClick={onClick}
        disabled={disabled}
        aria-label={label}
        className={`p-1.5 rounded-md transition-colors text-gray-300 ${
          disabled ? 'opacity-40 cursor-not-allowed' : 'hover:text-white hover:bg-gray-800'
        }`}
      >
        <Icon className="w-4 h-4" aria-hidden="true" />
      </button>
    </Tooltip>
  )
}

function Divider() {
  return <span className="mx-1 h-4 w-px bg-gray-800" aria-hidden="true" />
}
