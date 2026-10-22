import React, { useState, useEffect } from 'react'
import { Zap, RefreshCw, ChevronDown, ChevronRight, Brain, Clock, Shield, BookOpen, Trash2, AlertTriangle, ShieldCheck, ShieldAlert, ShieldX, ScanSearch, Download, Pencil, FilePlus2 } from 'lucide-react'
import { useStore } from '../store'
import { skillsApi } from '../api/client'
import SkillImporter from './SkillImporter'
import PageHeader, { primaryButton, secondaryButton } from './PageHeader'
import Time from './Time'
import SkillEditor, { NewSkillModal } from './SkillEditor'

// Neutral chip for tags / bundled / unscanned (UI review #9/#20: >=12px, AA contrast).
const chip = 'text-xs px-1.5 py-0.5 rounded border border-gray-700 bg-gray-900 text-gray-300 whitespace-nowrap'

// ── Scan badge component ────────────────────────────────────────────────────

function ScanBadge({ scanResult }) {
  if (!scanResult) {
    return (
      <span className={chip} title="Not scanned yet - run a security scan from the card">
        unscanned
      </span>
    )
  }

  if (scanResult.passed) {
    const score = scanResult.risk_score || 0
    if (score === 0) {
      return (
        <span className="text-xs px-1.5 py-0.5 rounded bg-green-900/60 text-green-300 flex items-center gap-1 whitespace-nowrap" title={`Risk: ${score}`}>
          <ShieldCheck className="w-3 h-3" /> clean
        </span>
      )
    }
    return (
      <span className="text-xs px-1.5 py-0.5 rounded bg-amber-900/50 text-amber-300 flex items-center gap-1 whitespace-nowrap" title={`Risk: ${score}`}>
        <ShieldAlert className="w-3 h-3" /> warnings
      </span>
    )
  }

  return (
    <span className="text-xs px-1.5 py-0.5 rounded bg-red-900/50 text-red-300 flex items-center gap-1 whitespace-nowrap" title={`Risk: ${scanResult.risk_score}`}>
      <ShieldX className="w-3 h-3" /> failed
    </span>
  )
}

// ── Scan results panel ──────────────────────────────────────────────────────

