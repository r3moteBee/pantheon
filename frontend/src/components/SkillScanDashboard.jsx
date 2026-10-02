import React, { useState, useEffect } from 'react'
import {
  ShieldCheck, ShieldX, ShieldQuestion, ScanSearch, RefreshCw,
  AlertTriangle, Package, ChevronDown, ChevronRight, Trash2
} from 'lucide-react'
import { useStore } from '../store'
import { skillsApi } from '../api/client'

const STATUS_CONFIG = {
  passed: { icon: ShieldCheck, color: 'text-green-400', bg: 'bg-green-900/30', label: 'Passed' },
  failed: { icon: ShieldX, color: 'text-red-400', bg: 'bg-red-900/30', label: 'Failed' },
  unscanned: { icon: ShieldQuestion, color: 'text-gray-500', bg: 'bg-gray-800', label: 'Unscanned' },
}

function StatusBadge({ status }) {
  const cfg = STATUS_CONFIG[status] || STATUS_CONFIG.unscanned
  const Icon = cfg.icon
  return (
    <span className={`inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded ${cfg.bg} ${cfg.color}`}>
      <Icon className="w-3 h-3" /> {cfg.label}
    </span>
  )
}

function CountCard({ label, count, icon: Icon, color, total }) {
  const pct = total > 0 ? Math.round((count / total) * 100) : 0
  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-gray-500">{label}</span>
        <Icon className={`w-4 h-4 ${color}`} />
      </div>
      <div className="flex items-baseline gap-2">
        <span className={`text-2xl font-bold ${color}`}>{count}</span>
        <span className="text-xs text-gray-600">/ {total} ({pct}%)</span>
      </div>
    </div>
  )
}

// ── Inline scan results (reused from Skills.jsx pattern) ───────────────────

const SEVERITY_COLOR = {
  critical: 'text-red-400 bg-red-900/30',
  warning: 'text-amber-400 bg-amber-900/30',
  info: 'text-blue-400 bg-blue-900/30',
}

function ScanFindings({ scanResult }) {
  if (!scanResult) return <p className="text-xs text-gray-500">No scan data available.</p>

  const { passed, risk_score, findings, scanned_at, scanner_version } = scanResult
  const criticals = findings?.filter((f) => f.severity === 'critical') || []
  const warnings = findings?.filter((f) => f.severity === 'warning') || []
  const infos = findings?.filter((f) => f.severity === 'info') || []

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-medium text-gray-400 flex items-center gap-1">
          <ScanSearch className="w-3 h-3" /> Security Scan
        </h4>
        <div className="flex items-center gap-2 text-[10px] text-gray-600">
          {scanner_version && <span>v{scanner_version}</span>}
          {scanned_at && <span>{new Date(scanned_at).toLocaleString()}</span>}
        </div>
      </div>

      <div className={`flex items-center gap-3 text-xs px-3 py-2 rounded ${passed ? 'bg-green-900/20 text-green-400' : 'bg-red-900/20 text-red-400'}`}>
        {passed ? <ShieldCheck className="w-4 h-4" /> : <ShieldX className="w-4 h-4" />}
        <span className="font-medium">{passed ? 'PASSED' : 'FAILED'}</span>
        <span className="text-gray-500">Risk: {Math.round((risk_score || 0) * 100)}%</span>
        <span className="text-gray-500">
          {criticals.length} critical · {warnings.length} warnings · {infos.length} info
        </span>
      </div>

      {findings?.length > 0 && (
        <div className="space-y-1 max-h-48 overflow-y-auto scrollbar-thin">
          {findings.map((f, i) => (
            <div key={i} className={`flex items-start gap-2 text-[11px] px-2 py-1.5 rounded ${SEVERITY_COLOR[f.severity] || SEVERITY_COLOR.info}`}>
              <span className="font-mono uppercase font-bold w-14 flex-shrink-0">{f.severity}</span>
              <span className="text-gray-300 flex-1">{f.message}</span>
              {f.file && <span className="font-mono text-gray-600 flex-shrink-0">{f.file}{f.line ? `:${f.line}` : ''}</span>}
            </div>
          ))}
        </div>
      )}

      {(!findings || findings.length === 0) && (
        <p className="text-[11px] text-gray-600 px-2">No findings.</p>
      )}
    </div>
  )
}

