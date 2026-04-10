import React, { useState, useRef, useEffect, useCallback } from 'react'
import { Send, Square, ChevronDown, ChevronRight, Zap, Brain, Clock, Sparkles, Paperclip, X, FileText, Image, File, Target, UserCircle, Wand2, Check, XCircle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useStore } from '../store'
import { createChatSocket, settingsApi, chatApi, skillsApi, filesApi } from '../api/client'
import SkillPicker from './SkillPicker'

// Parse workspace:// path from show_file result markdown
function parseShowFileResult(result) {
  if (!result) return null
  // Match ![caption](workspace://path) or [caption](workspace://path)
  const match = result.match(/!?\[([^\]]*)\]\(workspace:\/\/([^)]+)\)/)
  if (!match) return null
  return { caption: match[1], path: decodeURIComponent(match[2]) }
}

function FilePreview({ filePath, caption }) {
  const projectId = useStore((s) => s.activeProject?.id || 'default')
  const url = filesApi.viewUrl(filePath, projectId)
  const ext = (filePath || '').split('.').pop().toLowerCase()
  const imageExts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg']

  if (imageExts.includes(ext)) {
    return (
      <div className="my-2">
        <img src={url} alt={caption || filePath} className="rounded-lg max-w-full max-h-96 border border-gray-700" />
        {caption && <div className="text-xs text-gray-400 mt-1">{caption}</div>}
      </div>
    )
  }
  if (ext === 'pdf') {
    return (
      <div className="my-2 rounded-lg overflow-hidden border border-gray-700">
        <iframe src={url} title={caption || filePath} className="w-full bg-white" style={{ height: '500px' }} />
        <div className="bg-gray-800 px-3 py-1.5 text-xs text-gray-400 flex items-center gap-2">
          <FileText className="w-3 h-3" /> {caption || filePath}
        </div>
      </div>
    )
  }
  // Fallback: download link
  return (
    <div className="my-2">
      <a href={url} target="_blank" rel="noopener noreferrer"
        className="text-brand-400 hover:text-brand-300 underline inline-flex items-center gap-1 text-sm"
      >
        <File className="w-3.5 h-3.5" /> {caption || filePath}
      </a>
    </div>
  )
}

