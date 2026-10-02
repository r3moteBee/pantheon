import React, { useEffect, useState } from 'react'
import { AlertTriangle, Download, Loader2, Trash2, X } from 'lucide-react'
import { projectsApi } from '../api/client'

// Confirm dialog for deleting a project: lists what will be removed (from
// GET /projects/{id}/delete-preview) and needs an explicit checkbox before the
// Delete button works. Deleting removes everything the project owns
// (backend/api/project_purge.py), so the user should see it first.
export default function DeleteProjectModal({ project, onClose, onDeleted }) {
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState(null)
  const [understood, setUnderstood] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [exporting, setExporting] = useState(false)

  useEffect(() => {
    let alive = true
    projectsApi.deletePreview(project.id)
      .then((res) => { if (alive) setPreview(res.data) })
      .catch((e) => { if (alive) setError(e.response?.data?.detail || e.message) })
    return () => { alive = false }
  }, [project.id])

  const items = Object.entries(preview?.items || {}).sort((a, b) => b[1] - a[1])
  const busy = (preview?.running_jobs || 0) > 0

  const exportFirst = async () => {
    setExporting(true)
    try {
      const res = await projectsApi.exportProject(project.id)
      const url = window.URL.createObjectURL(new Blob([res.data]))
      const link = document.createElement('a')
      link.href = url
      link.setAttribute('download', `pantheon-${project.id}-export.zip`)
      document.body.appendChild(link)
      link.click()
      link.remove()
      window.URL.revokeObjectURL(url)
    } catch (e) {
      setError('Export failed: ' + (e.response?.data?.detail || e.message))
    }
    setExporting(false)
  }

  const doDelete = async () => {
    setDeleting(true)
    try {
      const res = await projectsApi.delete(project.id)
      onDeleted?.(res.data)
    } catch (e) {
      setError(e.response?.data?.detail || e.message)
      setDeleting(false)
    }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50 p-4">
      <div className="bg-gray-900 rounded-xl border border-red-900 max-w-lg w-full max-h-[90vh] overflow-y-auto">
        <div className="flex items-center justify-between px-5 py-4 border-b border-gray-700">
          <div className="flex items-center gap-3">
            <Trash2 className="w-5 h-5 text-red-400" />
            <div>
              <h2 className="text-lg font-semibold text-gray-100">Delete project</h2>
              <p className="text-xs text-gray-500">{project.name}</p>
            </div>
          </div>
          <button onClick={onClose} className="p-1 text-gray-500 hover:text-gray-300" aria-label="Close">
            <X className="w-5 h-5" />
          </button>
        </div>

        <div className="p-5 space-y-4 text-sm">
          <p className="text-gray-300">
            This permanently deletes <span className="font-semibold text-gray-100">{project.name}</span> and
            everything stored in it. It cannot be undone.
          </p>

          {!preview && !error && (
            <div className="flex items-center gap-2 text-gray-500 text-xs">
              <Loader2 className="w-4 h-4 animate-spin" /> Counting what the project contains…
            </div>
          )}

          {preview && (
            <div className="bg-gray-800 rounded-lg p-3">
              <div className="text-xs font-semibold text-gray-300 mb-2">What will be deleted</div>
              {items.length === 0 ? (
                <div className="text-xs text-gray-500">Nothing besides the project itself.</div>
              ) : (
                <ul className="text-xs text-gray-400 space-y-1">
                  {items.map(([label, n]) => (
                    <li key={label} className="flex justify-between gap-4">
                      <span>{label}</span>
                      <span className="text-gray-200 tabular-nums">{n.toLocaleString()}</span>
                    </li>
                  ))}
                </ul>
              )}
              <div className="text-[11px] text-gray-500 mt-3">
                Also removed: its scheduled tasks and job history, project settings, repo binding, and
                chat-channel mappings to it. Files that other projects also use are kept.
              </div>
            </div>
          )}

          {busy && (
            <div className="flex items-start gap-2 text-xs text-yellow-300 bg-yellow-900/30 rounded-lg p-3">
              <AlertTriangle className="w-4 h-4 shrink-0 mt-0.5" />
              A task is still running in this project. Cancel it (Tasks tab) before deleting the project.
            </div>
          )}

          {error && <div className="text-xs text-red-300 bg-red-900/30 rounded-lg p-3">{error}</div>}

          <button
            onClick={exportFirst}
            disabled={exporting}
            className="text-xs text-brand-300 hover:text-brand-200 flex items-center gap-1.5 disabled:opacity-50"
          >
            {exporting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Download className="w-3.5 h-3.5" />}
            Download an export of this project first
          </button>

          <label className="flex items-start gap-2 text-sm text-gray-300 cursor-pointer">
            <input
              type="checkbox"
              checked={understood}
              onChange={(e) => setUnderstood(e.target.checked)}
              className="accent-red-500 mt-1"
            />
            <span>I understand that this project and everything listed above will be permanently deleted.</span>
          </label>
        </div>

        <div className="flex gap-3 px-5 pb-5">
          <button
            onClick={onClose}
            className="flex-1 px-4 py-2.5 bg-gray-700 hover:bg-gray-600 text-gray-200 text-sm rounded-lg"
          >
            Cancel
          </button>
          <button
            onClick={doDelete}
            disabled={!understood || !preview || busy || deleting}
            className="flex-1 px-4 py-2.5 bg-red-700 hover:bg-red-600 text-white text-sm rounded-lg disabled:opacity-40 flex items-center justify-center gap-2"
          >
            {deleting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Trash2 className="w-4 h-4" />}
            Delete project
          </button>
        </div>
      </div>
    </div>
  )
}
