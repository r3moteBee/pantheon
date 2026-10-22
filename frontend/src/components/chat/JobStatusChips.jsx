import React, { useEffect, useMemo, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, XCircle, Loader2, Clock, Ban, AlertTriangle } from 'lucide-react'
import { jobsApi } from '../../api/client'

// Live status of background jobs an assistant message mentions (UI review
// #6): a reply can say "done" while the job behind it was cancelled. Job
// ids show up as full UUIDs ("job_id: 6a4754c3-...", tool results) or as
// 8-char prefixes / schedule ids ("schedule_id: 9c07f79d",
// "iteration/6a4754c3/"); both are matched against the project's jobs,
// so random hex in a reply only gets a chip when it IS a job here.

const UUID = /\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi
const SHORT = /(?:^|[^0-9a-z-])([0-9a-f]{8})(?![0-9a-z-])/gi

/** Candidate job/schedule ids in some text, as lower-case 8-char prefixes (deduped, in order). */
export function jobIdCandidates(text) {
  if (!text) return []
  const out = []
  const add = (id) => { const k = id.toLowerCase().slice(0, 8); if (!out.includes(k)) out.push(k) }
  for (const m of text.matchAll(UUID)) add(m[0])
  for (const m of text.matchAll(SHORT)) {
    // An all-letter or all-digit word ("deadbeef", "20261022") is rarely an id.
    if (/[0-9]/.test(m[1]) && /[a-f]/i.test(m[1])) add(m[1])
  }
  return out.slice(0, 12)
}

// One poller per project shared by every chip on screen.
const ACTIVE = new Set(['queued', 'running'])
const POLL_MS = 10_000
const stores = new Map() // projectId -> { jobs, subs:Set, timer, loading }

function storeFor(projectId) {
  let s = stores.get(projectId)
  if (!s) { s = { jobs: null, subs: new Set(), timer: null, loading: false, fetchedAt: 0 }; stores.set(projectId, s) }
  return s
}

async function refresh(projectId) {
  const s = storeFor(projectId)
  if (s.loading) return
  s.loading = true
  try {
    const r = await jobsApi.list({ project_id: projectId, limit: 300 })
    s.jobs = r.data?.jobs || []
    s.fetchedAt = Date.now()
  } catch {
    s.jobs = s.jobs || []
  } finally {
    s.loading = false
  }
  s.subs.forEach((fn) => fn(s.jobs))
  schedule(projectId)
}

function schedule(projectId) {
  const s = storeFor(projectId)
  clearTimeout(s.timer)
  s.timer = null
  // Keep polling only while something on screen can still change.
  if (s.subs.size && (s.jobs || []).some((j) => ACTIVE.has(j.status))) {
    s.timer = setTimeout(() => {
      if (document.visibilityState === 'hidden') schedule(projectId)
      else refresh(projectId)
    }, POLL_MS)
  }
}

function useProjectJobs(projectId, candidates) {
  const [jobs, setJobs] = useState(() => storeFor(projectId).jobs)
  const key = candidates.join(',')
  useEffect(() => {
    if (!key) return
    const s = storeFor(projectId)
    s.subs.add(setJobs)
    // Fetch when nothing is cached yet, or when this message names an id
    // the cached list doesn't know (a job created after the last fetch).
    const unknown = !s.jobs || resolveJobs(candidates, s.jobs).length < candidates.length
    if (!s.jobs || (unknown && Date.now() - s.fetchedAt > 5000)) refresh(projectId)
    else { setJobs(s.jobs); if (!s.timer) schedule(projectId) }
    return () => {
      s.subs.delete(setJobs)
      if (!s.subs.size) { clearTimeout(s.timer); s.timer = null }
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, key])
  return jobs
}

/** The job each candidate refers to: a job id prefix, else the latest run of a schedule id. */
export function resolveJobs(candidates, jobs) {
  if (!jobs?.length) return []
  const found = []
  for (const c of candidates) {
    let job = jobs.find((j) => (j.id || '').toLowerCase().startsWith(c))
    if (!job) {
      job = jobs
        .filter((j) => (j.schedule_id || '').toLowerCase() === c)
        .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)))[0]
    }
    if (job && !found.some((f) => f.id === job.id)) found.push(job)
  }
  return found
}

const STYLE = {
  queued:    { icon: Clock,         cls: 'border-gray-600 bg-gray-800 text-gray-200',            label: 'queued' },
  running:   { icon: Loader2,       cls: 'border-sky-700 bg-sky-950/60 text-sky-200',            label: 'running', spin: true },
  completed: { icon: CheckCircle2,  cls: 'border-emerald-700 bg-emerald-950/60 text-emerald-200', label: 'completed' },
  cancelled: { icon: Ban,           cls: 'border-amber-700 bg-amber-950/60 text-amber-200',      label: 'cancelled' },
  failed:    { icon: XCircle,       cls: 'border-red-700 bg-red-950/60 text-red-200',            label: 'failed' },
  stalled:   { icon: AlertTriangle, cls: 'border-amber-700 bg-amber-950/60 text-amber-200',      label: 'stalled' },
}

function JobChip({ job }) {
  const st = STYLE[job.status] || STYLE.queued
  const Icon = st.icon
  const name = job.title || job.job_type || 'Task'
  const tip = `${name} (${job.id.slice(0, 8)}): ${st.label}` + (job.error ? ` - ${String(job.error).slice(0, 200)}` : '') + '. Open in Tasks.'
  return (
    <Link
      to={`/chat?tab=tasks&job=${encodeURIComponent(job.id)}`}
      title={tip}
      aria-label={tip}
      className={`inline-flex max-w-full items-center gap-1.5 rounded-full border px-2 py-0.5 text-xs whitespace-nowrap hover:brightness-125 ${st.cls}`}
    >
      <Icon className={`w-3.5 h-3.5 shrink-0 ${st.spin ? 'animate-spin' : ''}`} aria-hidden="true" />
      <span className="truncate max-w-[16rem]">{name}</span>
      <span className="font-medium">· {st.label}</span>
    </Link>
  )
}

/** Chips for the jobs referenced by `text` (message content + tool results) in `projectId`. */
export default function JobStatusChips({ text, projectId }) {
  const candidates = useMemo(() => jobIdCandidates(text), [text])
  const jobs = useProjectJobs(projectId, candidates)
  const matched = useMemo(() => resolveJobs(candidates, jobs), [candidates, jobs])
  if (!matched.length) return null
  return (
    <div className="mt-1.5 flex flex-wrap gap-1.5" aria-label="Background tasks mentioned in this reply">
      {matched.map((j) => <JobChip key={j.id} job={j} />)}
    </div>
  )
}