function ToolCallBlock({ toolCall }) {
  const [expanded, setExpanded] = useState(false)
  const isContextLoad = toolCall.name === 'context_loaded'
  const isShowFile = toolCall.name === 'show_file'
  const showFileData = isShowFile ? parseShowFileResult(toolCall.result) : null

  // show_file with a successful result: render file preview, collapse the tool block
  if (isShowFile && showFileData) {
    return <FilePreview filePath={showFileData.path} caption={showFileData.caption} />
  }

  return (
    <div className={`my-2 border rounded-lg overflow-hidden text-xs ${isContextLoad ? 'border-brand-700' : 'border-gray-700'}`}>
      <button
        className={`w-full flex items-center gap-2 px-3 py-2 text-left ${isContextLoad ? 'bg-brand-900 hover:bg-brand-800' : 'bg-gray-800 hover:bg-gray-750'}`}
        onClick={() => setExpanded(!expanded)}
      >
        {isContextLoad
          ? <Brain className="w-3 h-3 text-brand-400 flex-shrink-0" />
          : <Zap className="w-3 h-3 text-yellow-400 flex-shrink-0" />}
        <span className={`font-mono font-medium ${isContextLoad ? 'text-brand-300' : 'text-yellow-300'}`}>
          {isContextLoad
            ? `corpus context loaded (${toolCall.args?.sources || 0} results from ${(toolCall.args?.tiers || []).join(', ')})`
            : toolCall.name}
        </span>
        {expanded ? <ChevronDown className="w-3 h-3 ml-auto text-gray-500" /> : <ChevronRight className="w-3 h-3 ml-auto text-gray-500" />}
      </button>
      {expanded && (
        <div className="px-3 py-2 bg-gray-900 space-y-2">
          {!isContextLoad && toolCall.args && Object.keys(toolCall.args).length > 0 && (
            <div>
              <div className="text-gray-500 mb-1">Args:</div>
              <pre className="text-green-300 whitespace-pre-wrap break-all">
                {JSON.stringify(toolCall.args, null, 2)}
              </pre>
            </div>
          )}
          {toolCall.result && (
            <div>
              <div className="text-gray-500 mb-1">{isContextLoad ? 'Injected context:' : 'Result:'}</div>
              <pre className={`whitespace-pre-wrap break-all ${isContextLoad ? 'text-brand-200' : 'text-blue-300'}`}>{toolCall.result}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function AttachmentIcon({ filename }) {
  const ext = (filename || '').split('.').pop().toLowerCase()
  const imageExts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp']
  const docExts = ['pdf', 'doc', 'docx', 'txt', 'md', 'csv', 'json', 'yaml', 'yml']
  if (imageExts.includes(ext)) return <Image className="w-3.5 h-3.5 text-purple-400" />
  if (docExts.includes(ext)) return <FileText className="w-3.5 h-3.5 text-blue-400" />
  return <File className="w-3.5 h-3.5 text-gray-400" />
}

function AttachmentPill({ file, onRemove }) {
  return (
    <div className="flex items-center gap-1.5 bg-gray-700 rounded-lg px-2 py-1 text-xs text-gray-200 max-w-[200px]">
      <AttachmentIcon filename={file.name} />
      <span className="truncate">{file.name}</span>
      {onRemove && (
        <button onClick={onRemove} className="ml-auto flex-shrink-0 hover:text-red-400 transition-colors">
          <X className="w-3 h-3" />
        </button>
      )}
    </div>
  )
}

// Helper: resolve workspace:// paths to viewable URLs
const isWorkspacePath = (url) => url && url.startsWith('workspace://')
const workspaceFilePath = (url) => decodeURIComponent(url.replace(/^workspace:\/\//, ''))
const imageExts = /\.(png|jpe?g|gif|webp|bmp|svg)$/i
const pdfExt = /\.pdf$/i

function useMarkdownComponents() {
  const projectId = useStore((s) => s.activeProject?.id || 'default')
  const resolveUrl = (wsPath) => {
    // Decode URI-encoded path from show_file tool, then let viewUrl re-encode
    const filePath = decodeURIComponent(wsPath.replace(/^workspace:\/\//, ''))
    return filesApi.viewUrl(filePath, projectId)
  }

  return {
    code: ({ node, inline, className, children, ...props }) => {
      if (inline) {
        return <code className="bg-gray-700 px-1 py-0.5 rounded text-xs font-mono" {...props}>{children}</code>
      }
      return (
        <pre className="bg-gray-900 rounded-lg p-3 overflow-x-auto">
          <code className="text-green-300 text-xs font-mono" {...props}>{children}</code>
        </pre>
      )
    },
    img: ({ src, alt, ...props }) => {
      if (isWorkspacePath(src)) {
        const url = resolveUrl(src)
        const path = workspaceFilePath(src)
        if (pdfExt.test(path)) {
          return (
            <div className="my-3 rounded-lg overflow-hidden border border-gray-700">
              <embed src={url} type="application/pdf" className="w-full" style={{ height: '500px' }} />
              <div className="bg-gray-800 px-3 py-1.5 text-xs text-gray-400 flex items-center gap-2">
                <FileText className="w-3 h-3" /> {alt || path}
              </div>
            </div>
          )
        }
        if (imageExts.test(path)) {
          return (
            <div className="my-3">
              <img src={url} alt={alt || path} className="rounded-lg max-w-full max-h-96 border border-gray-700" />
              {alt && <div className="text-xs text-gray-400 mt-1">{alt}</div>}
            </div>
          )
        }
      }
      return <img src={src} alt={alt} {...props} />
    },
    a: ({ href, children, ...props }) => {
      if (isWorkspacePath(href)) {
        const url = resolveUrl(href)
        return (
          <a href={url} target="_blank" rel="noopener noreferrer"
            className="text-brand-400 hover:text-brand-300 underline inline-flex items-center gap-1"
            {...props}
          >
            <File className="w-3 h-3 inline" />
            {children}
          </a>
        )
      }
      return <a href={href} target="_blank" rel="noopener noreferrer" {...props}>{children}</a>
    },
  }
}

function Message({ msg }) {
  const isUser = msg.role === 'user'
  const mdComponents = useMarkdownComponents()
  return (
    <div className={`flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}>
      <div className={`max-w-3xl ${isUser ? 'order-2' : 'order-1'}`}>
        {!isUser && (
          <div className="flex items-center gap-2 mb-1">
            <div className="w-6 h-6 rounded-full bg-brand-600 flex items-center justify-center">
              <Brain className="w-3.5 h-3.5 text-white" />
            </div>
            <span className="text-xs text-gray-500">Agent</span>
            {msg.timestamp && (
              <span className="text-xs text-gray-600">{new Date(msg.timestamp).toLocaleTimeString()}</span>
            )}
          </div>
        )}

        {/* Tool calls */}
        {msg.toolCalls && msg.toolCalls.length > 0 && (
          <div className="mb-2">
            {msg.toolCalls.map((tc, i) => (
              <ToolCallBlock key={i} toolCall={tc} />
            ))}
          </div>
        )}

        {/* Attachments */}
        {msg.attachments && msg.attachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-1.5">
            {msg.attachments.map((att, i) => (
              <AttachmentPill key={i} file={att} />
            ))}
          </div>
        )}

        {/* Message content */}
        <div
          className={`
            px-4 py-3 rounded-2xl text-sm leading-relaxed
            ${isUser
              ? 'bg-brand-600 text-white rounded-br-sm'
              : 'bg-gray-800 text-gray-100 rounded-bl-sm'
            }
          `}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap">{msg.content}</p>
          ) : (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              className="prose prose-invert prose-sm max-w-none"
              components={mdComponents}
            >
              {msg.content}
            </ReactMarkdown>
          )}
        </div>

        {isUser && msg.timestamp && (
          <div className="flex justify-end mt-1">
            <span className="text-xs text-gray-600">{new Date(msg.timestamp).toLocaleTimeString()}</span>
          </div>
        )}
      </div>
    </div>
  )
}

export default function Chat() {
  const mdComponents = useMarkdownComponents()
  const [input, setInput] = useState('')
  const [attachments, setAttachments] = useState([])
  const [uploading, setUploading] = useState(false)
  const [memoryRecall, setMemoryRecall] = useState(true)
  const [recallLoading, setRecallLoading] = useState(false)
  const [personalityWeight, setPersonalityWeight] = useState('balanced')
  const [contextFocus, setContextFocus] = useState('balanced')
  const [skillDiscovery, setSkillDiscovery] = useState('off')
  const [showSkillPicker, setShowSkillPicker] = useState(false)
  const [skillQuery, setSkillQuery] = useState('')
  const [activeSkillBadge, setActiveSkillBadge] = useState(null)
  const [pendingSuggestion, setPendingSuggestion] = useState(null)
  const messagesEndRef = useRef(null)
  const socketRef = useRef(null)
  const textareaRef = useRef(null)
  const fileInputRef = useRef(null)

  const messages = useStore((s) => s.messages)
  const addMessage = useStore((s) => s.addMessage)
  const isStreaming = useStore((s) => s.isStreaming)
  const setIsStreaming = useStore((s) => s.setIsStreaming)
  const streamingContent = useStore((s) => s.streamingContent)
  const setStreamingContent = useStore((s) => s.setStreamingContent)
  const appendStreamingContent = useStore((s) => s.appendStreamingContent)
  const currentToolCalls = useStore((s) => s.currentToolCalls)
  const addToolCall = useStore((s) => s.addToolCall)
  const clearToolCalls = useStore((s) => s.clearToolCalls)
  const sessionId = useStore((s) => s.sessionId)
  const setSessionId = useStore((s) => s.setSessionId)
  const activeProject = useStore((s) => s.activeProject)
  const addNotification = useStore((s) => s.addNotification)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, streamingContent])

  useEffect(() => {
    settingsApi.get().then((res) => {
      setMemoryRecall(res.data.memory_recall_enabled !== false)
      setPersonalityWeight(res.data.personality_weight || 'balanced')
      setContextFocus(res.data.context_focus || 'balanced')
    }).catch(() => {})
    // Load skill discovery setting for active project
    const pid = activeProject?.id || 'default'
    skillsApi.getDiscovery(pid).then((res) => {
      setSkillDiscovery(res.data.skill_discovery || 'off')
    }).catch(() => {})
  }, [activeProject?.id])

  const toggleMemoryRecall = async () => {
    const next = !memoryRecall
    setRecallLoading(true)
    try {
      await settingsApi.update({ memory_recall_enabled: next })
      setMemoryRecall(next)
      addNotification({
        type: 'success',
        message: next ? 'Memory recall on' : 'Memory recall off',
      })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setRecallLoading(false)
  }

  const cyclePersonalityWeight = async () => {
    const order = ['minimal', 'balanced', 'strong']
    const next = order[(order.indexOf(personalityWeight) + 1) % order.length]
    try {
      await settingsApi.update({ personality_weight: next })
      setPersonalityWeight(next)
      addNotification({ type: 'success', message: `Personality: ${next}` })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const cycleContextFocus = async () => {
    const order = ['broad', 'balanced', 'focused']
    const next = order[(order.indexOf(contextFocus) + 1) % order.length]
    try {
      await settingsApi.update({ context_focus: next })
      setContextFocus(next)
      addNotification({ type: 'success', message: `Focus: ${next}` })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const cycleSkillDiscovery = async () => {
    const order = ['off', 'suggest', 'auto']
    const next = order[(order.indexOf(skillDiscovery) + 1) % order.length]
    const pid = activeProject?.id || 'default'
    try {
      await skillsApi.setDiscovery(pid, next)
      setSkillDiscovery(next)
      addNotification({ type: 'success', message: `Auto-Skill: ${next}` })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  // ── File attachment handling ────────────────────────────────────────

  const handleFileSelect = (e) => {
    const files = Array.from(e.target.files || [])
    if (files.length === 0) return
    setAttachments((prev) => [...prev, ...files])
    // Reset input so the same file can be selected again
    if (fileInputRef.current) fileInputRef.current.value = ''
  }

  const removeAttachment = (index) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index))
  }

  const uploadAttachments = async (files) => {
    if (files.length === 0) return []
    const projectId = activeProject?.id || 'default'
    const results = []
    for (const file of files) {
      try {
        const res = await chatApi.attachFile(file, projectId)
        results.push({
          name: file.name,
          path: res.data.path,
          size: res.data.size,
          indexed: res.data.indexing || false,
          description: res.data.description || null,
        })
      } catch (err) {
        addNotification({ type: 'error', message: `Failed to upload ${file.name}: ${err.message}` })
      }
    }
    return results
  }

  // Handle paste events for images
  const handlePaste = (e) => {
    const items = e.clipboardData?.items
    if (!items) return
    const pastedFiles = []
    for (const item of items) {
      if (item.kind === 'file') {
        const file = item.getAsFile()
        if (file) pastedFiles.push(file)
      }
    }
    if (pastedFiles.length > 0) {
      e.preventDefault()
      setAttachments((prev) => [...prev, ...pastedFiles])
    }
  }

  // Handle drag and drop
  const [isDragging, setIsDragging] = useState(false)

  const handleDragOver = (e) => {
    e.preventDefault()
    setIsDragging(true)
  }

  const handleDragLeave = (e) => {
    e.preventDefault()
    setIsDragging(false)
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setIsDragging(false)
    const files = Array.from(e.dataTransfer.files || [])
    if (files.length > 0) {
      setAttachments((prev) => [...prev, ...files])
    }
  }

  // ── WebSocket ──────────────────────────────────────────────────────

  const connectSocket = useCallback(() => {
    if (socketRef.current?.readyState === WebSocket.OPEN) return

    const handleClose = (code, reason) => {
      if (useStore.getState().isStreaming) {
        useStore.getState().setIsStreaming(false)
        useStore.getState().setStreamingContent('')
        useStore.getState().clearToolCalls()
        useStore.getState().addNotification({
          type: 'error',
          message: `Connection closed unexpectedly (code ${code}). Try sending your message again.`,
        })
      }
    }

    socketRef.current = createChatSocket((event) => {
      switch (event.type) {
        case 'session_start':
          setSessionId(event.session_id)
          break
        case 'skill_active':
          setActiveSkillBadge(event.skill)
          break
        case 'skill_suggestion':
          setIsStreaming(false)
          setPendingSuggestion({
            skill: event.skill,
            description: event.description,
            reason: event.reason,
            suggestionId: event.suggestion_id,
          })
          break
        case 'text_delta':
          appendStreamingContent(event.content)
          break
        case 'tool_call':
          addToolCall({ name: event.name, args: event.args, id: event.id })
          break
        case 'tool_result':
          useStore.setState((state) => ({
            currentToolCalls: state.currentToolCalls.map((tc) =>
              tc.id === event.tool_id ? { ...tc, result: event.result } : tc
            ),
          }))
          break
        case 'done': {
          const finalContent = useStore.getState().streamingContent
          const finalToolCalls = useStore.getState().currentToolCalls
          if (finalContent || finalToolCalls.length > 0) {
            addMessage({
              role: 'assistant',
              content: finalContent,
              toolCalls: [...finalToolCalls],
              timestamp: new Date().toISOString(),
            })
          }
          setStreamingContent('')
          clearToolCalls()
          setIsStreaming(false)
          setActiveSkillBadge(null)
          break
        }
        case 'error':
          addNotification({ type: 'error', message: event.message })
          setIsStreaming(false)
          setStreamingContent('')
          clearToolCalls()
          break
      }
    }, handleClose)
  }, [])

  // ── Send message ───────────────────────────────────────────────────

  const sendMessage = useCallback(async () => {
    const msg = input.trim()
    if ((!msg && attachments.length === 0) || isStreaming) return

    // Upload attachments first
    let uploadedFiles = []
    if (attachments.length > 0) {
      setUploading(true)
      uploadedFiles = await uploadAttachments(attachments)
      setUploading(false)
      setAttachments([])
    }

    // Build message with attachment context
    let fullMessage = msg
    if (uploadedFiles.length > 0) {
      const fileList = uploadedFiles.map((f) => {
        const descLine = f.description ? ` — ${f.description}` : ''
        return `- ${f.name} (saved to workspace: ${f.path}${descLine})`
      }).join('\n')
      const attachmentNote = `\n\n[Attached files — uploaded to workspace/uploads/ and indexed into memory]\n${fileList}`
      fullMessage = msg ? msg + attachmentNote : `Please review the attached files:\n${fileList}`
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

    connectSocket()

    const payload = JSON.stringify({
      message: fullMessage,
      session_id: sessionId,
      project_id: activeProject?.id || 'default',
    })

    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(payload)
    } else {
      const sock = socketRef.current
      if (sock) {
        sock.onopen = () => sock.send(payload)
      }
    }
  }, [input, attachments, isStreaming, sessionId, activeProject])

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      sendMessage()
    }
  }

  const stopStreaming = () => {
    socketRef.current?.close()
    setIsStreaming(false)
    const content = streamingContent
    if (content) {
      addMessage({ role: 'assistant', content, toolCalls: [...currentToolCalls], timestamp: new Date().toISOString() })
    }
    setStreamingContent('')
    clearToolCalls()
  }

  const handleSkillAccept = useCallback(() => {
    if (!pendingSuggestion) return
    setIsStreaming(true)
    setStreamingContent('')
    clearToolCalls()
    connectSocket()
    const payload = JSON.stringify({
      type: 'skill_accept',
      suggestion_id: pendingSuggestion.suggestionId,
    })
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(payload)
    } else if (socketRef.current) {
      socketRef.current.onopen = () => socketRef.current.send(payload)
    }
    setPendingSuggestion(null)
  }, [pendingSuggestion, connectSocket])

  const handleSkillDecline = useCallback(() => {
    if (!pendingSuggestion) return
    setIsStreaming(true)
    setStreamingContent('')
    clearToolCalls()
    connectSocket()
    const payload = JSON.stringify({
      type: 'skill_decline',
      suggestion_id: pendingSuggestion.suggestionId,
    })
    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(payload)
    } else if (socketRef.current) {
      socketRef.current.onopen = () => socketRef.current.send(payload)
    }
    setPendingSuggestion(null)
  }, [pendingSuggestion, connectSocket])

  return (
    <div
      className="flex flex-col h-full"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
    >
      {/* Header */}
      <div className="px-4 py-3 bg-gray-900 border-b border-gray-800 flex items-center gap-3">
        <Brain className="w-4 h-4 text-brand-400" />
        <span className="text-sm font-medium text-gray-200">
          {activeProject?.name || 'Default Project'}
        </span>
        <div className="ml-auto flex items-center gap-3">
          {sessionId && (
            <span className="text-xs text-gray-600 font-mono">
              session: {sessionId.slice(0, 8)}
            </span>
          )}
          <button
            onClick={toggleMemoryRecall}
            disabled={recallLoading}
            title={memoryRecall ? 'Memory recall augmentation is ON — click to disable' : 'Memory recall augmentation is OFF — click to enable'}
            className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-colors disabled:opacity-50 ${
              memoryRecall
                ? 'bg-brand-900 text-brand-300 hover:bg-brand-800'
                : 'bg-gray-800 text-gray-500 hover:bg-gray-700'
            }`}
          >
            <Sparkles className="w-3 h-3" />
            Recall
          </button>
          <button
            onClick={cycleContextFocus}
            title={`Thread focus: ${contextFocus} — controls how strongly recent messages are favoured over older context. Click to cycle: broad → balanced → focused`}
            className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-colors ${
              contextFocus === 'focused'
                ? 'bg-amber-900 text-amber-300 hover:bg-amber-800'
                : contextFocus === 'broad'
                  ? 'bg-gray-800 text-gray-500 hover:bg-gray-700'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
            }`}
          >
            <Target className="w-3 h-3" />
            <span className="text-gray-500">Focus:</span> {contextFocus.charAt(0).toUpperCase() + contextFocus.slice(1)}
          </button>
          <button
            onClick={cycleSkillDiscovery}
            title={`Auto-Skill: ${skillDiscovery} — off = manual only, suggest = recommend skills, auto = activate automatically. Click to cycle.`}
            className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-colors ${
              skillDiscovery === 'auto'
                ? 'bg-emerald-900 text-emerald-300 hover:bg-emerald-800'
                : skillDiscovery === 'suggest'
                  ? 'bg-amber-900 text-amber-300 hover:bg-amber-800'
                  : 'bg-gray-800 text-gray-500 hover:bg-gray-700'
            }`}
          >
            <Wand2 className="w-3 h-3" />
            <span className="text-gray-500">Skill:</span> {skillDiscovery.charAt(0).toUpperCase() + skillDiscovery.slice(1)}
          </button>
          <button
            onClick={cyclePersonalityWeight}
            title={`Personality presence: ${personalityWeight} — controls how much the agent's identity shows in responses. Click to cycle: minimal → balanced → strong`}
            className={`flex items-center gap-1.5 px-2 py-1 rounded-md text-xs font-medium transition-colors ${
              personalityWeight === 'strong'
                ? 'bg-purple-900 text-purple-300 hover:bg-purple-800'
                : personalityWeight === 'minimal'
                  ? 'bg-gray-800 text-gray-500 hover:bg-gray-700'
                  : 'bg-gray-800 text-gray-400 hover:bg-gray-700'
            }`}
          >
            <UserCircle className="w-3 h-3" />
            <span className="text-gray-500">Persona:</span> {personalityWeight.charAt(0).toUpperCase() + personalityWeight.slice(1)}
          </button>
        </div>
      </div>

      {/* Drag overlay */}
      {isDragging && (
        <div className="absolute inset-0 z-50 bg-brand-900/60 backdrop-blur-sm flex items-center justify-center pointer-events-none">
          <div className="bg-gray-800 border-2 border-dashed border-brand-400 rounded-2xl px-8 py-6 text-center">
            <Paperclip className="w-8 h-8 text-brand-400 mx-auto mb-2" />
            <p className="text-brand-300 font-medium">Drop files to attach</p>
            <p className="text-xs text-gray-400 mt-1">Files will be uploaded and indexed into memory</p>
          </div>
        </div>
      )}

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
          <Message key={i} msg={msg} />
        ))}

        {/* Streaming response */}
        {isStreaming && (
          <div className="flex justify-start mb-4">
            <div className="max-w-3xl">
              <div className="flex items-center gap-2 mb-1">
                <div className="w-6 h-6 rounded-full bg-brand-600 flex items-center justify-center">
                  <Brain className="w-3.5 h-3.5 text-white" />
                </div>
                <span className="text-xs text-gray-500">Agent</span>
                {activeSkillBadge && (
                  <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-brand-900 text-brand-300">
                    <Zap className="w-2.5 h-2.5" />
                    {activeSkillBadge}
                  </span>
                )}
                <div className="flex gap-1 ml-1">
                  <div className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
                  <div className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
                  <div className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
                </div>
              </div>

              {currentToolCalls.map((tc, i) => (
                <ToolCallBlock key={i} toolCall={tc} />
              ))}

              {streamingContent && (
                <div className="px-4 py-3 rounded-2xl rounded-bl-sm bg-gray-800 text-gray-100 text-sm leading-relaxed">
                  <ReactMarkdown
                    remarkPlugins={[remarkGfm]}
                    className="prose prose-invert prose-sm max-w-none"
                    components={mdComponents}
                  >
                    {streamingContent}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          </div>
        )}
        {/* Skill suggestion prompt */}
        {pendingSuggestion && !isStreaming && (
          <div className="flex justify-start mb-4">
            <div className="max-w-3xl w-full">
              <div className="border border-amber-700/50 bg-amber-950/40 rounded-2xl px-4 py-3">
                <div className="flex items-center gap-2 mb-2">
                  <Wand2 className="w-4 h-4 text-amber-400" />
                  <span className="text-sm font-medium text-amber-300">Skill suggested</span>
                </div>
                <p className="text-sm text-gray-200 mb-1">
                  <span className="font-mono text-amber-300">/{pendingSuggestion.skill}</span>
                  {' — '}{pendingSuggestion.description}
                </p>
                {pendingSuggestion.reason && (
                  <p className="text-xs text-gray-500 mb-3">Matched: {pendingSuggestion.reason}</p>
                )}
                <div className="flex gap-2">
                  <button
                    onClick={handleSkillAccept}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-amber-700 hover:bg-amber-600 text-white transition-colors"
                  >
                    <Check className="w-3 h-3" />
                    Use skill
                  </button>
                  <button
                    onClick={handleSkillDecline}
                    className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
                  >
                    <XCircle className="w-3 h-3" />
                    Skip
                  </button>
                </div>
              </div>
            </div>
          </div>
        )}

        <div ref={messagesEndRef} />
      </div>

      {/* Attachment preview bar */}
      {attachments.length > 0 && (
        <div className="px-4 py-2 bg-gray-850 border-t border-gray-800">
          <div className="flex flex-wrap gap-2 max-w-4xl mx-auto">
            {attachments.map((file, i) => (
              <AttachmentPill key={i} file={file} onRemove={() => removeAttachment(i)} />
            ))}
          </div>
        </div>
      )}

      {/* Input area */}
      <div className="p-4 bg-gray-900 border-t border-gray-800">
        <div className="flex gap-2 items-end max-w-4xl mx-auto">
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
              projectId={activeProject?.id || 'default'}
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
                // Detect / at start of input for skill picker
                if (val.startsWith('/')) {
                  const afterSlash = val.slice(1).split(/\s/)[0]
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
              onPaste={handlePaste}
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
              onClick={stopStreaming}
              className="flex-shrink-0 w-10 h-12 bg-red-600 hover:bg-red-700 text-white rounded-xl flex items-center justify-center transition-colors"
            >
              <Square className="w-4 h-4" />
            </button>
          ) : (
            <button
              onClick={sendMessage}
              disabled={(!input.trim() && attachments.length === 0) || uploading}
              className="flex-shrink-0 w-10 h-12 bg-brand-600 hover:bg-brand-700 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl flex items-center justify-center transition-colors"
            >
              <Send className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
