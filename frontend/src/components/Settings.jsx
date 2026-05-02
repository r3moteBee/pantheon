import React, { useState, useEffect } from 'react'
import { Save, Eye, EyeOff, Trash2, Plus, Check, X, RefreshCw, Search, ChevronDown, ChevronRight, MessageCircle, RotateCw, Shield, Cpu, Plug, Key, Globe, Library, Clock, ArrowUpDown, Server, User as UserIcon } from 'lucide-react'
import { useStore } from '../store'
import { settingsApi, skillsApi, tasksApi, projectsApi, systemApi, taskRunsApi } from '../api/client'
import SecurityLog from './SecurityLog'
import PersonalityEditor from './PersonalityEditor'

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
  const [testQuery, setTestQuery] = useState('pantheon search test')
  const [testResult, setTestResult] = useState(null)  // { query, providerUsed, fallthrough, body, raw }
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
    setTestResult(null)
    try {
      const res = await settingsApi.testSearchChain(testQuery || 'pantheon search test')
      const raw = res.data.result || ''
      // Parse the leading "[searched via X — fallthrough: ...]" / "[cached via X]" tag
      const tagMatch = raw.match(/^\[(searched|cached) via ([^\s\]]+)(?: — fallthrough: ([^\]]+))?\]\n?/)
      let providerUsed = null
      let fallthrough = []
      let body = raw
      if (tagMatch) {
        providerUsed = tagMatch[2]
        if (tagMatch[3]) fallthrough = tagMatch[3].split(';').map((s) => s.trim()).filter(Boolean)
        body = raw.slice(tagMatch[0].length)
      } else if (raw.startsWith('No search results')) {
        providerUsed = 'none'
        body = raw
      }
      setTestResult({ query: res.data.query, providerUsed, fallthrough, body, raw })
      setUsage(res.data.usage || { providers: [] })
      addNotification({
        type: providerUsed && providerUsed !== 'none' ? 'success' : 'error',
        message: providerUsed && providerUsed !== 'none'
          ? `Test ok — answered by ${providerUsed}`
          : 'Test failed — no provider returned results',
      })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
      setTestResult({ query: testQuery, providerUsed: null, fallthrough: [], body: `Error: ${err.message}`, raw: '' })
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
                {u.remote && (
                  <div className="mt-1 px-2 py-1 rounded bg-gray-800/60 border border-gray-700/60 text-[10px] text-gray-400 space-y-0.5">
                    <div className="text-amber-400/80 font-semibold uppercase tracking-wide">Brave-reported</div>
                    {u.remote.month_limit != null && (
                      <div>
                        Month: <span className="font-mono text-gray-200">{u.remote.month_used ?? '?'} / {u.remote.month_limit}</span>
                        {u.remote.month_remaining != null && <span> ({u.remote.month_remaining} left)</span>}
                      </div>
                    )}
                    {u.remote.second_limit != null && (
                      <div>
                        This second: <span className="font-mono text-gray-200">{u.remote.second_remaining ?? '?'} / {u.remote.second_limit}</span> remaining
                      </div>
                    )}
                    {u.remote.captured_at && (
                      <div className="text-gray-600">captured {new Date(u.remote.captured_at).toLocaleString()}</div>
                    )}
                  </div>
                )}
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
      </div>

      {/* Test panel */}
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 space-y-2">
        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-400 whitespace-nowrap">Test query:</label>
          <input
            type="text"
            value={testQuery}
            onChange={(e) => setTestQuery(e.target.value)}
            placeholder="e.g. latest news on HBM supply"
            className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
            onKeyDown={(e) => { if (e.key === 'Enter') test() }}
          />
          <button
            onClick={test}
            disabled={testing}
            className="px-3 py-1 bg-gray-800 hover:bg-gray-700 text-sm text-gray-200 rounded border border-gray-700 disabled:opacity-50"
          >
            {testing ? 'Testing…' : 'Run test'}
          </button>
        </div>

        {testResult && (
          <div className="space-y-2 pt-1">
            <div className="flex items-center gap-2 text-xs">
              <span className="text-gray-500">Answered by:</span>
              {testResult.providerUsed && testResult.providerUsed !== 'none' ? (
                <span className="px-2 py-0.5 rounded bg-green-900/40 border border-green-700 text-green-300 font-mono">
                  {testResult.providerUsed}
                </span>
              ) : (
                <span className="px-2 py-0.5 rounded bg-red-900/40 border border-red-700 text-red-300 font-mono">
                  none
                </span>
              )}
            </div>

            {testResult.fallthrough.length > 0 && (
              <div className="text-xs text-gray-500">
                <div className="mb-1">Fallthrough trace:</div>
                <ul className="space-y-0.5 ml-2">
                  {testResult.fallthrough.map((line, i) => (
                    <li key={i} className="font-mono text-amber-400/80">• {line}</li>
                  ))}
                </ul>
              </div>
            )}

            <div>
              <div className="text-[10px] uppercase text-gray-500 mb-1">Result body</div>
              <pre className="text-[11px] text-gray-300 bg-black/40 border border-gray-800 rounded p-2 max-h-64 overflow-auto whitespace-pre-wrap font-mono">
                {testResult.body || '(empty)'}
              </pre>
            </div>
          </div>
        )}
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

