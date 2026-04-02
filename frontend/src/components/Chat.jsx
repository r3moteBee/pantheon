import React, { useState, useRef, useEffect, useCallback } from 'react'
import { Send, Square, ChevronDown, ChevronRight, Zap, Brain, Clock } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useStore } from '../store'
import { createChatSocket } from '../api/client'

function ToolCallBlock({ toolCall }) {
  const [expanded, setExpanded] = useState(false)
  return (
    <div className="my-2 border border-gray-700 rounded-lg overflow-hidden text-xs">
      <button
        className="w-full flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-750 text-left"
        onClick={() => setExpanded(!expanded)}
      >
        <Zap className="w-3 h-3 text-yellow-400 flex-shrink-0" />
        <span className="text-yellow-300 font-mono font-medium">{toolCall.name}</span>
        {expanded ? <ChevronDown className="w-3 h-3 ml-auto text-gray-500" /> : <ChevronRight className="w-3 h-3 ml-auto text-gray-500" />}
      </button>
      {expanded && (
        <div className="px-3 py-2 bg-gray-900 space-y-2">
          {toolCall.args && Object.keys(toolCall.args).length > 0 && (
            <div>
              <div className="text-gray-500 mb-1">Args:</div>
              <pre className="text-green-300 whitespace-pre-wrap break-all">
                {JSON.stringify(toolCall.args, null, 2)}
              </pre>
            </div>
          )}
          {toolCall.result && (
            <div>
              <div className="text-gray-500 mb-1">Result:</div>
              <pre className="text-blue-300 whitespace-pre-wrap break-all">{toolCall.result}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Message({ msg }) {
  const isUser = msg.role === 'user'
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
              components={{
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
              }}
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
  const [input, setInput] = useState('')
  const messagesEndRef = useRef(null)
  const socketRef = useRef(null)
  const textareaRef = useRef(null)

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

  const connectSocket = useCallback(() => {
    if (socketRef.current?.readyState === WebSocket.OPEN) return

    const handleClose = (code, reason) => {
      // Unexpected socket close — reset streaming state so UI isn't stuck
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

  const sendMessage = useCallback(() => {
    const msg = input.trim()
    if (!msg || isStreaming) return

    addMessage({
      role: 'user',
      content: msg,
      timestamp: new Date().toISOString(),
    })
    setInput('')
    setIsStreaming(true)
    setStreamingContent('')
    clearToolCalls()

    connectSocket()

    if (socketRef.current?.readyState === WebSocket.OPEN) {
      socketRef.current.send(JSON.stringify({
        message: msg,
        session_id: sessionId,
        project_id: activeProject?.id || 'default',
      }))
    } else {
      const sock = socketRef.current
      if (sock) {
        sock.onopen = () => {
          sock.send(JSON.stringify({
            message: msg,
            session_id: sessionId,
            project_id: activeProject?.id || 'default',
          }))
        }
      }
    }
  }, [input, isStreaming, sessionId, activeProject])

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

  return (
    <div className="flex flex-col h-full">
      {/* Header */}
      <div className="px-4 py-3 bg-gray-900 border-b border-gray-800 flex items-center gap-3">
        <Brain className="w-4 h-4 text-brand-400" />
        <span className="text-sm font-medium text-gray-200">
          {activeProject?.name || 'Default Project'}
        </span>
        {sessionId && (
          <span className="text-xs text-gray-600 font-mono ml-auto">
            session: {sessionId.slice(0, 8)}
          </span>
        )}
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto p-4 scrollbar-thin">
        {messages.length === 0 && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Brain className="w-12 h-12 text-gray-700 mb-4" />
            <h2 className="text-xl font-semibold text-gray-400 mb-2">Start a conversation</h2>
            <p className="text-sm text-gray-600 max-w-sm">
              Your agent has access to memory, files, web search, and autonomous task scheduling.
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
                  >
                    {streamingContent}
                  </ReactMarkdown>
                </div>
              )}
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input area */}
      <div className="p-4 bg-gray-900 border-t border-gray-800">
        <div className="flex gap-2 items-end max-w-4xl mx-auto">
          <div className="flex-1 relative">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Message the agent... (Enter to send, Shift+Enter for newline)"
              rows={1}
              disabled={isStreaming}
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
              className="flex-shrink-0 w-10 h-10 bg-red-600 hover:bg-red-700 text-white rounded-xl flex items-center justify-center transition-colors"
            >
              <Square className="w-4 h-4" />
            </button>
          ) : (
            <button
              onClick={sendMessage}
              disabled={!input.trim()}
              className="flex-shrink-0 w-10 h-10 bg-brand-600 hover:bg-brand-700 disabled:opacity-40 disabled:cursor-not-allowed text-white rounded-xl flex items-center justify-center transition-colors"
            >
              <Send className="w-4 h-4" />
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
