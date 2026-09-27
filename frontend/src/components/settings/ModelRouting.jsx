import { useEffect, useState } from 'react'
import { ArrowUp, ArrowDown, X, Plus } from 'lucide-react'
import { llmApi } from '../../api/client'
import InfoTooltip from '../help/InfoTooltip'

// Capability flags shown as badges; `requires` on a task class names one.
const CAPS = [
  { key: 'tools', label: 'tools' },
  { key: 'vision', label: 'vision' },
  { key: 'image_gen', label: 'image gen' },
  { key: 'embedding', label: 'embedding' },
]

const profileKey = (e) => `${e.endpoint}/${e.model}`

export default function ModelRouting({ refreshKey, onSaved }) {
  const [classes, setClasses] = useState([])
  const [endpoints, setEndpoints] = useState([])
  const [routes, setRoutes] = useState({})
  const [profiles, setProfiles] = useState({})
  const [warnings, setWarnings] = useState([])
  const [models, setModels] = useState({}) // endpoint -> [model ids] from probe
  const [saving, setSaving] = useState(false)
  const [status, setStatus] = useState('')
  const [dirty, setDirty] = useState(false)

  const applyView = (v) => {
    setRoutes(v.routes || {})
    setProfiles(v.profiles || {})
    setWarnings(v.warnings || [])
  }

  const refresh = async () => {
    const [tc, eps, view] = await Promise.all([
      llmApi.taskClasses(), llmApi.listEndpoints(), llmApi.getRoutes(),
    ])
    setClasses(tc)
    setEndpoints(eps)
    applyView(view)
    setDirty(false)
  }

  useEffect(() => { refresh() }, [refreshKey])

  const loadProfile = async (entry) => {
    if (!entry.endpoint || !entry.model || profiles[profileKey(entry)]) return
    try {
      const p = await llmApi.getProfile(entry.endpoint, entry.model)
      setProfiles((prev) => ({ ...prev, [profileKey(entry)]: p }))
    } catch { /* ignore */ }
  }

  const updateClass = (cls, entries) => {
    setRoutes((prev) => ({ ...prev, [cls]: entries }))
    setDirty(true)
    setStatus('')
  }

  const fetchModels = async (endpoint) => {
    if (!endpoint || models[endpoint]) return
    const r = await llmApi.probe({ endpoint_name: endpoint })
    setModels((prev) => ({ ...prev, [endpoint]: r.ok ? r.models : [] }))
  }

  const save = async () => {
    setSaving(true)
    setStatus('')
    try {
      const clean = {}
      for (const [cls, entries] of Object.entries(routes)) {
        clean[cls] = entries.filter((e) => e.endpoint && e.model)
      }
      applyView(await llmApi.setRoutes(clean))
      setDirty(false)
      setStatus('Saved')
      onSaved?.()
    } catch (e) {
      setStatus(`Error: ${String(e?.message || e)}`)
    } finally {
      setSaving(false)
    }
  }

  const classById = Object.fromEntries(classes.map((c) => [c.id, c]))

  return (
    <section className='space-y-2'>
      <header className='flex items-center justify-between'>
        <h3 className='text-sm font-semibold text-gray-200'>
          Model routing
          <InfoTooltip text='Each kind of work (task class) uses an ordered list of models. The first is used normally; the rest are tried in order when it is rate-limited, down, or missing. Empty classes inherit from another class where noted.' />
        </h3>
        <span className='text-xs text-gray-500'>{endpoints.length} endpoints available</span>
      </header>

      <div className='border border-gray-700 rounded-md bg-gray-900/40 divide-y divide-gray-800'>
        {classes.map((tc) => (
          <ClassRow
            key={tc.id}
            tc={tc}
            parentLabel={tc.inherits ? classById[tc.inherits]?.label : null}
            entries={routes[tc.id] || []}
            endpoints={endpoints}
            models={models}
            profiles={profiles}
            onFetchModels={fetchModels}
            onLoadProfile={loadProfile}
            onChange={(entries) => updateClass(tc.id, entries)}
            onProfileSaved={applyView}
          />
        ))}
      </div>

      {warnings.length > 0 && (
        <ul className='text-xs text-amber-300 space-y-0.5'>
          {warnings.map((w) => <li key={w}>⚠ {w}</li>)}
        </ul>
      )}

      <div className='flex items-center gap-3'>
        <button
          type='button'
          onClick={save}
          disabled={saving || !dirty}
          className='text-xs px-3 py-1 rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50'
        >
          {saving ? 'Saving…' : 'Save routing'}
        </button>
        {status && (
          <span className={`text-xs ${status.startsWith('Error') ? 'text-red-300' : 'text-emerald-300'}`}>
            {status}
          </span>
        )}
      </div>
    </section>
  )
}

