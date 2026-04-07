import React, { useState, useEffect } from 'react'
import { Save, Eye, EyeOff, Trash2, Plus, Check, X, RefreshCw, Search, ChevronDown, ChevronRight, MessageCircle, RotateCw, Shield, Cpu, Plug, Key } from 'lucide-react'
import { useStore } from '../store'
import { settingsApi } from '../api/client'
import SecurityLog from './SecurityLog'

function LLMSection() {
  const [settings, setSettings] = useState({
    llm_base_url: '',
    llm_api_key: '',
    llm_model: '',
    llm_prefill_model: '',
    prefill_base_url: '',
    prefill_api_key: '',
    llm_vision_model: '',
    vision_base_url: '',
    vision_api_key: '',
    embedding_model: '',
    embedding_base_url: '',
    embedding_api_key: '',
    reranker_model: '',
    reranker_base_url: '',
    reranker_api_key: '',
  })
  const [apiKeySet, setApiKeySet] = useState(false)
  const [prefillKeySet, setPrefillKeySet] = useState(false)
  const [visionKeySet, setVisionKeySet] = useState(false)
  const [embeddingKeySet, setEmbeddingKeySet] = useState(false)
  const [rerankerKeySet, setRerankerKeySet] = useState(false)
  const [models, setModels] = useState([])
  const [showKey, setShowKey] = useState(false)
  const [showPrefillOverride, setShowPrefillOverride] = useState(false)
  const [showVisionOverride, setShowVisionOverride] = useState(false)
  const [showEmbeddingOverride, setShowEmbeddingOverride] = useState(false)
  const [showRerankerOverride, setShowRerankerOverride] = useState(false)
  const [loading, setLoading] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testStatus, setTestStatus] = useState(null)
  const addNotification = useStore((s) => s.addNotification)

  useEffect(() => { loadSettings() }, [])

  const loadSettings = async () => {
    setLoading(true)
    try {
      const res = await settingsApi.get()
      const d = res.data
      setSettings({
        llm_base_url:      d.llm_base_url      || '',
        llm_api_key:       '',
        llm_model:         d.llm_model         || '',
        llm_prefill_model: d.llm_prefill_model || '',
        prefill_base_url:  d.prefill_base_url  || '',
        prefill_api_key:   '',
        llm_vision_model:  d.llm_vision_model  || '',
        vision_base_url:   d.vision_base_url   || '',
        vision_api_key:    '',
        embedding_model:   d.embedding_model   || '',
        embedding_base_url: d.embedding_base_url || '',
        embedding_api_key: '',
        reranker_model:    d.reranker_model    || '',
        reranker_base_url: d.reranker_base_url || '',
        reranker_api_key:  '',
      })
      setApiKeySet(d.llm_api_key_set || false)
      setPrefillKeySet(d.prefill_api_key_set || false)
      setVisionKeySet(d.vision_api_key_set || false)
      setEmbeddingKeySet(d.embedding_api_key_set || false)
      setRerankerKeySet(d.reranker_api_key_set || false)
      // Auto-expand override sections if they have values
      if (d.prefill_base_url) setShowPrefillOverride(true)
      if (d.vision_base_url || d.llm_vision_model) setShowVisionOverride(true)
      if (d.embedding_base_url) setShowEmbeddingOverride(true)
      if (d.reranker_base_url || d.reranker_model) setShowRerankerOverride(true)
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  const fetchModels = async () => {
    setLoading(true)
    try {
      const res = await settingsApi.listModels()
      setModels(res.data.models || [])
      addNotification({ type: 'success', message: `${res.data.count} models loaded` })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  const testConnection = async () => {
    setTesting(true)
    try {
      const res = await settingsApi.testConnection()
      const d = res.data
      const provs = d.providers || {}
      const lines = []
      for (const [name, info] of Object.entries(provs)) {
        if (info.status === 'ok') lines.push(`${name}: ✓ ${info.available_models} models`)
        else if (info.status === 'not_configured') lines.push(`${name}: skipped (not configured)`)
        else lines.push(`${name}: ✗ ${info.message || 'failed'}`)
      }
      const allOk = d.status === 'ok'
      setTestStatus(allOk ? 'success' : 'error')
      addNotification({
        type: allOk ? 'success' : 'warning',
        message: lines.join(' | '),
      })
      setTimeout(() => setTestStatus(null), 5000)
    } catch (err) {
      setTestStatus('error')
      addNotification({ type: 'error', message: err.message })
      setTimeout(() => setTestStatus(null), 3000)
    }
    setTesting(false)
  }

  const save = async () => {
    setLoading(true)
    try {
      const payload = {
        llm_base_url:      settings.llm_base_url,
        llm_model:         settings.llm_model,
        llm_prefill_model: settings.llm_prefill_model,
        prefill_base_url:  settings.prefill_base_url,
        llm_vision_model:  settings.llm_vision_model,
        vision_base_url:   settings.vision_base_url,
        embedding_model:   settings.embedding_model,
        embedding_base_url: settings.embedding_base_url,
        reranker_model:    settings.reranker_model,
        reranker_base_url: settings.reranker_base_url,
      }
      if (settings.llm_api_key) { payload.llm_api_key = settings.llm_api_key; setApiKeySet(true) }
      if (settings.prefill_api_key) { payload.prefill_api_key = settings.prefill_api_key; setPrefillKeySet(true) }
      if (settings.vision_api_key) { payload.vision_api_key = settings.vision_api_key; setVisionKeySet(true) }
      if (settings.embedding_api_key) { payload.embedding_api_key = settings.embedding_api_key; setEmbeddingKeySet(true) }
      if (settings.reranker_api_key) { payload.reranker_api_key = settings.reranker_api_key; setRerankerKeySet(true) }
      await settingsApi.update(payload)
      setSettings((s) => ({ ...s, llm_api_key: '', prefill_api_key: '', vision_api_key: '', embedding_api_key: '', reranker_api_key: '' }))
      addNotification({ type: 'success', message: 'LLM settings saved' })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  const inputClass = 'w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500'

  return (
    <div className="space-y-5">
      <h3 className="text-sm font-semibold text-gray-200">LLM Configuration</h3>

      {/* Endpoint */}
      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1.5">API Endpoint</label>
        <input
          type="text"
          value={settings.llm_base_url}
          onChange={(e) => setSettings({ ...settings, llm_base_url: e.target.value })}
          placeholder="https://api.openai.com/v1"
          className={inputClass}
        />
      </div>

      {/* API Key */}
      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1.5">
          API Key
          {apiKeySet && !settings.llm_api_key && (
            <span className="ml-2 text-green-500 font-normal">✓ key saved</span>
          )}
        </label>
        <div className="flex gap-2">
          <input
            type={showKey ? 'text' : 'password'}
            value={settings.llm_api_key}
            onChange={(e) => setSettings({ ...settings, llm_api_key: e.target.value })}
            placeholder={apiKeySet ? '(leave blank to keep existing key)' : 'sk-…'}
            className={`flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500`}
          />
          <button onClick={() => setShowKey(!showKey)} className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-gray-400">
            {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {/* Primary model — text input + datalist so current value always shows */}
      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1.5">Primary Model</label>
        <div className="flex gap-2">
          <input
            type="text"
            list="llm-models-list"
            value={settings.llm_model}
            onChange={(e) => setSettings({ ...settings, llm_model: e.target.value })}
            placeholder="e.g. gpt-4o"
            className={`flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500`}
          />
          <button
            onClick={fetchModels}
            disabled={loading}
            title="Fetch available models from endpoint"
            className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-gray-400 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
          <datalist id="llm-models-list">
            {models.map((m) => <option key={m} value={m} />)}
          </datalist>
        </div>
        {models.length > 0 && (
          <p className="text-xs text-gray-600 mt-1">{models.length} models available — type or pick from the list</p>
        )}
      </div>

      {/* Prefill model */}
      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1.5">
          Prefill Model
          <span className="ml-2 text-gray-600 font-normal">optional</span>
        </label>
        <input
          type="text"
          list="llm-models-list"
          value={settings.llm_prefill_model}
          onChange={(e) => setSettings({ ...settings, llm_prefill_model: e.target.value })}
          placeholder="Faster/cheaper model for summarisation & memory tasks"
          className={inputClass}
        />
        <p className="text-xs text-gray-600 mt-1">Used for background tasks like memory consolidation. Falls back to the primary model when blank.</p>

        {/* Prefill provider override */}
        <button
          onClick={() => setShowPrefillOverride(!showPrefillOverride)}
          className="flex items-center gap-1 mt-2 text-xs text-gray-500 hover:text-gray-300"
        >
          {showPrefillOverride ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          Separate provider endpoint
        </button>
        {showPrefillOverride && (
          <div className="mt-2 pl-3 border-l-2 border-gray-700 space-y-2">
            <input
              type="text"
              value={settings.prefill_base_url}
              onChange={(e) => setSettings({ ...settings, prefill_base_url: e.target.value })}
              placeholder="Endpoint URL (blank = use primary)"
              className={inputClass}
            />
            <input
              type="password"
              value={settings.prefill_api_key}
              onChange={(e) => setSettings({ ...settings, prefill_api_key: e.target.value })}
              placeholder={prefillKeySet ? '(key saved — blank to keep)' : 'API key (blank = use primary)'}
              className={inputClass}
            />
          </div>
        )}
      </div>

      {/* Vision model */}
      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1.5">
          Vision Model
          <span className="ml-2 text-gray-600 font-normal">optional</span>
        </label>
        <input
          type="text"
          list="llm-models-list"
          value={settings.llm_vision_model}
          onChange={(e) => setSettings({ ...settings, llm_vision_model: e.target.value })}
          placeholder="e.g. gpt-4o, llava, qwen2.5-vl"
          className={inputClass}
        />
        <p className="text-xs text-gray-600 mt-1">Dedicated model for image analysis. Falls back to primary → prefill when blank.</p>

        {/* Vision provider override */}
        <button
          onClick={() => setShowVisionOverride(!showVisionOverride)}
          className="flex items-center gap-1 mt-2 text-xs text-gray-500 hover:text-gray-300"
        >
          {showVisionOverride ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          Separate provider endpoint
        </button>
        {showVisionOverride && (
          <div className="mt-2 pl-3 border-l-2 border-gray-700 space-y-2">
            <input
              type="text"
              value={settings.vision_base_url}
              onChange={(e) => setSettings({ ...settings, vision_base_url: e.target.value })}
              placeholder="Endpoint URL (blank = use primary)"
              className={inputClass}
            />
            <input
              type="password"
              value={settings.vision_api_key}
              onChange={(e) => setSettings({ ...settings, vision_api_key: e.target.value })}
              placeholder={visionKeySet ? '(key saved — blank to keep)' : 'API key (blank = use primary)'}
              className={inputClass}
            />
          </div>
        )}
      </div>

      {/* Embedding model */}
      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1.5">
          Embedding Model
          <span className="ml-2 text-gray-600 font-normal">optional</span>
        </label>
        <input
          type="text"
          list="llm-models-list"
          value={settings.embedding_model}
          onChange={(e) => setSettings({ ...settings, embedding_model: e.target.value })}
          placeholder="text-embedding-3-small"
          className={inputClass}
        />
        <p className="text-xs text-gray-600 mt-1">Used for semantic memory search. Leave blank to use the provider default.</p>

        {/* Embedding provider override */}
        <button
          onClick={() => setShowEmbeddingOverride(!showEmbeddingOverride)}
          className="flex items-center gap-1 mt-2 text-xs text-gray-500 hover:text-gray-300"
        >
          {showEmbeddingOverride ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          Separate provider endpoint
        </button>
        {showEmbeddingOverride && (
          <div className="mt-2 pl-3 border-l-2 border-gray-700 space-y-2">
            <input
              type="text"
              value={settings.embedding_base_url}
              onChange={(e) => setSettings({ ...settings, embedding_base_url: e.target.value })}
              placeholder="Endpoint URL (blank = use primary)"
              className={inputClass}
            />
            <input
              type="password"
              value={settings.embedding_api_key}
              onChange={(e) => setSettings({ ...settings, embedding_api_key: e.target.value })}
              placeholder={embeddingKeySet ? '(key saved — blank to keep)' : 'API key (blank = use primary)'}
              className={inputClass}
            />
          </div>
        )}
      </div>

      {/* Reranker model */}
      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1.5">
          Reranker Model
          <span className="ml-2 text-gray-600 font-normal">optional</span>
        </label>
        <input
          type="text"
          value={settings.reranker_model}
          onChange={(e) => setSettings({ ...settings, reranker_model: e.target.value })}
          placeholder="e.g. qwen3-reranker-4b"
          className={inputClass}
        />
        <p className="text-xs text-gray-600 mt-1">Cross-encoder model for reranking memory recall results. Leave blank to skip reranking.</p>

        {/* Reranker provider override */}
        <button
          onClick={() => setShowRerankerOverride(!showRerankerOverride)}
          className="flex items-center gap-1 mt-2 text-xs text-gray-500 hover:text-gray-300"
        >
          {showRerankerOverride ? <ChevronDown className="w-3 h-3" /> : <ChevronRight className="w-3 h-3" />}
          Separate provider endpoint
        </button>
        {showRerankerOverride && (
          <div className="mt-2 pl-3 border-l-2 border-gray-700 space-y-2">
            <input
              type="text"
              value={settings.reranker_base_url}
              onChange={(e) => setSettings({ ...settings, reranker_base_url: e.target.value })}
              placeholder="Endpoint URL (blank = use primary)"
              className={inputClass}
            />
            <input
              type="password"
              value={settings.reranker_api_key}
              onChange={(e) => setSettings({ ...settings, reranker_api_key: e.target.value })}
              placeholder={rerankerKeySet ? '(key saved — blank to keep)' : 'API key (blank = use primary)'}
              className={inputClass}
            />
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="flex gap-2">
        <button
          onClick={testConnection}
          disabled={testing || loading}
          className="flex items-center gap-2 px-4 py-2 bg-blue-600 hover:bg-blue-700 text-white text-sm rounded-lg disabled:opacity-50"
        >
          {testStatus === 'success' ? <Check className="w-4 h-4" /> : testStatus === 'error' ? <X className="w-4 h-4" /> : <RefreshCw className="w-4 h-4" />}
          Test Connection
        </button>
        <button
          onClick={save}
          disabled={loading || testing}
          className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-lg disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          Save Settings
        </button>
      </div>
    </div>
  )
}

function SearchSection() {
  const [providers, setProviders] = useState([])
  const [usage, setUsage] = useState({ providers: [] })
  const [loading, setLoading] = useState(false)
  const [testing, setTesting] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  const load = async () => {
    try {
      const res = await settingsApi.getSearchProviders()
      setProviders(res.data.providers || [])
      setUsage(res.data.usage || { providers: [] })
    } catch (err) {
      addNotification({ type: 'error', message: 'Failed to load search providers' })
    }
  }

  useEffect(() => { load() }, [])

  const updateField = (idx, field, value) => {
    const next = [...providers]
    next[idx] = { ...next[idx], [field]: value }
    setProviders(next)
  }

  const moveUp = (idx) => {
    if (idx === 0) return
    const next = [...providers]
    ;[next[idx - 1], next[idx]] = [next[idx], next[idx - 1]]
    setProviders(next)
  }

  const moveDown = (idx) => {
    if (idx === providers.length - 1) return
    const next = [...providers]
    ;[next[idx], next[idx + 1]] = [next[idx + 1], next[idx]]
    setProviders(next)
  }

  const removeProvider = (idx) => {
    setProviders(providers.filter((_, i) => i !== idx))
  }

  const uniqueName = (base) => {
    const existing = new Set(providers.map((p) => p.name))
    if (!existing.has(base)) return base
    // Strip any trailing -N then increment until unique
    const root = base.replace(/-(\d+)$/, '')
    let i = 2
    while (existing.has(`${root}-${i}`)) i += 1
    return `${root}-${i}`
  }

  const addProvider = () => {
    setProviders([
      ...providers,
      { name: uniqueName(`provider-${providers.length + 1}`), type: 'generic', url: '', api_key_vault_key: '',
        daily_limit: 0, monthly_limit: 0, rps: 0, enabled: true },
    ])
  }

  const duplicateProvider = (idx) => {
    const src = providers[idx]
    const newName = uniqueName(src.name)
    // Suffix derived from the new name (e.g. brave -> brave-2 → suffix '-2')
    const suffix = newName.slice(src.name.replace(/-(\d+)$/, '').length) || `-${Date.now().toString().slice(-4)}`
    const newKey = src.api_key_vault_key
      ? `${src.api_key_vault_key.replace(/-(\d+)$/, '').replace(/_(\d+)$/, '')}${suffix.replace('-', '_')}`
      : ''
    const copy = {
      ...src,
      name: newName,
      api_key_vault_key: newKey,
      api_key: '',  // never copy the typed-in key value
    }
    const next = [...providers]
    next.splice(idx + 1, 0, copy)
    setProviders(next)
  }

  const save = async () => {
    setLoading(true)
    try {
      // Strip api_key field if blank to avoid clobbering vault
      const payload = providers.map((p) => {
        const out = { ...p, daily_limit: parseInt(p.daily_limit) || 0,
                      monthly_limit: parseInt(p.monthly_limit) || 0,
                      rps: parseFloat(p.rps) || 0 }
        if (!out.api_key) delete out.api_key
        return out
      })
      await settingsApi.setSearchProviders(payload)
      addNotification({ type: 'success', message: 'Search providers saved' })
      // Clear typed-in api_key fields, then reload
      setProviders((prev) => prev.map((p) => ({ ...p, api_key: '' })))
      await load()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  const reset = async (name, period) => {
    try {
      await settingsApi.resetSearchProvider(name, period)
      await load()
      addNotification({ type: 'success', message: `Reset ${period} usage for ${name}` })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const test = async () => {
    setTesting(true)
    try {
      const res = await settingsApi.testSearchChain('pantheon search test')
      addNotification({ type: 'success', message: 'Test ran — see usage rows below' })
      setUsage(res.data.usage || { providers: [] })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setTesting(false)
  }

  const usageFor = (name) => usage.providers?.find((u) => u.name === name) || {}

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Search className="w-4 h-4 text-gray-400" />
        <h3 className="text-sm font-semibold text-gray-200">Web Search Provider Chain</h3>
      </div>

      <p className="text-xs text-gray-500">
        Providers are tried top-to-bottom. The agent falls through to the next provider on
        any error, empty result set, or exhausted quota / rate limit. Per-provider quotas
        are tracked locally; reset them at the start of a new billing cycle.
      </p>

      <div className="space-y-3">
        {providers.map((p, idx) => {
          const u = usageFor(p.name)
          const monthlyPct = p.monthly_limit > 0 ? Math.min(100, (u.monthly_used || 0) / p.monthly_limit * 100) : 0
          const dailyPct = p.daily_limit > 0 ? Math.min(100, (u.daily_used || 0) / p.daily_limit * 100) : 0
          return (
            <div key={idx} className="bg-gray-900 border border-gray-700 rounded-lg p-3 space-y-2">
              <div className="flex items-center gap-2">
                <span className="text-xs text-gray-500 w-6">#{idx + 1}</span>
                <input
                  type="text"
                  value={p.name}
                  onChange={(e) => updateField(idx, 'name', e.target.value)}
                  className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 w-32"
                  placeholder="name"
                />
                <select
                  value={p.type}
                  onChange={(e) => updateField(idx, 'type', e.target.value)}
                  className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                >
                  <option value="brave">Brave</option>
                  <option value="searxng">SearXNG</option>
                  <option value="ddg">DuckDuckGo</option>
                  <option value="generic">Generic JSON</option>
                </select>
                <label className="flex items-center gap-1 text-xs text-gray-400">
                  <input
                    type="checkbox"
                    checked={p.enabled !== false}
                    onChange={(e) => updateField(idx, 'enabled', e.target.checked)}
                  />
                  enabled
                </label>
                <div className="flex-1" />
                <button onClick={() => moveUp(idx)} className="text-xs px-2 py-1 text-gray-400 hover:text-white" title="Move up">↑</button>
                <button onClick={() => moveDown(idx)} className="text-xs px-2 py-1 text-gray-400 hover:text-white" title="Move down">↓</button>
                <button onClick={() => duplicateProvider(idx)} className="text-xs px-2 py-1 text-gray-400 hover:text-white" title="Duplicate row">⎘</button>
                <button onClick={() => removeProvider(idx)} className="text-xs px-2 py-1 text-red-400 hover:text-red-300">remove</button>
              </div>

              {p.type !== 'ddg' && (
                <div className="grid grid-cols-2 gap-2">
                  <input
                    type="text"
                    value={p.url || ''}
                    onChange={(e) => updateField(idx, 'url', e.target.value)}
                    placeholder="URL"
                    className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                  />
                  <input
                    type="text"
                    value={p.api_key_vault_key || ''}
                    onChange={(e) => updateField(idx, 'api_key_vault_key', e.target.value)}
                    placeholder="api_key vault key (e.g. brave_api_key)"
                    className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                  />
                  <input
                    type="password"
                    value={p.api_key || ''}
                    onChange={(e) => updateField(idx, 'api_key', e.target.value)}
                    placeholder={u.api_key_set ? '(key saved — leave blank to keep)' : 'API key'}
                    className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 col-span-2"
                  />
                </div>
              )}

              <div className="grid grid-cols-3 gap-2">
                <div>
                  <label className="block text-[10px] uppercase text-gray-500 mb-0.5">Daily limit</label>
                  <input
                    type="number"
                    value={p.daily_limit || 0}
                    onChange={(e) => updateField(idx, 'daily_limit', e.target.value)}
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                  />
                </div>
                <div>
                  <label className="block text-[10px] uppercase text-gray-500 mb-0.5">Monthly limit</label>
                  <input
                    type="number"
                    value={p.monthly_limit || 0}
                    onChange={(e) => updateField(idx, 'monthly_limit', e.target.value)}
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                  />
                </div>
                <div>
                  <label className="block text-[10px] uppercase text-gray-500 mb-0.5">Req / sec</label>
                  <input
                    type="number"
                    step="0.1"
                    value={p.rps || 0}
                    onChange={(e) => updateField(idx, 'rps', e.target.value)}
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                  />
                </div>
              </div>

              <div className="text-[11px] text-gray-400 space-y-1 pt-1">
                <div className="flex items-center gap-2">
                  <span className="w-16">Daily:</span>
                  <span className="font-mono">{u.daily_used || 0}{p.daily_limit > 0 ? ` / ${p.daily_limit}` : ''}</span>
                  {p.daily_limit > 0 && (
                    <div className="flex-1 h-1 bg-gray-800 rounded overflow-hidden">
                      <div className="h-full bg-brand-500" style={{ width: `${dailyPct}%` }} />
                    </div>
                  )}
                  <button onClick={() => reset(p.name, 'daily')} className="text-[10px] text-gray-500 hover:text-gray-300">reset</button>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-16">Monthly:</span>
                  <span className="font-mono">{u.monthly_used || 0}{p.monthly_limit > 0 ? ` / ${p.monthly_limit}` : ''}</span>
                  {p.monthly_limit > 0 && (
                    <div className="flex-1 h-1 bg-gray-800 rounded overflow-hidden">
                      <div className="h-full bg-brand-500" style={{ width: `${monthlyPct}%` }} />
                    </div>
                  )}
                  <button onClick={() => reset(p.name, 'monthly')} className="text-[10px] text-gray-500 hover:text-gray-300">reset</button>
                </div>
                <div className="flex items-center gap-3 text-gray-500">
                  <span>errors: {u.errors || 0}</span>
                  <span>skipped: {u.skipped || 0}</span>
                  {u.last_used && <span>last: {new Date(u.last_used).toLocaleString()}</span>}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          onClick={addProvider}
          className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-xs text-gray-200 rounded border border-gray-700"
        >
          + Add provider
        </button>
        <button
          onClick={save}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-1.5 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          Save Provider Chain
        </button>
        <button
          onClick={test}
          disabled={testing}
          className="px-4 py-1.5 bg-gray-800 hover:bg-gray-700 text-sm text-gray-200 rounded border border-gray-700 disabled:opacity-50"
        >
          {testing ? 'Testing…' : 'Test chain'}
        </button>
      </div>
    </div>
  )
}

function TelegramSection() {
  const [botToken, setBotToken] = useState('')
  const [chatIds, setChatIds] = useState('')
  const [tokenSet, setTokenSet] = useState(false)
  const [showToken, setShowToken] = useState(false)
  const [loading, setLoading] = useState(false)
  const [restarting, setRestarting] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  useEffect(() => {
    settingsApi.get().then((res) => {
      setTokenSet(res.data.telegram_bot_token_set || false)
      setChatIds(res.data.telegram_allowed_chat_ids || '')
    }).catch(() => {})
  }, [])

  const save = async () => {
    setLoading(true)
    try {
      const payload = { telegram_allowed_chat_ids: chatIds }
      if (botToken) {
        payload.telegram_bot_token = botToken
        setTokenSet(true)
      }
      await settingsApi.update(payload)
      setBotToken('')
      addNotification({ type: 'success', message: 'Telegram settings saved.' })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  const restartBot = async () => {
    setRestarting(true)
    try {
      const res = await settingsApi.restartTelegram()
      const d = res.data || res
      if (d.status === 'ok') {
        addNotification({ type: 'success', message: d.message || 'Telegram bot restarted.' })
      } else if (d.status === 'no_token') {
        addNotification({ type: 'warning', message: d.message || 'No bot token configured.' })
      } else {
        addNotification({ type: 'error', message: d.message || 'Failed to restart bot.' })
      }
    } catch (err) {
      addNotification({ type: 'error', message: err.message || 'Failed to restart Telegram bot.' })
    }
    setRestarting(false)
  }

  const inputClass = 'w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500'

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <MessageCircle className="w-4 h-4 text-gray-400" />
        <h3 className="text-sm font-semibold text-gray-200">Telegram Bot</h3>
      </div>

      <p className="text-xs text-gray-500">
        Connect a Telegram bot to chat with the agent from your phone.
        Create a bot via <a href="https://t.me/BotFather" target="_blank" rel="noreferrer" className="text-brand-400 hover:underline">@BotFather</a> to
        get a token, then message your bot and use <a href="https://api.telegram.org" target="_blank" rel="noreferrer" className="text-brand-400 hover:underline">the Telegram API</a> to
        find your chat ID.
      </p>

      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1.5">
          Bot Token
          {tokenSet && !botToken && (
            <span className="ml-2 text-green-500 font-normal">✓ token saved</span>
          )}
        </label>
        <div className="flex gap-2">
          <input
            type={showToken ? 'text' : 'password'}
            value={botToken}
            onChange={(e) => setBotToken(e.target.value)}
            placeholder={tokenSet ? '(leave blank to keep existing token)' : '123456:ABC-DEF1234…'}
            className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500"
          />
          <button onClick={() => setShowToken(!showToken)} className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-gray-400">
            {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-400 mb-1.5">Allowed Chat IDs</label>
        <input
          type="text"
          value={chatIds}
          onChange={(e) => setChatIds(e.target.value)}
          placeholder="123456789, 987654321"
          className={inputClass}
        />
        <p className="text-xs text-gray-600 mt-1">Comma-separated list of Telegram chat IDs that can use the bot. Leave blank to allow all.</p>
      </div>

      <div className="flex items-center gap-3">
        <button
          onClick={save}
          disabled={loading}
          className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-lg disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          Save
        </button>
        <button
          onClick={restartBot}
          disabled={restarting}
          className="flex items-center gap-2 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-200 text-sm rounded-lg disabled:opacity-50"
        >
          <RotateCw className={`w-4 h-4 ${restarting ? 'animate-spin' : ''}`} />
          {restarting ? 'Restarting…' : 'Restart Bot'}
        </button>
      </div>
    </div>
  )
}


function SkillSecuritySection() {
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [isSet, setIsSet] = useState(false)
  const [loading, setLoading] = useState(true)
  const [showPassword, setShowPassword] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  useEffect(() => {
    checkStatus()
  }, [])

  const checkStatus = async () => {
    try {
      const res = await settingsApi.listSecrets()
      const keys = res.data.keys || []
      setIsSet(keys.includes('skill_security_override_password'))
    } catch (err) {
      // ignore
    }
    setLoading(false)
  }

  const handleSave = async () => {
    if (!password.trim()) {
      addNotification({ type: 'error', message: 'Password cannot be empty' })
      return
    }
    if (password.length < 8) {
      addNotification({ type: 'error', message: 'Password must be at least 8 characters' })
      return
    }
    if (password !== confirmPassword) {
      addNotification({ type: 'error', message: 'Passwords do not match' })
      return
    }
    try {
      await settingsApi.setSecret('skill_security_override_password', password)
      setIsSet(true)
      setPassword('')
      setConfirmPassword('')
      addNotification({ type: 'success', message: 'Security override password saved to vault' })
    } catch (err) {
      addNotification({ type: 'error', message: `Failed to save: ${err.message}` })
    }
  }

  const handleRemove = async () => {
    try {
      await settingsApi.deleteSecret('skill_security_override_password')
      setIsSet(false)
      addNotification({ type: 'success', message: 'Security override password removed' })
    } catch (err) {
      addNotification({ type: 'error', message: `Failed to remove: ${err.message}` })
    }
  }

  return (
    <div>
      <h2 className="text-lg font-semibold text-gray-100 mb-1">Skill Security Override</h2>
      <p className="text-sm text-gray-500 mb-4">
        Set a password to allow force-enabling skills that failed a security scan.
        This password is stored encrypted in the vault and required each time an override is used.
      </p>

      {loading ? (
        <p className="text-xs text-gray-600">Loading...</p>
      ) : isSet ? (
        <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Check className="w-4 h-4 text-green-400" />
              <span className="text-sm text-green-400">Override password is configured</span>
            </div>
            <button
              onClick={handleRemove}
              className="flex items-center gap-1 px-2.5 py-1 text-xs rounded bg-red-900/30 text-red-400 hover:bg-red-900/50 transition-colors"
            >
              <Trash2 className="w-3 h-3" /> Remove
            </button>
          </div>
          <p className="text-xs text-gray-600">
            To change the password, remove the current one and set a new one.
          </p>
        </div>
      ) : (
        <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 space-y-3">
          <div className="space-y-2">
            <label className="block text-xs text-gray-400">New Override Password</label>
            <div className="relative">
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Minimum 8 characters"
                className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 pr-10"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
              >
                {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>
          <div className="space-y-2">
            <label className="block text-xs text-gray-400">Confirm Password</label>
            <input
              type={showPassword ? 'text' : 'password'}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Re-enter password"
              className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
            />
          </div>
          <button
            onClick={handleSave}
            disabled={!password.trim() || !confirmPassword.trim()}
            className="flex items-center justify-center gap-2 px-3 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded disabled:opacity-50 w-full"
          >
            <Save className="w-4 h-4" /> Set Override Password
          </button>
        </div>
      )}
    </div>
  )
}

function SecretsSection() {
  const [secrets, setSecrets] = useState([])
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const [loading, setLoading] = useState(false)
  const [showValues, setShowValues] = useState({})
  const addNotification = useStore((s) => s.addNotification)

  useEffect(() => {
    loadSecrets()
  }, [])

  const loadSecrets = async () => {
    setLoading(true)
    try {
      const res = await settingsApi.listSecrets()
      // API returns { keys: ["key1", "key2"], count: N }
      const keys = res.data.keys || []
      setSecrets(keys.map((k) => ({ key: k })))
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  const addSecret = async () => {
    if (!newKey.trim() || !newValue.trim()) return
    // Normalize to lowercase — the backend vault lookups use lowercase keys
    const normalizedKey = newKey.trim().toLowerCase()
    try {
      await settingsApi.setSecret(normalizedKey, newValue)
      setNewKey('')
      setNewValue('')
      addNotification({ type: 'success', message: `Secret "${normalizedKey}" saved` })
      loadSecrets()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const deleteSecret = async (key) => {
    if (!confirm(`Delete secret "${key}"?`)) return
    try {
      await settingsApi.deleteSecret(key)
      addNotification({ type: 'success', message: 'Secret deleted' })
      loadSecrets()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-gray-200">API Secrets & Keys</h3>

      {/* Add new secret */}
      <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-2">Key Name</label>
            <input
              type="text"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder="telegram_bot_token"
              className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-2">Value</label>
            <input
              type="password"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              placeholder="***"
              className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
            />
          </div>
          <button
            onClick={addSecret}
            disabled={loading || !newKey.trim() || !newValue.trim()}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-green-600 hover:bg-green-700 text-white text-sm rounded disabled:opacity-50"
          >
            <Plus className="w-4 h-4" />
            Add Secret
          </button>
        </div>
      </div>

      {/* Secrets list */}
      <div className="space-y-2">
        {secrets.length === 0 ? (
          <p className="text-sm text-gray-600 text-center py-4">No secrets configured</p>
        ) : (
          secrets.map((secret) => (
            <div key={secret.key} className="bg-gray-800 rounded-lg p-3 border border-gray-700 flex items-center justify-between">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-mono text-gray-300">{secret.key}</p>
                <span className="text-xs text-gray-600">************</span>
              </div>
              <button
                onClick={() => deleteSecret(secret.key)}
                className="ml-2 p-1 text-gray-500 hover:text-red-400 hover:bg-gray-700 rounded"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

function AuditLogSection() {
  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <Shield className="w-5 h-5 text-gray-400" />
        <h2 className="text-lg font-semibold text-gray-200">Security Audit Log</h2>
      </div>
      <p className="text-xs text-gray-500 mb-4">
        All security-relevant events across authentication, skills, vault, and settings.
      </p>
      <div className="rounded-lg border border-gray-800 overflow-hidden" style={{ height: '480px' }}>
        <SecurityLog embedded />
      </div>
    </div>
  )
}

export default function Settings() {
  const [tab, setTab] = useState('llms')

  const tabs = [
    { id: 'llms', label: 'LLMs', icon: Cpu },
    { id: 'integrations', label: 'Integrations', icon: Plug },
    { id: 'security', label: 'Security', icon: Shield },
    { id: 'secrets', label: 'Secrets', icon: Key },
  ]

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center border-b border-gray-800 px-6 pt-4">
        {tabs.map((t) => {
          const Icon = t.icon
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-4 py-2 text-xs font-medium border-b-2 transition-colors ${
                tab === t.id
                  ? 'border-brand-400 text-brand-300'
                  : 'border-transparent text-gray-500 hover:text-gray-300'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          )
        })}
      </div>
      <div className="flex-1 overflow-hidden">
        {tab === 'llms' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-2xl mx-auto p-6 space-y-8">
              <LLMSection />
            </div>
          </div>
        )}
        {tab === 'integrations' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-2xl mx-auto p-6 space-y-8">
              <SearchSection />
              <div className="border-t border-gray-800" />
              <TelegramSection />
            </div>
          </div>
        )}
        {tab === 'security' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-2xl mx-auto p-6 space-y-8">
              <SkillSecuritySection />
              <div className="border-t border-gray-800" />
              <AuditLogSection />
            </div>
          </div>
        )}
        {tab === 'secrets' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-2xl mx-auto p-6 space-y-8">
              <SecretsSection />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
