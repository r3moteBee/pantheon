import React, { useState, useRef, useEffect } from 'react'
import { Send, Square, Paperclip } from 'lucide-react'
import { llmApi } from '../../api/client'
import SkillPicker from '../SkillPicker'
import { AttachmentPill } from './Attachments'
import { CHAT_COLUMN } from '../layout/columns'

function useMediaQuery(q) {
  const [matches, setMatches] = useState(() => typeof window !== 'undefined' && !!window.matchMedia?.(q).matches)
  useEffect(() => {
    const m = window.matchMedia?.(q)
    if (!m) return
    const on = () => setMatches(m.matches)
    m.addEventListener?.('change', on)
    return () => m.removeEventListener?.('change', on)
  }, [q])
  return matches
}
// Below the sm breakpoint the long placeholder hint gets cut off mid-word.
const useNarrow = () => useMediaQuery('(max-width: 639px)')
// Phone width: the mode picker shows abbreviated labels so "Quick chat"
// isn't clipped to "Quick ch" (UI review N9); the full label stays in the
// control's title / aria-label.
const usePhone = () => useMediaQuery('(max-width: 479px)')

const shortLabel = (label) => {
  const first = String(label || '').split(/[\s/–-]+/)[0] || label
  return first.length > 7 ? first.slice(0, 6) + '…' : first
}