function ClassRow({
  tc, parentLabel, entries, endpoints, models, profiles,
  onFetchModels, onLoadProfile, onChange, onProfileSaved,
}) {
  const setEntry = (i, patch) => {
    const next = entries.map((e, j) => (j === i ? { ...e, ...patch } : e))
    onChange(next)
    const e = next[i]
    if (e.endpoint && e.model) onLoadProfile(e)
  }
  const move = (i, d) => {
    const next = [...entries]
    const [x] = next.splice(i, 1)
    next.splice(i + d, 0, x)
    onChange(next)
  }
  const canAdd = entries.length < (tc.max_entries || 1)

  return (
    <div className='px-3 py-2 space-y-1.5'>
      <div className='flex items-baseline justify-between gap-2'>
        <div>
          <span className='text-sm text-gray-200'>{tc.label}</span>
          <span className='ml-2 text-xs text-gray-500'>{tc.description}</span>
        </div>
        {canAdd && (
          <button
            type='button'
            onClick={() => onChange([...entries, { endpoint: '', model: '' }])}
            className='text-xs text-gray-400 hover:text-gray-200 inline-flex items-center gap-1 whitespace-nowrap'
          >
            <Plus className='w-3 h-3' /> {entries.length ? 'fallback' : 'model'}
          </button>
        )}
      </div>

      {entries.length === 0 && (
        <div className='text-xs text-gray-500 italic'>
          {parentLabel ? `Not set — uses ${parentLabel}` : 'Not configured'}
        </div>
      )}

      {entries.map((e, i) => (
        <EntryRow
          key={i}
          index={i}
          count={entries.length}
          entry={e}
          requires={tc.requires}
          endpoints={endpoints}
          modelList={models[e.endpoint]}
          profile={profiles[profileKey(e)]}
          onFetchModels={onFetchModels}
          onChange={(patch) => setEntry(i, patch)}
          onMove={(d) => move(i, d)}
          onRemove={() => onChange(entries.filter((_, j) => j !== i))}
          onProfileSaved={onProfileSaved}
        />
      ))}
    </div>
  )
}

