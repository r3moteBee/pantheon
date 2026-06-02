import React, { useState, useEffect } from 'react'
import { Plug, Plus, Trash2, RefreshCw, CheckCircle, XCircle, ChevronDown, ChevronRight, Zap, Eye, EyeOff, Gauge, RotateCcw, ShieldAlert, Pencil, X, Save, Power, Search } from 'lucide-react'
import { useStore } from '../store'
import { mcpApi } from '../api/client'
import HelpDrawer from './help/HelpDrawer'
import { MCP_PROVIDERS } from './help/mcpProviders'


// ── Helpers ────────────────────────────────────────────────────────────────

/** Detect whether a connection is a metered service with usage tracking. */
function isMeteredConnection(conn) {
  const name = (conn.name || '').toLowerCase()
  const url = (conn.url || '').toLowerCase()
  // Tavily is the first supported metered service — add others here
  if (name.includes('tavily') || url.includes('tavily')) return 'tavily'
  return null
}

function barColor(pct) {
  if (pct >= 90) return 'bg-red-500'
  if (pct >= 70) return 'bg-amber-500'
  return 'bg-emerald-500'
}

// ── Inline Usage Panel (renders inside ConnectionCard) ─────────────────────

function ConnectionUsagePanel({ serviceType }) {
  const [usage, setUsage] = useState(null)
  const [editing, setEditing] = useState(false)
  const [dailyLimit, setDailyLimit] = useState('')
  const [monthlyLimit, setMonthlyLimit] = useState('')
  const [saving, setSaving] = useState(false)
  const [loadingUsage, setLoadingUsage] = useState(true)
  const addNotification = useStore((s) => s.addNotification)

  const loadUsage = async () => {
    setLoadingUsage(true)
    try {
      if (serviceType === 'tavily') {
        const res = await mcpApi.getTavilyUsage()
        setUsage(res.data)
        setDailyLimit(String(res.data?.thresholds?.daily_limit || 0))
        setMonthlyLimit(String(res.data?.thresholds?.monthly_limit || 0))
      }
    } catch {
      // Service not configured or endpoint unavailable
    }
    setLoadingUsage(false)
  }

  useEffect(() => { loadUsage() }, [serviceType])

  if (loadingUsage) {
    return <p className="text-[10px] text-gray-600 py-1">Loading usage data...</p>
  }
  if (!usage) return null

  const local = usage.local || {}
  const thresholds = usage.thresholds || {}
  const remote = usage.remote || {}

  // Remote data from Tavily API (key-level and account-level)
  // API returns: { key: { usage, limit, search_usage, ... }, account: { plan_usage, plan_limit, current_plan, ... } }
  const keyData = remote.key || {}
  const accountData = remote.account || {}

  const dailyUsed = local.daily_used || 0
  const monthlyUsed = local.monthly_used || 0
  const dLimit = thresholds.daily_limit || 0
  const mLimit = thresholds.monthly_limit || 0

  const dailyPct = dLimit > 0 ? Math.min(100, (dailyUsed / dLimit) * 100) : 0
  const monthlyPct = mLimit > 0 ? Math.min(100, (monthlyUsed / mLimit) * 100) : 0

  // Account-level plan usage (all keys combined)
  const planUsage = accountData.plan_usage ?? null
  const planLimit = accountData.plan_limit ?? null
  const planPct = planLimit > 0 ? Math.min(100, ((planUsage || 0) / planLimit) * 100) : 0

  // Key-level usage (just this API key)
  const keyUsage = keyData.usage ?? null
  const keyLimit = keyData.limit ?? null
  const keyPct = keyLimit > 0 ? Math.min(100, ((keyUsage || 0) / keyLimit) * 100) : 0

  const handleSave = async () => {
    setSaving(true)
    try {
      if (serviceType === 'tavily') {
        await mcpApi.setTavilyThresholds(parseInt(dailyLimit) || 0, parseInt(monthlyLimit) || 0)
      }
      addNotification({ type: 'success', message: 'Thresholds updated' })
      setEditing(false)
      await loadUsage()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setSaving(false)
  }

  const handleReset = async (period) => {
    try {
      if (serviceType === 'tavily') {
        if (period === 'daily') await mcpApi.resetTavilyDaily()
        else await mcpApi.resetTavilyMonthly()
      }
      addNotification({ type: 'success', message: `${period} usage reset` })
      await loadUsage()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  return (
    <div className="space-y-3">
      {/* Header */}
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-medium text-gray-400 flex items-center gap-1.5">
          <Gauge className="w-3 h-3 text-amber-400" /> API Credit Usage
        </h4>
        <div className="flex items-center gap-2">
          <button
            onClick={() => setEditing(!editing)}
            className="text-[10px] px-2 py-0.5 rounded bg-gray-700 hover:bg-gray-600 text-gray-400 transition-colors"
          >
            {editing ? 'Cancel' : 'Set Limits'}
          </button>
          <button onClick={loadUsage} className="text-gray-500 hover:text-gray-300 transition-colors">
            <RefreshCw className="w-3 h-3" />
          </button>
        </div>
      </div>

      {/* Account-level plan usage (all keys combined) */}
      {planUsage !== null && planLimit !== null && planLimit > 0 && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-gray-500">
              Account ({accountData.current_plan || 'unknown'})
            </span>
            <span className="text-xs font-mono text-gray-300">
              {planUsage.toLocaleString()} / {planLimit.toLocaleString()}
            </span>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-1.5">
            <div className={`h-1.5 rounded-full transition-all ${barColor(planPct)}`} style={{ width: `${planPct}%` }} />
          </div>
          {/* Account-level breakdown by tool type */}
          <div className="flex gap-3 mt-1 text-[10px] text-gray-600">
            {accountData.search_usage != null && <span>Search: {accountData.search_usage}</span>}
            {accountData.extract_usage != null && <span>Extract: {accountData.extract_usage}</span>}
            {accountData.research_usage != null && <span>Research: {accountData.research_usage}</span>}
            {accountData.crawl_usage != null && accountData.crawl_usage > 0 && <span>Crawl: {accountData.crawl_usage}</span>}
            {accountData.map_usage != null && accountData.map_usage > 0 && <span>Map: {accountData.map_usage}</span>}
          </div>
        </div>
      )}

      {/* Key-level usage (this API key only) */}
      {keyUsage !== null && keyLimit !== null && keyLimit > 0 && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-gray-500">This key</span>
            <span className="text-xs font-mono text-gray-300">
              {keyUsage.toLocaleString()} / {keyLimit.toLocaleString()}
            </span>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-1.5">
            <div className={`h-1.5 rounded-full transition-all ${barColor(keyPct)}`} style={{ width: `${keyPct}%` }} />
          </div>
        </div>
      )}

      {/* Local threshold tracking */}
      <div className="grid grid-cols-2 gap-3">
        {/* Daily */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-gray-500">Daily</span>
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-mono text-gray-300">
                {dailyUsed.toFixed(0)}{dLimit > 0 ? ` / ${dLimit}` : ''}
              </span>
              {dailyUsed > 0 && (
                <button onClick={() => handleReset('daily')} title="Reset daily" className="text-gray-600 hover:text-gray-400">
                  <RotateCcw className="w-2.5 h-2.5" />
                </button>
              )}
            </div>
          </div>
          {dLimit > 0 ? (
            <div className="w-full bg-gray-700 rounded-full h-1.5">
              <div className={`h-1.5 rounded-full transition-all ${barColor(dailyPct)}`} style={{ width: `${dailyPct}%` }} />
            </div>
          ) : (
            <p className="text-[10px] text-gray-600">No daily limit</p>
          )}
        </div>

        {/* Monthly */}
        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-gray-500">Monthly</span>
            <div className="flex items-center gap-1.5">
              <span className="text-xs font-mono text-gray-300">
                {monthlyUsed.toFixed(0)}{mLimit > 0 ? ` / ${mLimit}` : ''}
              </span>
              {monthlyUsed > 0 && (
                <button onClick={() => handleReset('monthly')} title="Reset monthly" className="text-gray-600 hover:text-gray-400">
                  <RotateCcw className="w-2.5 h-2.5" />
                </button>
              )}
            </div>
          </div>
          {mLimit > 0 ? (
            <div className="w-full bg-gray-700 rounded-full h-1.5">
              <div className={`h-1.5 rounded-full transition-all ${barColor(monthlyPct)}`} style={{ width: `${monthlyPct}%` }} />
            </div>
          ) : (
            <p className="text-[10px] text-gray-600">No monthly limit</p>
          )}
        </div>
      </div>

      {/* Cost reference */}
      {serviceType === 'tavily' && (
        <p className="text-[10px] text-gray-600">
          Search: 1-2 credits · Extract: 1-2/5 URLs · Map: 1/5-10 URLs · Crawl: map+extract.
          When limits hit, search falls back to built-in web search.
        </p>
      )}

      {/* Threshold editor */}
      {editing && (
        <div className="pt-2 border-t border-gray-700 flex items-end gap-3">
          <div>
            <label className="block text-[10px] text-gray-500 mb-1">Daily limit (0 = unlimited)</label>
            <input
              type="number"
              min="0"
              value={dailyLimit}
              onChange={(e) => setDailyLimit(e.target.value)}
              className="w-24 bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
            />
          </div>
          <div>
            <label className="block text-[10px] text-gray-500 mb-1">Monthly limit (0 = unlimited)</label>
            <input
              type="number"
              min="0"
              value={monthlyLimit}
              onChange={(e) => setMonthlyLimit(e.target.value)}
              className="w-24 bg-gray-900 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
            />
          </div>
          <button
            onClick={handleSave}
            disabled={saving}
            className="px-3 py-1 rounded bg-blue-600 hover:bg-blue-700 text-white text-xs disabled:opacity-50 transition-colors"
          >
            {saving ? 'Saving...' : 'Save'}
          </button>
        </div>
      )}
    </div>
  )
}

// ── Dev Rate Limit Control ─────────────────────────────────────────────────

function DevRateLimitControl({ conn, onUpdate }) {
  const isDevMode = (conn.request_interval_ms || 1000) > 1000
  const [editing, setEditing] = useState(false)
  const [seconds, setSeconds] = useState(String((conn.request_interval_ms || 1000) / 1000))
  const addNotification = useStore((s) => s.addNotification)

  const applyInterval = async (ms) => {
    try {
      await onUpdate(conn.name, { request_interval_ms: ms })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const handleToggle = () => {
    if (isDevMode) {
      // Turn off → back to 1s
      setSeconds('1')
      applyInterval(1000)
    } else {
      // Turn on → 5s default
      setSeconds('5')
      applyInterval(5000)
    }
  }

  const handleSecondsBlur = () => {
    const val = parseFloat(seconds)
    if (!isNaN(val) && val >= 0.5 && val <= 30) {
      applyInterval(Math.round(val * 1000))
    } else {
      setSeconds(String((conn.request_interval_ms || 1000) / 1000))
    }
    setEditing(false)
  }

  // Sync local state when prop changes
  useEffect(() => {
    if (!editing) setSeconds(String((conn.request_interval_ms || 1000) / 1000))
  }, [conn.request_interval_ms])

  return (
    <div className="flex items-center justify-between pt-2 border-t border-gray-700">
      <div className="flex items-center gap-2">
        <ShieldAlert className={`w-3.5 h-3.5 ${isDevMode ? 'text-amber-400' : 'text-gray-600'}`} />
        <div>
          <span className="text-xs text-gray-300">Dev rate limiting</span>
          <div className="flex items-center gap-1.5 mt-0.5">
            {isDevMode ? (
              editing ? (
                <input
                  type="number"
                  min="0.5"
                  max="30"
                  step="0.5"
                  value={seconds}
                  onChange={(e) => setSeconds(e.target.value)}
                  onBlur={handleSecondsBlur}
                  onKeyDown={(e) => e.key === 'Enter' && handleSecondsBlur()}
                  autoFocus
                  className="w-14 bg-gray-900 border border-amber-600 rounded px-1.5 py-0.5 text-[10px] font-mono text-amber-400 focus:outline-none"
                />
              ) : (
                <button
                  onClick={() => setEditing(true)}
                  className="text-[10px] font-mono text-amber-400 hover:text-amber-300 underline decoration-dotted"
                  title="Click to edit interval"
                >
                  {seconds}s
                </button>
              )
            ) : (
              <span className="text-[10px] text-gray-600">Off (1s default)</span>
            )}
            {isDevMode && !editing && (
              <span className="text-[10px] text-gray-600">between requests</span>
            )}
          </div>
        </div>
      </div>
      <button
        onClick={handleToggle}
        className={`relative w-9 h-5 rounded-full transition-colors ${
          isDevMode ? 'bg-amber-600' : 'bg-gray-600'
        }`}
      >
        <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
          isDevMode ? 'translate-x-4' : ''
        }`} />
      </button>
    </div>
  )
}

// ── Connection Edit Form (inline panel inside expanded card) ──────────────

function ConnectionEditForm({ conn, onSave, onCancel }) {
  const isOAuth = conn.auth_type === 'oauth2'
  const [url, setUrl] = useState(conn.url || '')
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [enabled, setEnabled] = useState(conn.enabled !== false)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const urlChanged = url.trim() !== (conn.url || '').trim()
  const enabledChanged = enabled !== (conn.enabled !== false)
  const apiKeyChanged = apiKey.length > 0
  const hasChanges = urlChanged || enabledChanged || apiKeyChanged

  const handleSave = async () => {
    if (!url.trim()) {
      setError('URL cannot be empty')
      return
    }
    setError(null)
    setSaving(true)
    const update = {}
    if (urlChanged) update.url = url.trim()
    if (enabledChanged) update.enabled = enabled
    if (apiKeyChanged) update.api_key = apiKey
    try {
      await onSave(conn.name, update)
    } catch (err) {
      setError(err?.response?.data?.detail || err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="border border-blue-800/60 rounded-md bg-gray-900/60 p-3 space-y-3">
      <h4 className="text-xs font-medium text-blue-300 flex items-center gap-1.5">
        <Pencil className="w-3 h-3" /> Edit connection
      </h4>

      <div>
        <label className="block text-[10px] text-gray-400 mb-1">Server URL</label>
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
        />
        {isOAuth && urlChanged && (
          <p className="text-[10px] text-amber-400 mt-1">
            Changing the URL on an OAuth connection may invalidate the
            current token. Click <b>Re-auth</b> after saving if requests
            start returning 401.
          </p>
        )}
      </div>

      {!isOAuth && (
        <div>
          <label className="block text-[10px] text-gray-400 mb-1">
            API Key <span className="text-gray-600">(leave empty to keep current)</span>
          </label>
          <div className="flex gap-2">
            <input
              type={showKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder={conn.has_api_key ? '••• current key set, leave empty to keep' : 'No API key set'}
              className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-xs text-gray-100 placeholder-gray-600 focus:outline-none focus:border-blue-500"
            />
            <button
              type="button"
              onClick={() => setShowKey(!showKey)}
              className="px-2 text-gray-500 hover:text-gray-300"
            >
              {showKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
            </button>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Power className={`w-3.5 h-3.5 ${enabled ? 'text-emerald-400' : 'text-gray-600'}`} />
          <span className="text-xs text-gray-300">
            {enabled ? 'Enabled — connects on startup' : 'Disabled — skipped on startup'}
          </span>
        </div>
        <button
          type="button"
          onClick={() => setEnabled(!enabled)}
          className={`relative w-9 h-5 rounded-full transition-colors ${
            enabled ? 'bg-emerald-600' : 'bg-gray-600'
          }`}
          title={enabled ? 'Disable connection' : 'Enable connection'}
        >
          <span className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
            enabled ? 'translate-x-4' : ''
          }`} />
        </button>
      </div>

      {error && (
        <p className="text-[10px] text-red-400 bg-red-950/40 border border-red-900 rounded px-2 py-1">
          {error}
        </p>
      )}

      <div className="flex gap-2 pt-1">
        <button
          type="button"
          onClick={handleSave}
          disabled={!hasChanges || saving}
          className="px-3 py-1 rounded bg-blue-600 hover:bg-blue-700 text-white text-[11px] font-medium disabled:opacity-40 flex items-center gap-1"
        >
          <Save className="w-3 h-3" /> {saving ? 'Saving…' : 'Save'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={saving}
          className="px-3 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-300 text-[11px] flex items-center gap-1"
        >
          <X className="w-3 h-3" /> Cancel
        </button>
        {!hasChanges && (
          <span className="text-[10px] text-gray-500 self-center">No changes to save</span>
        )}
      </div>
    </div>
  )
}

// ── Connection Card ────────────────────────────────────────────────────────

function ConnectionCard({ conn, onRemove, onTest, onReconnect, onUpdate, onRefresh, allTools, onAuthorize, onRevokeOauth }) {
  const [expanded, setExpanded] = useState(false)
  const [editing, setEditing] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const addNotification = useStore((s) => s.addNotification)

  const openEditor = () => {
    setEditing(true)
    setExpanded(true)
  }

  const handleSaveEdit = async (name, update) => {
    await onUpdate(name, update)
    addNotification({ type: 'success', message: `${name} updated` })
    setEditing(false)
  }

  // Tools belonging to this connection
  const connTools = (allTools || []).filter((t) => t.connection === conn.name)

  const meteredType = isMeteredConnection(conn)

  const handleTest = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await onTest(conn.name)
      setTestResult(res)
    } catch (err) {
      setTestResult({ status: 'error', message: err.message })
    }
    setTesting(false)
  }

  const statusIcon = conn.connected
    ? <CheckCircle className="w-3.5 h-3.5 text-green-400" />
    : <XCircle className="w-3.5 h-3.5 text-red-400" />

  return (
    <div className={`border rounded-lg overflow-hidden ${conn.enabled ? 'border-gray-700 bg-gray-800' : 'border-gray-800 bg-gray-900 opacity-60'}`}>
      <div className="flex items-start gap-3 p-4">
        <div className="w-8 h-8 rounded-lg bg-blue-900 flex items-center justify-center flex-shrink-0 mt-0.5">
          <Plug className="w-4 h-4 text-blue-400" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-white">{conn.name}</h3>
            {statusIcon}
            {conn.tools_count > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-blue-900 text-blue-300">
                {conn.tools_count} tools
              </span>
            )}
            {meteredType && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-400">
                metered
              </span>
            )}
          </div>
          <p className="text-xs text-gray-500 mt-0.5 truncate">{conn.url}</p>
          <div className="flex items-center gap-2 mt-1 text-[10px] text-gray-600">
            {conn.auth_type === 'oauth2' ? (
              conn.oauth_status === 'ok' ? (
                <span className="flex items-center gap-0.5 text-emerald-500">
                  <CheckCircle className="w-2.5 h-2.5" /> OAuth authorized
                </span>
              ) : (
                <span className="flex items-center gap-0.5 text-amber-500">
                  <ShieldAlert className="w-2.5 h-2.5" /> OAuth — needs sign-in
                </span>
              )
            ) : (
              conn.has_api_key && <span className="flex items-center gap-0.5"><Eye className="w-2.5 h-2.5" /> API key set</span>
            )}
          </div>
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
          {conn.auth_type === 'oauth2' && conn.oauth_status !== 'ok' && (
            <button
              onClick={() => onAuthorize(conn.name)}
              title="Open authorization in browser"
              className="px-2 py-1 rounded bg-amber-600 hover:bg-amber-700 text-white text-[10px] font-semibold"
            >
              Authorize
            </button>
          )}
          {conn.auth_type === 'oauth2' && conn.oauth_status === 'ok' && (
            <button
              onClick={() => onAuthorize(conn.name)}
              title="Re-authorize (e.g., after revocation)"
              className="px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 text-gray-200 text-[10px]"
            >
              Re-auth
            </button>
          )}
          <button
            onClick={handleTest}
            disabled={testing}
            title="Test connection"
            className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-white transition-colors disabled:opacity-50"
          >
            <RefreshCw className={`w-3.5 h-3.5 ${testing ? 'animate-spin' : ''}`} />
          </button>
          <button
            onClick={() => onReconnect(conn.name)}
            title="Reconnect"
            className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-white transition-colors"
          >
            <Plug className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={openEditor}
            title="Edit connection"
            className="p-1.5 rounded hover:bg-gray-700 text-gray-400 hover:text-white transition-colors"
          >
            <Pencil className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => onRemove(conn.name)}
            title="Remove"
            className="p-1.5 rounded hover:bg-red-900 text-gray-500 hover:text-red-400 transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
          <button
            onClick={() => setExpanded(!expanded)}
            className="p-1.5 rounded hover:bg-gray-700 text-gray-500 hover:text-gray-300 transition-colors"
          >
            {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
          </button>
        </div>
      </div>

      {testResult && (
        <div className={`px-4 py-2 text-xs border-t ${testResult.status === 'ok' ? 'bg-green-900/30 border-green-800 text-green-300' : 'bg-red-900/30 border-red-800 text-red-300'}`}>
          {testResult.status === 'ok'
            ? `Connected — ${testResult.tools_count} tools: ${testResult.tool_names?.join(', ')}`
            : `Error: ${testResult.message}`
          }
        </div>
      )}

      {expanded && (
        <div className="border-t border-gray-700 px-4 py-3 bg-gray-850 space-y-3">
          {editing && (
            <ConnectionEditForm
              conn={conn}
              onSave={handleSaveEdit}
              onCancel={() => setEditing(false)}
            />
          )}
          {connTools.length > 0 ? (
            <div>
              <h4 className="text-xs font-medium text-gray-400 mb-2">Tools</h4>
              <div className="space-y-1.5">
                {connTools.map((tool) => (
                  <div key={tool.name} className="flex items-center justify-between gap-2 group">
                    <div className="flex items-center gap-2 min-w-0 flex-1">
                      <Zap className={`w-3 h-3 flex-shrink-0 ${tool.excluded ? 'text-gray-600' : 'text-yellow-400'}`} />
                      <span className={`text-xs font-mono truncate ${tool.excluded ? 'text-gray-600 line-through' : 'text-yellow-300'}`}>
                        {tool.name}
                      </span>
                    </div>
                    <button
                      onClick={async () => {
                        try {
                          await mcpApi.toggleTool(conn.name, tool.name, !tool.excluded)
                          // Refresh tools list
                          await onRefresh()
                        } catch (err) {
                          addNotification({ type: 'error', message: err.message })
                        }
                      }}
                      className={`relative w-7 h-4 rounded-full transition-colors flex-shrink-0 ${
                        tool.excluded ? 'bg-gray-700' : 'bg-green-600'
                      }`}
                      title={tool.excluded ? `Enable ${tool.name}` : `Disable ${tool.name}`}
                    >
                      <span className={`absolute top-0.5 left-0.5 w-3 h-3 rounded-full bg-white transition-transform ${
                        tool.excluded ? '' : 'translate-x-3'
                      }`} />
                    </button>
                  </div>
                ))}
              </div>
            </div>
          ) : conn.connected ? (
            <p className="text-xs text-gray-500">No tools discovered from this server.</p>
          ) : (
            <p className="text-xs text-gray-500">Not connected — click the test button to connect.</p>
          )}

          {/* Dev rate limiting */}
          <DevRateLimitControl conn={conn} onUpdate={onUpdate} />

          {/* Metered service usage — inline in the card */}
          {meteredType && conn.connected && (
            <div className="pt-2 border-t border-gray-700">
              <ConnectionUsagePanel serviceType={meteredType} />
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── Add Connection Form ────────────────────────────────────────────────────

function AddConnectionForm({ onAdd, onCancel, prefill, onPrefillConsumed }) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [authType, setAuthType] = useState('api_key')
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!prefill) return
    if (prefill.name) setName(prefill.name)
    if (prefill.url) setUrl(prefill.url)
    if (prefill.auth_type) setAuthType(prefill.auth_type)
    if (prefill.api_key != null) setApiKey(prefill.api_key)
    onPrefillConsumed?.()
  }, [prefill, onPrefillConsumed])

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!name.trim() || !url.trim()) return
    setSubmitting(true)
    await onAdd(name.trim(), url.trim(), apiKey.trim(), authType)
    setSubmitting(false)
  }


  return (
    <form onSubmit={handleSubmit} className="border border-blue-800 rounded-lg p-4 bg-gray-850 space-y-3">
      <h3 className="text-sm font-medium text-white flex items-center gap-2">
        <Plus className="w-4 h-4 text-blue-400" /> Add MCP Connection
      </h3>
      <div>
        <label className="block text-xs text-gray-400 mb-1">Connection Name</label>
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="e.g., tavily"
          className="w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-blue-500"
        />
      </div>
      <div>
        <label className="block text-xs text-gray-400 mb-1">Server URL</label>
        <input
          type="text"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="e.g., https://mcp.tavily.com/mcp/"
          className="w-full bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-blue-500"
        />
      </div>
      <div>
        <label className="block text-xs text-gray-400 mb-1">Authentication</label>
        <div className="flex gap-3 text-xs text-gray-300">
          <label className="flex items-center gap-1.5 cursor-pointer">
            <input
              type="radio"
              name="authType"
              value="api_key"
              checked={authType === 'api_key'}
              onChange={() => setAuthType('api_key')}
              className="accent-blue-500"
            />
            API key
          </label>
          <label className="flex items-center gap-1.5 cursor-pointer">
            <input
              type="radio"
              name="authType"
              value="oauth2"
              checked={authType === 'oauth2'}
              onChange={() => setAuthType('oauth2')}
              className="accent-blue-500"
            />
            OAuth 2.1 (browser sign-in)
          </label>
        </div>
        {authType === 'oauth2' && (
          <p className="text-[10px] text-gray-500 mt-1.5">
            After adding, click <b>Authorize</b> on the connection card —
            a browser tab will open for sign-in.
          </p>
        )}
      </div>
      {authType === 'api_key' && (
        <div>
          <label className="block text-xs text-gray-400 mb-1">API Key (optional)</label>
          <div className="flex gap-2">
            <input
              type={showKey ? 'text' : 'password'}
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Server API key"
              className="flex-1 bg-gray-800 border border-gray-700 rounded-md px-3 py-2 text-sm text-gray-100 placeholder-gray-600 focus:outline-none focus:border-blue-500"
            />
            <button
              type="button"
              onClick={() => setShowKey(!showKey)}
              className="px-2 text-gray-500 hover:text-gray-300"
            >
              {showKey ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>
        </div>
      )}
      <div className="flex gap-2 pt-1">
        <button
          type="submit"
          disabled={!name.trim() || !url.trim() || submitting}
          className="px-4 py-1.5 rounded-md bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium disabled:opacity-50 transition-colors"
        >
          {submitting
            ? (authType === 'oauth2' ? 'Adding...' : 'Connecting...')
            : (authType === 'oauth2' ? 'Add (Authorize next)' : 'Add & Connect')}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="px-4 py-1.5 rounded-md bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs transition-colors"
        >
          Cancel
        </button>
      </div>
    </form>
  )
}

