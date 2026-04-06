import React, { useState, useEffect } from 'react'
import { Plug, Plus, Trash2, RefreshCw, CheckCircle, XCircle, ChevronDown, ChevronRight, Zap, Eye, EyeOff, Gauge, RotateCcw, ShieldAlert } from 'lucide-react'
import { useStore } from '../store'
import { mcpApi } from '../api/client'

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
  const keyData = remote.key || {}
  const accountData = remote.account || {}

  const dailyUsed = local.daily_used || 0
  const monthlyUsed = local.monthly_used || 0
  const dLimit = thresholds.daily_limit || 0
  const mLimit = thresholds.monthly_limit || 0

  const dailyPct = dLimit > 0 ? Math.min(100, (dailyUsed / dLimit) * 100) : 0
  const monthlyPct = mLimit > 0 ? Math.min(100, (monthlyUsed / mLimit) * 100) : 0

  // Remote account usage (if available)
  const planUsage = accountData.current_period_usage ?? keyData.usage ?? null
  const planLimit = accountData.plan_limit ?? keyData.limit ?? null
  const planPct = planLimit > 0 ? Math.min(100, ((planUsage || 0) / planLimit) * 100) : 0

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

      {/* Remote account usage (from real API) */}
      {planUsage !== null && planLimit !== null && planLimit > 0 && (
        <div>
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-gray-500">
              Account plan ({accountData.current_plan || keyData.plan || 'unknown'})
            </span>
            <span className="text-xs font-mono text-gray-300">
              {planUsage.toLocaleString()} / {planLimit.toLocaleString()}
            </span>
          </div>
          <div className="w-full bg-gray-700 rounded-full h-1.5">
            <div className={`h-1.5 rounded-full transition-all ${barColor(planPct)}`} style={{ width: `${planPct}%` }} />
          </div>
        </div>
      )}

      {/* Remote breakdown (search vs extract) */}
      {(keyData.search_usage != null || keyData.extract_usage != null) && (
        <div className="flex gap-4 text-[10px] text-gray-500">
          {keyData.search_usage != null && <span>Search: {keyData.search_usage.toLocaleString()}</span>}
          {keyData.extract_usage != null && <span>Extract: {keyData.extract_usage.toLocaleString()}</span>}
          {keyData.crawl_usage != null && <span>Crawl: {keyData.crawl_usage.toLocaleString()}</span>}
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

// ── Connection Card ────────────────────────────────────────────────────────

function ConnectionCard({ conn, onRemove, onTest, onReconnect, onUpdate, onRefresh, allTools }) {
  const [expanded, setExpanded] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState(null)
  const addNotification = useStore((s) => s.addNotification)

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
            {conn.has_api_key && <span className="flex items-center gap-0.5"><Eye className="w-2.5 h-2.5" /> API key set</span>}
          </div>
        </div>
        <div className="flex items-center gap-1.5 flex-shrink-0">
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

          {/* Request throttle control */}
          <div className="pt-2 border-t border-gray-700">
            <div className="flex items-center gap-2 mb-2">
              <ShieldAlert className="w-3.5 h-3.5 text-amber-400" />
              <span className="text-xs text-gray-300">Request throttle</span>
              <span className="text-[10px] font-mono text-amber-400">{((conn.request_interval_ms || 1000) / 1000).toFixed(1)}s</span>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-[10px] text-gray-600 w-6">1s</span>
              <input
                type="range"
                min={1000}
                max={10000}
                step={500}
                value={conn.request_interval_ms || 1000}
                onChange={async (e) => {
                  const newInterval = parseInt(e.target.value)
                  try {
                    await onUpdate(conn.name, { request_interval_ms: newInterval })
                  } catch (err) {
                    addNotification({ type: 'error', message: err.message })
                  }
                }}
                className="flex-1 h-1.5 accent-amber-500 cursor-pointer"
              />
              <span className="text-[10px] text-gray-600 w-6">10s</span>
            </div>
            <p className="text-[10px] text-gray-600 mt-1">
              Min interval between requests. Increase for free/dev-tier API keys to avoid rate limits.
            </p>
          </div>

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

function AddConnectionForm({ onAdd, onCancel }) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [showKey, setShowKey] = useState(false)
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!name.trim() || !url.trim()) return
    setSubmitting(true)
    await onAdd(name.trim(), url.trim(), apiKey.trim())
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
      <div className="flex gap-2 pt-1">
        <button
          type="submit"
          disabled={!name.trim() || !url.trim() || submitting}
          className="px-4 py-1.5 rounded-md bg-blue-600 hover:bg-blue-700 text-white text-xs font-medium disabled:opacity-50 transition-colors"
        >
          {submitting ? 'Connecting...' : 'Add & Connect'}
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
  const addNotification = useStore((s) => s.addNotification)

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

  const handleAdd = async (name, url, apiKey) => {
    try {
      const res = await mcpApi.addConnection(name, url, apiKey)
      const status = res.data.status
      if (status === 'connected') {
        addNotification({ type: 'success', message: `${name} connected — ${res.data.tools?.length || 0} tools discovered` })
      } else if (status === 'added_but_connection_failed') {
        addNotification({ type: 'error', message: `${name} added but connection failed: ${res.data.error}` })
      } else {
        addNotification({ type: 'success', message: `${name} added` })
      }
      setShowAdd(false)
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
          {!showAdd && (
            <button
              onClick={() => setShowAdd(true)}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-blue-600 hover:bg-blue-700 text-xs text-white font-medium transition-colors"
            >
              <Plus className="w-3 h-3" /> Add Connection
            </button>
          )}
        </div>

        <p className="text-sm text-gray-400 mb-6">
          Connect to external MCP servers to give the agent access to additional tools.
          Tools discovered from connected servers are automatically available to the agent alongside built-in tools.
        </p>

        {showAdd && (
          <div className="mb-4">
            <AddConnectionForm onAdd={handleAdd} onCancel={() => setShowAdd(false)} />
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
            {connections.map((conn) => (
              <ConnectionCard
                key={conn.name}
                conn={conn}
                onRemove={handleRemove}
                onTest={handleTest}
                onReconnect={handleReconnect}
                onUpdate={handleUpdate}
                onRefresh={loadConnections}
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
