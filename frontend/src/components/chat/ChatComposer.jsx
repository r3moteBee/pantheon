import React, { useState, useRef, useEffect } from 'react'
import { Send, Square, Paperclip } from 'lucide-react'
import { llmApi } from '../../api/client'
import SkillPicker from '../SkillPicker'
import { AttachmentPill } from './Attachments'

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
  const textareaRef = useRef(null)
  const fileInputRef = useRef(null)

  useEffect(() => {
    llmApi.getRouter()
      .then((v) => setRouteOptions((v.classes || []).filter((c) => c.usable && c.name !== 'agent')))
      .catch(() => setRouteOptions([]))
  }, [])

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
        <div className="px-4 py-2 bg-gray-850 border-t border-gray-800">
          <div className="flex flex-wrap gap-2 max-w-4xl mx-auto">
            {attachments.map((file, i) => (
              <AttachmentPill key={i} file={file} onRemove={() => onRemoveAttachment(i)} />
            ))}
          </div>
        </div>
      )}

      {/* Input area */}
      <div className="p-4 bg-gray-900 border-t border-gray-800">
        <div className="flex gap-2 items-end max-w-4xl mx-auto">
          {/* Model picker — only when the router has somewhere else to send turns */}
          {routeOptions.length > 0 && (
            <select
              value={modelClass}
              onChange={(e) => setModelClass(e.target.value)}
              disabled={isStreaming}
              title="Model for this conversation. Auto lets the chat router pick per turn; /model <class> works too."
              className="flex-shrink-0 h-12 bg-gray-800 border border-gray-700 rounded-xl px-2 text-xs text-gray-300 focus:outline-none focus:border-brand-500 disabled:opacity-40"
            >
              <option value="auto">Auto</option>
              <option value="agent">Agent</option>
              {routeOptions.map((c) => (
                <option key={c.name} value={c.name}>{c.label}</option>
              ))}
            </select>
          )}
          {/* Attach button */}
          <button
            onClick={() => fileInputRef.current?.click()}
            disabled={isStreaming || uploading}
            title="Attach file (uploaded to workspace and indexed into memory)"
            className="flex-shrink-0 w-10 h-12 bg-gray-800 hover:bg-gray-700 disabled:opacity-40 disabled:cursor-not-allowed text-gray-400 hover:text-gray-200 rounded-xl flex items-center justify-center transition-colors"
          >
            <Paperclip className="w-4 h-4" />
          </button>
          <input
            ref={fileInputRef}
            type="file"
            multiple
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
              placeholder={uploading ? 'Uploading files...' : 'Message the agent... (Enter to send, Shift+Enter for newline)'}
              rows={1}
              disabled={isStreaming || uploading}
              className="w-full resize-none bg-gray-800 border border-gray-700 rounded-xl px-4 py-3 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500 disabled:opacity-50 scrollbar-thin"
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
              onClick={onStop}
              className="flex-shrink-0 w-10 h-12 bg-red-600 hover:bg-red-700 text-white rounded-xl flex items-center justify-center transition-colors"
            >
              <Square className="w-4 h-4" />
            </button>
          ) : (
            <button
              onClick={onSend}
              disabled={(!input.trim() && attachments.length === 0) || uploading}
              className="flex-shrink-0 w-10 h-12 bg-brand-600 hover:bg-brand-700 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl flex items-center justify-center transition-colors"
            >
              <Send className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>
    </>
  )
}
