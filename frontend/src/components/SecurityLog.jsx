import React, { useState, useEffect } from 'react'
import { Shield, RefreshCw, Trash2, ChevronDown, ChevronUp, AlertTriangle, ShieldX, ShieldCheck, Key, Settings, Zap } from 'lucide-react'
import { settingsApi } from '../api/client'
import { useStore } from '../store'

const EVENT_CONFIG = {
  'auth.login_success':          { icon: ShieldCheck, color: 'text-green-400', label: 'Login success' },
  'auth.login_failure':          { icon: ShieldX, color: 'text-red-400', label: 'Login failure' },
  'skill.scan_passed':           { icon: ShieldCheck, color: 'text-green-400', label: 'Scan passed' },
  'skill.scan_failed':           { icon: ShieldX, color: 'text-red-400', label: 'Scan failed' },
  'skill.scan_all':              { icon: Shield, color: 'text-blue-400', label: 'Scan all' },
  'skill.enabled':               { icon: Zap, color: 'text-green-400', label: 'Skill enabled' },
  'skill.disabled':              { icon: Zap, color: 'text-gray-500', label: 'Skill disabled' },
  'skill.override_used':         { icon: AlertTriangle, color: 'text-amber-400', label: 'Override used' },
  'skill.override_failed':       { icon: ShieldX, color: 'text-red-400', label: 'Override failed' },
  'skill.quarantined':           { icon: ShieldX, color: 'text-red-400', label: 'Quarantined' },
  'skill.unquarantined':         { icon: ShieldCheck, color: 'text-amber-400', label: 'Unquarantined' },
  'skill.deleted':               { icon: Trash2, color: 'text-red-400', label: 'Skill deleted' },
  'skill.name_collision_blocked': { icon: AlertTriangle, color: 'text-amber-400', label: 'Name collision blocked' },
  'skill.execution_start':       { icon: Zap, color: 'text-blue-400', label: 'Script started' },
  'skill.execution_timeout':     { icon: AlertTriangle, color: 'text-amber-400', label: 'Script timeout' },
  'skill.execution_failed':      { icon: ShieldX, color: 'text-red-400', label: 'Script failed' },
  'skill.path_traversal_blocked': { icon: ShieldX, color: 'text-red-500', label: 'Path traversal blocked' },
  'vault.secret_set':            { icon: Key, color: 'text-blue-400', label: 'Secret set' },
  'vault.secret_deleted':        { icon: Key, color: 'text-amber-400', label: 'Secret deleted' },
  'settings.updated':            { icon: Settings, color: 'text-blue-400', label: 'Settings updated' },
}

const LEVEL_COLOR = {
  CRITICAL: 'bg-red-900/40 border-red-800',
  WARNING: 'bg-amber-900/20 border-amber-900/40',
  INFO: 'bg-gray-800 border-gray-700',
}

const FILTER_OPTIONS = [
  { value: 'all', label: 'All Events' },
  { value: 'auth', label: 'Authentication' },
  { value: 'skill', label: 'Skills' },
  { value: 'vault', label: 'Vault' },
  { value: 'settings', label: 'Settings' },
  { value: 'warning', label: 'Warnings Only' },
]