function EntryRow({
  index, count, entry, requires, endpoints, modelList, profile,
  onFetchModels, onChange, onMove, onRemove, onProfileSaved,
}) {
  const [editing, setEditing] = useState(false)
  const missing = requires && profile && !profile[requires]

  return (
    <div className='space-y-1'>
      <div className='flex items-center gap-2'>
        <span className='text-[10px] w-14 text-gray-500 uppercase'>{index === 0 ? 'primary' : `fallback ${index}`}</span>
        <select
          value={entry.endpoint}
          onChange={(ev) => { onChange({ endpoint: ev.target.value, model: '' }); onFetchModels(ev.target.value) }}
          className='w-40 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-xs'
        >
          <option value=''>— endpoint —</option>
          {endpoints.map((ep) => <option key={ep.name} value={ep.name}>{ep.name}</option>)}
        </select>
        {modelList && modelList.length > 0 ? (
          <select
            value={entry.model}
            onChange={(ev) => onChange({ model: ev.target.value })}
            className='flex-1 min-w-0 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-xs'
          >
            <option value=''>— model —</option>
            {modelList.map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        ) : (
          <input
            type='text'
            value={entry.model}
            onChange={(ev) => onChange({ model: ev.target.value })}
            onFocus={() => onFetchModels(entry.endpoint)}
            placeholder='model id'
            disabled={!entry.endpoint}
            className='flex-1 min-w-0 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-xs disabled:opacity-50'
          />
        )}
        <button type='button' title='Move up' disabled={index === 0} onClick={() => onMove(-1)}
          className='p-1 text-gray-400 hover:text-gray-200 disabled:opacity-30'><ArrowUp className='w-3 h-3' /></button>
        <button type='button' title='Move down' disabled={index === count - 1} onClick={() => onMove(1)}
          className='p-1 text-gray-400 hover:text-gray-200 disabled:opacity-30'><ArrowDown className='w-3 h-3' /></button>
        <button type='button' title='Remove' onClick={onRemove}
          className='p-1 text-gray-400 hover:text-red-300'><X className='w-3 h-3' /></button>
      </div>

      {profile && (
        <div className='flex items-center gap-1 flex-wrap pl-16'>
          <span className={`text-[10px] px-1.5 rounded ${tierColor(profile.tier)}`}>{profile.tier}</span>
          {profile.context_window && (
            <span className='text-[10px] px-1.5 rounded bg-gray-800 text-gray-400'>{fmtCtx(profile.context_window)} ctx</span>
          )}
          {CAPS.filter((c) => profile[c.key]).map((c) => (
            <span key={c.key} className='text-[10px] px-1.5 rounded bg-gray-800 text-gray-300'>{c.label}</span>
          ))}
          {missing && <span className='text-[10px] text-amber-300'>missing {requires.replace('_', ' ')}</span>}
          <span className='text-[10px] text-gray-600'>
            {profile.source === 'user' ? 'edited' : profile.source === 'known' ? 'auto-detected' : 'unknown model'}
          </span>
          <button type='button' onClick={() => setEditing((v) => !v)} className='text-[10px] text-brand-400 hover:text-brand-300'>
            {editing ? 'close' : 'edit'}
          </button>
        </div>
      )}
      {editing && profile && (
        <ProfileEditor entry={entry} profile={profile} onSaved={(v) => { onProfileSaved(v); setEditing(false) }} />
      )}
    </div>
  )
}

function ProfileEditor({ entry, profile, onSaved }) {
  const [p, setP] = useState(profile)
  const [busy, setBusy] = useState(false)
  const set = (k, v) => setP((prev) => ({ ...prev, [k]: v }))

  const save = async () => {
    setBusy(true)
    try {
      const { source, ...body } = p
      onSaved(await llmApi.saveProfile(entry.endpoint, entry.model, {
        ...body, context_window: body.context_window ? Number(body.context_window) : null,
      }))
    } finally { setBusy(false) }
  }
  const reset = async () => {
    setBusy(true)
    try { onSaved(await llmApi.resetProfile(entry.endpoint, entry.model)) } finally { setBusy(false) }
  }

  return (
    <div className='ml-16 p-2 rounded border border-gray-700 bg-gray-900 flex flex-wrap items-center gap-3 text-xs'>
      <label className='flex items-center gap-1'>tier
        <select value={p.tier} onChange={(e) => set('tier', e.target.value)}
          className='bg-gray-800 border border-gray-600 rounded px-1 py-0.5'>
          {['fast', 'standard', 'frontier'].map((t) => <option key={t}>{t}</option>)}
        </select>
      </label>
      <label className='flex items-center gap-1'>context
        <input type='number' value={p.context_window || ''} onChange={(e) => set('context_window', e.target.value)}
          className='w-24 bg-gray-800 border border-gray-600 rounded px-1 py-0.5' placeholder='tokens' />
      </label>
      {CAPS.map((c) => (
        <label key={c.key} className='flex items-center gap-1'>
          <input type='checkbox' checked={!!p[c.key]} onChange={(e) => set(c.key, e.target.checked)} />
          {c.label}
        </label>
      ))}
      <input type='text' value={p.notes || ''} onChange={(e) => set('notes', e.target.value)}
        placeholder='notes' className='flex-1 min-w-[8rem] bg-gray-800 border border-gray-600 rounded px-1 py-0.5' />
      <button type='button' disabled={busy} onClick={save}
        className='px-2 py-0.5 rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50'>Save</button>
      {profile.source === 'user' && (
        <button type='button' disabled={busy} onClick={reset}
          className='px-2 py-0.5 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-50'>Reset to auto</button>
      )}
    </div>
  )
}

function tierColor(tier) {
  if (tier === 'frontier') return 'bg-purple-900/60 text-purple-200'
  if (tier === 'fast') return 'bg-sky-900/60 text-sky-200'
  return 'bg-gray-800 text-gray-300'
}

function fmtCtx(n) {
  return n >= 1_000_000 ? `${(n / 1_000_000).toFixed(n % 1_000_000 ? 1 : 0)}M` : `${Math.round(n / 1000)}k`
}
