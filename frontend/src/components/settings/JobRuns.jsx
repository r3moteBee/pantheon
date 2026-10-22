import React, { useState, useEffect } from 'react'
import { RefreshCw, Clock } from 'lucide-react'
import { jobsApi } from '../../api/client'
import useProjectNames from './useProjectNames'
import { useStore } from '../../store'
import Time from '../Time'

// ── Cross-project job dashboard (uses /api/jobs) ─────────────────────────────
const RUN_STATUS_BADGE = {
  running:   'bg-blue-900 text-blue-200',
  completed: 'bg-green-900 text-green-200',
  failed:    'bg-red-900 text-red-200',
  stalled:   'bg-amber-900 text-amber-200',
  cancelled: 'bg-amber-950 text-amber-300',
  queued:    'bg-gray-800 text-gray-300',
}

function fmtDuration(ms) {
  if (!ms && ms !== 0) return ''
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms/1000).toFixed(1)}s`
  return `${Math.floor(ms/60_000)}m ${Math.floor((ms%60_000)/1000)}s`
}

export default function JobRuns() {
  const [runs, setRuns] = useState([])
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const projectName = useProjectNames()

  const refresh = async () => {
    setLoading(true)
    try {
      const params = { limit: 100, include_system: true }
      if (filter) params.status = filter
      const res = await jobsApi.list(params)
      setRuns(res.data?.jobs || [])
    } catch (e) {
      useStore.getState().addNotification({ type: 'error', message: `Couldn't load job runs: ${e.message}` })
    } finally { setLoading(false) }
  }
  useEffect(() => { refresh() }, [filter])

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Clock className="w-4 h-4 text-brand-400" />
          Job runs (cross-project, all types)
        </h3>
        <div className="flex items-center gap-2">
          <select
            value={filter} onChange={(e) => setFilter(e.target.value)}
            aria-label="Filter job runs by status"
            className="text-xs bg-gray-900 border border-gray-700 rounded px-2 py-1"
          >
            <option value="">All statuses</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <button onClick={refresh} className="text-xs text-gray-300 hover:text-gray-100 flex items-center gap-1 whitespace-nowrap">
            <RefreshCw className="w-3 h-3" /> Refresh
          </button>
        </div>
      </div>
      {loading && <div className="text-xs text-gray-400">Loading…</div>}
      {!loading && runs.length === 0 && (
        <div className="text-sm text-gray-400 italic">
          No autonomous task runs yet across any project.
        </div>
      )}
      <div className="space-y-1">
        {runs.map((r) => {
          const dur = (r.started_at && r.completed_at) ? (new Date(r.completed_at) - new Date(r.started_at)) : null
          return (
            <div key={r.id} className="p-2.5 rounded border border-gray-800 bg-gray-900">
              <div className="flex items-center gap-2 flex-wrap">
                <span className={`text-xs px-1.5 py-0.5 rounded ${RUN_STATUS_BADGE[r.status] || RUN_STATUS_BADGE.queued}`}>
                  {r.status}
                </span>
                <span className="text-xs px-1.5 py-0.5 rounded bg-brand-900 text-brand-200">
                  {projectName(r.project_id)}
                </span>
                <span className="text-xs px-1.5 py-0.5 rounded bg-gray-800 text-gray-300">
                  {r.job_type}
                </span>
                <span className="text-sm font-medium text-gray-200 truncate flex-1">{r.title || r.task_name || '(untitled)'}</span>
                <span className="text-xs text-gray-400 whitespace-nowrap"><Time value={r.started_at} /></span>
                {dur != null && (
                  <span className="text-xs text-gray-400">· {fmtDuration(dur)}</span>
                )}
              </div>
              {r.progress && r.status === 'running' && (
                <div className="text-xs text-gray-400 mt-1">{r.progress}</div>
              )}
              {r.description && <div className="text-xs text-gray-400 mt-1">{r.description}</div>}
              {r.status === 'cancelled' && (r.cancel_reason || r.error)
                ? <div className="text-xs text-amber-200 mt-1">Cancelled: {r.cancel_reason || r.error}</div>
                : r.error && <div className="text-xs text-red-300 mt-1">{r.error}</div>}
              {r.session_id && (
                <div className="text-xs text-gray-400 mt-1 font-mono">session: {r.session_id.slice(0,16)}</div>
              )}
              {r.pr_url && (
                <a href={r.pr_url} target="_blank" rel="noreferrer" className="text-xs text-brand-300 underline mt-1 inline-block">
                  {r.pr_url}
                </a>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}