// ── Main Component ─────────────────────────────────────────────────────────

export default function MCPConnections() {
  const [connections, setConnections] = useState([])
  const [allTools, setAllTools] = useState([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [prefill, setPrefill] = useState(null)
  const [scanning, setScanning] = useState(false)
  const [scannedPorts, setScannedPorts] = useState([])
  const addNotification = useStore((s) => s.addNotification)

  const handleScan = async () => {
    setScanning(true)
    setScannedPorts([])
    try {
      const res = await mcpApi.scanPorts()
      const discovered = res.data.discovered || []
      setScannedPorts(discovered)
      if (discovered.length === 0) {
        addNotification({ type: 'info', message: 'No running local MCP servers found.' })
      } else {
        addNotification({ type: 'success', message: `Found ${discovered.length} active local port(s)!` })
      }
    } catch (err) {
      addNotification({ type: 'error', message: `Scan failed: ${err.message}` })
    } finally {
      setScanning(false)
    }
  }

  const useProvider = (p) => {
    setPrefill({
      name: p.name.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
      url: p.url,
      auth_type: 'api_key',
      api_key: '',
    })
    setShowAdd(true)
    setTimeout(() => {
      document.querySelector('form')?.scrollIntoView({ behavior: 'smooth' })
    }, 100)
  }


  const loadConnections = async () => {
    try {
      const [connRes, toolsRes] = await Promise.all([
        mcpApi.listConnections(),
        mcpApi.listTools(),
      ])
      setConnections(connRes.data.connections || [])
      setAllTools(toolsRes.data.tools || [])
    } catch (err) {
      addNotification({ type: 'error', message: `Failed to load MCP connections: ${err.message}` })
    }
    setLoading(false)
  }

  useEffect(() => {
    loadConnections()
  }, [])

  const handleAdd = async (name, url, apiKey, authType = 'api_key') => {
    try {
      const res = await mcpApi.addConnection(name, url, apiKey, {}, true, authType)
      const status = res.data.status
      if (status === 'connected') {
        addNotification({ type: 'success', message: `${name} connected — ${res.data.tools?.length || 0} tools discovered` })
      } else if (status === 'added_but_connection_failed') {
        addNotification({ type: 'error', message: `${name} added but connection failed: ${res.data.error}` })
      } else if (status === 'added' && res.data.next === 'start_oauth') {
        addNotification({ type: 'success', message: `${name} added — starting OAuth…` })
        setShowAdd(false)
        await loadConnections()
        await handleAuthorize(name)
        return
      } else {
        addNotification({ type: 'success', message: `${name} added` })
      }
      setShowAdd(false)
      await loadConnections()
    } catch (err) {
      addNotification({ type: 'error', message: err.response?.data?.detail || err.message })
    }
  }

  const handleAuthorize = async (name) => {
    try {
      const res = await mcpApi.startOauth(name)
      const url = res.data.authorize_url
      if (!url) throw new Error('No authorize_url returned')
      // Open in a new tab — the callback completes the exchange server-side.
      const popup = window.open(url, '_blank', 'noopener,noreferrer')
      if (!popup) {
        addNotification({
          type: 'error',
          message: 'Browser blocked the OAuth popup — please allow popups and try again.',
        })
        return
      }
      addNotification({ type: 'success', message: `Authorizing ${name} — finish sign-in in the new tab.` })
      // Poll the connections list so the UI reflects the new auth status
      // as soon as the callback completes (typically within seconds).
      let elapsed = 0
      const interval = setInterval(async () => {
        elapsed += 2000
        await loadConnections()
        if (elapsed >= 60000) clearInterval(interval)
      }, 2000)
    } catch (err) {
      addNotification({
        type: 'error',
        message: `OAuth start failed: ${err.response?.data?.detail || err.message}`,
      })
    }
  }

  const handleRevokeOauth = async (name) => {
    try {
      await mcpApi.revokeOauth(name)
      addNotification({ type: 'success', message: `OAuth tokens revoked for ${name}` })
      await loadConnections()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const handleRemove = async (name) => {
    try {
      await mcpApi.removeConnection(name)
      addNotification({ type: 'success', message: `${name} removed` })
      await loadConnections()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const handleTest = async (name) => {
    const res = await mcpApi.testConnection(name)
    await loadConnections()
    return res.data
  }

  const handleReconnect = async (name) => {
    try {
      const res = await mcpApi.reconnect(name)
      if (res.data.status === 'connected') {
        addNotification({ type: 'success', message: `${name} reconnected — ${res.data.tools_count} tools` })
      } else {
        addNotification({ type: 'error', message: `Reconnect failed: ${res.data.message || 'unknown'}` })
      }
      await loadConnections()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const handleUpdate = async (name, data) => {
    await mcpApi.updateConnection(name, data)
    await loadConnections()
  }

  return (
    <div className="h-full overflow-y-auto p-6 scrollbar-thin">
      <div className="max-w-4xl mx-auto">
        <div className="flex items-center justify-between mb-6">
          <div className="flex items-center gap-3">
            <Plug className="w-5 h-5 text-blue-400" />
            <h1 className="text-lg font-semibold text-white">MCP Connections</h1>
            <span className="text-xs text-gray-500">{connections.length} configured</span>
            {allTools.length > 0 && (
              <span className="text-xs text-blue-400">
                {allTools.filter((t) => !t.excluded).length} tools active
                {allTools.some((t) => t.excluded) && ` (${allTools.filter((t) => t.excluded).length} disabled)`}
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={handleScan}
              disabled={scanning}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-gray-800 hover:bg-gray-700 text-xs text-gray-300 font-medium transition-colors border border-gray-700 disabled:opacity-50"
            >
              <Search className={`w-3 h-3 ${scanning ? 'animate-spin text-blue-400' : ''}`} />
              {scanning ? 'Scanning...' : 'Scan Local Ports'}
            </button>
            {!showAdd && (
              <button
                onClick={() => {
                  setPrefill(null)
                  setShowAdd(true)
                }}
                className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-blue-600 hover:bg-blue-700 text-xs text-white font-medium transition-colors"
              >
                <Plus className="w-3 h-3" /> Add Connection
              </button>
            )}
          </div>
        </div>

        <p className="text-sm text-gray-400 mb-6">
          Connect to external MCP servers to give the agent access to additional tools.
          Tools discovered from connected servers are automatically available to the agent alongside built-in tools.
        </p>

        {scannedPorts.length > 0 && (
          <div className="mb-6 p-4 rounded-lg bg-blue-950/30 border border-blue-900/50 space-y-2">
            <h4 className="text-xs font-semibold text-blue-300 flex items-center gap-1.5">
              <Plug className="w-3.5 h-3.5 text-blue-400" /> Discovered Local MCP Servers
            </h4>
            <p className="text-[11px] text-gray-400">
              The following running servers were found on your machine. Click to add them instantly:
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {scannedPorts.map((server) => (
                <div key={server.port} className="flex items-center justify-between p-2 rounded bg-gray-800/80 border border-gray-700/80 text-xs">
                  <div>
                    <span className="font-medium text-white block">{server.name}</span>
                    <span className="font-mono text-[10px] text-gray-500">{server.url}</span>
                  </div>
                  <button
                    onClick={() => {
                      setPrefill({
                        name: server.name.toLowerCase().replace(/[^a-z0-9]+/g, '-'),
                        url: server.url,
                        auth_type: 'api_key',
                        api_key: '',
                      })
                      setShowAdd(true)
                    }}
                    className="px-2.5 py-1 rounded bg-blue-600/90 hover:bg-blue-600 text-white text-[10px] font-medium transition-all"
                  >
                    Use
                  </button>
                </div>
              ))}
            </div>
          </div>
        )}

        {showAdd && (
          <div className="mb-4">
            <AddConnectionForm
              onAdd={handleAdd}
              onCancel={() => setShowAdd(false)}
              prefill={prefill}
              onPrefillConsumed={() => setPrefill(null)}
            />
          </div>
        )}

        {loading ? (
          <div className="flex items-center gap-2 text-sm text-gray-500">
            <RefreshCw className="w-4 h-4 animate-spin" /> Loading connections...
          </div>
        ) : connections.length === 0 && !showAdd ? (
          <div className="text-center py-12 text-gray-600">
            <Plug className="w-8 h-8 mx-auto mb-3 opacity-50" />
            <p className="text-sm">No MCP connections configured.</p>
            <p className="text-xs mt-1">Add a connection to integrate external tools like Tavily search, database access, and more.</p>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="mb-6">
              <HelpDrawer title="Popular MCP Presets Helper" storageKey="help.mcp-presets">
                <p className="text-xs text-gray-400 mb-3">
                  Select one of these popular pre-configured MCP servers to pre-fill the form fields.
                  Click the links on the right to sign up for accounts/keys where required.
                </p>
                <div className="overflow-x-auto">
                  <table className="w-full text-xs text-left">
                    <thead className="text-gray-500 uppercase tracking-wider text-[10px]">
                      <tr className="border-b border-gray-800">
                        <th className="pb-2 font-medium">Server Preset</th>
                        <th className="pb-2 font-medium">Default URL</th>
                        <th className="pb-2 font-medium">Description</th>
                        <th className="pb-2 font-medium">Resources</th>
                      </tr>
                    </thead>
                    <tbody className="text-gray-300 divide-y divide-gray-800">
                      {MCP_PROVIDERS.map((p) => (
                        <tr key={p.name} className="hover:bg-gray-850/30">
                          <td className="py-2.5 pr-4">
                            <button
                              type="button"
                              onClick={() => useProvider(p)}
                              className="text-left text-blue-400 hover:text-blue-300 font-semibold focus:outline-none hover:underline"
                            >
                              {p.name}
                            </button>
                          </td>
                          <td className="py-2.5 pr-4 font-mono text-[10px] text-gray-500">
                            {p.url}
                          </td>
                          <td className="py-2.5 pr-4 text-gray-400">
                            {p.description}
                          </td>
                          <td className="py-2.5">
                            {p.signup_url ? (
                              <a
                                href={p.signup_url}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="text-blue-400 hover:underline"
                              >
                                {p.signup_label}
                              </a>
                            ) : (
                              <span className="text-gray-500 italic">Self-contained</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </HelpDrawer>
            </div>

            {connections.map((conn) => (
              <ConnectionCard
                key={conn.name}
                conn={conn}
                onRemove={handleRemove}
                onTest={handleTest}
                onReconnect={handleReconnect}
                onUpdate={handleUpdate}
                onRefresh={loadConnections}
                onAuthorize={handleAuthorize}
                onRevokeOauth={handleRevokeOauth}
                allTools={allTools}
              />
            ))}
          </div>
        )}

        {allTools.length > 0 && (
          <div className="mt-8">
            <h2 className="text-sm font-medium text-gray-300 mb-3 flex items-center gap-2">
              <Zap className="w-4 h-4 text-yellow-400" /> All Discovered Tools
            </h2>
            <div className="bg-gray-800 rounded-lg border border-gray-700 divide-y divide-gray-700">
              {allTools.filter((t) => !t.excluded).map((tool) => (
                <div key={tool.prefixed_name} className="px-4 py-2.5 flex items-start gap-3">
                  <Zap className="w-3 h-3 text-yellow-400 mt-1 flex-shrink-0" />
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono font-medium text-yellow-300">{tool.name}</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-700 text-gray-500">{tool.connection}</span>
                    </div>
                    <p className="text-[10px] text-gray-500 mt-0.5 line-clamp-1">{tool.description}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
