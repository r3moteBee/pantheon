import React, { useEffect, useState } from 'react'
import { ListTodo, RefreshCw, X, Check } from 'lucide-react'
import { taskRunsApi } from '../../api/client'

const STATUS_BADGE = {
  running:   'bg-blue-900 text-blue-200',
  completed: 'bg-green-900 text-green-200',
  failed:    'bg-red-900 text-red-200',
  cancelled: 'bg-amber-900 text-amber-200',
  queued:    'bg-gray-800 text-gray-300',
}

function fmtDuration(ms) {
  if (!ms) return ''
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms/1000).toFixed(1)}s`
  return `${Math.floor(ms/60_000)}m ${Math.floor((ms%60_000)/1000)}s`
}

export default function ProjectTasksPanel({ projectId }) {
  const [runs, setRuns] = useState([])
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('')

  const refresh = async () => {
    setLoading(true)
    try {
      const params = { project_id: projectId, limit: 100 }
      if (filter) params.status = filter
      const res = await taskRunsApi.list(params)
      setRuns(res.data?.runs || [])
    } finally { setLoading(false) }
  }

  useEffect(() => { refresh() }, [projectId, filter])

  return (
    <div className="h-full overflow-y-auto p-6 max-w-4xl mx-auto">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-lg font-semibold flex items-center gap-2">
          <ListTodo className="w-5 h-5" /> Tasks
        </h2>
        <div className="flex items-center gap-2">
          <select
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
            className="text-xs bg-gray-900 border border-gray-800 rounded px-2 py-1"
          >
            <option value="">All statuses</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <button onClick={refresh} className="text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1">
            <RefreshCw className="w-3 h-3" /> Refresh
          </button>
        </div>
      </div>
      {loading && <div className="text-xs text-gray-500">Loading…</div>}
      {!loading && runs.length === 0 && (
        <div className="text-sm text-gray-500 italic">
          No autonomous task runs yet for this project. Schedule one from the global tasks list in Settings.
        </div>
      )}
      <div className="space-y-2">
        {runs.map((r) => (
          <div key={r.id} className="p-3 rounded-md border border-gray-800 bg-gray-900">
            <div className="flex items-center gap-2">
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${STATUS_BADGE[r.status] || STATUS_BADGE.queued}`}>
                {r.status}
              </span>
              <span className="text-sm font-medium text-gray-200 truncate flex-1">{r.task_name}</span>
              <span className="text-[10px] text-gray-500">{r.started_at?.slice(0,16).replace('T',' ')}</span>
              {r.duration_ms != null && (
                <span className="text-[10px] text-gray-500">· {fmtDuration(r.duration_ms)}</span>
              )}
            </div>
            {r.description && <div className="text-xs text-gray-400 mt-1">{r.description}</div>}
            {r.error && <div className="text-xs text-red-400 mt-1">{r.error}</div>}
            {r.session_id && (
              <div className="text-[10px] text-gray-600 mt-1 font-mono">session: {r.session_id.slice(0,16)}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
