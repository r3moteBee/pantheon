import React from 'react'
import { History, X } from 'lucide-react'
import { useStore } from '../../store'
import { conversationsApi } from '../../api/client'

// Slide-over list of recent conversations for the active project; opening
// one replaces the chat with its persisted history.
export default function ChatHistoryDrawer() {
  const open = useStore((s) => s.historyOpen)
  const setOpen = useStore((s) => s.setHistoryOpen)
  const projectId = useStore((s) => s.activeProject?.id || 'default')
  const setSessionId = useStore((s) => s.setSessionId)
  const setMessages = useStore((s) => s.setMessages)
  const clearMessages = useStore((s) => s.clearMessages)
  const projects = useStore((s) => s.projects)
  const setActiveProject = useStore((s) => s.setActiveProject)
  const addNotification = useStore((s) => s.addNotification)
  const [items, setItems] = React.useState([])
  const [loading, setLoading] = React.useState(false)

  React.useEffect(() => {
    if (!open) return
    setLoading(true)
    conversationsApi.list(projectId, 50)
      .then((r) => setItems(r.data.conversations || []))
      .finally(() => setLoading(false))
  }, [open, projectId])

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
    if (!confirm('Delete this conversation?')) return
    await conversationsApi.delete(sessionId)
    setItems((xs) => xs.filter((x) => x.session_id !== sessionId))
  }

  if (!open) return null
  return (
    <div className="fixed inset-0 z-40 flex">
      <div className="absolute inset-0 bg-black/50" onClick={() => setOpen(false)} />
      <div className="ml-auto h-full w-96 bg-gray-950 border-l border-gray-800 shadow-xl overflow-y-auto z-50">
        <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
          <div className="text-sm font-semibold flex items-center gap-2">
            <History className="w-4 h-4" /> Recent conversations
          </div>
          <button onClick={() => setOpen(false)} className="text-gray-400 hover:text-gray-200">
            <X className="w-4 h-4" />
          </button>
        </div>
        {loading && <div className="p-4 text-xs text-gray-500">Loading…</div>}
        {!loading && items.length === 0 && (
          <div className="p-4 text-xs text-gray-500 italic">No prior conversations yet.</div>
        )}
        {items.map((c) => (
          <div key={c.session_id} className="p-3 border-b border-gray-900 hover:bg-gray-900">
            <div className="flex items-start justify-between gap-2">
              <button
                onClick={() => resume(c.session_id)}
                className="flex-1 text-left"
              >
                <div className="text-xs font-medium text-gray-200 truncate">
                  {c.title || `Chat ${c.session_id?.slice(0, 8)}`}
                </div>
                <div className="text-[10px] text-gray-500">
                  {c.message_count || 0} messages · last {c.updated_at?.slice(0, 16).replace('T', ' ')}
                </div>
              </button>
              <button
                onClick={() => remove(c.session_id)}
                className="text-gray-600 hover:text-red-400"
                title="Delete"
              >
                <X className="w-3 h-3" />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
