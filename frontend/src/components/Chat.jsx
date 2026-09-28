import React, { useState, useRef, useEffect, useCallback } from 'react'
import { Brain } from 'lucide-react'
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
  const messagesEndRef = useRef(null)

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

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingContent])

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
      <div className="flex-1 overflow-y-auto p-4 scrollbar-thin">
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

        <div ref={messagesEndRef} />
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
