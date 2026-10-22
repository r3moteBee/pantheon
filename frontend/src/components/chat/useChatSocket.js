import { useState, useRef, useEffect, useCallback } from 'react'
import { useStore } from '../../store'
import { createChatSocket } from '../../api/client'

// Chat WebSocket wiring. The socket itself is the module-level singleton in
// api/client.js (createChatSocket reuses an open one and re-binds its
// handlers), so it survives navigation; this hook only keeps a ref to it and
// turns server events into store updates plus the per-turn UI state below.
//
// Returns:
//   send(payload)          connect if needed, send now or on open
//   close()                close the socket (stop button)
//   liveRoute              current turn's model_route event (or null)
//   activeSkillBadge       skill name while a skill turn streams
//   pendingSuggestion      { skill, description, reason, suggestionId } | null
//   acceptSuggestion()     / declineSuggestion()
//   modelClass, setModelClass   chat mode = router pin ('auto' = unpinned),
//                               remembered per project in localStorage
const modeKey = (projectId) => `pantheon_chat_mode_${projectId}`
const loadMode = (projectId) => {
  try { return localStorage.getItem(modeKey(projectId)) || 'auto' } catch { return 'auto' }
}
const saveMode = (projectId, mode) => {
  try {
    if (!mode || mode === 'auto') localStorage.removeItem(modeKey(projectId))
    else localStorage.setItem(modeKey(projectId), mode)
  } catch { /* storage blocked: the mode just isn't remembered */ }
}

export default function useChatSocket() {
  const [activeSkillBadge, setActiveSkillBadge] = useState(null)
  const [pendingSuggestion, setPendingSuggestion] = useState(null)
  // Chat router: the chat mode ("auto" = let the router pick), sent with
  // every message so the backend pins the conversation to it, and the
  // current turn's route.
  const projectId = useStore((s) => s.activeProject?.id || 'default')
  const projectIdRef = useRef(projectId)
  projectIdRef.current = projectId
  const [modelClass, setModelClassState] = useState(() => loadMode(projectId))
  const setModelClass = useCallback((mode) => {
    setModelClassState(mode)
    saveMode(projectIdRef.current, mode)
  }, [])
  const [liveRoute, setLiveRoute] = useState(null)
  const liveRouteRef = useRef(null)
  const socketRef = useRef(null)

  const addMessage = useStore((s) => s.addMessage)
  const setIsStreaming = useStore((s) => s.setIsStreaming)
  const setStreamingContent = useStore((s) => s.setStreamingContent)
  const appendStreamingContent = useStore((s) => s.appendStreamingContent)
  const addToolCall = useStore((s) => s.addToolCall)
  const clearToolCalls = useStore((s) => s.clearToolCalls)
  const setSessionId = useStore((s) => s.setSessionId)
  const addNotification = useStore((s) => s.addNotification)

  // Each project keeps its own mode (new conversations and reloads included).
  useEffect(() => { setModelClassState(loadMode(projectId)) }, [projectId])

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
        case 'model_route':
          liveRouteRef.current = event
          setLiveRoute(event)
          break
        case 'model_pin':
          setModelClass(event.task_class || 'auto')
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
              route: event.route || liveRouteRef.current,
              timestamp: new Date().toISOString(),
            })
          }
          setStreamingContent('')
          clearToolCalls()
          setIsStreaming(false)
          setActiveSkillBadge(null)
          liveRouteRef.current = null
          setLiveRoute(null)
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

  const send = useCallback((payload) => {
    connectSocket()
    const sock = socketRef.current
    if (sock?.readyState === WebSocket.OPEN) {
      sock.send(payload)
    } else if (sock) {
      sock.onopen = () => sock.send(payload)
    }
  }, [connectSocket])

  const close = useCallback(() => { socketRef.current?.close() }, [])

  const answerSuggestion = useCallback((type) => {
    if (!pendingSuggestion) return
    setIsStreaming(true)
    setStreamingContent('')
    clearToolCalls()
    send(JSON.stringify({ type, suggestion_id: pendingSuggestion.suggestionId }))
    setPendingSuggestion(null)
  }, [pendingSuggestion, send])

  const acceptSuggestion = useCallback(() => answerSuggestion('skill_accept'), [answerSuggestion])
  const declineSuggestion = useCallback(() => answerSuggestion('skill_decline'), [answerSuggestion])

  return {
    send,
    close,
    liveRoute,
    activeSkillBadge,
    pendingSuggestion,
    acceptSuggestion,
    declineSuggestion,
    modelClass,
    setModelClass,
  }
}