// Attachment tray + input row: model picker, attach button, skill picker,
// textarea and send/stop.
export default function ChatComposer({
  input, setInput, onSend, onStop,
  isStreaming, uploading,
  attachments, onAddFiles, onRemoveAttachment, onPaste,
  modelClass, setModelClass, projectId,
}) {
  const [showSkillPicker, setShowSkillPicker] = useState(false)
  const [skillQuery, setSkillQuery] = useState('')
  // Chat router: the classes that can take a turn besides agent.
  const [routeOptions, setRouteOptions] = useState([])
  const [routesLoaded, setRoutesLoaded] = useState(false)
  const textareaRef = useRef(null)
  const fileInputRef = useRef(null)
  const narrow = useNarrow()
  const phone = usePhone()

  useEffect(() => {
    llmApi.getRouter()
      .then((v) => setRouteOptions((v.classes || []).filter((c) => c.usable && c.name !== 'agent')))
      .catch(() => setRouteOptions([]))
      .finally(() => setRoutesLoaded(true))
  }, [])

  // A mode restored from an earlier visit (per project, see useChatSocket)
  // may no longer be offered by the router: fall back to Auto rather than
  // sending a class the backend rejects.
  useEffect(() => {
    if (!routesLoaded || modelClass === 'auto' || modelClass === 'agent') return
    if (!routeOptions.some((c) => c.name === modelClass)) setModelClass('auto')
  }, [routesLoaded, routeOptions, modelClass, setModelClass])

  const handleFileSelect = (e) => {
    const files = Array.from(e.target.files || [])
    if (files.length === 0) return
    onAddFiles(files)
    // Reset input so the same file can be selected again
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onSend()
    }
  }

  return (
    <>
      {/* Attachment preview bar */}
      {attachments.length > 0 && (
        <div className="py-2 bg-gray-850 border-t border-gray-800">
          <div className={`flex flex-wrap gap-2 ${CHAT_COLUMN}`}>
            {attachments.map((file, i) => (
              <AttachmentPill key={i} file={file} onRemove={() => onRemoveAttachment(i)} />
            ))}
          </div>
        </div>
      )}

      {/* Input area */}
      <div className="py-4 bg-gray-900 border-t border-gray-800">
        <div data-chat-column="composer" className={`flex gap-2 items-end ${CHAT_COLUMN}`}>
          {/* Model picker — only when the router has somewhere else to send turns */}
          {routeOptions.length > 0 && (() => {
            const modes = [['auto', 'Auto'], ['agent', 'Agent'], ...routeOptions.map((c) => [c.name, c.label || c.name])]
            const current = (modes.find(([v]) => v === modelClass) || modes[0])[1]
            return (
              <select
                value={modelClass}
                onChange={(e) => setModelClass(e.target.value)}
                disabled={isStreaming}
                title={`Chat mode: ${current} (remembered per project). Auto lets the chat router pick per turn; /model <class> works too.`}
                aria-label={`Chat mode: ${current}`}
                className="flex-shrink-0 h-12 max-w-[4.5rem] sm:max-w-none bg-gray-800 border border-gray-700 rounded-xl px-2 text-xs text-gray-200 focus:border-brand-500 disabled:opacity-40"
              >
                {modes.map(([value, label]) => (
                  <option key={value} value={value} title={label}>{phone ? shortLabel(label) : label}</option>
                ))}
              </select>
            )
          })()}
          {/* Attach button */}
          <button
            type="button"
            onClick={() => fileInputRef.current?.click()}
            disabled={isStreaming || uploading}
            title="Attach files (uploaded to the workspace and indexed into memory)"
            aria-label="Attach files"
            className="flex-shrink-0 w-10 h-12 bg-gray-800 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed text-gray-400 hover:text-gray-200 rounded-xl flex items-center justify-center transition-colors"
          >
            <Paperclip className="w-4 h-4" aria-hidden="true" />
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
            tabIndex={-1}
            aria-hidden="true"
            onChange={handleFileSelect}
            className="hidden"
            accept="*/*"
          />

          <div className="flex-1 relative">
            <SkillPicker
              query={skillQuery}
              projectId={projectId}
              visible={showSkillPicker}
              onSelect={(name) => {
                setInput(`/${name} `)
                setShowSkillPicker(false)
                setSkillQuery('')
                textareaRef.current?.focus()
              }}
              onClose={() => {
                setShowSkillPicker(false)
                setSkillQuery('')
              }}
            />
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => {
                const val = e.target.value
                setInput(val)
                // Skill picker only while typing the command name: closes
                // after a space (so "/skill args" + Enter sends) and for the
                // built-in /model command.
                if (val.startsWith('/') && !/\s/.test(val) && val !== '/model') {
                  const afterSlash = val.slice(1)
                  setSkillQuery(afterSlash)
                  setShowSkillPicker(true)
                } else {
                  setShowSkillPicker(false)
                  setSkillQuery('')
                }
              }}
              onKeyDown={(e) => {
                // Let SkillPicker handle keys when open
                if (showSkillPicker && ['ArrowUp', 'ArrowDown', 'Tab', 'Escape'].includes(e.key)) {
                  return // SkillPicker handles these via its own listener
                }
                if (showSkillPicker && e.key === 'Enter' && !e.shiftKey) {
                  return // SkillPicker handles Enter
                }
                handleKeyDown(e)
              }}
              onPaste={onPaste}
              placeholder={uploading ? 'Uploading files…' : narrow ? 'Message the agent…' : 'Message the agent… (Enter to send, Shift+Enter for newline)'}
              aria-label="Message the agent (Enter to send, Shift+Enter for a new line)"
              rows={1}
              disabled={isStreaming || uploading}
              className="w-full resize-none bg-gray-800 border border-gray-700 rounded-xl px-3 sm:px-4 py-3 text-sm text-gray-100 focus:border-brand-500 focus:ring-1 focus:ring-brand-500 disabled:opacity-50 scrollbar-thin"
              style={{
                minHeight: '48px',
                maxHeight: '200px',
                height: 'auto',
              }}
              onInput={(e) => {
                e.target.style.height = 'auto'
                e.target.style.height = Math.min(e.target.scrollHeight, 200) + 'px'
              }}
            />
          </div>
          {isStreaming ? (
            <button
              type="button"
              onClick={onStop}
              aria-label="Stop the reply"
              title="Stop the reply"
              className="flex-shrink-0 w-10 h-12 bg-red-600 hover:bg-red-700 text-white rounded-xl flex items-center justify-center transition-colors"
            >
              <Square className="w-4 h-4" aria-hidden="true" />
            </button>
          ) : (
            <button
              type="button"
              onClick={onSend}
              disabled={(!input.trim() && attachments.length === 0) || uploading}
              aria-label="Send message"
              title="Send (Enter)"
              className="flex-shrink-0 w-10 h-12 bg-brand-600 hover:bg-brand-700 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl flex items-center justify-center transition-colors"
            >
              <Send className="w-4 h-4" aria-hidden="true" />
            </button>
          )}
        </div>
      </div>
    </>
  )
}
