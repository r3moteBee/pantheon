import React from 'react'
import { History, X, Trash2, Search } from 'lucide-react'
import { useStore } from '../../store'
import { conversationsApi } from '../../api/client'
import Time from '../Time'

// Background sessions get their kind from the session id prefix the job
// handlers use (jobs/handlers/*: "iteration-<job>-t3-exec-ab12", ...).
const SESSION_KINDS = {
  iteration: { label: 'iteration', cls: 'bg-violet-950 text-violet-200 border-violet-800' },
  autonomous: { label: 'autonomous', cls: 'bg-sky-950 text-sky-200 border-sky-800' },
  coding: { label: 'coding', cls: 'bg-emerald-950 text-emerald-200 border-emerald-800' },
  research: { label: 'research', cls: 'bg-amber-950 text-amber-200 border-amber-800' },
  telegram: { label: 'telegram', cls: 'bg-gray-800 text-gray-200 border-gray-700' },
}

export function sessionKind(sessionId) {
  const prefix = String(sessionId || '').split('-')[0]
  return SESSION_KINDS[prefix] ? prefix : null
}

const oneLine = (text) => String(text || '')
  .replace(/\[Attached files[\s\S]*$/, '')
  .replace(/[`*_#>]+/g, '')
  .split('\n').map((l) => l.trim()).find(Boolean) || ''

/** A readable title: the stored title, else the first user message, else what the session is. */
export function conversationTitle(c) {
  const stored = (c.title || '').trim()
  if (stored && stored !== 'New Chat') return stored
  const first = c.first_user_message || ''
  const kind = sessionKind(c.session_id)
  if (kind === 'iteration') {
    const m = first.match(/turn (\d+) of (\d+) of an iterative development loop on '([^']+)'/i)
    const role = /-rev-/.test(c.session_id) ? 'review' : 'execute'
    if (m) return `Turn ${m[1]}/${m[2]} ${role} · ${m[3]}`
    const t = c.session_id.match(/-t(\d+)-/)
    if (t) return `Iteration turn ${t[1]} (${role})`
  }
  const line = oneLine(first)
  if (line) return line.length > 90 ? line.slice(0, 88).trimEnd() + '…' : line
  if (kind) return `${SESSION_KINDS[kind].label[0].toUpperCase()}${SESSION_KINDS[kind].label.slice(1)} session`
  return 'Untitled chat'
}

// Slide-over list of recent conversations for the active project; opening
// one replaces the chat with its persisted history.
export default function ChatHistoryDrawer() {
  const open = useStore((s) => s.historyOpen)
  const setOpen = useStore((s) => s.setHistoryOpen)
  const projectId = useStore((s) => s.activeProject?.id || 'default')
  const currentSessionId = useStore((s) => s.sessionId)
  const setSessionId = useStore((s) => s.setSessionId)
  const setMessages = useStore((s) => s.setMessages)
  const clearMessages = useStore((s) => s.clearMessages)
  const projects = useStore((s) => s.projects)
  const setActiveProject = useStore((s) => s.setActiveProject)
  const addNotification = useStore((s) => s.addNotification)
  const [items, setItems] = React.useState([])
  const [loading, setLoading] = React.useState(false)
  const [filter, setFilter] = React.useState('')
  const [confirming, setConfirming] = React.useState(null) // session id awaiting delete confirmation
  const filterRef = React.useRef(null)

  React.useEffect(() => {
    if (!open) return
    setLoading(true)
    setConfirming(null)
    conversationsApi.list(projectId, 100)
      .then((r) => setItems(r.data.conversations || []))
      .catch((e) => addNotification({ type: 'error', message: 'Could not load chat history: ' + e.message }))
      .finally(() => setLoading(false))
    setTimeout(() => filterRef.current?.focus(), 0)
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, projectId])

  React.useEffect(() => {
    if (!open) return
    const onKey = (e) => { if (e.key === 'Escape') { if (confirming) setConfirming(null); else setOpen(false) } }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, confirming, setOpen])

  const rows = React.useMemo(() => {
    const q = filter.trim().toLowerCase()
    return items
      .map((c) => ({ ...c, _title: conversationTitle(c), _kind: sessionKind(c.session_id) }))
      .filter((c) => !q || [c._title, c.first_user_message, c.session_id, c._kind].some((v) => (v || '').toLowerCase().includes(q)))
  }, [items, filter])

  const resume = async (sessionId) => {
    try {
      const res = await conversationsApi.resume(sessionId, projectId)
      const msgs = (res.data.messages || []).map((m) => ({
        role: m.role,
        content: m.content,
        timestamp: m.timestamp,
        route: m.metadata?.route,
      }))
      clearMessages()
      setMessages(msgs)
      setSessionId(sessionId)

      // Sync active project to match the session's project so the
      // pill never disagrees with the chat content. If the session's
      // project no longer exists in the loaded list, leave the pill
      // alone — chat content still loads.
      const sessionProjectId = res.data?.project_id
      if (sessionProjectId && sessionProjectId !== projectId) {
        const target = projects.find((p) => p.id === sessionProjectId)
        if (target) setActiveProject(target)
      }

      setOpen(false)
    } catch (e) {
      addNotification({ type: 'error', message: 'Resume failed: ' + e.message })
    }
  }

  const remove = async (sessionId) => {
    setConfirming(null)
    try {
      await conversationsApi.delete(sessionId)
      setItems((xs) => xs.filter((x) => x.session_id !== sessionId))
      if (sessionId === currentSessionId) { setSessionId(null); clearMessages() }
    } catch (e) {
      addNotification({ type: 'error', message: 'Delete failed: ' + e.message })
    }
  }

  if (!open) return null
  return (
    <div className="fixed inset-0 z-40 flex" role="dialog" aria-modal="true" aria-label="Chat history">
      <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
      <div className="ml-auto h-full w-full max-w-sm sm:w-96 bg-gray-950 border-l border-gray-800 shadow-xl flex flex-col z-50">
        <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold text-gray-100 flex items-center gap-2">
            <History className="w-4 h-4" aria-hidden="true" /> Recent conversations
          </h2>
          <button
            type="button"
            onClick={() => setOpen(false)}
            aria-label="Close chat history"
            title="Close"
            className="p-1 rounded text-gray-400 hover:text-gray-100 hover:bg-gray-800"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="px-3 py-2 border-b border-gray-800">
          <label className="relative block">
            <span className="sr-only">Filter conversations</span>
            <Search className="w-4 h-4 text-gray-400 absolute left-2.5 top-1/2 -translate-y-1/2" aria-hidden="true" />
            <input
              ref={filterRef}
              type="search"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter by title, message or type"
              className="w-full rounded-md bg-gray-900 border border-gray-700 pl-8 pr-2 py-1.5 text-sm text-gray-100 focus:border-brand-500"
            />
          </label>
        </div>
        <div className="flex-1 overflow-y-auto scrollbar-thin">
          {loading && <div className="p-4 text-xs text-gray-400">Loading…</div>}
          {!loading && items.length === 0 && (
            <div className="p-4 text-xs text-gray-400 italic">No prior conversations yet.</div>
          )}
          {!loading && items.length > 0 && rows.length === 0 && (
            <div className="p-4 text-xs text-gray-400 italic">No conversations match “{filter}”.</div>
          )}
          <ul>
            {rows.map((c) => {
              const current = c.session_id === currentSessionId
              const kind = c._kind && SESSION_KINDS[c._kind]
              return (
                <li
                  key={c.session_id}
                  className={`relative border-b border-gray-900 ${current ? 'bg-gray-800/70' : 'hover:bg-gray-900'}`}
                >
                  {current && <span className="absolute left-0 top-0 bottom-0 w-1 bg-brand-500" aria-hidden="true" />}
                  {confirming === c.session_id ? (
                    <div className="p-3 flex items-center gap-2 text-sm">
                      <span className="flex-1 min-w-0 text-gray-200 truncate">Delete “{c._title}”?</span>
                      <button
                        type="button"
                        onClick={() => remove(c.session_id)}
                        className="px-2 py-1 rounded bg-red-800 hover:bg-red-700 text-white text-xs whitespace-nowrap"
                        autoFocus
                      >
                        Delete
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirming(null)}
                        className="px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-100 text-xs whitespace-nowrap"
                      >
                        Cancel
                      </button>
                    </div>
                  ) : (
                    <div className="flex items-start gap-1 p-3 pl-4">
                      <button
                        type="button"
                        onClick={() => resume(c.session_id)}
                        aria-current={current ? 'true' : undefined}
                        title={c.session_id}
                        className="flex-1 min-w-0 text-left"
                      >
                        <div className="text-sm font-medium text-gray-100 truncate">{c._title}</div>
                        <div className="mt-0.5 flex flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-gray-400">
                          {current && <span className="text-brand-300 font-medium">Current ·</span>}
                          {kind && (
                            <span className={`px-1.5 rounded border ${kind.cls}`}>{kind.label}</span>
                          )}
                          <span>{c.message_count || 0} message{c.message_count === 1 ? '' : 's'}</span>
                          <span aria-hidden="true">·</span>
                          <Time value={c.updated_at} />
                        </div>
                      </button>
                      <button
                        type="button"
                        onClick={() => setConfirming(c.session_id)}
                        className="p-1.5 rounded text-gray-400 hover:text-red-300 hover:bg-gray-800"
                        title="Delete conversation"
                        aria-label={`Delete conversation: ${c._title}`}
                      >
                        <Trash2 className="w-4 h-4" />
                      </button>
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
        </div>
      </div>
    </div>
  )
}
