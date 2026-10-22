import { useEffect, useRef, useState } from 'react'
import { MoreHorizontal, Trash2 } from 'lucide-react'
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
  // Delete lives in an overflow menu, away from Probe (UI review #35).
  const [menuOpen, setMenuOpen] = useState(false)
  const menuRef = useRef(null)
  useEffect(() => {
    if (!menuOpen) return
    const close = (e) => {
      if (e.type === 'keydown' ? e.key === 'Escape' : !menuRef.current?.contains(e.target)) setMenuOpen(false)
    }
    document.addEventListener('mousedown', close)
    document.addEventListener('keydown', close)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', close) }
  }, [menuOpen])

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
    setMenuOpen(false)
    if (!window.confirm(`Delete endpoint "${endpoint.name}"? Roles using it will be unbound.`)) return
    await llmApi.deleteEndpoint(endpoint.name)
    onChange?.()
  }

  return (
    <div className='border border-gray-700 rounded-md p-3 bg-gray-900/50'>
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <div className='flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0'>
          <button
            type='button'
            onClick={() => setExpanded((v) => !v)}
            className='text-gray-300 hover:text-white'
            aria-expanded={expanded}
            aria-label={`${expanded ? 'Hide' : 'Show'} details for ${endpoint.name}`}
            title={expanded ? 'Hide details' : 'Show details'}
          >
            {expanded ? '▾' : '▸'}
          </button>
          <span className='font-mono font-semibold text-gray-100'>{endpoint.name}</span>
          <span className='text-xs px-2 py-0.5 rounded border border-gray-700 bg-gray-800 text-gray-300 whitespace-nowrap'>
            {API_TYPE_LABELS[endpoint.api_type] || endpoint.api_type}
          </span>
          {!endpoint.api_key_set && (
            <span className='text-xs text-amber-300 whitespace-nowrap'>no API key</span>
          )}
        </div>
        <div className='flex items-center gap-2'>
          <button
            type='button'
            onClick={handleProbe}
            disabled={probeBusy}
            title='List the models this endpoint serves'
            className='text-xs px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-50 whitespace-nowrap'
          >
            {probeBusy ? 'Probing…' : 'Probe'}
          </button>
          <div className='relative' ref={menuRef}>
            <button
              type='button'
              onClick={() => setMenuOpen((v) => !v)}
              aria-haspopup='menu'
              aria-expanded={menuOpen}
              aria-label={`More actions for ${endpoint.name}`}
              title='More actions'
              className='p-1 rounded text-gray-300 hover:text-white hover:bg-gray-700'
            >
              <MoreHorizontal className='w-4 h-4' aria-hidden='true' />
            </button>
            {menuOpen && (
              <div role='menu' className='absolute right-0 z-20 mt-1 w-44 rounded-md border border-gray-700 bg-gray-900 py-1 shadow-lg'>
                <button
                  type='button'
                  role='menuitem'
                  onClick={handleDelete}
                  className='flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-red-300 hover:bg-red-950/60'
                >
                  <Trash2 className='w-3.5 h-3.5' aria-hidden='true' /> Delete endpoint…
                </button>
              </div>
            )}
          </div>
        </div>
      </div>

      {expanded && (
        <div className='mt-3 text-sm text-gray-300 space-y-1 pl-6'>
          <div>
            <span className='text-gray-400'>URL:</span>{' '}
            <code className='text-xs'>{endpoint.base_url}</code>
          </div>
          {probeResult && (
            <div className='mt-2'>
              {probeResult.ok ? (
                <div className='text-emerald-300 text-xs'>
                  Found {probeResult.models.length} models
                  {' — '}
                  {Object.keys(probeResult.capabilities || {}).length
                    ? `${Object.keys(probeResult.capabilities).length} publish their capabilities`
                    : 'none publish capabilities (profiles are guessed from names)'}
                  {probeResult.models.length > 0 && (
                    <ul className='mt-1 max-h-40 overflow-auto text-gray-400'>
                      {probeResult.models.map((m) => {
                        const c = (probeResult.capabilities || {})[m]
                        const tags = c ? ['tools', 'vision', 'embedding', 'image_gen'].filter((k) => c[k]) : []
                        return (
                          <li key={m}>
                            <code className='text-xs'>{m}</code>
                            {c && (
                              <span className='ml-2 text-xs text-gray-400'>
                                {tags.join(', ') || 'text only'}
                                {c.context_window ? ` · ${Math.round(c.context_window / 1000)}k ctx` : ''}
                              </span>
                            )}
                          </li>
                        )
                      })}
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
