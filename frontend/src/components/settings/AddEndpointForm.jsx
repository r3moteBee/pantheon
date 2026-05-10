import { useEffect, useRef, useState } from 'react'
import { llmApi } from '../../api/client'
import InfoTooltip from '../help/InfoTooltip'

const API_TYPES = [
  { value: 'openai', label: 'OpenAI / OpenAI-compatible', placeholder: 'https://api.openai.com/v1' },
  { value: 'anthropic', label: 'Anthropic', placeholder: 'https://api.anthropic.com' },
  { value: 'ollama', label: 'Ollama', placeholder: 'http://localhost:11434/v1' },
  { value: 'custom', label: 'Custom', placeholder: 'https://your.endpoint/v1' },
]

const HINTS = {
  name: "A short label for this endpoint (e.g. openai, local-ollama). Used internally; you won't see it in chat.",
  api_type: "Pick the protocol the endpoint speaks. Most cloud providers and OpenRouter speak openai. Anthropic uses its own. Use ollama for local Ollama.",
  base_url: "The root URL the API responds at — usually ends in /v1. See the help drawer above for common values.",
  api_key: "Stored encrypted in the local vault and never leaves your machine.",
}

export default function AddEndpointForm({ onSaved, prefill, onPrefillConsumed }) {
  const [name, setName] = useState('')
  const [apiType, setApiType] = useState('openai')
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [testResult, setTestResult] = useState(null)
  const formRef = useRef(null)

  const apiTypeMeta = API_TYPES.find((t) => t.value === apiType)

  // Consume prefill from the help drawer's "Use this" buttons. We
  // overwrite the three known fields, leave api_key alone (the user
  // pastes that), and scroll the form into view so they see the change
  // even when the drawer is below the fold.
  useEffect(() => {
    if (!prefill) return
    if (prefill.name) setName(prefill.name)
    if (prefill.api_type) setApiType(prefill.api_type)
    if (prefill.base_url) setBaseUrl(prefill.base_url)
    setTestResult(null)
    setError('')
    if (formRef.current) {
      formRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
    }
    onPrefillConsumed?.()
  }, [prefill, onPrefillConsumed])

  const handleTest = async () => {
    setError('')
    setTestResult(null)
    setBusy(true)
    try {
      const r = await llmApi.probe({
        base_url: baseUrl, api_type: apiType, api_key: apiKey,
      })
      setTestResult(r)
    } catch (e) {
      setError(String(e?.response?.data?.detail || e?.message || e))
    } finally {
      setBusy(false)
    }
  }

  const handleSave = async (e) => {
    e.preventDefault()
    setError('')
    setBusy(true)
    try {
      await llmApi.saveEndpoint({
        name, base_url: baseUrl, api_type: apiType, api_key: apiKey || null,
      })
      setName('')
      setBaseUrl('')
      setApiKey('')
      setTestResult(null)
      onSaved?.()
    } catch (e) {
      setError(String(e?.response?.data?.detail || e?.message || e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <form
      ref={formRef}
      onSubmit={handleSave}
      className='border border-gray-700 rounded-md p-3 bg-gray-900/30 space-y-3'
    >
      <div className='grid grid-cols-1 md:grid-cols-2 gap-3'>
        <div>
          <label className='block text-xs text-gray-400 mb-1'>
            Name <InfoTooltip text={HINTS.name} />
          </label>
          <input
            type='text'
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder='e.g. local-ollama'
            required
            className='w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm'
          />
        </div>
        <div>
          <label className='block text-xs text-gray-400 mb-1'>
            API type <InfoTooltip text={HINTS.api_type} />
          </label>
          <select
            value={apiType}
            onChange={(e) => setApiType(e.target.value)}
            className='w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm'
          >
            {API_TYPES.map((t) => (
              <option key={t.value} value={t.value}>{t.label}</option>
            ))}
          </select>
        </div>
      </div>
      <div>
        <label className='block text-xs text-gray-400 mb-1'>
          Base URL <InfoTooltip text={HINTS.base_url} />
        </label>
        <input
          type='url'
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder={apiTypeMeta?.placeholder || ''}
          required
          className='w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm'
        />
      </div>
      <div>
        <label className='block text-xs text-gray-400 mb-1'>
          API key <InfoTooltip text={HINTS.api_key} />
        </label>
        <div className='flex gap-2'>
          <input
            type={showKey ? 'text' : 'password'}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder='(leave blank for Ollama / open endpoints)'
            className='flex-1 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm'
          />
          <button
            type='button'
            onClick={() => setShowKey((v) => !v)}
            className='text-xs px-2 py-1 rounded bg-gray-700'
          >
            {showKey ? 'Hide' : 'Show'}
          </button>
        </div>
      </div>
      <div className='flex items-center gap-2'>
        <button
          type='button'
          onClick={handleTest}
          disabled={busy || !baseUrl}
          className='text-xs px-3 py-1 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-50'
        >
          {busy ? 'Testing…' : 'Test'}
        </button>
        <button
          type='submit'
          disabled={busy || !name || !baseUrl}
          className='text-xs px-3 py-1 rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50'
        >
          Save endpoint
        </button>
        {testResult && (
          <span className={`text-xs ${testResult.ok ? 'text-emerald-300' : 'text-red-300'}`}>
            {testResult.ok
              ? `OK — ${testResult.models.length} models`
              : `Failed: ${testResult.error}`}
          </span>
        )}
        {error && <span className='text-xs text-red-300'>{error}</span>}
      </div>
    </form>
  )
}
