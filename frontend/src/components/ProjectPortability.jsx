import React, { useState, useRef } from 'react'
import {
  Download,
  Upload,
  Shield,
  ShieldCheck,
  ShieldX,
  AlertTriangle,
  CheckCircle,
  XCircle,
  Info,
  Package,
  FileText,
  Brain,
  ListTodo,
  Database,
  Loader2,
  X,
} from 'lucide-react'
import { projectsApi } from '../api/client'
import { useStore } from '../store'

// ── Export Modal ──────────────────────────────────────────────────────────────

function ExportModal({ project, onClose }) {
  const [components, setComponents] = useState({
    metadata: true,
    memory: true,
    files: true,
    tasks: true,
  })
  const [loading, setLoading] = useState(false)
  const [preview, setPreview] = useState(null)
  const [loadingPreview, setLoadingPreview] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  const selectedComponents = Object.entries(components)
    .filter(([, v]) => v)
    .map(([k]) => k)

  const toggleComponent = (key) => {
    setComponents((prev) => ({ ...prev, [key]: !prev[key] }))
    setPreview(null)
  }

  const loadPreview = async () => {
    setLoadingPreview(true)
    try {
      const res = await projectsApi.exportPreview(project.id, selectedComponents)
      setPreview(res.data)
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoadingPreview(false)
  }

  const doExport = async () => {
    setLoading(true)
    try {
      const res = await projectsApi.exportProject(project.id, selectedComponents)
      // Download the blob
      const url = window.URL.createObjectURL(new Blob([res.data]))
      const link = document.createElement('a')
      link.href = url
      link.setAttribute('download', `pantheon-${project.id}-export.zip`)
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
      addNotification({ type: 'success', message: 'Project exported' })
      onClose()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  const componentInfo = {
    metadata: { icon: FileText, label: 'Metadata', desc: 'Project settings and config' },
    memory: { icon: Brain, label: 'Memory', desc: 'Episodic, semantic, and graph memory' },
    files: { icon: Database, label: 'Files', desc: 'Workspace, personality, and notes' },
    tasks: { icon: ListTodo, label: 'Tasks', desc: 'Scheduled task definitions' },
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-900 rounded-xl border border-gray-700 max-w-lg w-full max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <div className="flex items-center gap-3">
            <Download className="w-5 h-5 text-brand-400" />
            <div>
              <h2 className="text-lg font-semibold text-gray-100">Export Project</h2>
              <p className="text-xs text-gray-500">{project.name}</p>
            </div>
          </div>
          <button onClick={onClose} className="p-1 text-gray-500 hover:text-gray-300">
            <X className="w-5 h-5" />
          </button>
        </div>

        {/* Component selection */}
        <div className="p-5 space-y-3">
          <p className="text-sm text-gray-400 mb-4">Select what to include in the export:</p>

          {Object.entries(componentInfo).map(([key, { icon: Icon, label, desc }]) => (
            <label
              key={key}
              className={`flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors ${
                components[key]
                  ? 'bg-brand-900/30 border-brand-700'
                  : 'bg-gray-800 border-gray-700 hover:border-gray-600'
              }`}
            >
              <input
                type="checkbox"
                checked={components[key]}
                onChange={() => toggleComponent(key)}
                className="accent-brand-500"
              />
              <Icon className="w-4 h-4 text-gray-400 shrink-0" />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-gray-200">{label}</div>
                <div className="text-xs text-gray-500">{desc}</div>
              </div>
            </label>
          ))}
        </div>

        {/* Preview */}
        {preview && (
          <div className="px-5 pb-3">
            <div className="bg-gray-800 rounded-lg p-3 text-xs text-gray-400 space-y-1">
              <div className="font-semibold text-gray-300 mb-2">Export Preview</div>
              {preview.components?.memory && (
                <>
                  <div>Conversations: {preview.components.memory.conversations}</div>
                  <div>Messages: {preview.components.memory.messages}</div>
                  <div>Memory notes: {preview.components.memory.memory_notes}</div>
                  <div>Graph nodes: {preview.components.memory.graph_nodes}</div>
                  <div>Graph edges: {preview.components.memory.graph_edges}</div>
                  <div>Semantic memories: {preview.components.memory.semantic_memories}</div>
                </>
              )}
              {preview.components?.files && (
                <div>Files: {preview.components.files.count}</div>
              )}
              {preview.components?.tasks && (
                <div>Tasks: {preview.components.tasks.count}</div>
              )}
            </div>
          </div>
        )}

        {/* Actions */}
        <div className="flex gap-3 px-5 pb-5">
          <button
            onClick={loadPreview}
            disabled={loadingPreview || selectedComponents.length === 0}
            className="flex-1 px-4 py-2.5 bg-gray-700 hover:bg-gray-600 text-gray-200 text-sm rounded-lg disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
          >
            {loadingPreview ? <Loader2 className="w-4 h-4 animate-spin" /> : <Info className="w-4 h-4" />}
            Preview
          </button>
          <button
            onClick={doExport}
            disabled={loading || selectedComponents.length === 0}
            className="flex-1 px-4 py-2.5 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-lg disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
          >
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Download className="w-4 h-4" />}
            Export
          </button>
        </div>
      </div>
    </div>
  )
}

// ── Import Modal ─────────────────────────────────────────────────────────────

const severityStyles = {
  info: { icon: Info, color: 'text-blue-400', bg: 'bg-blue-900/30' },
  warning: { icon: AlertTriangle, color: 'text-yellow-400', bg: 'bg-yellow-900/30' },
  error: { icon: XCircle, color: 'text-red-400', bg: 'bg-red-900/30' },
  critical: { icon: ShieldX, color: 'text-red-300', bg: 'bg-red-900/50' },
}

function ImportModal({ onClose, onImported }) {
  const [file, setFile] = useState(null)
  const [scanning, setScanning] = useState(false)
  const [importing, setImporting] = useState(false)
  const [scanResult, setScanResult] = useState(null)
  const [importResult, setImportResult] = useState(null)
  const [targetId, setTargetId] = useState('')
  const [overwrite, setOverwrite] = useState(false)
  const [components, setComponents] = useState({
    metadata: true,
    memory: true,
    files: true,
    tasks: true,
  })
  const fileRef = useRef(null)
  const addNotification = useStore((s) => s.addNotification)

  const selectedComponents = Object.entries(components)
    .filter(([, v]) => v)
    .map(([k]) => k)

  const handleFileSelect = (e) => {
    const f = e.target.files?.[0]
    if (f) {
      setFile(f)
      setScanResult(null)
      setImportResult(null)
    }
  }

  const runScan = async () => {
    if (!file) return
    setScanning(true)
    setScanResult(null)
    try {
      const res = await projectsApi.scanImport(file)
      setScanResult(res.data)
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setScanning(false)
  }

  const doImport = async () => {
    if (!file) return
    setImporting(true)
    try {
      const res = await projectsApi.importProject(file, {
        targetId: targetId || null,
        components: selectedComponents,
        overwrite,
      })
      setImportResult(res.data)
      if (res.data.success) {
        addNotification({ type: 'success', message: `Project imported: ${res.data.project_name}` })
        if (onImported) onImported()
      } else {
        addNotification({ type: 'error', message: res.data.message || 'Import failed' })
      }
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setImporting(false)
  }

  const scanPassed = scanResult?.passed
  const hasFile = !!file

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-900 rounded-xl border border-gray-700 max-w-xl w-full max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <div className="flex items-center gap-3">
            <Upload className="w-5 h-5 text-green-400" />
            <h2 className="text-lg font-semibold text-gray-100">Import Project</h2>
          </div>
          <button onClick={onClose} className="p-1 text-gray-500 hover:text-gray-300">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4">
          {/* File picker */}
          <div
            onClick={() => fileRef.current?.click()}
            className="border-2 border-dashed border-gray-600 rounded-lg p-6 text-center cursor-pointer hover:border-gray-500 transition-colors"
          >
            <input
              ref={fileRef}
              type="file"
              accept=".zip"
              onChange={handleFileSelect}
              className="hidden"
            />
            <Package className="w-8 h-8 text-gray-500 mx-auto mb-2" />
            {file ? (
              <div className="text-sm text-gray-300">{file.name} ({(file.size / 1024).toFixed(1)} KB)</div>
            ) : (
              <div className="text-sm text-gray-500">Click to select a .zip export file</div>
            )}
          </div>

          {/* Options */}
          {hasFile && !importResult?.success && (
            <div className="space-y-3">
              <div>
                <label className="block text-xs text-gray-400 mb-1">Target Project ID (optional)</label>
                <input
                  type="text"
                  value={targetId}
                  onChange={(e) => setTargetId(e.target.value)}
                  placeholder="Leave empty to use original ID"
                  className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
                />
              </div>

              <label className="flex items-center gap-2 text-sm text-gray-300">
                <input
                  type="checkbox"
                  checked={overwrite}
                  onChange={(e) => setOverwrite(e.target.checked)}
                  className="accent-brand-500"
                />
                Merge into existing project if it exists
              </label>

              {/* Component selection */}
              <div className="text-xs text-gray-400 mt-2 mb-1">Components to import:</div>
              <div className="flex flex-wrap gap-2">
                {['metadata', 'memory', 'files', 'tasks'].map((key) => (
                  <label
                    key={key}
                    className={`flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs cursor-pointer transition-colors ${
                      components[key]
                        ? 'bg-brand-900/40 text-brand-300 border border-brand-700'
                        : 'bg-gray-800 text-gray-500 border border-gray-700'
                    }`}
                  >
                    <input
                      type="checkbox"
                      checked={components[key]}
                      onChange={() => setComponents((p) => ({ ...p, [key]: !p[key] }))}
                      className="hidden"
                    />
                    {key}
                  </label>
                ))}
              </div>
            </div>
          )}

          {/* Scan Results */}
          {scanResult && (
            <div className={`rounded-lg border p-4 ${scanPassed ? 'border-green-800 bg-green-900/20' : 'border-red-800 bg-red-900/20'}`}>
              <div className="flex items-center gap-2 mb-3">
                {scanPassed ? (
                  <ShieldCheck className="w-5 h-5 text-green-400" />
                ) : (
                  <ShieldX className="w-5 h-5 text-red-400" />
                )}
                <span className={`font-semibold text-sm ${scanPassed ? 'text-green-300' : 'text-red-300'}`}>
                  {scanPassed ? 'Security scan passed' : 'Security scan failed'}
                </span>
              </div>

              {/* Stats */}
              {scanResult.stats && (
                <div className="text-xs text-gray-400 mb-3 space-y-0.5">
                  {scanResult.stats.project_name && <div>Project: {scanResult.stats.project_name}</div>}
                  {scanResult.stats.archive_size && (
                    <div>Archive: {(scanResult.stats.archive_size / 1024).toFixed(1)} KB</div>
                  )}
                  {scanResult.stats.file_count && <div>Files: {scanResult.stats.file_count}</div>}
                  {scanResult.stats.exported_at && <div>Exported: {new Date(scanResult.stats.exported_at).toLocaleString()}</div>}
                </div>
              )}

              {/* Findings */}
              {scanResult.findings?.length > 0 && (
                <div className="space-y-2 max-h-48 overflow-y-auto">
                  {scanResult.findings.map((f, i) => {
                    const style = severityStyles[f.severity] || severityStyles.info
                    const Icon = style.icon
                    return (
                      <div key={i} className={`flex items-start gap-2 p-2 rounded ${style.bg}`}>
                        <Icon className={`w-3.5 h-3.5 mt-0.5 shrink-0 ${style.color}`} />
                        <div>
                          <div className="text-xs text-gray-200">{f.message}</div>
                          {f.detail && <div className="text-xs text-gray-500 mt-0.5">{f.detail}</div>}
                        </div>
                      </div>
                    )
                  })}
                </div>
              )}
            </div>
          )}

          {/* Import Result */}
          {importResult && (
            <div className={`rounded-lg border p-4 ${importResult.success ? 'border-green-800 bg-green-900/20' : 'border-red-800 bg-red-900/20'}`}>
              <div className="flex items-center gap-2 mb-2">
                {importResult.success ? (
                  <CheckCircle className="w-5 h-5 text-green-400" />
                ) : (
                  <XCircle className="w-5 h-5 text-red-400" />
                )}
                <span className={`font-semibold text-sm ${importResult.success ? 'text-green-300' : 'text-red-300'}`}>
                  {importResult.message}
                </span>
              </div>
              {importResult.components_imported?.length > 0 && (
                <div className="text-xs text-gray-400">
                  Imported: {importResult.components_imported.join(', ')}
                </div>
              )}
              {importResult.stats && Object.keys(importResult.stats).length > 0 && (
                <div className="text-xs text-gray-500 mt-2 space-y-0.5">
                  {Object.entries(importResult.stats).map(([k, v]) => (
                    <div key={k}>{k.replace(/_/g, ' ')}: {v}</div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Actions */}
        <div className="flex gap-3 px-5 pb-5">
          {!importResult?.success && (
            <>
              <button
                onClick={runScan}
                disabled={!hasFile || scanning}
                className="flex-1 px-4 py-2.5 bg-gray-700 hover:bg-gray-600 text-gray-200 text-sm rounded-lg disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
              >
                {scanning ? <Loader2 className="w-4 h-4 animate-spin" /> : <Shield className="w-4 h-4" />}
                Scan
              </button>
              <button
                onClick={doImport}
                disabled={!hasFile || importing || selectedComponents.length === 0}
                className="flex-1 px-4 py-2.5 bg-green-700 hover:bg-green-600 text-white text-sm rounded-lg disabled:opacity-50 transition-colors flex items-center justify-center gap-2"
              >
                {importing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
                Import
              </button>
            </>
          )}
          {importResult?.success && (
            <button
              onClick={onClose}
              className="w-full px-4 py-2.5 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-lg transition-colors"
            >
              Done
            </button>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Exported buttons (used from ProjectCard) ─────────────────────────────────

export function ExportButton({ project }) {
  const [showModal, setShowModal] = useState(false)
  return (
    <>
      <button
        onClick={() => setShowModal(true)}
        title="Export project"
        className="px-3 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm rounded transition-colors"
      >
        <Download className="w-3.5 h-3.5" />
      </button>
      {showModal && <ExportModal project={project} onClose={() => setShowModal(false)} />}
    </>
  )
}

export function ImportButton({ onImported }) {
  const [showModal, setShowModal] = useState(false)
  return (
    <>
      <button
        onClick={() => setShowModal(true)}
        className="px-3 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 hover:text-green-300 text-sm rounded transition-colors flex items-center gap-2"
      >
        <Upload className="w-3.5 h-3.5" />
        Import Project
      </button>
      {showModal && (
        <ImportModal
          onClose={() => setShowModal(false)}
          onImported={() => {
            setShowModal(false)
            if (onImported) onImported()
          }}
        />
      )}
    </>
  )
}
