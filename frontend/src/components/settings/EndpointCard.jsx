import { useState } from 'react'
import { llmApi } from '../../api/client'

const API_TYPE_LABELS = {
  openai: 'OpenAI / OpenAI-compatible',
  anthropic: 'Anthropic',
  ollama: 'Ollama',
  custom: 'Custom',
}

export default function EndpointCard({ endpoint, onChange }) {
  const [expanded, setExpanded] = useState(false)
  const [probeBusy, setProbeBusy] = useState(false)
  const [probeResult, setProbeResult] = useState(null)

  const handleProbe = async () => {
    setProbeBusy(true)
    setProbeResult(null)
    try {
      const r = await llmApi.probe({ endpoint_name: endpoint.name })
      setProbeResult(r)
    } catch (e) {
      setProbeResult({ ok: false, error: String(e?.message || e), models: [] })
    } finally {
      setProbeBusy(false)
    }
  }

  const handleDelete = async () => {
    if (!window.confirm(`Delete endpoint "${endpoint.name}"? Roles using it will be unbound.`)) return
    await llmApi.deleteEndpoint(endpoint.name)
    onChange?.()
  }

  return (
    <div className='border border-gray-700 rounded-md p-3 bg-gray-900/50'>
      <div className='flex items-center justify-between'>
        <div className='flex items-center gap-3'>
          <button
            type='button'
            onClick={() => setExpanded((v) => !v)}
            className='text-gray-300 hover:text-white'
            aria-expanded={expanded}
          >
            {expanded ? '▾' : '▸'}
          </button>
          <span className='font-mono font-semibold text-gray-100'>{endpoint.name}</span>
          <span className='text-xs px-2 py-0.5 rounded bg-gray-700 text-gray-300'>
            {API_TYPE_LABELS[endpoint.api_type] || endpoint.api_type}
          </span>
          {!endpoint.api_key_set && (
            <span className='text-xs text-amber-400'>no API key</span>
          )}
        </div>
        <div className='flex items-center gap-2'>
          <button
            type='button'
            onClick={handleProbe}
            disabled={probeBusy}
            className='text-xs px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-50'
          >
            {probeBusy ? 'Probing…' : 'Probe'}
          </button>
          <button
            type='button'
            onClick={handleDelete}
            className='text-xs px-2 py-1 rounded bg-red-900/50 hover:bg-red-800 text-red-200'
          >
            Delete
          </button>
        </div>
      </div>

      {expanded && (
        <div className='mt-3 text-sm text-gray-300 space-y-1 pl-6'>
          <div>
            <span className='text-gray-500'>URL:</span>{' '}
            <code className='text-xs'>{endpoint.base_url}</code>
          </div>
          {probeResult && (
            <div className='mt-2'>
              {probeResult.ok ? (
                <div className='text-emerald-300 text-xs'>
                  Found {probeResult.models.length} models
                  {probeResult.models.length > 0 && (
                    <ul className='mt-1 max-h-40 overflow-auto text-gray-400'>
                      {probeResult.models.map((m) => (
                        <li key={m}>
                          <code className='text-xs'>{m}</code>
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ) : (
                <div className='text-red-300 text-xs'>Probe failed: {probeResult.error}</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
