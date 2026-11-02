import React, { useState, useRef, useEffect, useLayoutEffect } from 'react'
import { Send, Square, Paperclip, X } from 'lucide-react'
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
// Attachment tray + mode chip + input row: slash-command picker, textarea
// (with the attach button inside it) and send/stop. There is no mode dropdown: Auto routes each
// turn, and "/model <class>" (listed in the / menu) pins a conversation; the
// chip shows a pin and clears it.

// What the / menu offers for /model: Auto plus every class the router can use.
const modelCommands = (routeOptions) => [
  { name: 'model auto', description: 'Let Pantheon pick the mode for each message (default)', tags: ['mode'], command: true },
  { name: 'model agent', description: 'Pin this conversation to Agent (tool use)', tags: ['mode'], command: true },
  ...routeOptions.map((c) => ({
    name: `model ${c.name}`, description: `Pin this conversation to ${c.label || c.name}`, tags: ['mode'], command: true,
  })),
]
// "/model", "/model ", "/model q": still choosing; "/model quick " is done
const choosingModel = (val) => /^\/model(\s+\S*)?$/.test(val)
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

  // Fit the box to its text whenever the text changes, not only on typing:
  // clearing it after a send (or filling it from the skill picker) used to
  // leave the grown height behind, so the empty box stayed several lines tall.
  useLayoutEffect(() => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = Math.min(el.scrollHeight, 200) + 'px'
  }, [input])

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
        <div className="py-2 bg-gray-950 border-t border-gray-800">
          <div className={`flex flex-wrap gap-2 ${CHAT_COLUMN}`}>
            {attachments.map((file, i) => (
              <AttachmentPill key={i} file={file} onRemove={() => onRemoveAttachment(i)} />
            ))}
          </div>
        </div>
      )}

      {/* Input area: the same background as the transcript; one divider
          above it (on the attachment tray when that shows) */}
      <div className={`py-4 bg-gray-950 ${attachments.length > 0 ? '' : 'border-t border-gray-800'}`}>
        {/* Pinned mode: the conversation is held to one class until cleared */}
        {modelClass && modelClass !== 'auto' && (() => {
          const label = modelClass === 'agent' ? 'Agent (tool use)'
            : (routeOptions.find((c) => c.name === modelClass)?.label || modelClass)
          return (
            <div className={`flex mb-2 ${CHAT_COLUMN}`}>
              <div className="flex">
                <span className="inline-flex items-center gap-1.5 rounded-full bg-gray-800 border border-gray-700 pl-3 pr-1 py-0.5 text-xs text-gray-300">
                  Mode: <span className="text-gray-100">{label}</span>
                  <button
                    type="button"
                    onClick={() => setModelClass('auto')}
                    disabled={isStreaming}
                    title="Back to Auto (Pantheon picks the mode for each message)"
                    aria-label="Back to Auto mode"
                    className="ml-1 rounded-full p-0.5 hover:bg-gray-700 disabled:opacity-40"
                  >
                    <X className="w-3 h-3" aria-hidden="true" />
                  </button>
                </span>
              </div>
            </div>
          )
        })()}
        <div data-chat-column="composer" className={`flex gap-2 items-end ${CHAT_COLUMN}`}>
          <div className="flex-1 relative">
            {/* Attach button, inside the text box at its bottom-left corner */}
            <button
              type="button"
              onClick={() => fileInputRef.current?.click()}
              disabled={isStreaming || uploading}
              title="Attach files (uploaded to the workspace and indexed into memory)"
              aria-label="Attach files"
              className="absolute left-1 bottom-1 z-10 w-9 h-10 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed text-gray-400 hover:text-gray-200 rounded-lg flex items-center justify-center transition-colors"
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
            <SkillPicker
              commands={modelCommands(routeOptions)}
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
                // Picker only while typing the command name: closes after a
                // space (so "/skill args" + Enter sends), except that
                // "/model <class>" stays open until the class is typed.
                if (val.startsWith('/') && (!/\s/.test(val) || choosingModel(val))) {
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
              className="block w-full resize-none bg-gray-800 border border-gray-700 rounded-xl pl-11 pr-3 sm:pr-4 py-3 text-sm text-gray-100 focus:border-brand-500 focus:ring-1 focus:ring-brand-500 disabled:opacity-50 scrollbar-thin"
              style={{
                minHeight: '48px',
                maxHeight: '200px',
                height: 'auto',
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
