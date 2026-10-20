import { useEffect, useState } from 'react'
import { X, Folder, FileText, HardDriveDownload, ChevronLeft, Loader } from 'lucide-react'
import { filesApi } from '../api/client'

/**
 * Copy files the agent left in the project workspace (write_file) into the
 * project's artifacts, so they can be downloaded as a folder, built and
 * pushed. Browse the workspace, then copy a folder or a file.
 *
 * Props: projectId, onClose, onDone(result)
 */
export default function WorkspaceImportModal({ projectId, onClose, onDone }) {
  const [path, setPath] = useState('')
  const [listing, setListing] = useState(null)
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  useEffect(() => {
    setListing(null); setError(null)
    filesApi.list(projectId, path).then((r) => setListing(r.data)).catch((e) => setError(e.message))
  }, [projectId, path])

  const copy = async (p) => {
    setBusy(p); setError(null)
    try {
      const r = await filesApi.toArtifacts(projectId, p)
      onDone?.(r.data)
      onClose()
    } catch (e) { setError(e.message) } finally { setBusy(null) }
  }

  const up = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : ''
  const row = 'flex items-center justify-between gap-2 px-2 py-1 rounded hover:bg-gray-900'
  const copyBtn = (p) => (
    <button onClick={() => copy(p)} disabled={!!busy}
            className="px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-200 disabled:opacity-50 flex items-center gap-1">
      {busy === p ? <Loader className="w-3 h-3 animate-spin" /> : null} Copy to Artifacts
    </button>
  )
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div className="w-full max-w-lg bg-gray-950 border border-gray-800 rounded-lg shadow-xl max-h-[85vh] flex flex-col"
           onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-gray-800">
          <div className="flex items-center gap-2 text-sm font-semibold"><HardDriveDownload className="w-4 h-4" /> Import from workspace</div>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-200"><X className="w-4 h-4" /></button>
        </div>
        <div className="px-4 pt-3 text-xs text-gray-400">
          Files the agent wrote to the project workspace. Copy a folder to make it an artifact folder you can download, build and push.
        </div>
        <div className="px-4 py-2 text-xs flex items-center gap-2">
          {path && <button onClick={() => setPath(up)} className="text-gray-400 hover:text-gray-200"><ChevronLeft className="w-3 h-3" /></button>}
          <code className="text-gray-300">/{path}</code>
          {path && <span className="ml-auto">{copyBtn(path)}</span>}
        </div>
        <div className="px-2 pb-3 overflow-y-auto text-xs">
          {!listing && !error && <div className="px-2 py-2 text-gray-500">Loading…</div>}
          {listing && listing.directories.length === 0 && listing.files.length === 0 &&
            <div className="px-2 py-2 text-gray-500">The workspace is empty here.</div>}
          {(listing?.directories || []).map((d) => (
            <div key={d.path} className={row}>
              <button onClick={() => setPath(d.path)} className="flex items-center gap-1 text-gray-200"><Folder className="w-3 h-3" /> {d.name}/</button>
              {copyBtn(d.path)}
            </div>
          ))}
          {(listing?.files || []).map((f) => (
            <div key={f.path} className={row}>
              <span className="flex items-center gap-1 text-gray-300"><FileText className="w-3 h-3" /> {f.name}</span>
              {copyBtn(f.path)}
            </div>
          ))}
          {error && <div className="px-2 py-2 text-red-400">{error}</div>}
        </div>
      </div>
    </div>
  )
}
