import React, { useState, useEffect } from 'react'
import { Save, Eye, EyeOff, Trash2, Plus, Check, X, RefreshCw, Search, Brain, ChevronDown, ChevronRight, MessageCircle, RotateCw } from 'lucide-react'
import { useStore } from '../store'
import { settingsApi } from '../api/client'

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

function AgentBehaviorSection() {
  const [memoryRecall, setMemoryRecall] = useState(true)
  const [loading, setLoading] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  useEffect(() => {
    settingsApi.get().then((res) => {
      // Default to true if the field is missing
      setMemoryRecall(res.data.memory_recall_enabled !== false)
    }).catch(() => {})
  }, [])

  const toggle = async (newValue) => {
    setLoading(true)
    try {
      await settingsApi.update({ memory_recall_enabled: newValue })
      setMemoryRecall(newValue)
      addNotification({
        type: 'success',
        message: newValue ? 'Memory recall augmentation enabled' : 'Memory recall augmentation disabled',
      })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <Brain className="w-4 h-4 text-gray-400" />
        <h3 className="text-sm font-semibold text-gray-200">Agent Behavior</h3>
      </div>

      <div className="flex items-center justify-between bg-gray-800 rounded-lg p-4 border border-gray-700">
        <div className="flex-1 mr-4">
          <p className="text-sm font-medium text-gray-200">Memory Recall Augmentation</p>
          <p className="text-xs text-gray-500 mt-1">
            Pre-loads relevant memories from semantic, episodic, and graph tiers into each prompt.
            Disable this if chat is crashing or ChromaDB is unavailable.
          </p>
        </div>
        <button
          onClick={() => toggle(!memoryRecall)}
          disabled={loading}
          className={`relative inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none disabled:opacity-50 ${
            memoryRecall ? 'bg-brand-600' : 'bg-gray-600'
          }`}
        >
          <span
            className={`inline-block h-4 w-4 transform rounded-full bg-white transition-transform ${
              memoryRecall ? 'translate-x-6' : 'translate-x-1'
            }`}
          />
        </button>
      </div>
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
          <AgentBehaviorSection />
          <div className="border-t border-gray-800" />
          <SearchSection />
          <div className="border-t border-gray-800" />
          <TelegramSection />
          <div className="border-t border-gray-800" />
          <SecretsSection />
        </div>
      </div>
    </div>
  )
}
