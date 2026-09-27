import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { llmApi } from '../../api/client'
import InfoTooltip from '../help/InfoTooltip'

const WINDOWS = [
  { hours: 24, label: '24h' },
  { hours: 24 * 7, label: '7d' },
  { hours: 24 * 30, label: '30d' },
]

const pct = (x) => (x == null ? '—' : `${Math.round(x * 100)}%`)
const ms = (v) => (v == null ? '—' : v >= 1000 ? `${(v / 1000).toFixed(1)}s` : `${v}ms`)

// Phase 3: how routed chat turns turned out, and suggestions from that.
export default function RouterTuning({ onApplied }) {
  const [hours, setHours] = useState(24 * 7)
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(false)
  const [previews, setPreviews] = useState({})
  const [status, setStatus] = useState('')

  const load = async (h = hours) => {
    setLoading(true)
    try {
      setData(await llmApi.routerTuning(h))
    } catch {
      setData(null)
    } finally {
      setLoading(false)
    }
  }
  useEffect(() => { load(hours) }, [hours])

  const preview = async (rec) => {
    setPreviews((p) => ({ ...p, [rec.id]: { loading: true } }))
    try {
      const r = await llmApi.routerSimulate(rec.action.patch, Math.min(30, Math.max(1, hours / 24)))
      setPreviews((p) => ({ ...p, [rec.id]: r }))
    } catch (e) {
      setPreviews((p) => ({ ...p, [rec.id]: { error: e?.response?.data?.detail || e.message } }))
    }
  }

  const apply = async (rec) => {
    setStatus('')
    try {
      await llmApi.routerApply(rec.id, hours)
      setStatus(`Applied: ${rec.title}`)
      onApplied?.()
      await load()
    } catch (e) {
      setStatus(`Error: ${e?.response?.data?.detail || e.message}`)
    }
  }

  const recs = data?.recommendations || []
  const classes = data?.stats?.classes || []

  return (
    <section className='space-y-2'>
      <header className='flex items-center justify-between'>
        <h3 className='text-sm font-semibold text-gray-200'>
          Routing tuning
          <InfoTooltip text='Learns from how routed chat turns turned out. A "problem" is a turn that errored, hit the tool-step limit, got a push-back reply ("no, that&apos;s wrong…") or a re-pin with /model, or a 👎. Suggestions never apply themselves — Preview replays your recent chat messages through the router with the change to show which turns would move.' />
        </h3>
        <div className='flex items-center gap-1'>
          {WINDOWS.map((w) => (
            <button key={w.hours} type='button' onClick={() => setHours(w.hours)}
              className={`text-xs px-2 py-0.5 rounded ${hours === w.hours ? 'bg-gray-700 text-gray-100' : 'text-gray-400 hover:text-gray-200'}`}>
              {w.label}
            </button>
          ))}
          <button type='button' onClick={() => load()} title='Refresh' className='p-1 text-gray-400 hover:text-gray-200'>
            <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </header>

      {recs.length === 0 && data && (
        <div className='text-xs text-gray-500 italic'>No suggestions — routing looks healthy for this window.</div>
      )}

      <div className='space-y-2'>
        {recs.map((rec) => {
          const pv = previews[rec.id]
          return (
            <div key={rec.id}
              className={`border rounded-md px-3 py-2 text-xs ${rec.severity === 'warn'
                ? 'border-amber-700/60 bg-amber-950/30' : 'border-gray-700 bg-gray-900/40'}`}>
              <div className='flex items-start justify-between gap-2'>
                <div>
                  <div className={`font-medium ${rec.severity === 'warn' ? 'text-amber-300' : 'text-gray-200'}`}>{rec.title}</div>
                  <div className='text-gray-400 mt-0.5'>{rec.detail}</div>
                </div>
                {rec.action && (
                  <div className='flex gap-1 flex-shrink-0'>
                    {rec.action.kind === 'router_config' && (
                      <button type='button' onClick={() => preview(rec)}
                        className='px-2 py-0.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-200'>Preview</button>
                    )}
                    <button type='button' onClick={() => apply(rec)}
                      className='px-2 py-0.5 rounded bg-brand-700 hover:bg-brand-600 text-white'>Apply</button>
                  </div>
                )}
              </div>
              {pv && (
                <div className='mt-1.5 text-gray-400'>
                  {pv.loading && 'Replaying recent messages…'}
                  {pv.error && <span className='text-red-300'>{pv.error}</span>}
                  {pv.messages != null && (
                    <>
                      <div>
                        {pv.changed} of {pv.messages} recent chat messages would route differently
                        {Object.keys(pv.moves || {}).length > 0 && ` (${Object.entries(pv.moves).map(([k, v]) => `${k}: ${v}`).join(', ')})`}.
                      </div>
                      {(pv.examples || []).slice(0, 5).map((ex, i) => (
                        <div key={i} className='truncate text-gray-500' title={ex.text}>
                          {ex.from}→{ex.to}: “{ex.text}”
                        </div>
                      ))}
                    </>
                  )}
                </div>
              )}
            </div>
          )
        })}
      </div>

      {classes.length > 0 && (
        <div className='overflow-x-auto border border-gray-700 rounded-md'>
          <table className='w-full text-xs'>
            <thead className='bg-gray-900 text-gray-400'>
              <tr>
                {['Class', 'Turns', 'Problems', 'Used tools', 'Tool errors', 'Corrected', '👍/👎', 'p50', 'Fallback'].map((h) => (
                  <th key={h} className='text-left font-normal px-2 py-1 whitespace-nowrap'>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className='divide-y divide-gray-800'>
              {classes.map((c) => (
                <tr key={c.task_class} className='text-gray-300'>
                  <td className='px-2 py-1'>{c.task_class}</td>
                  <td className='px-2 py-1'>{c.turns}</td>
                  <td className={`px-2 py-1 ${(c.problem_rate || 0) > 0.2 ? 'text-amber-300' : ''}`}>{pct(c.problem_rate)}</td>
                  <td className='px-2 py-1'>{pct(c.tool_use_rate)}</td>
                  <td className='px-2 py-1'>{pct(c.tool_error_rate)}</td>
                  <td className='px-2 py-1'>{c.corrected}</td>
                  <td className='px-2 py-1 whitespace-nowrap'>{c.thumbs_up} / {c.thumbs_down}</td>
                  <td className='px-2 py-1'>{ms(c.p50_ms)}</td>
                  <td className='px-2 py-1'>{pct(c.fallback_rate)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {status && <div className={`text-xs ${status.startsWith('Error') ? 'text-red-300' : 'text-emerald-300'}`}>{status}</div>}
    </section>
  )
}
