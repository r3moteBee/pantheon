import { useEffect, useRef, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { useStore, readProjectConversation, writeProjectConversation } from '../store'
import { conversationsApi } from '../api/client'

/**
 * Keeps the open conversation across reloads and project switches (UI review #3).
 *
 * - The URL carries it as ?c=<session id> (shareable, survives reload).
 * - The store remembers the last conversation per project (setSessionId
 *   writes it), so /chat with no ?c reopens the project's last chat, and
 *   switching projects opens that project's last chat (or a fresh one).
 * - A ?c for a session of another project switches to that project.
 *
 * Mounted once by ChatTabs (all /chat tabs). Leaves the chat mode selector
 * alone: it only sets the session and its messages.
 */

const toMessages = (data) => (data?.messages || []).map((m) => ({
  role: m.role,
  content: m.content,
  timestamp: m.timestamp,
  route: m.metadata?.route,
}))

export default function useConversationPersistence() {
  const [params, setParams] = useSearchParams()
  const urlSession = params.get('c')
  const projectId = useStore((s) => s.activeProject?.id || 'default')
  const sessionId = useStore((s) => s.sessionId)
  const [ready, setReady] = useState(false)
  const loadSeq = useRef(0)
  const prev = useRef({ projectId, sessionId })
  const lastSession = useRef(sessionId)

  const setUrlSession = (sid) => setParams((p0) => {
    const p = new URLSearchParams(p0)
    if (sid) p.set('c', sid)
    else p.delete('c')
    return p
  }, { replace: true })

  // Load `sid` into the chat. `follow`: if the session belongs to another
  // project, switch to it (explicit links); otherwise treat the stored id as
  // stale and start fresh. Dropped if anything else changed the session
  // while the request was in flight.
  const open = async (sid, { follow }) => {
    const seq = ++loadSeq.current
    const st = useStore.getState()
    const startSession = st.sessionId
    const startProject = st.activeProject?.id || 'default'
    try {
      const res = await conversationsApi.resume(sid, startProject)
      const now = useStore.getState()
      if (seq !== loadSeq.current || now.sessionId !== startSession) return
      const owner = res.data?.project_id
      if (owner && owner !== startProject) {
        if (!follow) throw new Error('stale')
        const target = (now.projects || []).find((p) => p.id === owner) || { id: owner, name: '' }
        now.setActiveProject(target) // before setSessionId: it records under the active project
      }
      now.clearMessages()
      now.setMessages(toMessages(res.data))
      now.setSessionId(sid)
    } catch {
      const now = useStore.getState()
      if (seq !== loadSeq.current || now.sessionId !== startSession) return
      writeProjectConversation(startProject, null)
      if (now.sessionId) { now.setSessionId(null); now.clearMessages() }
      if (new URLSearchParams(window.location.search).get('c') === sid) setUrlSession(null)
    }
  }

  // First mount: URL wins, then the project's remembered conversation. If the
  // store still holds a chat of another project (switched on /projects), drop it.
  useEffect(() => {
    let cancelled = false
    const st = useStore.getState()
    const pid = st.activeProject?.id || 'default'
    const target = urlSession || readProjectConversation(pid)
    const finish = () => {
      if (cancelled) return
      const now = useStore.getState()
      prev.current = { projectId: now.activeProject?.id || 'default', sessionId: now.sessionId }
      lastSession.current = null // forces the URL to be synced once
      setReady(true)
    }
    if (target && target === st.sessionId) { finish(); return () => { cancelled = true } }
    if (!target) {
      if (st.sessionId) { st.setSessionId(null); st.clearMessages() }
      finish()
      return () => { cancelled = true }
    }
    open(target, { follow: !!urlSession }).finally(finish)
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Project switched with the conversation unchanged: open that project's
  // last conversation. Both changed together = a history resume that also
  // switched project: just record it.
  useEffect(() => {
    if (!ready) return
    const p = prev.current
    prev.current = { projectId, sessionId }
    if (p.projectId === projectId) return
    if (p.sessionId !== sessionId) {
      writeProjectConversation(projectId, sessionId)
      if (p.sessionId) writeProjectConversation(p.projectId, p.sessionId)
      return
    }
    const st = useStore.getState()
    if (st.isStreaming) return
    const stored = readProjectConversation(projectId)
    if (stored && stored === sessionId) return
    // setState, not setSessionId(null): that would forget the new project's chat.
    if (sessionId) { st.clearMessages(); useStore.setState({ sessionId: null }) }
    if (stored) open(stored, { follow: false })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, projectId, sessionId])

  // URL <-> session. A session change (first send, new chat, history) is
  // written to ?c; a ?c changed by navigation (back/forward, a link) is opened.
  useEffect(() => {
    if (!ready) return
    const sessionChanged = lastSession.current !== sessionId
    lastSession.current = sessionId
    if (sessionChanged || !urlSession) {
      if ((urlSession || null) !== (sessionId || null)) setUrlSession(sessionId)
      return
    }
    if (urlSession !== sessionId) open(urlSession, { follow: true })
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, urlSession, sessionId])
}
