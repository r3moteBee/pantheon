import React, { useState, useEffect, useCallback, useRef } from 'react'
import {
  X, FileText, FilePlus2, Trash2, Save, Sparkles, Wand2, Target,
  Play, Loader2, AlertCircle, CheckCircle2, Info, ChevronRight, FileCode,
  Check, GitCompare,
} from 'lucide-react'
import CoreEditor from './CoreEditor'
import { skillsApi } from '../api/client'

// ── Helpers ────────────────────────────────────────────────────────────────

function severityColor(sev) {
  if (sev === 'critical') return 'text-red-400 border-red-900 bg-red-950/30'
  if (sev === 'warning') return 'text-amber-400 border-amber-900 bg-amber-950/30'
  return 'text-gray-400 border-gray-800 bg-gray-900/40'
}

function severityIcon(sev) {
  if (sev === 'critical') return <AlertCircle className="w-3.5 h-3.5" />
  if (sev === 'warning') return <AlertCircle className="w-3.5 h-3.5" />
  return <Info className="w-3.5 h-3.5" />
}

// ── Line-level diff (LCS) ──────────────────────────────────────────────────

function diffLines(a, b) {
  const A = (a || '').split('\n')
  const B = (b || '').split('\n')
  const n = A.length, m = B.length
  // LCS length table
  const dp = Array.from({ length: n + 1 }, () => new Int32Array(m + 1))
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      if (A[i] === B[j]) dp[i][j] = dp[i + 1][j + 1] + 1
      else dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1])
    }
  }
  const out = []
  let i = 0, j = 0
  while (i < n && j < m) {
    if (A[i] === B[j]) { out.push({ type: 'ctx', text: A[i] }); i++; j++ }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { out.push({ type: 'del', text: A[i] }); i++ }
    else { out.push({ type: 'add', text: B[j] }); j++ }
  }
  while (i < n) out.push({ type: 'del', text: A[i++] })
  while (j < m) out.push({ type: 'add', text: B[j++] })
  return out
}