function ScanResults({ scanResult }) {
  if (!scanResult) return null

  const { passed, risk_score, findings, scanned_at, scanner_version } = scanResult
  const criticals = findings?.filter((f) => f.severity === 'critical') || []
  const warnings = findings?.filter((f) => f.severity === 'warning') || []
  const infos = findings?.filter((f) => f.severity === 'info') || []

  const severityColor = {
    critical: 'text-red-400 bg-red-900/30',
    warning: 'text-amber-400 bg-amber-900/30',
    info: 'text-blue-400 bg-blue-900/30',
  }

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between">
        <h4 className="text-xs font-medium text-gray-400 flex items-center gap-1">
          <ScanSearch className="w-3 h-3" /> Security Scan
        </h4>
        <div className="flex items-center gap-2 text-xs text-gray-400">
          <span>v{scanner_version}</span>
          {scanned_at && <Time value={scanned_at} />}
        </div>
      </div>

      <div className={`flex items-center gap-3 text-xs px-3 py-2 rounded ${passed ? 'bg-green-900/20 text-green-400' : 'bg-red-900/20 text-red-400'}`}>
        {passed ? <ShieldCheck className="w-4 h-4" /> : <ShieldX className="w-4 h-4" />}
        <span className="font-medium">{passed ? 'PASSED' : 'FAILED'}</span>
        <span className="text-gray-400">Risk: {Math.round((risk_score || 0) * 100)}%</span>
        <span className="text-gray-400">
          {criticals.length} critical · {warnings.length} warnings · {infos.length} info
        </span>
      </div>

      {findings?.length > 0 && (
        <div className="space-y-1 max-h-48 overflow-y-auto scrollbar-thin">
          {findings.map((f, i) => (
            <div key={i} className={`flex items-start gap-2 text-xs px-2 py-1.5 rounded ${severityColor[f.severity] || severityColor.info}`}>
              <span className="font-mono uppercase font-bold w-14 flex-shrink-0">{f.severity}</span>
              <span className="text-gray-300 flex-1">{f.message}</span>
              {f.file && <span className="font-mono text-gray-400 flex-shrink-0">{f.file}{f.line ? `:${f.line}` : ''}</span>}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── Skill card component ────────────────────────────────────────────────────

function SkillCard({ skill, projectId, stats, onToggle, onDelete, onScan, onEdit }) {
  const [expanded, setExpanded] = useState(false)
  const [details, setDetails] = useState(null)
  const [loading, setLoading] = useState(false)
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [scanning, setScanning] = useState(false)

  const isEnabled = !skill.disabled_projects?.includes(projectId)

  const handleExpand = async () => {
    if (!expanded && !details) {
      setLoading(true)
      try {
        const res = await skillsApi.get(skill.name)
        setDetails(res.data)
      } catch (err) {
        console.error('Failed to load skill details:', err)
      }
      setLoading(false)
    }
    setExpanded(!expanded)
  }

  const handleScan = async () => {
    setScanning(true)
    try {
      await onScan(skill.name)
      // Refresh details to pick up scan result
      const detailRes = await skillsApi.get(skill.name)
      setDetails(detailRes.data)
    } catch (err) {
      console.error('Scan failed:', err)
    }
    setScanning(false)
  }

  return (
    <div className={`border rounded-lg overflow-hidden transition-colors ${isEnabled ? 'border-brand-700/60 bg-gray-800' : 'border-gray-800 bg-gray-900/60 opacity-80 hover:opacity-100'}`}>
      <div className="flex items-start gap-3 p-4">
        <div className="w-8 h-8 rounded-lg bg-brand-900 flex items-center justify-center flex-shrink-0 mt-0.5">
          <Zap className="w-4 h-4 text-brand-400" />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-sm font-semibold text-white">{skill.name}</h3>
            {skill.is_bundled && (
              <span className={chip}>bundled</span>
            )}
            <ScanBadge scanResult={skill.scan_result} />
            {stats?.fires > 0 && (
              <span
                className="text-xs px-1.5 py-0.5 rounded bg-brand-900/60 text-brand-300"
                title={`Fired ${stats.fires} times (${stats.fires_explicit || 0} explicit, ${stats.fires_auto || 0} auto)${stats.last_fired ? ` — last ${stats.last_fired}` : ''}`}
              >
                {stats.fires} fire{stats.fires === 1 ? '' : 's'}
              </span>
            )}
            {skill.schedulable && (
              <Clock className="w-3 h-3 text-amber-400" aria-label="Schedulable" role="img"><title>Schedulable</title></Clock>
            )}
            {skill.project_aware && (
              <Shield className="w-3 h-3 text-green-400" aria-label="Project-aware" role="img"><title>Project-aware</title></Shield>
            )}
          </div>
          <p className="text-xs text-gray-400 mt-1 line-clamp-2">{skill.description}</p>
          <div className="flex flex-wrap gap-1.5 mt-2">
            {skill.tags?.map((tag) => (
              <span key={tag} className={chip}>
                {tag}
              </span>
            ))}
          </div>
          {(skill.memory_reads?.length > 0 || skill.memory_writes?.length > 0) && (
            <div className="flex items-center gap-2 mt-2 text-xs text-gray-400">
              <Brain className="w-3 h-3" />
              {skill.memory_reads?.length > 0 && <span>reads: {skill.memory_reads.join(', ')}</span>}
              {skill.memory_writes?.length > 0 && <span>writes: {skill.memory_writes.join(', ')}</span>}
            </div>
          )}
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <button
            onClick={handleScan}
            disabled={scanning}
            title="Run security scan"
            aria-label={`Run security scan on ${skill.name}`}
            className="text-gray-400 hover:text-brand-400 transition-colors disabled:opacity-50"
          >
            {scanning ? (
              <RefreshCw className="w-4 h-4 animate-spin" />
            ) : (
              <ScanSearch className="w-4 h-4" />
            )}
          </button>
          <div className="flex items-center gap-2">
            <span
              className={`text-xs font-semibold uppercase tracking-wide ${
                isEnabled ? 'text-brand-400' : 'text-gray-400'
              }`}
            >
              {isEnabled ? 'Enabled' : 'Disabled'}
            </span>
            <button
              type="button"
              role="switch"
              aria-checked={isEnabled}
              onClick={() => onToggle(skill.name, !isEnabled)}
              title={isEnabled ? 'Disable for this project' : 'Enable for this project'}
              aria-label={`${skill.name}: enabled for this project`}
              className={`relative inline-flex h-5 w-9 items-center rounded-full border transition-colors focus:ring-2 focus:ring-brand-500 focus:ring-offset-2 focus:ring-offset-gray-900 ${
                isEnabled
                  ? 'bg-brand-700 border-brand-500'
                  : 'bg-gray-700 border-gray-600 hover:bg-gray-600'
              }`}
            >
              <span
                className={`inline-block h-3.5 w-3.5 transform rounded-full bg-white shadow transition-transform ${
                  isEnabled ? 'translate-x-4' : 'translate-x-0.5'
                }`}
              />
            </button>
          </div>
          <button
            onClick={() => onEdit && onEdit(skill.name)}
            title="Edit skill"
            aria-label={`Edit ${skill.name}`}
            className="text-gray-400 hover:text-brand-400 transition-colors"
          >
            <Pencil className="w-4 h-4" />
          </button>
          <button
            onClick={() => setConfirmDelete(true)}
            title="Delete skill"
            aria-label={`Delete ${skill.name}`}
            className="text-gray-400 hover:text-red-400 transition-colors"
          >
            <Trash2 className="w-4 h-4" />
          </button>
          <button
            onClick={handleExpand}
            title={expanded ? 'Hide details' : 'Show details'}
            aria-label={`${expanded ? 'Hide' : 'Show'} details for ${skill.name}`}
            aria-expanded={expanded}
            className="text-gray-400 hover:text-gray-300 transition-colors"
          >
            {expanded ? <ChevronDown className="w-4 h-4" /> : <ChevronRight className="w-4 h-4" />}
          </button>
        </div>
      </div>

      {confirmDelete && (
        <div className="border-t border-gray-700 px-4 py-3 bg-red-950/30 space-y-2">
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
                onClick={() => setConfirmDelete(false)}
                className="px-2.5 py-1 text-xs rounded bg-gray-700 text-gray-300 hover:bg-gray-600 transition-colors"
              >
                Cancel
              </button>
              <button
                onClick={() => { setConfirmDelete(false); onDelete(skill.name, skill.is_bundled) }}
                className="px-2.5 py-1 text-xs rounded bg-red-700 text-white hover:bg-red-600 transition-colors"
              >
                {skill.is_bundled ? 'Remove' : 'Delete'}
              </button>
            </div>
          </div>
        </div>
      )}

      {expanded && (
        <div className="border-t border-gray-700 px-4 py-3 bg-gray-850 space-y-3">
          {loading ? (
            <div className="flex items-center gap-2 text-xs text-gray-400">
              <RefreshCw className="w-3 h-3 animate-spin" /> Loading...
            </div>
          ) : details ? (
            <>
              {details.scan_result && (
                <ScanResults scanResult={details.scan_result} />
              )}
              <div>
                <h4 className="text-xs font-medium text-gray-400 mb-1">Triggers</h4>
                <div className="flex flex-wrap gap-1">
                  {details.triggers?.map((t, i) => (
                    <span key={i} className="text-xs px-2 py-0.5 rounded bg-brand-900 text-brand-300">"{t}"</span>
                  ))}
                </div>
              </div>
              {details.parameters?.length > 0 && (
                <div>
                  <h4 className="text-xs font-medium text-gray-400 mb-1">Parameters</h4>
                  {details.parameters.map((p, i) => (
                    <div key={i} className="text-xs text-gray-300 ml-2">
                      <span className="font-mono text-green-300">{p.name}</span>
                      <span className="text-gray-400"> ({p.type}){p.required ? ' *' : ''}</span>
                      {p.description && <span className="text-gray-400"> — {p.description}</span>}
                    </div>
                  ))}
                </div>
              )}
              {details.instructions && (
                <div>
                  <h4 className="text-xs font-medium text-gray-400 mb-1 flex items-center gap-1">
                    <BookOpen className="w-3 h-3" /> Instructions
                  </h4>
                  <pre className="text-xs text-gray-300 whitespace-pre-wrap bg-gray-900 rounded p-2 max-h-60 overflow-y-auto scrollbar-thin">
                    {details.instructions}
                  </pre>
                </div>
              )}
              <div className="text-xs text-gray-400">
                v{details.version} · {details.author} · {details.skill_dir}
              </div>
            </>
          ) : (
            <p className="text-xs text-gray-400">No details available</p>
          )}
        </div>
      )}
    </div>
  )
}

// ── Main Skills component ───────────────────────────────────────────────────

// ── Override password modal ─────────────────────────────────────────────────

function OverrideModal({ skillName, onConfirm, onCancel }) {
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async () => {
    if (!password.trim()) {
      setError('Password is required')
      return
    }
    setSubmitting(true)
    setError('')
    const result = await onConfirm(password)
    if (result?.error) {
      setError(result.error)
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
      <div className="bg-gray-800 rounded-lg border border-gray-700 w-full max-w-md p-5 space-y-4">
        <div className="flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-amber-400 flex-shrink-0 mt-0.5" />
          <div>
            <h3 className="text-sm font-semibold text-white">Security Override</h3>
            <p className="text-xs text-gray-400 mt-1">
              <strong>{skillName}</strong> failed its security scan. Enter the security
              override password to force-enable it. This action will be logged.
            </p>
          </div>
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Override Password</label>
          <input
            type="password"
            value={password}
            onChange={(e) => { setPassword(e.target.value); setError('') }}
            onKeyDown={(e) => e.key === 'Enter' && handleSubmit()}
            placeholder="Enter security override password"
            autoFocus
            className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:border-brand-500"
          />
          {error && <p className="text-xs text-red-400 mt-1">{error}</p>}
        </div>
        <div className="flex justify-end gap-2">
          <button
            onClick={onCancel}
            className="px-3 py-1.5 text-xs rounded bg-gray-700 text-gray-300 hover:bg-gray-600 transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleSubmit}
            disabled={submitting || !password.trim()}
            className="px-3 py-1.5 text-xs rounded bg-amber-700 text-white hover:bg-amber-600 transition-colors disabled:opacity-50"
          >
            {submitting ? 'Verifying...' : 'Force Enable'}
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Main Skills component ───────────────────────────────────────────────────

export default function Skills() {
  const [skills, setSkills] = useState([])
  const [analytics, setAnalytics] = useState({})
  const [loading, setLoading] = useState(true)
  const [reloading, setReloading] = useState(false)
  const [overrideTarget, setOverrideTarget] = useState(null) // skill name pending override
  const [showImporter, setShowImporter] = useState(false)
  const [showNewSkill, setShowNewSkill] = useState(false)
  const [editingSkill, setEditingSkill] = useState(null)
  const activeProject = useStore((s) => s.activeProject)
  const addNotification = useStore((s) => s.addNotification)

  const projectId = activeProject?.id || 'default'

  const loadSkills = async () => {
    try {
      const res = await skillsApi.list(projectId)
      setSkills(res.data.skills || [])
    } catch (err) {
      addNotification({ type: 'error', message: `Failed to load skills: ${err.message}` })
    }
    setLoading(false)
  }

  const loadAnalytics = async () => {
    try {
      const res = await skillsApi.getAnalytics()
      setAnalytics(res.data.stats || {})
    } catch {
      // Non-fatal; leave analytics empty
    }
  }

  useEffect(() => {
    loadSkills()
    loadAnalytics()
  }, [projectId])

  const handleReload = async () => {
    setReloading(true)
    try {
      const res = await skillsApi.reload()
      addNotification({ type: 'success', message: `Reloaded ${res.data.count} skills` })
      await loadSkills()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setReloading(false)
  }

  const handleToggle = async (skillName, enabled) => {
    try {
      await skillsApi.toggle(skillName, projectId, enabled)
      addNotification({ type: 'success', message: `${skillName} ${enabled ? 'enabled' : 'disabled'}` })
      await loadSkills()
    } catch (err) {
      const status = err.response?.status
      const detail = err.message
      if (status === 403 && enabled && detail.includes('security scan')) {
        // Check if override password is configured, then offer force-enable
        try {
          const statusRes = await skillsApi.overrideStatus()
          if (statusRes.data.configured) {
            setOverrideTarget(skillName)
            return
          }
        } catch (_) { /* ignore */ }
        addNotification({ type: 'error', message: `${detail} Configure a security override password in Settings to force-enable.` })
      } else {
        addNotification({ type: 'error', message: detail })
      }
    }
  }

  const handleForceEnable = async (password) => {
    try {
      await skillsApi.toggle(overrideTarget, projectId, true, {
        forceEnable: true,
        overridePassword: password,
      })
      addNotification({ type: 'warning', message: `${overrideTarget} force-enabled via security override` })
      setOverrideTarget(null)
      await loadSkills()
      return {}
    } catch (err) {
      const detail = err.message
      return { error: detail }
    }
  }

  const handleDelete = async (skillName, wasBundled) => {
    try {
      await skillsApi.delete(skillName)
      const msg = wasBundled
        ? `${skillName} removed from registry (reload to restore)`
        : `${skillName} deleted`
      addNotification({ type: 'success', message: msg })
      await loadSkills()
    } catch (err) {
      addNotification({ type: 'error', message: `Failed to delete ${skillName}: ${err.message}` })
    }
  }

  const handleScan = async (skillName) => {
    try {
      const res = await skillsApi.scan(skillName)
      const scan = res.data.scan
      if (scan?.passed) {
        addNotification({ type: 'success', message: `${skillName}: scan passed (risk ${scan.risk_score})` })
      } else {
        addNotification({
          type: 'warning',
          message: `${skillName}: scan ${scan?.passed === false ? 'FAILED' : 'completed'} (risk ${scan?.risk_score})${res.data.quarantined ? ' — quarantined' : ''}`,
        })
      }
      await loadSkills()
      return res
    } catch (err) {
      addNotification({ type: 'error', message: `Scan failed for ${skillName}: ${err.message}` })
      throw err
    }
  }

  return (
    <div className="h-full overflow-y-auto scrollbar-thin">
      <PageHeader
        title="Skills Library"
        description={`${skills.length} skill${skills.length === 1 ? '' : 's'}`}
        actions={
          <>
            <button onClick={() => setShowNewSkill(true)} className={primaryButton}>
              <FilePlus2 className="w-3.5 h-3.5" aria-hidden="true" />
              New
            </button>
            <button onClick={() => setShowImporter(true)} className={secondaryButton}>
              <Download className="w-3.5 h-3.5" aria-hidden="true" />
              Import
            </button>
            <button onClick={handleReload} disabled={reloading} className={secondaryButton}>
              <RefreshCw className={`w-3.5 h-3.5 ${reloading ? 'animate-spin' : ''}`} aria-hidden="true" />
              Reload
            </button>
          </>
        }
      />
      <div className="max-w-4xl mx-auto p-6">
        <p className="text-sm text-gray-400 mb-6">
          Skills extend agent capabilities with specialised procedures. Use <code className="text-brand-300 bg-gray-800 px-1 rounded">/skill-name</code> in chat to invoke explicitly,
          or turn on Skill discovery in the chat header to let the agent match skills automatically.
        </p>

        {loading ? (
          <div className="flex items-center gap-2 text-sm text-gray-400">
            <RefreshCw className="w-4 h-4 animate-spin" /> Loading skills...
          </div>
        ) : skills.length === 0 ? (
          <div className="text-center py-12 text-gray-400">
            <Zap className="w-8 h-8 mx-auto mb-3 opacity-50" />
            <p className="text-sm">No skills found.</p>
            <p className="text-xs mt-1">Add skill directories to <code>skills/</code> or <code>data/skills/</code>.</p>
          </div>
        ) : (
          <div className="space-y-3">
            {skills.map((skill) => (
              <SkillCard
                key={skill.name}
                skill={skill}
                projectId={projectId}
                stats={analytics[skill.name]}
                onToggle={handleToggle}
                onDelete={handleDelete}
                onScan={handleScan}
                onEdit={(name) => setEditingSkill(name)}
              />
            ))}
          </div>
        )}
      </div>

      {/* Override password modal */}
      {overrideTarget && (
        <OverrideModal
          skillName={overrideTarget}
          onConfirm={handleForceEnable}
          onCancel={() => setOverrideTarget(null)}
        />
      )}

      {/* New skill modal */}
      {showNewSkill && (
        <NewSkillModal
          onClose={() => setShowNewSkill(false)}
          onCreated={async (name) => {
            setShowNewSkill(false)
            await loadSkills()
            setEditingSkill(name)
          }}
        />
      )}

      {/* Editor modal */}
      {editingSkill && (
        <SkillEditor
          skillName={editingSkill}
          onClose={() => setEditingSkill(null)}
          onSaved={loadSkills}
        />
      )}

      {/* Import modal */}
      {showImporter && (
        <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50 p-4">
          <SkillImporter
            onClose={() => setShowImporter(false)}
            onImportComplete={async () => {
              await loadSkills()
              addNotification({ type: 'success', message: 'Skill library updated' })
            }}
          />
        </div>
      )}
    </div>
  )
}
