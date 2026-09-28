import { useEffect, useState } from 'react'
import { llmApi } from '../../api/client'
import InfoTooltip from '../help/InfoTooltip'

// Per-turn chat router settings + which chat classes are currently usable.
export default function ChatRouter({ refreshKey }) {
  const [view, setView] = useState(null)
  const [status, setStatus] = useState('')

  const load = async () => {
    try { setView(await llmApi.getRouter()) } catch { /* ignore */ }
  }
  useEffect(() => { load() }, [refreshKey])

  const patch = async (p) => {
    setStatus('')
    try {
      setView(await llmApi.setRouter(p))
    } catch (e) {
      setStatus(`Error: ${e.message}`)
    }
  }

  if (!view) return null
  const cfg = view.config

  return (
    <section className='space-y-2'>
      <h3 className='text-sm font-semibold text-gray-200'>
        Chat router
        <InfoTooltip text='Picks the model for each chat turn before the tool loop starts: /model pin → image the Agent model cannot see → context too long → skill preference → code/traceback → recent code turns → short message with no tool intent (Quick chat) → Agent. A rule only fires when its class has its own tool-capable model below, so with nothing extra configured every turn stays on Agent. Background jobs are not affected.' />
      </h3>

      <div className='flex flex-wrap items-center gap-4 text-xs text-gray-300'>
        <label className='flex items-center gap-1.5'>
          <input type='checkbox' checked={cfg.enabled} onChange={(e) => patch({ enabled: e.target.checked })} />
          Route chat turns automatically
        </label>
        <label className='flex items-center gap-1.5' title='When no rule decides, ask the Structured extraction model to classify the message (≤3s; any failure → Agent).'>
          <input type='checkbox' checked={cfg.classifier} disabled={!cfg.enabled}
            onChange={(e) => patch({ classifier: e.target.checked })} />
          LLM classifier for ambiguous turns
        </label>
        <label className='flex items-center gap-1.5'>
          Quick if ≤
          <input type='number' min='0' max='2000' value={cfg.quick_max_chars}
            onChange={(e) => setView({ ...view, config: { ...cfg, quick_max_chars: e.target.value } })}
            onBlur={(e) => patch({ quick_max_chars: Number(e.target.value) || 0 })}
            className='w-16 bg-gray-800 border border-gray-700 rounded px-1 py-0.5' />
          chars
        </label>
        <label className='flex items-center gap-1.5'>
          Stay on code for
          <input type='number' min='0' max='20' value={cfg.sticky_turns}
            onChange={(e) => setView({ ...view, config: { ...cfg, sticky_turns: e.target.value } })}
            onBlur={(e) => patch({ sticky_turns: Number(e.target.value) || 0 })}
            className='w-12 bg-gray-800 border border-gray-700 rounded px-1 py-0.5' />
          turns
        </label>
      </div>

      <div className='flex flex-wrap gap-2'>
        {view.classes.map((c) => (
          <span key={c.name} title={c.usable ? `${c.endpoint}/${c.model}` : c.reason}
            className={`text-[11px] px-2 py-0.5 rounded border ${c.usable
              ? 'border-emerald-700 text-emerald-300 bg-emerald-950/40'
              : 'border-gray-700 text-gray-500'}`}>
            {c.label}: {c.usable ? c.model : 'off'}
          </span>
        ))}
      </div>
      {status && <div className='text-xs text-red-300'>{status}</div>}
    </section>
  )
}