// ── Skill row with expand + delete ─────────────────────────────────────────

function SkillRow({ skill, onDelete, onDataChange }) {
  const [expanded, setExpanded] = useState(false)
  const [scanResult, setScanResult] = useState(null)
  const [loadingScan, setLoadingScan] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [scanning, setScanning] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  const handleExpand = async () => {
    if (!expanded && !scanResult && skill.scan_status !== 'unscanned') {
      setLoadingScan(true)
      try {
        const res = await skillsApi.getScan(skill.name)
        setScanResult(res.data.scan || res.data)
      } catch (err) {
        // scan result might not exist yet
        setScanResult(null)
      }
      setLoadingScan(false)
    }
    setExpanded(!expanded)
  }

  const handleScan = async (e) => {
    e.stopPropagation()
    setScanning(true)
    try {
      const res = await skillsApi.scan(skill.name)
      const scan = res.data.scan
      setScanResult(scan)
      if (scan?.passed) {
        addNotification({ type: 'success', message: `${skill.name}: scan passed (risk ${scan.risk_score})` })
      } else {
        addNotification({
          type: 'warning',
          message: `${skill.name}: scan FAILED (risk ${scan?.risk_score})${res.data.quarantined ? ' — quarantined' : ''}`,
        })
      }
      onDataChange()
    } catch (err) {
      addNotification({ type: 'error', message: `Scan failed for ${skill.name}: ${err.message}` })
    }
    setScanning(false)
  }

  const handleDelete = async () => {
    setConfirmDelete(false)
    try {
      await skillsApi.delete(skill.name)
      const msg = skill.is_bundled
        ? `${skill.name} removed from registry (reload to restore)`
        : `${skill.name} deleted`
      addNotification({ type: 'success', message: msg })
      onDataChange()
    } catch (err) {
      addNotification({ type: 'error', message: `Failed to delete ${skill.name}: ${err.message}` })
    }
  }

  return (
    <div className="border-b border-gray-750 last:border-b-0">
      {/* Main row */}
      <div
        className="flex items-center gap-4 px-4 py-2.5 hover:bg-gray-750 transition-colors cursor-pointer"
        onClick={handleExpand}
      >
        <div className="text-gray-600">
          {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm text-white font-medium">{skill.name}</span>
            {skill.is_bundled && (
              <span className="text-[9px] px-1 py-0 rounded bg-gray-700 text-gray-500">bundled</span>
            )}
            {skill.has_scripts && (
              <span className="text-[9px] px-1 py-0 rounded bg-amber-900/40 text-amber-500">has scripts</span>
            )}
          </div>
          {skill.scanned_at && (
            <span className="text-[10px] text-gray-600">
              Scanned {new Date(skill.scanned_at).toLocaleString()}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          {skill.findings_count > 0 && (
            <span className="text-[10px] text-gray-500">{skill.findings_count} findings</span>
          )}
          {skill.risk_score != null && skill.risk_score > 0 && (
            <span className={`text-[10px] font-mono ${skill.risk_score >= 0.5 ? 'text-red-400' : skill.risk_score >= 0.2 ? 'text-amber-400' : 'text-yellow-500'}`}>
              risk: {Math.round(skill.risk_score * 100)}%
            </span>
          )}
          <StatusBadge status={skill.scan_status} />

          {/* Scan button */}
          <button
            onClick={handleScan}
            disabled={scanning}
            title="Run security scan"
            className="text-gray-500 hover:text-brand-400 transition-colors disabled:opacity-50"
          >
            {scanning ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <ScanSearch className="w-3.5 h-3.5" />}
          </button>

          {/* Delete button */}
          <button
            onClick={(e) => { e.stopPropagation(); setConfirmDelete(true) }}
            title="Delete skill"
            className="text-gray-600 hover:text-red-400 transition-colors"
          >
            <Trash2 className="w-3.5 h-3.5" />
          </button>
        </div>
      </div>

      {/* Delete confirmation */}
      {confirmDelete && (
        <div className="px-4 py-3 bg-red-950/30 border-t border-gray-700 space-y-2">
          {skill.is_bundled && (
            <div className="flex items-start gap-2 text-amber-400 text-xs">
              <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <span>
                <strong>{skill.name}</strong> is a bundled skill shipped with Pantheon.
                Deleting it removes it from the registry until the next reload.
              </span>
            </div>
          )}
          <div className="flex items-center justify-between">
            <span className="text-xs text-red-300">
              {skill.is_bundled
                ? 'Remove from registry? (Reload will restore it)'
                : 'Permanently delete this skill and its files?'}
            </span>
            <div className="flex gap-2">
              <button
                onClick={(e) => { e.stopPropagation(); setConfirmDelete(false) }}
                className="px-2.5 py-1 text-[11px] rounded bg-gray-700 text-gray-300 hover:bg-gray-600 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); handleDelete() }}
                className="px-2.5 py-1 text-[11px] rounded bg-red-700 text-white hover:bg-red-600 transition-colors"
              >
                {skill.is_bundled ? 'Remove' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Expanded scan details */}
      {expanded && (
        <div className="px-4 py-3 bg-gray-850 border-t border-gray-700">
          {loadingScan ? (
            <div className="flex items-center gap-2 text-xs text-gray-500">
              <RefreshCw className="w-3 h-3 animate-spin" /> Loading scan results...
            </div>
          ) : (
            <ScanFindings scanResult={scanResult} />
          )}
        </div>
      )}
    </div>
  )
}

// ── Main dashboard component ───────────────────────────────────────────────

export default function SkillScanDashboard() {
  const [summary, setSummary] = useState(null)
  const [quarantined, setQuarantined] = useState([])
  const [loading, setLoading] = useState(true)
  const [scanning, setScanning] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  const loadData = async () => {
    try {
      const [summaryRes, quarantineRes] = await Promise.all([
        skillsApi.scanSummary(),
        skillsApi.listQuarantined(),
      ])
      setSummary(summaryRes.data)
      setQuarantined(quarantineRes.data.quarantined || [])
    } catch (err) {
      addNotification({ type: 'error', message: `Failed to load scan data: ${err.message}` })
    }
    setLoading(false)
  }

  useEffect(() => { loadData() }, [])

  const handleScanAll = async () => {
    setScanning(true)
    try {
      const res = await skillsApi.scanAll(false) // fast scan, no AI review
      const d = res.data
      addNotification({
        type: d.failed > 0 ? 'warning' : 'success',
        message: `Scan complete: ${d.passed} passed, ${d.failed} failed, ${d.errors} errors`,
      })
      await loadData()
    } catch (err) {
      addNotification({ type: 'error', message: `Scan all failed: ${err.message}` })
    }
    setScanning(false)
  }

  const handleUnquarantine = async (skillName) => {
    try {
      await skillsApi.unquarantine(skillName)
      addNotification({ type: 'success', message: `${skillName} restored from quarantine` })
      await loadData()
    } catch (err) {
      addNotification({ type: 'error', message: `Restore failed: ${err.message}` })
    }
  }

  const handleDeleteQuarantined = async (skillName) => {
    try {
      await skillsApi.delete(skillName)
      addNotification({ type: 'success', message: `${skillName} deleted` })
      await loadData()
    } catch (err) {
      addNotification({ type: 'error', message: `Failed to delete ${skillName}: ${err.message}` })
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500 text-sm gap-2">
        <RefreshCw className="w-4 h-4 animate-spin" /> Loading scan data...
      </div>
    )
  }

  const counts = summary?.counts || { total: 0, passed: 0, failed: 0, unscanned: 0 }
  const skills = summary?.skills || []

  // Sort: failed first, then unscanned, then passed
  const sortOrder = { failed: 0, unscanned: 1, passed: 2 }
  const sorted = [...skills].sort((a, b) => {
    const ao = sortOrder[a.scan_status] ?? 1
    const bo = sortOrder[b.scan_status] ?? 1
    if (ao !== bo) return ao - bo
    return (b.risk_score || 0) - (a.risk_score || 0)
  })

  return (
    <div className="h-full overflow-y-auto p-6 scrollbar-thin">
      <div className="max-w-4xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <ScanSearch className="w-5 h-5 text-brand-400" />
            <h1 className="text-lg font-semibold text-white">Security Dashboard</h1>
          </div>
          <button
            onClick={handleScanAll}
            disabled={scanning}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-brand-900 hover:bg-brand-800 text-xs text-brand-300 transition-colors disabled:opacity-50"
          >
            {scanning ? (
              <><RefreshCw className="w-3 h-3 animate-spin" /> Scanning...</>
            ) : (
              <><ScanSearch className="w-3 h-3" /> Scan All Skills</>
            )}
          </button>
        </div>

        {/* Summary cards */}
        <div className="grid grid-cols-4 gap-3">
          <CountCard label="Total Skills" count={counts.total} icon={Package} color="text-gray-300" total={counts.total} />
          <CountCard label="Passed" count={counts.passed} icon={ShieldCheck} color="text-green-400" total={counts.total} />
          <CountCard label="Failed" count={counts.failed} icon={ShieldX} color="text-red-400" total={counts.total} />
          <CountCard label="Unscanned" count={counts.unscanned} icon={ShieldQuestion} color="text-gray-500" total={counts.total} />
        </div>

        {/* Skills table */}
        <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
          <div className="px-4 py-2.5 border-b border-gray-700 bg-gray-850">
            <h2 className="text-xs font-medium text-gray-400">All Skills — click to view scan details</h2>
          </div>
          {sorted.length === 0 ? (
            <div className="px-4 py-6 text-center text-xs text-gray-600">No skills loaded.</div>
          ) : (
            <div>
              {sorted.map((skill) => (
                <SkillRow
                  key={skill.name}
                  skill={skill}
                  onDelete={() => {}} // handled internally
                  onDataChange={loadData}
                />
              ))}
            </div>
          )}
        </div>

        {/* Quarantine section */}
        {quarantined.length > 0 && (
          <div className="bg-red-950/20 rounded-lg border border-red-900/40 overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-2.5 border-b border-red-900/30">
              <AlertTriangle className="w-4 h-4 text-red-400" />
              <h2 className="text-xs font-medium text-red-400">Quarantined Skills ({quarantined.length})</h2>
            </div>
            <div className="divide-y divide-red-900/20">
              {quarantined.map((q) => (
                <div key={q.name} className="flex items-center justify-between px-4 py-2.5">
                  <div>
                    <span className="text-sm text-red-300">{q.name}</span>
                    {q.description && <p className="text-[10px] text-gray-600 mt-0.5">{q.description}</p>}
                  </div>
                  <div className="flex gap-2">
                    <button
                      onClick={() => handleDeleteQuarantined(q.name)}
                      className="text-[11px] px-2.5 py-1 rounded bg-red-900/40 text-red-400 hover:text-red-300 hover:bg-red-900/60 transition-colors"
                    >
                      Delete
                    </button>
                    <button
                      onClick={() => handleUnquarantine(q.name)}
                      className="text-[11px] px-2.5 py-1 rounded bg-gray-800 text-gray-400 hover:text-white hover:bg-gray-700 transition-colors"
                    >
                      Restore
                    </button>
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
