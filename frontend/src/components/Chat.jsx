import React, { useState, useRef, useEffect, useLayoutEffect, useCallback } from 'react'
import { Brain, ArrowDown } from 'lucide-react'
import { useStore } from '../store'
import Message, { StreamingMessage } from './chat/Message'
import SkillSuggestionCard from './chat/SkillSuggestionCard'
import ChatComposer from './chat/ChatComposer'
import ChatHistoryDrawer from './chat/ChatHistoryDrawer'
import SaveToArtifactModal, { defaultSavePath } from './chat/SaveToArtifactModal'
import { DropOverlay } from './chat/Attachments'
import useChatSocket from './chat/useChatSocket'
import useChatAttachments from './chat/useChatAttachments'

// Composition shell: the transcript, the composer, and the history drawer /
// save modal. Socket events live in useChatSocket, attachments in
// useChatAttachments, rendering in ./chat/*.
export default function Chat() {
  const [input, setInput] = useState('')
  const scrollRef = useRef(null)

  const messages = useStore((s) => s.messages)
  const addMessage = useStore((s) => s.addMessage)
  const isStreaming = useStore((s) => s.isStreaming)
  const setIsStreaming = useStore((s) => s.setIsStreaming)
  const streamingContent = useStore((s) => s.streamingContent)
  const setStreamingContent = useStore((s) => s.setStreamingContent)
  const currentToolCalls = useStore((s) => s.currentToolCalls)
  const clearToolCalls = useStore((s) => s.clearToolCalls)
  const sessionId = useStore((s) => s.sessionId)
  const activeProject = useStore((s) => s.activeProject)
  const addNotification = useStore((s) => s.addNotification)
  const projectId = activeProject?.id || 'default'

  const socket = useChatSocket()
  const files = useChatAttachments()

  const [saveModal, setSaveModal] = useState(null) // { content, defaultPath } | null
  const handleSaveMessage = useCallback((content) => {
    if (!content) return
    setSaveModal({ content, defaultPath: defaultSavePath(content, activeProject?.name) })
  }, [activeProject])

  // Scrolling: the transcript never follows the agent's output on its own,
  // so text being read doesn't move while the reply streams in below it.
  // It jumps to the end only when a chat is opened or the user sends a
  // message; otherwise the down-arrow button (or scrolling) goes there.
  const [atBottom, setAtBottom] = useState(true)
  const scrollAfterSendRef = useRef(false)
  const prevMessagesRef = useRef(messages)

  const updateAtBottom = useCallback(() => {
    const el = scrollRef.current
    if (!el) return
    setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 40)
  }, [])

  const scrollToBottom = useCallback((behavior = 'smooth') => {
    const el = scrollRef.current
    if (el) el.scrollTo({ top: el.scrollHeight, behavior })
  }, [])

  useLayoutEffect(() => {
    const prev = prevMessagesRef.current
    prevMessagesRef.current = messages
    // A single message appended to the same chat is a turn in progress;
    // anything else (history opened, chat cleared) is a different chat.
    const appended = messages.length === prev.length + 1 && messages[0] === (prev[0] ?? messages[0])
    if (!appended) {
      scrollToBottom('auto')
    } else if (scrollAfterSendRef.current) {
      scrollAfterSendRef.current = false
      scrollToBottom()
    }
    updateAtBottom()
  }, [messages, scrollToBottom, updateAtBottom])

  // Output growing below the fold changes whether we are at the end.
  useEffect(() => { updateAtBottom() }, [streamingContent, currentToolCalls, updateAtBottom])

  // ── Send message ───────────────────────────────────────────────────

  const sendMessage = async () => {
    const msg = input.trim()
    if ((!msg && files.attachments.length === 0) || isStreaming) return

    // Upload attachments first
    const uploadedFiles = await files.uploadPending()

    // Build message with attachment context
    let fullMessage = msg
    if (uploadedFiles.length > 0) {
      const lines = uploadedFiles.map((f) => {
        const isImage = (f.contentType || '').startsWith('image/')
        if (isImage) {
          // Marker the backend agent's _build_user_content regex picks up:
          //   [image: <path> (artifact:<id>)]
          const status = f.extractionJobId
            ? '_extracting in background…_'
            : '_no extraction queued_'
          return `- [image: ${f.path} (artifact:${f.artifactId})] ${status}`
        }
        return `- ${f.name} (artifact:${f.artifactId} — ${f.path})`
      }).join('\n')
      const note = `\n\n[Attached files — saved as artifacts]\n${lines}`
      fullMessage = msg ? msg + note : `Please review the attached files:\n${lines}`
    }

    scrollAfterSendRef.current = true
    addMessage({
      role: 'user',
      content: msg || 'Attached files for review',
      attachments: uploadedFiles.length > 0 ? uploadedFiles : undefined,
      timestamp: new Date().toISOString(),
    })
    setInput('')
    setIsStreaming(true)
    setStreamingContent('')
    clearToolCalls()

    socket.send(JSON.stringify({
      message: fullMessage,
      session_id: sessionId,
      project_id: projectId,
      model_class: socket.modelClass,
    }))
  }

  const stopStreaming = () => {
    socket.close()
    setIsStreaming(false)
    const content = streamingContent
    if (content) {
      addMessage({ role: 'assistant', content, toolCalls: [...currentToolCalls], timestamp: new Date().toISOString() })
    }
    setStreamingContent('')
    clearToolCalls()
  }

  return (
    <div
      className="flex flex-col h-full"
      {...files.dropHandlers}
    >

      {/* Drag overlay */}
      {files.isDragging && <DropOverlay />}

      {/* Messages */}
      <div className="relative flex-1 min-h-0 flex flex-col">
      <div ref={scrollRef} onScroll={updateAtBottom} className="flex-1 overflow-y-auto p-4 scrollbar-thin">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Brain className="w-12 h-12 text-gray-700 mb-4" />
            <h2 className="text-xl font-semibold text-gray-400 mb-2">Start a conversation</h2>
            <p className="text-sm text-gray-600 max-w-sm">
              Your agent has access to memory, files, web search, and autonomous task scheduling.
              Attach documents with the clip icon or drag and drop.
            </p>
          </div>
        )}

        {messages.map((msg, i) => (
          <Message key={i} msg={msg} onSaveMessage={handleSaveMessage} />
        ))}

        {/* Streaming response */}
        {isStreaming && (
          <StreamingMessage
            liveRoute={socket.liveRoute}
            activeSkillBadge={socket.activeSkillBadge}
            toolCalls={currentToolCalls}
            content={streamingContent}
          />
        )}
        {/* Skill suggestion prompt */}
        {socket.pendingSuggestion && !isStreaming && (
          <SkillSuggestionCard
            suggestion={socket.pendingSuggestion}
            onAccept={socket.acceptSuggestion}
            onDecline={socket.declineSuggestion}
          />
        )}

      </div>
      {!atBottom && messages.length > 0 && (
        <button
          type="button"
          onClick={() => scrollToBottom()}
          aria-label="Scroll to the end"
          title="Scroll to the end"
          className="absolute bottom-3 left-1/2 -translate-x-1/2 flex items-center gap-1.5 rounded-full border border-gray-700 bg-gray-900/90 px-3 py-1.5 text-xs text-gray-300 shadow-lg hover:bg-gray-800 hover:text-white"
        >
          <ArrowDown className={`w-4 h-4 ${isStreaming ? 'animate-bounce' : ''}`} />
          {isStreaming ? 'New output below' : 'Jump to end'}
        </button>
      )}
      </div>

      <ChatComposer
        input={input}
        setInput={setInput}
        onSend={sendMessage}
        onStop={stopStreaming}
        isStreaming={isStreaming}
        uploading={files.uploading}
        attachments={files.attachments}
        onAddFiles={files.addFiles}
        onRemoveAttachment={files.removeAttachment}
        onPaste={files.handlePaste}
        modelClass={socket.modelClass}
        setModelClass={socket.setModelClass}
        projectId={projectId}
      />
    <ChatHistoryDrawer />
    {saveModal && (
      <SaveToArtifactModal
        defaultPath={saveModal.defaultPath}
        content={saveModal.content}
        projectId={projectId}
        onClose={() => setSaveModal(null)}
        onSaved={(art) => {
          setSaveModal(null)
          addNotification({ type: 'success', message: `Saved artifact: ${art.path}` })
        }}
      />
    )}
        </div>
  )
}