function LogEntry({ entry }) {
  const [expanded, setExpanded] = useState(false)
  const cfg = EVENT_CONFIG[entry.event] || { icon: Shield, color: 'text-gray-400', label: entry.event }
  const Icon = cfg.icon
  const levelStyle = LEVEL_COLOR[entry.level] || LEVEL_COLOR.INFO

  // Build detail fields (everything except ts, event, level)
  const details = Object.entries(entry).filter(
    ([k]) => !['ts', 'event', 'level'].includes(k)
  )

  const ts = entry.ts ? new Date(entry.ts).toLocaleString() : ''

  return (
    <div
      className={`border rounded px-3 py-2 cursor-pointer transition-colors hover:brightness-110 ${levelStyle}`}
      onClick={() => details.length > 0 && setExpanded(!expanded)}
    >
      <div className="flex items-center gap-2">
        <Icon className={`w-3.5 h-3.5 flex-shrink-0 ${cfg.color}`} />
        <span className={`text-xs font-medium ${cfg.color}`}>{cfg.label}</span>
        <span className="flex-1" />
        {details.length > 0 && (
          <span className="text-[10px] text-gray-600">
            {details.slice(0, 3).map(([k, v]) => `${k}: ${typeof v === 'object' ? JSON.stringify(v) : v}`).join(' · ')}
          </span>
        )}
        <span className="text-[10px] text-gray-600 flex-shrink-0">{ts}</span>
        {details.length > 0 && (
          expanded ? <ChevronUp className="w-3 h-3 text-gray-600" /> : <ChevronDown className="w-3 h-3 text-gray-600" />
        )}
      </div>
      {expanded && details.length > 0 && (
        <div className="mt-2 pl-5 space-y-0.5">
          {details.map(([k, v]) => (
            <div key={k} className="text-[11px]">
              <span className="text-gray-500">{k}:</span>{' '}
              <span className="text-gray-300 font-mono">
                {typeof v === 'object' ? JSON.stringify(v) : String(v)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default function SecurityLog({ embedded = false }) {
  const [entries, setEntries] = useState([])
  const [total, setTotal] = useState(0)
  const [loading, setLoading] = useState(true)
  const [filter, setFilter] = useState('all')
  const [confirmClear, setConfirmClear] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  const loadLog = async () => {
    setLoading(true)
    try {
      const res = await settingsApi.getSecurityLog(500)
      setEntries(res.data.entries || [])
      setTotal(res.data.total || 0)
    } catch (err) {
      addNotification({ type: 'error', message: `Failed to load security log: ${err.message}` })
    }
    setLoading(false)
  }

  useEffect(() => { loadLog() }, [])

  const handleClear = async () => {
    try {
      await settingsApi.clearSecurityLog()
      setEntries([])
      setTotal(0)
      setConfirmClear(false)
      addNotification({ type: 'success', message: 'Security log cleared' })
    } catch (err) {
      addNotification({ type: 'error', message: `Failed to clear log: ${err.message}` })
    }
  }

  // Apply filter
  const filtered = entries.filter((e) => {
    if (filter === 'all') return true
    if (filter === 'warning') return e.level === 'WARNING' || e.level === 'CRITICAL'
    return e.event?.startsWith(filter + '.')
  })

  return (
    <div className={`h-full overflow-y-auto scrollbar-thin ${embedded ? 'p-0' : 'p-6'}`}>
      <div className={`space-y-4 ${embedded ? '' : 'max-w-4xl mx-auto'}`}>
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            {!embedded && <Shield className="w-5 h-5 text-brand-400" />}
            {!embedded && <h1 className="text-lg font-semibold text-white">Security Log</h1>}
            <span className="text-xs text-gray-500">{total} events</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={loadLog}
              disabled={loading}
              className="flex items-center gap-1 px-2.5 py-1.5 rounded text-xs bg-gray-800 text-gray-300 hover:bg-gray-700 transition-colors"
            >
              <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} /> Refresh
            </button>
            {confirmClear ? (
              <div className="flex items-center gap-1">
                <button
                  onClick={handleClear}
                  className="px-2.5 py-1.5 rounded text-xs bg-red-700 text-white hover:bg-red-600 transition-colors"
                >
                  Confirm Clear
                </button>
                <button
                  onClick={() => setConfirmClear(false)}
                  className="px-2.5 py-1.5 rounded text-xs bg-gray-700 text-gray-300 hover:bg-gray-600 transition-colors"
                >
                  Cancel
                </button>
              </div>
            ) : (
              <button
                onClick={() => setConfirmClear(true)}
                disabled={entries.length === 0}
                className="flex items-center gap-1 px-2.5 py-1.5 rounded text-xs bg-gray-800 text-gray-500 hover:text-red-400 hover:bg-gray-700 transition-colors disabled:opacity-50"
              >
                <Trash2 className="w-3 h-3" /> Clear
              </button>
            )}
          </div>
        </div>

        {/* Filters */}
        <div className="flex gap-1.5">
          {FILTER_OPTIONS.map((opt) => (
            <button
              key={opt.value}
              onClick={() => setFilter(opt.value)}
              className={`px-2.5 py-1 rounded text-[11px] transition-colors ${
                filter === opt.value
                  ? 'bg-brand-900 text-brand-300'
                  : 'bg-gray-800 text-gray-500 hover:text-gray-300'
              }`}
            >
              {opt.label}
            </button>
          ))}
        </div>

        {/* Log entries */}
        {loading ? (
          <div className="flex items-center gap-2 text-sm text-gray-500 py-8">
            <RefreshCw className="w-4 h-4 animate-spin" /> Loading...
          </div>
        ) : filtered.length === 0 ? (
          <div className="text-center py-12 text-gray-600">
            <Shield className="w-8 h-8 mx-auto mb-3 opacity-50" />
            <p className="text-sm">No security events recorded.</p>
          </div>
        ) : (
          <div className="space-y-1">
            {filtered.map((entry, i) => (
              <LogEntry key={i} entry={entry} />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
