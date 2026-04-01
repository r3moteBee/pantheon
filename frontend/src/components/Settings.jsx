import React, { useState, useEffect } from 'react'
import { Save, Eye, EyeOff, Trash2, Plus, Check, X, RefreshCw, Search } from 'lucide-react'
import { useStore } from '../store'
import { settingsApi } from '../api/client'

function LLMSection() {
  const [settings, setSettings] = useState({
    llm_base_url: '',
    llm_api_key: '',       // new value only; blank = keep existing
    llm_model: '',
    llm_prefill_model: '',
    embedding_model: '',
  })
  const [apiKeySet, setApiKeySet] = useState(false)
  const [models, setModels] = useState([])
  const [showKey, setShowKey] = useState(false)
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
        llm_api_key:       '',                      // never returned; blank = keep existing
        llm_model:         d.llm_model         || '',
        llm_prefill_model: d.llm_prefill_model || '',
        embedding_model:   d.embedding_model   || '',
      })
      setApiKeySet(d.llm_api_key_set || false)
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
      await settingsApi.testConnection()
      setTestStatus('success')
      addNotification({ type: 'success', message: 'Connection successful' })
      setTimeout(() => setTestStatus(null), 3000)
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
        embedding_model:   settings.embedding_model,
      }
      // Only send key if user typed a new one
      if (settings.llm_api_key) {
        payload.llm_api_key = settings.llm_api_key
        setApiKeySet(true)
      }
      await settingsApi.update(payload)
      setSettings((s) => ({ ...s, llm_api_key: '' }))  // clear the key field after save
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
  const [searchUrl, setSearchUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [apiKeySet, setApiKeySet] = useState(false)
  const [loading, setLoading] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  useEffect(() => {
    settingsApi.get().then((res) => {
      setSearchUrl(res.data.search_url || '')
      setApiKeySet(res.data.search_api_key_set || false)
    }).catch(() => {})
  }, [])

  const save = async () => {
    setLoading(true)
    try {
      const payload = { search_url: searchUrl }
      if (apiKey) payload.search_api_key = apiKey
      await settingsApi.update(payload)
      if (apiKey) setApiKeySet(true)
      addNotification({ type: 'success', message: 'Search settings saved' })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  const PRESETS = [
    { label: 'SearXNG (local)',  value: 'http://localhost:8080' },
    { label: 'Brave Search API', value: 'https://api.search.brave.com/res/v1/web/search' },
    { label: 'DuckDuckGo (default)', value: '' },
  ]

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Search className="w-4 h-4 text-gray-400" />
        <h3 className="text-sm font-semibold text-gray-200">Web Search</h3>
      </div>

      <p className="text-xs text-gray-500">
        Configure the backend the agent uses for <code className="text-gray-400">web_search</code> calls.
        Leave blank to use DuckDuckGo (no key required).
      </p>

      {/* Quick presets */}
      <div className="flex flex-wrap gap-2">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            onClick={() => setSearchUrl(p.value)}
            className={`px-2 py-1 rounded text-xs border transition-colors ${
              searchUrl === p.value
                ? 'bg-brand-600 border-brand-500 text-white'
                : 'bg-gray-800 border-gray-700 text-gray-400 hover:text-white hover:border-gray-500'
            }`}
          >
            {p.label}
          </button>
        ))}
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-400 mb-2">Search URL</label>
        <input
          type="text"
          value={searchUrl}
          onChange={(e) => setSearchUrl(e.target.value)}
          placeholder="http://localhost:8080  (blank = DuckDuckGo)"
          className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500"
        />
      </div>

      <div>
        <label className="block text-xs font-medium text-gray-400 mb-2">
          API Key
          {apiKeySet && !apiKey && (
            <span className="ml-2 text-green-500 font-normal">✓ key saved</span>
          )}
        </label>
        <div className="flex gap-2">
          <input
            type={showKey ? 'text' : 'password'}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={apiKeySet ? '(leave blank to keep existing)' : 'Optional — required for Brave Search'}
            className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500"
          />
          <button
            onClick={() => setShowKey(!showKey)}
            className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-gray-400"
          >
            {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
          </button>
        </div>
      </div>

      <button
        onClick={save}
        disabled={loading}
        className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-lg disabled:opacity-50"
      >
        <Save className="w-4 h-4" />
        Save Search Settings
      </button>
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
      setSecrets(res.data.secrets || [])
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  const addSecret = async () => {
    if (!newKey.trim() || !newValue.trim()) return
    try {
      await settingsApi.setSecret(newKey, newValue)
      setNewKey('')
      setNewValue('')
      addNotification({ type: 'success', message: 'Secret saved' })
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
              placeholder="TELEGRAM_BOT_TOKEN"
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
                <div className="flex items-center gap-2 mt-1">
                  <span className="text-xs text-gray-600">
                    {showValues[secret.key] ? secret.value : '***'.repeat(4)}
                  </span>
                  <button
                    onClick={() => setShowValues({ ...showValues, [secret.key]: !showValues[secret.key] })}
                    className="text-xs text-gray-500 hover:text-gray-400"
                  >
                    {showValues[secret.key] ? <EyeOff className="w-3 h-3" /> : <Eye className="w-3 h-3" />}
                  </button>
                </div>
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

export default function Settings() {
  return (
    <div className="flex flex-col h-full bg-gray-950">
      {/* Header */}
      <div className="px-6 py-4 bg-gray-900 border-b border-gray-800">
        <h1 className="text-xl font-bold text-gray-100">Settings</h1>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto scrollbar-thin">
        <div className="max-w-2xl mx-auto p-6 space-y-8">
          <LLMSection />
          <div className="border-t border-gray-800" />
          <SearchSection />
          <div className="border-t border-gray-800" />
          <SecretsSection />
        </div>
      </div>
    </div>
  )
}
