import { useEffect, useMemo, useState } from 'react'
import { X, AlertTriangle } from 'lucide-react'
import FolderTree from './FolderTree'
import { artifactsApi } from '../api/client'

/**
 * Move / Duplicate modal.
 *
 * Props:
 *  - ids: string[]                              // 1 or many
 *  - mode: 'move' | 'duplicate'
 *  - projects: Array<{ id, name }>              // all projects in the system
 *  - foldersByProject: Record<string, string[]> // folder paths keyed by project_id
 *  - currentProjectId: string
 *  - onClose: () => void
 *  - onComplete: (response) => void
 */
export default function MoveModal({
  ids,
  mode,
  projects,
  foldersByProject,
  currentProjectId,
  onClose,
  onComplete,
}) {
  const [destProject, setDestProject] = useState(currentProjectId)
  const [destFolder, setDestFolder] = useState('')
  const [newFolder, setNewFolder] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  const nodes = useMemo(() => projects.map((p) => ({
    project_id: p.id,
    project_name: p.name,
    folders: foldersByProject[p.id] || [],
  })), [projects, foldersByProject])

  const effectiveFolder = newFolder.trim() || destFolder
  const crossProject = destProject && destProject !== currentProjectId
  const canConfirm = ids.length > 0 && destProject && !busy

  const handleSelect = ({ project_id, folder }) => {
    setDestProject(project_id)
    setDestFolder(folder || '')
    setNewFolder('')
  }

  const confirm = async () => {
    if (!canConfirm) return
    setBusy(true)
    setError(null)
    try {
      let response
      if (ids.length === 1) {
        const res = await artifactsApi.move(ids[0], effectiveFolder, {
          dest_project_id: destProject,
          mode,
        })
        response = { results: [{ ...res.data }] }
      } else {
        const res = await artifactsApi.moveBulk(ids, effectiveFolder, {
          dest_project_id: destProject,
          mode,
        })
        response = res.data
      }
      onComplete(response)
      onClose()
    } catch (e) {
      setError(e?.response?.data?.detail || e?.message || 'Failed')
      setBusy(false)
    }
  }

  const title = `${mode === 'move' ? 'Move' : 'Duplicate'} ${ids.length} artifact${ids.length === 1 ? '' : 's'}`

  return (
    <div className="fixed inset-0 z-40 bg-black/60 flex items-center justify-center" onClick={onClose}>
      <div
        className="bg-gray-950 border border-gray-800 rounded-lg w-[480px] max-h-[80vh] flex flex-col"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between p-3 border-b border-gray-800">
          <div className="text-sm font-semibold text-gray-200">{title}</div>
          <button onClick={onClose} className="text-gray-500 hover:text-gray-200">
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-auto p-3 space-y-3">
          <FolderTree
            nodes={nodes}
            selected={{ project_id: destProject, folder: destFolder }}
            onSelect={handleSelect}
            collapsedKey="pan_artifacts_move_modal_collapsed"
            showProjects="always"
          />
          <div>
            <label className="text-xs text-gray-500 mb-1 block">Or enter a new folder path (relative to project):</label>
            <input
              type="text"
              value={newFolder}
              onChange={(e) => setNewFolder(e.target.value)}
              placeholder="e.g. research/q2-2026"
              className="w-full bg-gray-900 border border-gray-800 text-gray-200 text-xs rounded px-2 py-1"
            />
          </div>
          {crossProject && (
            <div className="flex gap-2 text-xs bg-amber-900/30 border border-amber-700/50 rounded p-2 text-amber-200">
              <AlertTriangle className="w-4 h-4 flex-shrink-0 mt-0.5" />
              <div>
                {mode === 'move'
                  ? 'This is a cross-project move. The artifact and its graph + semantic memory will be removed from the source project and re-extracted in the destination.'
                  : 'This is a cross-project duplicate. A new independent artifact will be created in the destination project with its own memory.'}
              </div>
            </div>
          )}
          {error && (
            <div className="text-xs bg-red-900/30 border border-red-700/50 rounded p-2 text-red-200">
              {error}
            </div>
          )}
        </div>
        <div className="p-3 border-t border-gray-800 flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-3 py-1 text-xs text-gray-400 hover:text-gray-200"
          >
            Cancel
          </button>
          <button
            onClick={confirm}
            disabled={!canConfirm}
            className={`px-3 py-1 text-xs rounded ${
              canConfirm
                ? (crossProject ? 'bg-amber-700 hover:bg-amber-600 text-white' : 'bg-brand-600 hover:bg-brand-500 text-white')
                : 'bg-gray-800 text-gray-600 cursor-not-allowed'
            }`}
          >
            {busy ? 'Working…' : (mode === 'move' ? 'Move' : 'Duplicate')}
          </button>
        </div>
      </div>
    </div>
  )
}
