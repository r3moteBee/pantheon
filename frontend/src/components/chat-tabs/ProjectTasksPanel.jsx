import React, { useEffect, useState } from 'react'
import {
  ListTodo, RefreshCw, X, Check, Clock, AlertTriangle, RotateCcw, Trash2,
  ExternalLink, FileText,
} from 'lucide-react'
import { jobsApi, tasksApi } from '../../api/client'

const STATUS_BADGE = {
  running:   'bg-blue-900 text-blue-200',
  queued:    'bg-gray-800 text-gray-300',
  completed: 'bg-green-900 text-green-200',
  failed:    'bg-red-900 text-red-200',
  stalled:   'bg-amber-900 text-amber-200',
  cancelled: 'bg-amber-950 text-amber-300',
}

const SYSTEM_TYPES = new Set(['extraction', 'file_indexing'])

function fmtDuration(ms) {
  if (ms == null) return ''
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms/1000).toFixed(1)}s`
  return `${Math.floor(ms/60000)}m ${Math.floor((ms%60000)/1000)}s`
}

function durationOf(j) {
  if (!j.started_at) return null
  const end = j.completed_at ? new Date(j.completed_at) : new Date()
  const start = new Date(j.started_at)
  return end - start
}

export default function ProjectTasksPanel({ projectId }) {
  const [jobs, setJobs] = useState([])
  const [schedules, setSchedules] = useState([])
  const [loading, setLoading] = useState(true)
  const [statusFilter, setStatusFilter] = useState('')
  const [includeSystem, setIncludeSystem] = useState(false)
  const [selectedJob, setSelectedJob] = useState(null)

  const refresh = async () => {
    setLoading(true)
    try {
      const params = {
        project_id: projectId,
        include_system: includeSystem,
        limit: 100,
      }
      if (statusFilter) params.status = statusFilter
      const [jobsRes, schedRes] = await Promise.all([
        jobsApi.list(params),
        tasksApi.listAll().catch(() => ({ data: { tasks: [] } })),
      ])
      setJobs(jobsRes.data?.jobs || [])
      // Filter scheduled tasks to this project
      const allSched = schedRes.data?.tasks || []
      setSchedules(allSched.filter((t) => (t.project_id || 'default') === projectId))
    } finally { setLoading(false) }
  }

  useEffect(() => { refresh() }, [projectId, statusFilter, includeSystem])

  const cancel = async (id) => {
    if (!confirm('Cancel this job?')) return
    await jobsApi.cancel(id); await refresh()
  }
  const retry = async (id) => {
    const res = await jobsApi.retry(id)
    setSelectedJob(res.data)
    await refresh()
  }
  const remove = async (id) => {
    if (!confirm('Delete this job record?')) return
    await jobsApi.delete(id); await refresh()
    if (selectedJob?.id === id) setSelectedJob(null)
  }

  return (
    <div className="h-full flex">
      {/* Main column */}
      <div className="flex-1 overflow-y-auto p-6 max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-semibold flex items-center gap-2">
            <ListTodo className="w-5 h-5" /> Tasks
          </h2>
          <div className="flex items-center gap-2">
            <select
              value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}
              className="text-xs bg-gray-900 border border-gray-800 rounded px-2 py-1"
            >
              <option value="">All statuses</option>
              <option value="queued">Queued</option>
              <option value="running">Running</option>
              <option value="completed">Completed</option>
              <option value="failed">Failed</option>
              <option value="stalled">Stalled</option>
              <option value="cancelled">Cancelled</option>
            </select>
            <label className="text-xs text-gray-400 flex items-center gap-1" title="Show extraction/file_indexing rows">
              <input type="checkbox" checked={includeSystem}
                     onChange={(e) => setIncludeSystem(e.target.checked)} />
              system jobs
            </label>
            <button onClick={refresh} className="text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1">
              <RefreshCw className="w-3 h-3" /> Refresh
            </button>
          </div>
        </div>

        {/* Schedules section */}
        <section className="mb-6">
          <h3 className="text-xs font-semibold text-gray-400 uppercase mb-2 flex items-center gap-2">
            <Clock className="w-3 h-3" /> Schedules ({schedules.length})
          </h3>
          {schedules.length === 0 && (
            <div className="text-xs text-gray-500 italic">
              No scheduled tasks for this project. Schedule one with the agent or via /api/tasks/create.
            </div>
          )}
          {schedules.map((s) => (
            <div key={s.id} className="p-2 mb-1 rounded border border-gray-800 bg-gray-900 flex items-center gap-2 text-xs">
              <span className="font-medium text-gray-200 truncate flex-1">{s.name}</span>
              <span className="text-gray-500 truncate">{s.schedule || s.trigger}</span>
              <span className="text-gray-500">next: {s.next_run?.slice(0,16).replace('T',' ') || '(none)'}</span>
            </div>
          ))}
        </section>

        {/* Job runs */}
        <section>
          <h3 className="text-xs font-semibold text-gray-400 uppercase mb-2">
            Job runs ({jobs.length})
          </h3>
          {loading && <div className="text-xs text-gray-500">Loading…</div>}
          {!loading && jobs.length === 0 && (
            <div className="text-sm text-gray-500 italic">No job runs in this project yet.</div>
          )}
          <div className="space-y-1">
            {jobs.map((j) => {
              const dur = durationOf(j)
              const isActive = selectedJob?.id === j.id
              return (
                <button
                  key={j.id}
                  onClick={() => setSelectedJob(j)}
                  className={`w-full text-left p-2.5 rounded border ${
                    isActive ? 'border-brand-700 bg-brand-950' : 'border-gray-800 bg-gray-900'
                  } hover:bg-gray-800`}
                >
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded ${STATUS_BADGE[j.status] || STATUS_BADGE.queued}`}>
                      {j.status}
                    </span>
                    <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-300">
                      {j.job_type}
                    </span>
                    {SYSTEM_TYPES.has(j.job_type) && (
                      <span className="text-[10px] text-gray-600">system</span>
                    )}
                    <span className="text-sm font-medium text-gray-200 truncate flex-1">
                      {j.title || '(untitled)'}
                    </span>
                    <span className="text-[10px] text-gray-500">{j.started_at?.slice(0,16).replace('T',' ')}</span>
                    {dur != null && j.status !== 'running' && (
                      <span className="text-[10px] text-gray-500">· {fmtDuration(dur)}</span>
                    )}
                  </div>
                  {j.progress && j.status === 'running' && (
                    <div className="text-xs text-gray-400 mt-1 truncate">
                      {j.progress}
                    </div>
                  )}
                  {j.error && (
                    <div className="text-xs text-red-400 mt-1 truncate flex items-start gap-1">
                      <AlertTriangle className="w-3 h-3 flex-shrink-0 mt-0.5" /> {j.error}
                    </div>
                  )}
                  {j.pr_url && (
                    <div className="text-[10px] text-brand-300 mt-1 truncate">{j.pr_url}</div>
                  )}
                </button>
              )
            })}
          </div>
        </section>
      </div>

      {/* Side drawer */}
      {selectedJob && (
        <JobDetail
          job={selectedJob}
          onClose={() => setSelectedJob(null)}
          onCancel={() => cancel(selectedJob.id)}
          onRetry={() => retry(selectedJob.id)}
          onDelete={() => remove(selectedJob.id)}
          onRefresh={refresh}
        />
      )}
    </div>
  )
}


