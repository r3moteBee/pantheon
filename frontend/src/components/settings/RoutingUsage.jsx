import { useEffect, useState } from 'react'
import { RefreshCw } from 'lucide-react'
import { llmApi } from '../../api/client'
import InfoTooltip from '../help/InfoTooltip'

const WINDOWS = [
  { hours: 24, label: '24h' },
  { hours: 24 * 7, label: '7d' },
  { hours: 24 * 30, label: '30d' },
]

export default function RoutingUsage() {
  const [hours, setHours] = useState(24)
  const [rows, setRows] = useState([])
  const [loading, setLoading] = useState(false)

  const load = async (h = hours) => {
    setLoading(true)
    try {
      setRows((await llmApi.usage(h)).rows || [])
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load(hours) }, [hours])

  return (
    <section className='space-y-2'>
      <header className='flex items-center justify-between'>
        <h3 className='text-sm font-semibold text-gray-200'>
          Model usage
          <InfoTooltip text='Every routed LLM call is logged (one row per attempt). "Fallback" counts calls this model served after an earlier model failed. Use this to judge whether a class is on the right model.' />
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

      {rows.length === 0 ? (
        <div className='text-xs text-gray-500 italic'>No routed calls in this window yet.</div>
      ) : (
        <div className='overflow-x-auto border border-gray-700 rounded-md'>
          <table className='w-full text-xs'>
            <thead className='bg-gray-900 text-gray-400'>
              <tr>
                {['Class', 'Model', 'Calls', 'Errors', 'Fallback', 'p50', 'p95', 'Tokens in/out'].map((h) => (
                  <th key={h} className='text-left font-normal px-2 py-1 whitespace-nowrap'>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody className='divide-y divide-gray-800'>
              {rows.map((r) => (
                <tr key={`${r.task_class}|${r.endpoint}|${r.model}`} className='text-gray-300'>
                  <td className='px-2 py-1'>{r.task_class}</td>
                  <td className='px-2 py-1'>
                    <span className='text-gray-500'>{r.endpoint}/</span>{r.model}
                    {r.last_error && <div className='text-[10px] text-red-300 truncate max-w-xs' title={r.last_error}>{r.last_error}</div>}
                  </td>
                  <td className='px-2 py-1'>{r.calls}</td>
                  <td className={`px-2 py-1 ${r.errors ? 'text-red-300' : ''}`}>{r.errors}</td>
                  <td className='px-2 py-1'>{r.served_as_fallback}</td>
                  <td className='px-2 py-1'>{fmtMs(r.p50_ms)}</td>
                  <td className='px-2 py-1'>{fmtMs(r.p95_ms)}</td>
                  <td className='px-2 py-1 whitespace-nowrap'>{fmtTok(r.prompt_tokens)} / {fmtTok(r.completion_tokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  )
}

function fmtMs(ms) {
  if (ms == null) return '—'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${ms}ms`
}

function fmtTok(n) {
  if (!n) return '0'
  return n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n)
}