function DiffModal({ title, path, before, after, onAccept, onReject }) {
  const rows = diffLines(before, after)
  const adds = rows.filter((r) => r.type === 'add').length
  const dels = rows.filter((r) => r.type === 'del').length
  return (
    <div className="fixed inset-0 bg-black/70 z-[60] flex items-center justify-center p-4">
      <div className="w-full max-w-3xl max-h-[80vh] bg-gray-950 border border-gray-800 rounded-lg flex flex-col shadow-2xl">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
          <div className="flex items-center gap-2 min-w-0">
            <GitCompare className="w-4 h-4 text-brand-400" />
            <h3 className="text-sm font-semibold text-gray-200 truncate">{title}</h3>
            <span className="text-[10px] text-gray-500 font-mono truncate">{path}</span>
            <span className="text-[10px] text-green-400">+{adds}</span>
            <span className="text-[10px] text-red-400">−{dels}</span>
          </div>
          <button onClick={onReject} className="p-1 text-gray-500 hover:text-gray-200">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-auto font-mono text-xs">
          {rows.map((r, i) => (
            <div
              key={i}
              className={
                r.type === 'add'
                  ? 'bg-green-950/40 text-green-300 px-3 py-0.5'
                  : r.type === 'del'
                  ? 'bg-red-950/40 text-red-300 px-3 py-0.5 line-through opacity-80'
                  : 'text-gray-500 px-3 py-0.5'
              }
            >
              <span className="inline-block w-4 select-none">
                {r.type === 'add' ? '+' : r.type === 'del' ? '−' : ' '}
              </span>
              <span className="whitespace-pre-wrap break-words">{r.text || ' '}</span>
            </div>
          ))}
        </div>
        <div className="flex items-center justify-end gap-2 px-4 py-3 border-t border-gray-800">
          <button
            onClick={onReject}
            className="px-3 py-1.5 text-xs rounded bg-gray-800 hover:bg-gray-700 text-gray-200 flex items-center gap-1"
          >
            <X className="w-3 h-3" /> Reject
          </button>
          <button
            onClick={onAccept}
            className="px-3 py-1.5 text-xs rounded bg-brand-600 hover:bg-brand-500 text-white flex items-center gap-1"
          >
            <Check className="w-3 h-3" /> Accept changes
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Sample message tester ──────────────────────────────────────────────────

function SkillTester({ skillName }) {
  const [message, setMessage] = useState('')
  const [result, setResult] = useState(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const run = async () => {
    if (!message.trim() || !skillName) return
    setBusy(true)
    setError('')
    try {
      const res = await skillsApi.testSkill(skillName, message.trim())
      setResult(res.data)
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="space-y-2">
      <div className="text-xs text-gray-400">
        Test how the resolver scores this skill against a sample user message.
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={message}
          onChange={(e) => setMessage(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && run()}
          placeholder="e.g. summarize this PDF for me"
          className="flex-1 px-2 py-1.5 text-xs bg-gray-950 border border-gray-800 rounded text-gray-200"
          disabled={!skillName}
        />
        <button
          onClick={run}
          disabled={busy || !skillName || !message.trim()}
          className="px-3 py-1.5 text-xs rounded bg-brand-600 hover:bg-brand-500 text-white flex items-center gap-1 disabled:opacity-50"
        >
          {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Play className="w-3 h-3" />} Test
        </button>
      </div>
      {error && <div className="text-xs text-red-400">{error}</div>}
      {result && (
        <div className="p-2 rounded border border-gray-800 bg-gray-950 text-xs space-y-1">
          <div className="flex items-center justify-between">
            <span className="text-gray-400">Score</span>
            <span className={`font-mono ${result.would_fire ? 'text-brand-400' : 'text-gray-500'}`}>
              {result.score}
            </span>
          </div>
          <div className="flex items-center justify-between">
            <span className="text-gray-400">Would fire?</span>
            <span className={result.would_fire ? 'text-brand-400' : 'text-gray-500'}>
              {result.would_fire ? 'Yes' : 'No'}
            </span>
          </div>
          {result.breakdown && (
            <div className="pt-1 border-t border-gray-800 text-gray-500 space-y-0.5">
              {result.breakdown.name && <div>✓ name match (+2.0)</div>}
              {result.breakdown.trigger?.length > 0 && (
                <div>✓ triggers: {result.breakdown.trigger.join(', ')} (+3.0 each)</div>
              )}
              {result.breakdown.partial_trigger?.length > 0 && (
                <div>≈ partial: {result.breakdown.partial_trigger.join(', ')} (+1.5 each)</div>
              )}
              {result.breakdown.tag?.length > 0 && (
                <div>✓ tags: {result.breakdown.tag.join(', ')} (+1.0 each)</div>
              )}
              {result.breakdown.description_overlap?.length > 0 && (
                <div>· desc words: {result.breakdown.description_overlap.join(', ')} (+0.5 each)</div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ── AI assist panel ────────────────────────────────────────────────────────

function AIAssistPanel({ skillName, manifestContent, instructionsContent, onProposeInstructions, onProposeTriggers }) {
  const [improveBusy, setImproveBusy] = useState(false)
  const [optBusy, setOptBusy] = useState(false)
  const [goal, setGoal] = useState('')
  const [error, setError] = useState('')
  const [proposedTriggers, setProposedTriggers] = useState(null)

  const handleImprove = async () => {
    setImproveBusy(true)
    setError('')
    try {
      const res = await skillsApi.improve(instructionsContent, { goal: goal || null, skillName })
      onProposeInstructions(res.data.instructions)
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally {
      setImproveBusy(false)
    }
  }

  const handleOptimize = async () => {
    setOptBusy(true)
    setError('')
    setProposedTriggers(null)
    try {
      let manifest = {}
      try { manifest = JSON.parse(manifestContent || '{}') } catch {}
      const res = await skillsApi.optimizeTriggers(
        manifest.description || '',
        instructionsContent,
        manifest.triggers || [],
      )
      setProposedTriggers(res.data.triggers || [])
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally {
      setOptBusy(false)
    }
  }

  return (
    <div className="space-y-3">
      <div>
        <div className="text-xs font-semibold text-gray-300 mb-1.5 flex items-center gap-1.5">
          <Wand2 className="w-3.5 h-3.5" /> Improve instructions
        </div>
        <input
          type="text"
          value={goal}
          onChange={(e) => setGoal(e.target.value)}
          placeholder="Optional goal: e.g. add more examples"
          className="w-full px-2 py-1.5 text-xs bg-gray-950 border border-gray-800 rounded text-gray-200 mb-1.5"
        />
        <button
          onClick={handleImprove}
          disabled={improveBusy || !instructionsContent.trim()}
          className="w-full px-3 py-1.5 text-xs rounded bg-brand-600 hover:bg-brand-500 text-white flex items-center justify-center gap-1 disabled:opacity-50"
        >
          {improveBusy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Wand2 className="w-3 h-3" />}
          Rewrite instructions
        </button>
      </div>

      <div>
        <div className="text-xs font-semibold text-gray-300 mb-1.5 flex items-center gap-1.5">
          <Target className="w-3.5 h-3.5" /> Optimize triggers
        </div>
        <button
          onClick={handleOptimize}
          disabled={optBusy}
          className="w-full px-3 py-1.5 text-xs rounded bg-brand-600 hover:bg-brand-500 text-white flex items-center justify-center gap-1 disabled:opacity-50"
        >
          {optBusy ? <Loader2 className="w-3 h-3 animate-spin" /> : <Target className="w-3 h-3" />}
          Suggest triggers
        </button>
        {proposedTriggers && (
          <div className="mt-2 p-2 rounded border border-gray-800 bg-gray-950">
            <div className="text-[10px] uppercase text-gray-500 mb-1">Proposed</div>
            <div className="text-xs text-gray-300 space-y-0.5">
              {proposedTriggers.map((t, i) => <div key={i}>• {t}</div>)}
            </div>
            <button
              onClick={() => { onProposeTriggers(proposedTriggers); setProposedTriggers(null) }}
              className="mt-2 w-full px-2 py-1 text-[11px] rounded bg-gray-800 hover:bg-gray-700 text-gray-200"
            >
              Review diff →
            </button>
          </div>
        )}
      </div>

      {error && <div className="text-xs text-red-400">{error}</div>}
    </div>
  )
}

// ── Main SkillEditor ───────────────────────────────────────────────────────

export default function SkillEditor({ skillName, onClose, onSaved }) {
  const [files, setFiles] = useState([])
  const [editable, setEditable] = useState(true)
  const [activePath, setActivePath] = useState('skill.json')
  const [content, setContent] = useState('')
  const [contents, setContents] = useState({}) // unsaved content per path
  const [dirty, setDirty] = useState({}) // path -> bool
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [lint, setLint] = useState({ findings: [] })
  const lintTimer = useRef(null)
  const [pendingDiff, setPendingDiff] = useState(null)  // { path, before, after, title }
  const [showSaveAllConfirm, setShowSaveAllConfirm] = useState(false)

  const loadFiles = useCallback(async () => {
    setLoading(true)
    try {
      const res = await skillsApi.listFiles(skillName)
      setFiles(res.data.files || [])
      setEditable(res.data.editable)
      setError('')
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally {
      setLoading(false)
    }
  }, [skillName])

  const loadFile = useCallback(async (path) => {
    if (contents[path] !== undefined) {
      setContent(contents[path])
      setActivePath(path)
      return
    }
    try {
      const res = await skillsApi.readFile(skillName, path)
      const text = res.data.content || ''
      setContents((c) => ({ ...c, [path]: text }))
      setContent(text)
      setActivePath(path)
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    }
  }, [skillName, contents])

  useEffect(() => { loadFiles() }, [loadFiles])
  useEffect(() => { if (skillName) loadFile('skill.json') }, [skillName])

  // Live lint (debounced) — runs whenever the manifest or instructions change
  useEffect(() => {
    if (lintTimer.current) clearTimeout(lintTimer.current)
    lintTimer.current = setTimeout(async () => {
      try {
        const manifestJson = contents['skill.json'] || ''
        const instructions = contents['instructions.md'] || ''
        const res = await skillsApi.lint(manifestJson, instructions)
        setLint(res.data)
      } catch {}
    }, 600)
    return () => clearTimeout(lintTimer.current)
  }, [contents])

  const handleChange = (val) => {
    setContent(val)
    setContents((c) => ({ ...c, [activePath]: val }))
    setDirty((d) => ({ ...d, [activePath]: true }))
  }

  const saveActive = async () => {
    if (!editable || !dirty[activePath]) return
    setSaving(true)
    setError('')
    try {
      await skillsApi.writeFile(skillName, activePath, contents[activePath])
      setDirty((d) => ({ ...d, [activePath]: false }))
      if (onSaved) onSaved()
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally {
      setSaving(false)
    }
  }

  const saveAll = async () => {
    setSaving(true)
    setError('')
    try {
      for (const path of Object.keys(dirty)) {
        if (dirty[path]) {
          await skillsApi.writeFile(skillName, path, contents[path])
        }
      }
      setDirty({})
      if (onSaved) onSaved()
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally {
      setSaving(false)
    }
  }

  const proposeInstructions = (text) => {
    setPendingDiff({
      path: 'instructions.md',
      before: contents['instructions.md'] || '',
      after: text,
      title: 'AI — Improve instructions',
    })
  }

  const proposeTriggers = (triggers) => {
    let manifest = {}
    try { manifest = JSON.parse(contents['skill.json'] || '{}') } catch { return }
    manifest.triggers = triggers
    const next = JSON.stringify(manifest, null, 2)
    setPendingDiff({
      path: 'skill.json',
      before: contents['skill.json'] || '',
      after: next,
      title: 'AI — Optimize triggers',
    })
  }

  const acceptPendingDiff = () => {
    if (!pendingDiff) return
    const { path, after } = pendingDiff
    setContents((c) => ({ ...c, [path]: after }))
    setDirty((d) => ({ ...d, [path]: true }))
    if (activePath === path) setContent(after)
    setPendingDiff(null)
  }

  const dirtyPaths = Object.keys(dirty).filter((p) => dirty[p])
  const dirtyCount = dirtyPaths.length

  const confirmSaveAll = () => {
    if (dirtyCount === 0) return
    if (dirtyCount === 1) {
      // single file — no need for a confirmation step
      saveAll()
      return
    }
    setShowSaveAllConfirm(true)
  }

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="w-full max-w-6xl h-[88vh] bg-gray-950 border border-gray-800 rounded-lg flex flex-col shadow-2xl">
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
          <div className="flex items-center gap-2 min-w-0">
            <FileCode className="w-4 h-4 text-brand-400" />
            <h2 className="text-sm font-semibold text-gray-200 truncate">
              {skillName}
              {!editable && <span className="ml-2 text-xs text-gray-500">(read-only)</span>}
            </h2>
            {dirtyCount > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-900/40 text-amber-400">
                {dirtyCount} unsaved
              </span>
            )}
          </div>
          <div className="flex items-center gap-2">
            {editable && (
              <div className="relative">
                <button
                  onClick={confirmSaveAll}
                  disabled={saving || dirtyCount === 0}
                  className="px-3 py-1.5 text-xs rounded bg-brand-600 hover:bg-brand-500 text-white flex items-center gap-1 disabled:opacity-50"
                >
                  {saving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                  Save all{dirtyCount > 0 ? ` (${dirtyCount})` : ''}
                </button>
                {showSaveAllConfirm && (
                  <div className="absolute right-0 top-full mt-1 w-64 bg-gray-900 border border-gray-800 rounded-lg shadow-2xl z-10 p-3">
                    <div className="text-[10px] uppercase text-gray-500 mb-1.5">
                      Save {dirtyCount} file{dirtyCount === 1 ? '' : 's'}?
                    </div>
                    <div className="max-h-40 overflow-y-auto space-y-0.5 mb-2">
                      {dirtyPaths.map((p) => (
                        <div key={p} className="text-xs text-gray-300 flex items-center gap-1.5 truncate">
                          <span className="text-amber-400">●</span>
                          <FileText className="w-3 h-3 text-gray-500 flex-shrink-0" />
                          <span className="truncate font-mono">{p}</span>
                        </div>
                      ))}
                    </div>
                    <div className="flex gap-2">
                      <button
                        onClick={() => setShowSaveAllConfirm(false)}
                        className="flex-1 px-2 py-1 text-xs rounded bg-gray-800 hover:bg-gray-700 text-gray-200"
                      >
                        Cancel
                      </button>
                      <button
                        onClick={() => { setShowSaveAllConfirm(false); saveAll() }}
                        className="flex-1 px-2 py-1 text-xs rounded bg-brand-600 hover:bg-brand-500 text-white flex items-center justify-center gap-1"
                      >
                        <Check className="w-3 h-3" /> Save all
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )}
            <button onClick={onClose} className="p-1.5 text-gray-500 hover:text-gray-200">
              <X className="w-4 h-4" />
            </button>
          </div>
        </div>

        {error && (
          <div className="px-4 py-2 border-b border-gray-800 text-xs text-red-400 bg-red-950/30">
            {error}
          </div>
        )}

        {/* Body — three columns */}
        <div className="flex-1 flex overflow-hidden">
          {/* File tree */}
          <div className="w-48 border-r border-gray-800 overflow-y-auto p-2 flex-shrink-0">
            <div className="text-[10px] uppercase text-gray-600 mb-1 px-1">Files</div>
            {loading ? (
              <div className="text-xs text-gray-500 p-1">Loading…</div>
            ) : (
              files.map((f) => (
                <button
                  key={f.path}
                  onClick={() => loadFile(f.path)}
                  className={`w-full text-left px-2 py-1 text-xs rounded flex items-center gap-1.5 truncate ${
                    activePath === f.path
                      ? 'bg-gray-800 text-brand-300'
                      : 'text-gray-400 hover:bg-gray-900 hover:text-gray-200'
                  }`}
                  title={f.path}
                >
                  <FileText className="w-3 h-3 flex-shrink-0" />
                  <span className="truncate">{f.path}</span>
                  {dirty[f.path] && <span className="text-amber-400">●</span>}
                </button>
              ))
            )}
          </div>

          {/* Editor */}
          <div className="flex-1 flex flex-col min-w-0">
            <div className="px-3 py-1.5 border-b border-gray-800 text-xs text-gray-500 flex items-center gap-1">
              <ChevronRight className="w-3 h-3" /> {activePath}
            </div>
            <div className="flex-1 overflow-auto">
              <CoreEditor
                value={content}
                filename={activePath}
                editable={editable}
                onChange={handleChange}
                onSaveHotkey={saveActive}
              />
            </div>
            {/* Lint bar */}
            <div className="border-t border-gray-800 max-h-32 overflow-y-auto p-2 space-y-1">
              {lint.findings.length === 0 ? (
                <div className="flex items-center gap-1.5 text-xs text-gray-500">
                  <CheckCircle2 className="w-3.5 h-3.5 text-brand-500" /> No lint issues
                </div>
              ) : (
                lint.findings.map((f, i) => (
                  <div
                    key={i}
                    className={`flex items-center gap-1.5 text-xs px-2 py-1 rounded border ${severityColor(f.severity)}`}
                  >
                    {severityIcon(f.severity)}
                    <span>{f.message}</span>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* Right rail: AI assist + tester */}
          <div className="w-72 border-l border-gray-800 overflow-y-auto flex-shrink-0">
            {editable && (
              <div className="p-3 border-b border-gray-800">
                <div className="text-[10px] uppercase text-gray-600 mb-2 flex items-center gap-1">
                  <Sparkles className="w-3 h-3" /> AI Assist
                </div>
                <AIAssistPanel
                  skillName={skillName}
                  manifestContent={contents['skill.json'] || ''}
                  instructionsContent={contents['instructions.md'] || ''}
                  onProposeInstructions={proposeInstructions}
                  onProposeTriggers={proposeTriggers}
                />
              </div>
            )}
            <div className="p-3">
              <div className="text-[10px] uppercase text-gray-600 mb-2 flex items-center gap-1">
                <Play className="w-3 h-3" /> Test
              </div>
              <SkillTester skillName={skillName} />
            </div>
          </div>
        </div>
      </div>

      {pendingDiff && (
        <DiffModal
          title={pendingDiff.title}
          path={pendingDiff.path}
          before={pendingDiff.before}
          after={pendingDiff.after}
          onAccept={acceptPendingDiff}
          onReject={() => setPendingDiff(null)}
        />
      )}
    </div>
  )
}

// ── New Skill modal — scaffold flow ────────────────────────────────────────

export function NewSkillModal({ onClose, onCreated }) {
  const [mode, setMode] = useState('blank') // 'blank' | 'ai'
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [brief, setBrief] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const submit = async () => {
    setBusy(true)
    setError('')
    try {
      if (mode === 'blank') {
        await skillsApi.createBlank(name, description)
        onCreated(name)
      } else {
        const res = await skillsApi.scaffold(brief, { nameHint: name || null, materialize: true })
        onCreated(res.data.name)
      }
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="w-full max-w-md bg-gray-950 border border-gray-800 rounded-lg shadow-2xl">
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
          <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
            <FilePlus2 className="w-4 h-4" /> New Skill
          </h2>
          <button onClick={onClose} className="p-1 text-gray-500 hover:text-gray-200">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="p-4 space-y-3">
          <div className="flex gap-2">
            <button
              onClick={() => setMode('blank')}
              className={`flex-1 px-3 py-1.5 text-xs rounded border ${
                mode === 'blank' ? 'border-brand-500 bg-brand-900/30 text-brand-300' : 'border-gray-800 text-gray-400'
              }`}
            >
              Blank
            </button>
            <button
              onClick={() => setMode('ai')}
              className={`flex-1 px-3 py-1.5 text-xs rounded border flex items-center justify-center gap-1 ${
                mode === 'ai' ? 'border-brand-500 bg-brand-900/30 text-brand-300' : 'border-gray-800 text-gray-400'
              }`}
            >
              <Sparkles className="w-3 h-3" /> AI Scaffold
            </button>
          </div>

          <div>
            <label className="block text-[10px] uppercase text-gray-500 mb-1">
              Name {mode === 'ai' && '(optional)'}
            </label>
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="my-skill"
              className="w-full px-2 py-1.5 text-xs bg-gray-900 border border-gray-800 rounded text-gray-200"
            />
          </div>

          {mode === 'blank' ? (
            <div>
              <label className="block text-[10px] uppercase text-gray-500 mb-1">Description</label>
              <input
                type="text"
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="One-line summary"
                className="w-full px-2 py-1.5 text-xs bg-gray-900 border border-gray-800 rounded text-gray-200"
              />
            </div>
          ) : (
            <div>
              <label className="block text-[10px] uppercase text-gray-500 mb-1">Describe the skill</label>
              <textarea
                value={brief}
                onChange={(e) => setBrief(e.target.value)}
                rows={5}
                placeholder="What should this skill do? When should it fire? Be specific about the workflow."
                className="w-full px-2 py-1.5 text-xs bg-gray-900 border border-gray-800 rounded text-gray-200 resize-none"
              />
            </div>
          )}

          {error && <div className="text-xs text-red-400">{error}</div>}

          <button
            onClick={submit}
            disabled={busy || (mode === 'blank' ? !name : !brief)}
            className="w-full px-3 py-2 text-xs rounded bg-brand-600 hover:bg-brand-500 text-white flex items-center justify-center gap-1 disabled:opacity-50"
          >
            {busy ? <Loader2 className="w-3 h-3 animate-spin" /> : <FilePlus2 className="w-3 h-3" />}
            {mode === 'ai' ? 'Generate skill' : 'Create skill'}
          </button>
        </div>
      </div>
    </div>
  )
}