function JobDetail({ job, onClose, onCancel, onRetry, onDelete, onRefresh }) {
  const [fresh, setFresh] = useState(job)
  useEffect(() => { setFresh(job) }, [job?.id])
  const reload = async () => {
    const res = await jobsApi.get(job.id)
    setFresh(res.data)
    onRefresh?.()
  }

  const j = fresh || job
  const result = j.result && Object.keys(j.result).length > 0 ? j.result : null

  return (
    <div className="w-96 border-l border-gray-800 bg-gray-950 overflow-y-auto p-4 text-xs">
      <div className="flex items-center justify-between mb-2">
        <span className={`text-[10px] px-1.5 py-0.5 rounded ${STATUS_BADGE[j.status] || STATUS_BADGE.queued}`}>
          {j.status}
        </span>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-200">
          <X className="w-4 h-4" />
        </button>
      </div>
      <h3 className="text-sm font-medium text-gray-100 mb-1">{j.title || '(untitled)'}</h3>
      <div className="text-[10px] text-gray-500 font-mono break-all mb-3">
        {j.id}  · {j.job_type}
      </div>

      <KV label="Project" value={j.project_id} />
      <KV label="Created" value={j.created_at?.replace('T', ' ').slice(0, 19)} />
      <KV label="Started" value={j.started_at?.replace('T', ' ').slice(0, 19) || '(not yet)'} />
      <KV label="Completed" value={j.completed_at?.replace('T', ' ').slice(0, 19) || '(running)'} />
      <KV label="Attempts" value={`${j.attempts} / ${j.max_attempts}`} />
      {j.timeout_seconds && <KV label="Timeout" value={`${j.timeout_seconds}s`} />}
      {j.last_heartbeat_at && (
        <KV label="Last heartbeat" value={j.last_heartbeat_at.replace('T',' ').slice(0,19)} />
      )}

      {j.progress && (
        <Section title="Progress">
          <pre className="whitespace-pre-wrap text-gray-300 break-words">{j.progress}</pre>
        </Section>
      )}

      {j.error && (
        <Section title="Error" tone="danger">
          <pre className="whitespace-pre-wrap text-red-300 break-words">{j.error}</pre>
        </Section>
      )}

      {result && (
        <Section title="Result">
          <pre className="whitespace-pre-wrap text-gray-300 break-words">{JSON.stringify(result, null, 2)}</pre>
        </Section>
      )}

      <Section title="Cross-references">
        {j.session_id && <KV label="session" value={<code className="text-brand-300 break-all">{j.session_id}</code>} />}
        {j.artifact_id && (
          <KV label="artifact" value={
            <a href={`/artifacts?tab=`} className="text-brand-400 underline flex items-center gap-1">
              <FileText className="w-3 h-3" /> {j.artifact_id.slice(0,8)}
            </a>
          } />
        )}
        {j.pr_url && (
          <KV label="PR" value={
            <a href={j.pr_url} target="_blank" rel="noreferrer" className="text-brand-400 underline flex items-center gap-1">
              <ExternalLink className="w-3 h-3" /> {j.pr_url.split('/').slice(-2).join('/')}
            </a>
          } />
        )}
      </Section>

      <div className="flex gap-2 pt-3 border-t border-gray-800 mt-3 flex-wrap">
        <button onClick={reload} className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 flex items-center gap-1">
          <RefreshCw className="w-3 h-3" /> Reload
        </button>
        {(j.status === 'queued' || j.status === 'running') && (
          <button onClick={onCancel} className="px-2 py-1 rounded bg-amber-900 hover:bg-amber-800 text-amber-100">
            Cancel
          </button>
        )}
        {(j.status === 'failed' || j.status === 'stalled' || j.status === 'cancelled') && (
          <button onClick={onRetry} className="px-2 py-1 rounded bg-brand-700 hover:bg-brand-600 text-white flex items-center gap-1">
            <RotateCcw className="w-3 h-3" /> Retry
          </button>
        )}
        <button onClick={onDelete} className="ml-auto px-2 py-1 rounded text-gray-500 hover:text-red-400 flex items-center gap-1">
          <Trash2 className="w-3 h-3" /> Delete record
        </button>
      </div>
    </div>
  )
}


function KV({ label, value }) {
  return (
    <div className="flex items-start gap-2 mb-1">
      <span className="text-gray-500 w-20 flex-shrink-0">{label}</span>
      <span className="text-gray-300 break-words flex-1 min-w-0">{value}</span>
    </div>
  )
}


function Section({ title, children, tone }) {
  const ring = tone === 'danger' ? 'border-red-900/50' : 'border-gray-800'
  return (
    <div className={`mt-3 pt-2 border-t ${ring}`}>
      <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">{title}</div>
      {children}
    </div>
  )
}