function SkillHubsSection() {
  const [registries, setRegistries] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [form, setForm] = useState({
    id: '', url: '', display_name: '', auth_type: 'none', bearer_token: '',
  })
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const { data } = await skillsApi.listRegistries()
      setRegistries(data.registries || [])
      setError('')
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const resetForm = () => {
    setForm({ id: '', url: '', display_name: '', auth_type: 'none', bearer_token: '' })
    setEditingId(null)
    setShowForm(false)
  }

  const startEdit = (r) => {
    setEditingId(r.id)
    setForm({
      id: r.id,
      url: r.url,
      display_name: r.display_name || '',
      auth_type: r.auth?.type || 'none',
      bearer_token: '',
    })
    setShowForm(true)
  }

  const submit = async () => {
    setBusy(true)
    setError('')
    try {
      const payload = {
        url: form.url,
        display_name: form.display_name || null,
        auth_type: form.auth_type,
        bearer_token: form.bearer_token || null,
      }
      if (editingId) {
        await skillsApi.updateRegistry(editingId, payload)
      } else {
        await skillsApi.createRegistry({ id: form.id, ...payload })
      }
      resetForm()
      await load()
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (id) => {
    if (!confirm(`Remove skill registry "${id}"?`)) return
    try {
      await skillsApi.deleteRegistry(id)
      await load()
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
            <Globe className="w-4 h-4" /> Skill Registry Hubs
          </h2>
          <p className="text-xs text-gray-500 mt-1">
            Configure private skill registries that implement the{' '}
            <span className="text-gray-400">Pantheon Skill Registry Protocol v1.0</span>.
            They become searchable from Skills → Import.
          </p>
        </div>
        {!showForm && (
          <button
            onClick={() => setShowForm(true)}
            className="flex items-center gap-1 px-3 py-1.5 text-xs rounded bg-brand-600 hover:bg-brand-500 text-white"
          >
            <Plus className="w-3.5 h-3.5" /> Add Hub
          </button>
        )}
      </div>

      {error && (
        <div className="mb-3 p-2 rounded border border-red-800 bg-red-900/30 text-xs text-red-300">
          {error}
        </div>
      )}

      {showForm && (
        <div className="mb-4 p-4 rounded border border-gray-800 bg-gray-900/40 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1">ID</label>
              <input
                type="text"
                value={form.id}
                disabled={!!editingId}
                onChange={(e) => setForm({ ...form, id: e.target.value })}
                placeholder="acme"
                className="w-full px-2 py-1.5 text-xs bg-gray-950 border border-gray-800 rounded text-gray-200 disabled:opacity-50"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Display Name</label>
              <input
                type="text"
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                placeholder="Acme Internal Skills"
                className="w-full px-2 py-1.5 text-xs bg-gray-950 border border-gray-800 rounded text-gray-200"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">URL</label>
            <input
              type="text"
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
              placeholder="https://skills.acme.internal"
              className="w-full px-2 py-1.5 text-xs bg-gray-950 border border-gray-800 rounded text-gray-200"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1">Auth</label>
              <select
                value={form.auth_type}
                onChange={(e) => setForm({ ...form, auth_type: e.target.value })}
                className="w-full px-2 py-1.5 text-xs bg-gray-950 border border-gray-800 rounded text-gray-200"
              >
                <option value="none">None</option>
                <option value="bearer">Bearer Token</option>
              </select>
            </div>
            {form.auth_type === 'bearer' && (
              <div>
                <label className="block text-xs text-gray-400 mb-1">
                  Bearer Token {editingId && <span className="text-gray-600">(leave blank to keep)</span>}
                </label>
                <input
                  type="password"
                  value={form.bearer_token}
                  onChange={(e) => setForm({ ...form, bearer_token: e.target.value })}
                  className="w-full px-2 py-1.5 text-xs bg-gray-950 border border-gray-800 rounded text-gray-200"
                />
              </div>
            )}
          </div>
          <div className="flex gap-2 justify-end">
            <button
              onClick={resetForm}
              className="px-3 py-1.5 text-xs rounded border border-gray-800 text-gray-300 hover:bg-gray-900"
            >
              Cancel
            </button>
            <button
              onClick={submit}
              disabled={busy || !form.url || (!editingId && !form.id)}
              className="px-3 py-1.5 text-xs rounded bg-brand-600 hover:bg-brand-500 text-white disabled:opacity-50"
            >
              {editingId ? 'Save' : 'Add'}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-xs text-gray-500">Loading…</div>
      ) : registries.length === 0 ? (
        <div className="p-4 rounded border border-dashed border-gray-800 text-xs text-gray-500 text-center">
          No hubs available.
        </div>
      ) : (
        <div className="space-y-2">
          {registries.map((r) => (
            <div key={r.id} className="p-3 rounded border border-gray-800 bg-gray-900/40 flex items-center justify-between">
              <div className="min-w-0">
                <div className="text-sm text-gray-200 truncate">
                  {r.display_name || r.id}
                  <span className="ml-2 text-xs text-gray-600">[{r.id}]</span>
                  {r.builtin && (
                    <span className="ml-2 px-1.5 py-0.5 text-[10px] rounded bg-gray-800 text-gray-400 uppercase">Built-in</span>
                  )}
                  {r.builtin && !r.searchable && (
                    <span className="ml-1 px-1.5 py-0.5 text-[10px] rounded bg-gray-800 text-gray-500">Not searchable</span>
                  )}
                </div>
                {r.url && <div className="text-xs text-gray-500 truncate">{r.url}</div>}
                {!r.builtin && (
                  <div className="text-xs text-gray-600 mt-0.5">
                    Auth: {r.auth?.type}
                    {r.auth?.type === 'bearer' && (
                      <span className="ml-1">{r.auth?.token_set ? '✓' : '(missing token)'}</span>
                    )}
                  </div>
                )}
              </div>
              {!r.builtin && (
                <div className="flex gap-1 ml-3">
                  <button
                    onClick={() => startEdit(r)}
                    className="p-1.5 text-gray-400 hover:text-gray-200"
                    title="Edit"
                  >
                    <RefreshCw className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => remove(r.id)}
                    className="p-1.5 text-gray-400 hover:text-red-400"
                    title="Remove"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function GlobalTasksSection() {
  const [tasks, setTasks] = useState([])
  const [projectNames, setProjectNames] = useState({}) // id -> name lookup
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState('next_run') // next_run | name | project_name
  const [sortDir, setSortDir] = useState('asc')
  const [expandedTask, setExpandedTask] = useState(null)
  const [logs, setLogs] = useState([])
  const [logsLoading, setLogsLoading] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  // Load project id -> name mapping
  useEffect(() => {
    projectsApi.list().then((res) => {
      const map = {}
      for (const p of res.data.projects || []) {
        map[p.id] = p.name || p.id
      }
      map['default'] = 'Default'
      setProjectNames(map)
    }).catch(() => {})
  }, [])

  const projectName = (id) => projectNames[id] || id

  const loadTasks = async () => {
    setLoading(true)
    try {
      const res = await tasksApi.listAll()
      setTasks(res.data.tasks || [])
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  useEffect(() => { loadTasks() }, [])

  const loadLogs = async (taskId, projectId) => {
    setLogsLoading(true)
    try {
      const res = await tasksApi.getLogs(taskId, projectId)
      setLogs(res.data.logs || [])
    } catch {
      setLogs([])
    }
    setLogsLoading(false)
  }

  const cancelTask = async (taskId) => {
    try {
      await tasksApi.cancel(taskId)
      addNotification({ type: 'success', message: 'Task cancelled' })
      loadTasks()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const toggleSort = (col) => {
    if (sortBy === col) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortBy(col)
      setSortDir('asc')
    }
  }

  const filtered = tasks.filter((t) => {
    if (!search.trim()) return true
    const q = search.toLowerCase()
    const pName = projectName(t.project_id).toLowerCase()
    return (
      (t.name || '').toLowerCase().includes(q) ||
      pName.includes(q) ||
      (t.project_id || '').toLowerCase().includes(q) ||
      (t.description || '').toLowerCase().includes(q)
    )
  })

  const sorted = [...filtered].sort((a, b) => {
    const dir = sortDir === 'asc' ? 1 : -1
    if (sortBy === 'next_run') {
      const aVal = a.next_run || ''
      const bVal = b.next_run || ''
      return aVal.localeCompare(bVal) * dir
    }
    if (sortBy === 'project_name') {
      return projectName(a.project_id).localeCompare(projectName(b.project_id)) * dir
    }
    const aVal = (a[sortBy] || '').toLowerCase()
    const bVal = (b[sortBy] || '').toLowerCase()
    return aVal.localeCompare(bVal) * dir
  })

  const formatTime = (ts) => {
    try { return new Date(ts).toLocaleString() } catch { return ts || '—' }
  }

  const SortHeader = ({ col, label }) => (
    <button onClick={() => toggleSort(col)} className="flex items-center gap-1 text-xs font-semibold text-gray-400 hover:text-gray-200">
      {label}
      {sortBy === col && <ArrowUpDown className="w-3 h-3" />}
    </button>
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold text-gray-200">All Tasks</h2>
        <button onClick={loadTasks} disabled={loading} className="p-2 text-gray-400 hover:text-gray-300 disabled:opacity-50">
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>
      <p className="text-xs text-gray-500">Tasks across all projects. Tasks always run on schedule; the per-project Task Monitor filters by active project.</p>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter by project, task name, or description..."
          className="w-full bg-gray-900 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
        />
      </div>

      {/* Table */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
        {/* Header */}
        <div className="grid grid-cols-12 gap-2 px-4 py-2 bg-gray-850 border-b border-gray-700">
          <div className="col-span-3"><SortHeader col="name" label="Task" /></div>
          <div className="col-span-2"><SortHeader col="project_name" label="Project" /></div>
          <div className="col-span-2"><SortHeader col="next_run" label="Next Run" /></div>
          <div className="col-span-2 text-xs font-semibold text-gray-400">Schedule</div>
          <div className="col-span-1 text-xs font-semibold text-gray-400">Status</div>
          <div className="col-span-2" />
        </div>

        {/* Rows */}
        {sorted.length === 0 ? (
          <p className="text-center text-gray-600 text-sm py-8">
            {tasks.length === 0 ? 'No tasks scheduled' : 'No tasks match your search'}
          </p>
        ) : (
          sorted.map((task) => (
            <div key={task.id}>
              <div
                className="grid grid-cols-12 gap-2 px-4 py-2.5 hover:bg-gray-750 cursor-pointer items-center border-b border-gray-700/50"
                onClick={() => {
                  if (expandedTask === task.id) {
                    setExpandedTask(null)
                  } else {
                    setExpandedTask(task.id)
                    loadLogs(task.id, task.project_id)
                  }
                }}
              >
                <div className="col-span-3 text-sm text-gray-200 truncate">{task.name}</div>
                <div className="col-span-2 text-xs text-gray-500 truncate">{projectName(task.project_id)}</div>
                <div className="col-span-2 text-xs text-gray-500">{formatTime(task.next_run)}</div>
                <div className="col-span-2 text-xs text-gray-500 font-mono truncate">{task.schedule}</div>
                <div className="col-span-1">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                    task.status === 'scheduled' ? 'bg-emerald-900 text-emerald-300' : 'bg-gray-700 text-gray-400'
                  }`}>{task.status}</span>
                </div>
                <div className="col-span-2 flex justify-end">
                  <button
                    onClick={(e) => { e.stopPropagation(); cancelTask(task.id) }}
                    className="text-xs text-red-400 hover:text-red-300 px-2 py-1"
                    title="Cancel task"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              </div>

              {/* Expanded detail */}
              {expandedTask === task.id && (
                <div className="px-4 py-3 bg-gray-900 border-b border-gray-700 space-y-2">
                  {task.description && (
                    <p className="text-xs text-gray-400"><span className="text-gray-500">Description:</span> {task.description}</p>
                  )}
                  <div>
                    <p className="text-xs font-semibold text-gray-400 mb-1">Execution Log</p>
                    <div className="bg-gray-950 rounded text-xs text-gray-400 p-2 max-h-40 overflow-y-auto font-mono scrollbar-thin">
                      {logsLoading ? (
                        <p className="text-gray-600">Loading...</p>
                      ) : logs.length === 0 ? (
                        <p className="text-gray-600">No logs yet</p>
                      ) : (
                        logs.map((log, i) => (
                          <div key={log.id || i} className="whitespace-pre-wrap break-words py-0.5">
                            <span className="text-gray-600">{formatTime(log.timestamp)}</span>{' '}
                            <span className={log.event === 'failed' ? 'text-red-400' : log.event === 'completed' ? 'text-emerald-400' : 'text-gray-400'}>
                              [{log.event}]
                            </span>{' '}
                            {log.details || ''}
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}

function SandboxSection() {
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await systemApi.sandboxHealth()
      setHealth(res.data)
    } catch (e) {
      setError(e?.response?.data?.detail || e.message || 'Request failed')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const backend = health?.backend || 'unknown'
  const status = health?.status || 'unknown'
  const isFirecracker = backend === 'firecracker'
  const tone = isFirecracker && status === 'healthy'
    ? 'border-green-700 bg-green-950'
    : status === 'degraded'
      ? 'border-amber-700 bg-amber-950'
      : 'border-gray-700 bg-gray-900'

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Server className="w-4 h-4 text-brand-400" />
          Sandbox
        </h3>
        <button
          onClick={refresh}
          className="text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1"
        >
          <RefreshCw className="w-3 h-3" />
          Refresh
        </button>
      </div>
      {loading && <div className="text-xs text-gray-500">Loading…</div>}
      {error && <div className="text-xs text-red-400">{error}</div>}
      {!loading && health && (
        <div className={`border rounded-md p-3 text-xs ${tone}`}>
          <div className="flex items-center justify-between mb-2">
            <span className="font-medium text-gray-200">
              Backend: {backend}
            </span>
            <span className="text-gray-400">Status: {status}</span>
          </div>
          {health.note && (
            <div className="text-gray-400 text-xs mb-2">{health.note}</div>
          )}
          {Array.isArray(health.issues) && health.issues.length > 0 && (
            <div className="mt-2 space-y-1">
              <div className="text-amber-300 font-medium">Issues:</div>
              {health.issues.map((iss, i) => (
                <div key={i} className="text-amber-200 pl-2">• {iss}</div>
              ))}
            </div>
          )}
          {health.kvm_available !== undefined && (
            <div className="mt-2 text-gray-400">
              KVM available: {health.kvm_available ? 'yes' : 'no'}
              {health.arch && ` · arch: ${health.arch}`}
            </div>
          )}
          {Array.isArray(health.rootfs_images) && health.rootfs_images.length > 0 && (
            <div className="mt-2 text-gray-400">
              Rootfs images: {health.rootfs_images.join(', ')}
            </div>
          )}
          {!isFirecracker && (
            <div className="mt-3 border-t border-gray-700 pt-2 text-gray-300">
              Subprocess sandbox runs code on this host with no filesystem
              isolation. For real isolation, run{' '}
              <code className="text-brand-300">scripts/setup_firecracker.sh</code>{' '}
              on a Linux+KVM host and set{' '}
              <code className="text-brand-300">PANTHEON_SANDBOX=firecracker</code>.
            </div>
          )}
        </div>
      )}
    </div>
  )
}




// ── Cross-project task-run dashboard (uses /api/tasks/runs) ─────────────────
const RUN_STATUS_BADGE = {
  running:   'bg-blue-900 text-blue-200',
  completed: 'bg-green-900 text-green-200',
  failed:    'bg-red-900 text-red-200',
  cancelled: 'bg-amber-900 text-amber-200',
  queued:    'bg-gray-800 text-gray-300',
}

function fmtDuration(ms) {
  if (!ms && ms !== 0) return ''
  if (ms < 1000) return `${ms}ms`
  if (ms < 60_000) return `${(ms/1000).toFixed(1)}s`
  return `${Math.floor(ms/60_000)}m ${Math.floor((ms%60_000)/1000)}s`
}

function TaskRunsSection() {
  const [runs, setRuns] = useState([])
  const [projectNames, setProjectNames] = useState({})
  const [filter, setFilter] = useState('')
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    projectsApi.list().then((res) => {
      const m = {}
      for (const p of res.data.projects || []) m[p.id] = p.name || p.id
      m.default = 'Default'
      setProjectNames(m)
    }).catch(() => {})
  }, [])

  const refresh = async () => {
    setLoading(true)
    try {
      const params = { limit: 100 }
      if (filter) params.status = filter
      const res = await taskRunsApi.list(params)
      setRuns(res.data?.runs || [])
    } finally { setLoading(false) }
  }
  useEffect(() => { refresh() }, [filter])

  const projectName = (id) => projectNames[id] || id

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Clock className="w-4 h-4 text-brand-400" />
          Task runs (cross-project)
        </h3>
        <div className="flex items-center gap-2">
          <select
            value={filter} onChange={(e) => setFilter(e.target.value)}
            className="text-xs bg-gray-900 border border-gray-800 rounded px-2 py-1"
          >
            <option value="">All statuses</option>
            <option value="running">Running</option>
            <option value="completed">Completed</option>
            <option value="failed">Failed</option>
            <option value="cancelled">Cancelled</option>
          </select>
          <button onClick={refresh} className="text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1">
            <RefreshCw className="w-3 h-3" /> Refresh
          </button>
        </div>
      </div>
      {loading && <div className="text-xs text-gray-500">Loading…</div>}
      {!loading && runs.length === 0 && (
        <div className="text-sm text-gray-500 italic">
          No autonomous task runs yet across any project.
        </div>
      )}
      <div className="space-y-1">
        {runs.map((r) => (
          <div key={r.id} className="p-2.5 rounded border border-gray-800 bg-gray-900">
            <div className="flex items-center gap-2 flex-wrap">
              <span className={`text-[10px] px-1.5 py-0.5 rounded ${RUN_STATUS_BADGE[r.status] || RUN_STATUS_BADGE.queued}`}>
                {r.status}
              </span>
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-brand-900 text-brand-200">
                {projectName(r.project_id)}
              </span>
              <span className="text-sm font-medium text-gray-200 truncate flex-1">{r.task_name}</span>
              <span className="text-[10px] text-gray-500">{r.started_at?.slice(0,16).replace('T',' ')}</span>
              {r.duration_ms != null && (
                <span className="text-[10px] text-gray-500">· {fmtDuration(r.duration_ms)}</span>
              )}
            </div>
            {r.description && <div className="text-xs text-gray-400 mt-1">{r.description}</div>}
            {r.error && <div className="text-xs text-red-400 mt-1">{r.error}</div>}
            {r.session_id && (
              <div className="text-[10px] text-gray-600 mt-1 font-mono">session: {r.session_id.slice(0,16)}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}

function PersonalitySection() {
  return (
    <div>
      <h3 className="text-sm font-semibold text-gray-200 flex items-center gap-2 mb-2">
        <UserIcon className="w-4 h-4 text-brand-400" />
        Global agent identity
      </h3>
      <p className="text-[11px] text-gray-500 mb-4">
        This is the agent's stable identity across every project (the
        soul.md / agent.md files). Per-project tone overrides live in
        each project's settings tab.
      </p>
      <PersonalityEditor />
    </div>
  )
}

export default function Settings() {
  const [tab, setTab] = useState('llms')

  const tabs = [
    { id: 'llms', label: 'LLMs', icon: Cpu },
    { id: 'integrations', label: 'Integrations', icon: Plug },
    { id: 'personality', label: 'Personality', icon: UserIcon },
    { id: 'skills', label: 'Skills', icon: Library },
    { id: 'tasks', label: 'Tasks', icon: Clock },
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
        {tab === 'skills' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-2xl mx-auto p-6 space-y-8">
              <SkillHubsSection />
            </div>
          </div>
        )}
        {tab === 'tasks' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-3xl mx-auto p-6 space-y-8">
              <GlobalTasksSection />
              <div className="border-t border-gray-800" />
              <TaskRunsSection />
            </div>
          </div>
        )}
        {tab === 'personality' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-3xl mx-auto p-6">
              <PersonalitySection />
            </div>
          </div>
        )}
        {tab === 'security' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-2xl mx-auto p-6 space-y-8">
              <SandboxSection />
              <div className="border-t border-gray-800" />
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
