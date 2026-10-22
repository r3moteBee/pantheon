import React, { useState, useEffect, useRef } from 'react'
import {
  FolderOpen, FileText, Code, Image as ImageIcon, FileSpreadsheet,
  Presentation, FileType, Star, Trash2, Plus, Upload, RefreshCw, Search,
  Save, Download, X, Tag, Edit3, Eye, History, FileCode,
  PanelLeftClose, PanelLeftOpen, Move, Copy, Github, HardDriveDownload, Pencil, Info,
} from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import DOMPurify from 'dompurify'
import { api, artifactsApi, projectsApi } from '../api/client'
import CoreEditor from '../components/CoreEditor'
import InfoBox from '../components/InfoBox'
import Time from '../components/Time'
import { findLikelyDuplicates } from '../components/memory/duplicates'
import FolderTree from '../components/FolderTree'
import MoveModal from '../components/MoveModal'
import PushToGitHubModal from '../components/PushToGitHubModal'
import WorkspaceImportModal from '../components/WorkspaceImportModal'
import { useStore } from '../store'
import Markdown from '../components/Markdown'
import Mermaid from '../components/Mermaid'
import ExportMenu from '../components/ExportMenu'

function iconForType(ct) {
  if (!ct) return FileText
  if (ct.startsWith('text/markdown')) return FileText
  if (ct.startsWith('text/x-') || ct.startsWith('text/javascript') || ct.startsWith('text/typescript')) return Code
  if (ct.startsWith('application/json') || ct.startsWith('application/yaml') || ct.startsWith('application/xml')) return FileCode
  if (ct === 'image/svg+xml') return ImageIcon
  if (ct.startsWith('image/')) return ImageIcon
  if (ct.endsWith('spreadsheetml.sheet')) return FileSpreadsheet
  if (ct.endsWith('presentationml.presentation')) return Presentation
  if (ct.endsWith('wordprocessingml.document')) return FileType
  if (ct === 'application/pdf') return FileType
  return FileText
}

const notify = (type, message) => useStore.getState().addNotification({ type, message })

function formatBytes(n) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

// Agents tend to title re-saves "Foo.swift (Updated)"; every save is already a
// version, so show the version number instead of the suffix (#27).
const UPDATED_SUFFIX = /\s*[([]\s*(updated|revised|fixed|v\d+)\s*[)\]]\s*$/i
function displayTitle(a) {
  return String(a.title || a.path || '').replace(UPDATED_SUFFIX, '') || a.path
}

const RAIL_MIN = 180, RAIL_MAX = 480

// "About artifacts": neutral help, collapsed by default (#13).
function AboutArtifacts() {
  const [open, setOpen] = useState(() => {
    try { return localStorage.getItem('help.artifacts') === 'true' } catch { return false }
  })
  const toggle = () => {
    setOpen((v) => { try { localStorage.setItem('help.artifacts', String(!v)) } catch {} return !v })
  }
  return (
    <div>
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="flex items-center gap-1.5 text-xs text-sky-300 hover:text-sky-200 whitespace-nowrap"
      >
        <Info className="w-3.5 h-3.5" aria-hidden="true" /> {open ? 'Hide' : 'About artifacts'}
      </button>
      {open && (
        <InfoBox className="mt-2 text-xs" title="About artifacts">
          <p className="mb-2">
            <strong>Artifacts</strong> are durable, indexed, searchable
            storage, saved via <code className="text-sky-200">save_to_artifact</code>{' '}
            in chat or by the source-adapter pipeline. They are distinct
            from <strong>workspace files</strong> (scratch files on
            disk, accessed via <code className="text-sky-200">read_file</code> /{' '}
            <code className="text-sky-200">write_file</code>).
          </p>
          <ul className="list-disc pl-5 space-y-1">
            <li><strong>Project scope</strong> (left rail): <em>Current</em> shows
              only this project's artifacts; <em>All</em> shows every project.</li>
            <li><strong>Tags + pins</strong>: tag to group, pin to keep
              important items at the top of search. Rename a tag (pencil) to fix or merge it.</li>
            <li><strong>Versions</strong>: every save creates a new version;
              open the history pane (clock icon) to diff or restore.</li>
            <li><strong>Bulk select</strong>: check rows to zip-export, move or delete in batch.</li>
            <li>Saving to the same path <em>updates</em> the
              existing artifact (a new version). Pass{' '}
              <code className="text-sky-200">force_new=true</code>{' '}
              when you want a separate row.</li>
          </ul>
        </InfoBox>
      )}
    </div>
  )
}

function TagRow({ tag, count, active, canRename, duplicates, onSelect, onRename }) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(tag)
  const dupTitle = duplicates?.length
    ? `Looks like a duplicate of ${duplicates.map((d) => `"${d.label}"`).join(', ')}. Rename one to the other to merge them.`
    : ''
  if (editing) {
    const submit = async () => {
      const next = draft.trim()
      setEditing(false)
      if (next && next !== tag) await onRename(tag, next)
    }
    return (
      <form
        onSubmit={(e) => { e.preventDefault(); submit() }}
        className="flex items-center gap-1 px-1 py-0.5"
      >
        <input
          autoFocus
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Escape') { setDraft(tag); setEditing(false) } }}
          onBlur={() => { setDraft(tag); setEditing(false) }}
          aria-label={`New name for tag ${tag}`}
          className="flex-1 min-w-0 text-xs bg-gray-900 border border-gray-700 rounded px-1.5 py-0.5"
        />
      </form>
    )
  }
  return (
    <div className={`group flex items-center rounded ${active ? 'bg-brand-700 text-white' : 'hover:bg-gray-900 text-gray-300'}`}>
      <button
        onClick={onSelect}
        title={dupTitle || tag}
        className="flex-1 min-w-0 text-left text-xs px-2 py-1 flex items-center gap-1"
      >
        <span className="truncate min-w-0">{tag}</span>
        {dupTitle && <span className={`shrink-0 ${active ? 'text-white' : 'text-amber-300'}`} aria-label="possible duplicate">≈</span>}
        <span className={`ml-auto shrink-0 ${active ? 'text-white' : 'text-gray-400'}`}>{count}</span>
      </button>
      {canRename && (
        <button
          onClick={() => { setDraft(tag); setEditing(true) }}
          aria-label={`Rename or merge tag ${tag}`}
          title="Rename / merge tag"
          className={`p-1 rounded opacity-60 group-hover:opacity-100 focus-visible:opacity-100 ${active ? 'text-white' : 'text-gray-400 hover:text-gray-200'}`}
        >
          <Pencil className="w-3 h-3" />
        </button>
      )}
    </div>
  )
}

export default function ArtifactsPage({ lockedProjectId = null }) {
  const activeProject = useStore((s) => s.activeProject)
  const projects = useStore((s) => s.projects) || []
  const [projectScope, setProjectScope] = useState(lockedProjectId ? 'current' : 'current')  // 'current' | 'all'
  // UI layout state — collapsible folders rail (persisted in localStorage)
  const [foldersCollapsed, setFoldersCollapsed] = useState(() => {
    try { return localStorage.getItem('pan_artifacts_folders_collapsed') === '1' }
    catch { return false }
  })
  React.useEffect(() => {
    try { localStorage.setItem('pan_artifacts_folders_collapsed', foldersCollapsed ? '1' : '0') } catch {}
  }, [foldersCollapsed])
  const [railWidth, setRailWidth] = useState(() => {
    try { const w = Number(localStorage.getItem('pan_artifacts_rail_width')); return w >= RAIL_MIN && w <= RAIL_MAX ? w : 240 }
    catch { return 240 }
  })
  React.useEffect(() => {
    try { localStorage.setItem('pan_artifacts_rail_width', String(railWidth)) } catch {}
  }, [railWidth])
  const [mobileRailOpen, setMobileRailOpen] = useState(false)
  const startRailResize = (e) => {
    e.preventDefault()
    const startX = e.clientX, startW = railWidth
    const move = (ev) => setRailWidth(Math.max(RAIL_MIN, Math.min(RAIL_MAX, startW + ev.clientX - startX)))
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up) }
    window.addEventListener('pointermove', move)
    window.addEventListener('pointerup', up)
  }
  const projectId = lockedProjectId
    ? lockedProjectId
    : (projectScope === 'all' ? 'all' : (activeProject?.id || 'default'))

  const [items, setItems] = useState([])
  const [folders, setFolders] = useState([])
  const [tagCounts, setTagCounts] = useState({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const [filterFolder, setFilterFolder] = useState('')
  const [filterTag, setFilterTag] = useState(null)
  const [filterPinned, setFilterPinned] = useState(false)
  const [search, setSearch] = useState('')
  const [sort, setSort] = useState('modified_desc')

  const [selected, setSelected] = useState(new Set()) // ids checked in list
  // /artifacts?id=<artifact id> opens that artifact (e.g. a job's output).
  // The embedded per-project tab (lockedProjectId) ignores the URL.
  const [searchParams] = useSearchParams()
  const linkedId = lockedProjectId ? null : searchParams.get('id')
  const [activeId, setActiveId] = useState(linkedId)
  useEffect(() => { if (linkedId) setActiveId(linkedId) }, [linkedId])

  const [allFoldersByProject, setAllFoldersByProject] = useState({})
  const [allProjects, setAllProjects] = useState([])

  const [crossProjectMove, setCrossProjectMove] = useState(null)
  // crossProjectMove shape: { id, source, dest: { project_id, folder }, mode: 'move'|'duplicate' }
  const [moveModal, setMoveModal] = useState(null)
  const [pushModal, setPushModal] = useState(null)   // { pathPrefix?, artifactIds?, label }
  const [showWorkspaceImport, setShowWorkspaceImport] = useState(false)
  // moveModal shape: { ids: string[], mode: 'move' | 'duplicate' }
  const [toast, setToast] = useState(null)
  const [showCreate, setShowCreate] = useState(false)
  const [showUpload, setShowUpload] = useState(false)

  useEffect(() => {
    if (!toast) return
    const t = setTimeout(() => setToast(null), 3500)
    return () => clearTimeout(t)
  }, [toast])

  const refreshSeq = useRef(0)
  const refresh = async () => {
    const seq = ++refreshSeq.current
    setLoading(true); setError(null)
    try {
      const params = {}
      if (filterFolder) params.path_prefix = filterFolder + '/'
      if (filterTag) params.tag = filterTag
      if (filterPinned) params.pinned_only = true
      if (search) params.search = search
      params.sort = sort

      const [list, foldersRes, tagsRes] = await Promise.all([
        artifactsApi.list(projectId, params),
        artifactsApi.folders(projectId),
        artifactsApi.tags(projectId),
      ])
      // Drop stale responses — a newer refresh has superseded this one.
      if (seq !== refreshSeq.current) return
      setItems(list.data.artifacts || [])
      setFolders(foldersRes.data.folders || [])
      setTagCounts(tagsRes.data.tags || {})
    } catch (e) {
      if (seq !== refreshSeq.current) return
      setError(e.message)
    } finally {
      if (seq === refreshSeq.current) setLoading(false)
    }
  }

  useEffect(() => {
    const id = setTimeout(refresh, search ? 300 : 0)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, projectScope, filterFolder, filterTag, filterPinned, search, sort])

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const projRes = await projectsApi.list()
        const projects = projRes.data?.projects || []
        if (cancelled) return
        setAllProjects(projects)
        const folderEntries = await Promise.all(projects.map(async (p) => {
          const res = await artifactsApi.folders(p.id)
          return [p.id, res.data?.folders || []]
        }))
        if (cancelled) return
        setAllFoldersByProject(Object.fromEntries(folderEntries))
      } catch (e) {
        console.warn('failed to load projects/folders for tree', e)
      }
    })()
    return () => { cancelled = true }
  }, [])

  const toggleSelect = (id) => {
    const next = new Set(selected)
    next.has(id) ? next.delete(id) : next.add(id)
    setSelected(next)
  }

  const handleBulkDelete = async () => {
    if (!confirm(`Delete ${selected.size} artifact(s)?`)) return
    await artifactsApi.bulkDelete(Array.from(selected))
    setSelected(new Set())
    await refresh()
  }

  const handleBulkExport = async () => {
    try {
      const res = await artifactsApi.bulkExport(Array.from(selected))
      // Verify content-type — if the auth middleware returned HTML we want
      // to surface that rather than save a broken file as .zip.
      const ct = (res.headers?.['content-type'] || '').toLowerCase()
      if (!ct.includes('zip') && !ct.includes('octet-stream')) {
        const text = await res.data.text?.()
        notify('error', 'Export returned non-zip response: ' + (text || ct).slice(0, 200))
        return
      }
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data], { type: 'application/zip' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = 'artifacts.zip'; a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      notify('error', 'Export failed: ' + e.message)
    }
  }

  const handleArtifactDrop = async ({ project_id, folder }, event) => {
    let payload
    try {
      payload = JSON.parse(event.dataTransfer.getData('application/pantheon-artifact'))
    } catch { return }
    if (!payload || !payload.id) return
    const mode = event.altKey ? 'duplicate' : 'move'
    if (payload.project_id !== project_id) {
      setCrossProjectMove({
        id: payload.id,
        source: payload,
        dest: { project_id, folder: folder || '' },
        mode,
      })
      return
    }
    await performMove(payload.id, project_id, folder || '', mode, payload)
  }

  const tagDuplicates = React.useMemo(
    () => findLikelyDuplicates(Object.keys(tagCounts).map((t) => ({ id: t, label: t }))),
    [tagCounts],
  )

  const renameTag = async (oldTag, newTag) => {
    const merging = Object.prototype.hasOwnProperty.call(tagCounts, newTag)
    if (merging && !confirm(`Merge tag "${oldTag}" into "${newTag}"? Every artifact tagged "${oldTag}" gets "${newTag}" instead.`)) return
    try {
      const res = await api.post('/api/artifacts/bulk/rename-tag', { project_id: projectId, old: oldTag, new: newTag })
      if (filterTag === oldTag) setFilterTag(newTag)
      await refresh()
      const n = res.data?.updated || 0
      setToast(`${merging ? 'Merged' : 'Renamed'} "${oldTag}" → "${newTag}" on ${n} artifact${n === 1 ? '' : 's'}`)
    } catch (e) {
      notify('error', 'Tag rename failed: ' + e.message)
    }
  }

  const performMove = async (id, dest_project_id, dest_folder, mode, payload) => {
    try {
      const res = await artifactsApi.move(id, dest_folder, { dest_project_id, mode })
      await refresh()
      const basename = (payload?.path || '').split('/').pop()
      const newPath = res.data?.path || ''
      const renamed = basename && newPath && !newPath.endsWith(basename)
      const verb = mode === 'duplicate' ? 'Duplicated' : 'Moved'
      setToast(`${verb} ${basename} → ${newPath}${renamed ? ' (renamed to avoid conflict)' : ''}`)
    } catch (e) {
      setToast(`${mode === 'duplicate' ? 'Duplicate' : 'Move'} failed: ${e.message}`)
    }
  }

  return (
    <div className="h-full flex relative" data-area="artifacts">
      {/* Left rail: folders + tags. md+: collapsible and resizable. Phones: a drawer
          opened from the list header ("Folders"), so the list gets the full width. */}
      {mobileRailOpen && (
        <div className="md:hidden absolute inset-0 z-20 bg-black/50" onClick={() => setMobileRailOpen(false)} aria-hidden="true" />
      )}
      <div
        style={{ '--rail-w': `${railWidth}px` }}
        className={
          (mobileRailOpen ? "absolute inset-y-0 left-0 z-30 w-72 max-w-[85vw] shadow-2xl block " : "hidden ") +
          "md:static md:z-auto md:shadow-none md:max-w-none md:block " +
          (foldersCollapsed ? "md:w-10 " : "md:w-[var(--rail-w)] ") +
          "border-r border-gray-800 bg-gray-950 overflow-y-auto overflow-x-hidden p-2 space-y-3 flex-shrink-0 " +
          ""
        }
      >
        <div className={"flex " + (foldersCollapsed ? "md:justify-center justify-end" : "justify-end")}>
          <button
            onClick={() => setMobileRailOpen(false)}
            aria-label="Close folders"
            title="Close folders"
            className="md:hidden text-gray-400 hover:text-gray-200 p-1 rounded"
          >
            <X className="w-4 h-4" />
          </button>
          <button
            onClick={() => setFoldersCollapsed(v => !v)}
            aria-label={foldersCollapsed ? "Expand folders" : "Collapse folders"}
            title={foldersCollapsed ? "Expand folders" : "Collapse folders"}
            className="hidden md:block text-gray-400 hover:text-gray-200 p-1 rounded"
          >
            {foldersCollapsed ? <PanelLeftOpen className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
          </button>
        </div>
        {(!foldersCollapsed || mobileRailOpen) && (<div className={foldersCollapsed ? 'md:hidden space-y-3' : 'space-y-3'}>
        {!lockedProjectId && (
          <div>
            <div className="text-xs font-semibold text-gray-400 uppercase mb-2">
              Project scope
            </div>
            <div className="flex gap-1 mb-1 text-xs">
              <button
                onClick={() => setProjectScope('current')}
                className={`flex-1 min-w-0 truncate px-2 py-1 rounded ${projectScope === 'current' ? 'bg-brand-700 text-white' : 'bg-gray-900 text-gray-300 hover:bg-gray-800'}`}
                title={activeProject?.name || 'default'}
              >
                {activeProject?.name || 'Default'}
              </button>
              <button
                onClick={() => setProjectScope('all')}
                className={`px-2 py-1 rounded whitespace-nowrap ${projectScope === 'all' ? 'bg-brand-700 text-white' : 'bg-gray-900 text-gray-300 hover:bg-gray-800'}`}
              >
                All
              </button>
            </div>
          </div>
        )}
        <div>
          <div className="text-xs font-semibold text-gray-400 uppercase mb-2 flex items-center gap-1">
            <FolderOpen className="w-3 h-3" /> Folders
          </div>
          <button
            onClick={() => setFilterFolder('')}
            className={`w-full text-left text-xs px-2 py-1 rounded ${!filterFolder ? 'bg-brand-700 text-white' : 'hover:bg-gray-900 text-gray-300'}`}
          >
            All
          </button>
          <FolderTree
            nodes={
              projectId === 'all'
                ? allProjects.map((p) => ({
                    project_id: p.id,
                    project_name: p.name,
                    folders: allFoldersByProject[p.id] || [],
                  }))
                : [{
                    project_id: projectId,
                    project_name: (allProjects.find((p) => p.id === projectId) || {}).name || projectId,
                    folders,
                  }]
            }
            selected={{ project_id: projectId, folder: filterFolder }}
            onSelect={({ folder }) => { setFilterFolder(folder || ''); setMobileRailOpen(false) }}
            onDrop={handleArtifactDrop}
            collapsedKey="pan_artifacts_rail_collapsed"
            showProjects="multi-only"
          />
        </div>

        <div>
          <div className="text-xs font-semibold text-gray-400 uppercase mb-2 flex items-center gap-1">
            <Tag className="w-3 h-3" /> Tags
          </div>
          <button
            onClick={() => setFilterTag(null)}
            className={`w-full text-left text-xs px-2 py-1 rounded ${!filterTag ? 'bg-brand-700 text-white' : 'hover:bg-gray-900 text-gray-300'}`}
          >
            All
          </button>
          {Object.entries(tagCounts).map(([t, n]) => (
            <TagRow
              key={t}
              tag={t}
              count={n}
              active={filterTag === t}
              canRename={projectId !== 'all'}
              duplicates={tagDuplicates.get(t)}
              onSelect={() => { setFilterTag(t); setMobileRailOpen(false) }}
              onRename={renameTag}
            />
          ))}
        </div>

        <div>
          <button
            onClick={() => setFilterPinned(!filterPinned)}
            className={`w-full text-left text-xs px-2 py-1 rounded flex items-center gap-1 ${
              filterPinned ? 'bg-brand-700 text-white' : 'hover:bg-gray-900 text-gray-300'
            }`}
          >
            <Star className="w-3 h-3" /> Pinned only
          </button>
        </div>
        </div>)}
      </div>
      {!foldersCollapsed && (
        <div
          role="separator"
          aria-orientation="vertical"
          aria-label="Resize folders pane"
          aria-valuenow={railWidth}
          aria-valuemin={RAIL_MIN}
          aria-valuemax={RAIL_MAX}
          tabIndex={0}
          title="Drag to resize"
          onPointerDown={startRailResize}
          onDoubleClick={() => setRailWidth(240)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowLeft') setRailWidth((w) => Math.max(RAIL_MIN, w - 16))
            if (e.key === 'ArrowRight') setRailWidth((w) => Math.min(RAIL_MAX, w + 16))
          }}
          className="hidden md:block w-1 -ml-1 cursor-col-resize hover:bg-brand-700/60 focus-visible:bg-brand-700/60 shrink-0 z-10"
        />
      )}

      {/* Middle: list */}
      <div className={
        "min-w-0 border-r border-gray-800 flex flex-col " +
        (activeId
          ? "md:w-80 md:flex-shrink-0 hidden md:flex "
          : "flex-1 ")
      }>
        {!activeId && (
          <div className="px-3 py-2 border-b border-gray-800 flex items-start gap-3">
            <button
              type="button"
              onClick={() => setMobileRailOpen(true)}
              className="md:hidden flex items-center gap-1 text-xs text-gray-300 hover:text-gray-100 whitespace-nowrap"
            >
              <FolderOpen className="w-3.5 h-3.5" aria-hidden="true" />
              {filterFolder ? filterFolder.split('/').pop() : 'Folders & tags'}
            </button>
            <div className="flex-1 min-w-0"><AboutArtifacts /></div>
          </div>
        )}
        <div className="p-3 border-b border-gray-800 space-y-2">
          {/* Create & Upload buttons */}
          <div className="flex gap-2 mb-1.5">
            <button
              onClick={() => setShowCreate(true)}
              className="flex-1 py-1.5 px-2 bg-brand-700 hover:bg-brand-600 text-white rounded text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors whitespace-nowrap"
            >
              <Plus className="w-3.5 h-3.5" /> Create
            </button>
            <button
              onClick={() => setShowUpload(true)}
              className="flex-1 py-1.5 px-2 bg-gray-850 hover:bg-gray-800 text-gray-200 rounded text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors whitespace-nowrap"
            >
              <Upload className="w-3.5 h-3.5" /> Upload
            </button>
            {projectId !== 'all' && (
              <button
                onClick={() => setShowWorkspaceImport(true)}
                title="Copy files the agent wrote to the project workspace into artifacts"
                aria-label="Import workspace files into artifacts"
                className="py-1.5 px-2 bg-gray-850 hover:bg-gray-800 text-gray-200 rounded text-xs font-semibold flex items-center justify-center gap-1.5 transition-colors"
              >
                <HardDriveDownload className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            <Search className="w-3 h-3 text-gray-400 shrink-0" aria-hidden="true" />
            <input
              type="text"
              placeholder="Search artifacts…"
              aria-label="Search artifacts"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="flex-1 min-w-0 bg-gray-900 border border-gray-800 rounded px-2 py-1 text-xs"
            />
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              aria-label="Sort artifacts"
              className="bg-gray-900 border border-gray-800 rounded px-2 py-1 text-xs"
            >
              <option value="modified_desc">Modified ↓</option>
              <option value="modified_asc">Modified ↑</option>
              <option value="created_desc">Created ↓</option>
              <option value="title_asc">Title</option>
              <option value="size_desc">Size</option>
            </select>
            {filterFolder && (
              <a
                href={artifactsApi.folderZipUrl(projectId, filterFolder)}
                download
                className="text-gray-400 hover:text-gray-200"
                title={`Download ${filterFolder.split('/').pop()} as a zip`}
                aria-label={`Download ${filterFolder.split('/').pop()} as a zip`}
              >
                <Download className="w-3 h-3" />
              </a>
            )}
            {filterFolder && projectId !== 'all' && (
              <button
                onClick={() => setPushModal({ pathPrefix: filterFolder + '/', label: filterFolder })}
                className="text-gray-400 hover:text-gray-200"
                title={`Push ${filterFolder.split('/').pop()} to GitHub`}
                aria-label={`Push ${filterFolder.split('/').pop()} to GitHub`}
              >
                <Github className="w-3 h-3" />
              </button>
            )}
            <button onClick={refresh} className="text-gray-400 hover:text-gray-200" aria-label="Reload artifacts" title="Reload">
              <RefreshCw className={`w-3 h-3 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </div>
          {selected.size > 0 && (
            <div className="flex items-center gap-2 text-xs flex-wrap">
              <span className="text-gray-400">{selected.size} selected</span>
              <button onClick={handleBulkExport} className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 flex items-center gap-1">
                <Download className="w-3 h-3" /> Download zip
              </button>
              {projectId !== 'all' && (
                <button
                  onClick={() => setPushModal({ artifactIds: Array.from(selected), label: `${selected.size} files` })}
                  className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 flex items-center gap-1"
                >
                  <Github className="w-3 h-3" /> Push to GitHub
                </button>
              )}
              <button
                onClick={() => setMoveModal({ ids: Array.from(selected), mode: 'move' })}
                className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 flex items-center gap-1"
              >
                <Move className="w-3 h-3" /> Move
              </button>
              <button
                onClick={() => setMoveModal({ ids: Array.from(selected), mode: 'duplicate' })}
                className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 flex items-center gap-1"
              >
                <Copy className="w-3 h-3" /> Duplicate
              </button>
              <button onClick={handleBulkDelete} className="px-2 py-1 rounded bg-red-900 hover:bg-red-800 flex items-center gap-1">
                <Trash2 className="w-3 h-3" /> Delete
              </button>
              <button onClick={() => setSelected(new Set())} className="text-gray-400 hover:text-gray-200">Clear</button>
            </div>
          )}
        </div>
        {/* Select-all row */}
        {items.length > 0 && (
          <div className="px-3 py-1.5 border-b border-gray-900 flex items-center gap-2 text-xs text-gray-400 bg-gray-950">
            <input
              type="checkbox"
              aria-label="Select all"
              checked={selected.size > 0 && selected.size === items.length}
              ref={(el) => {
                if (el) el.indeterminate = selected.size > 0 && selected.size < items.length
              }}
              onChange={(e) => {
                if (e.target.checked) {
                  setSelected(new Set(items.map((it) => it.id)))
                } else {
                  setSelected(new Set())
                }
              }}
            />
            <span>
              {selected.size === 0
                ? `${items.length} item${items.length === 1 ? '' : 's'}`
                : `${selected.size} / ${items.length} selected`}
            </span>
          </div>
        )}
        <div className="flex-1 overflow-y-auto">
          {loading && items.length === 0 && <div className="p-4 text-xs text-gray-400">Loading…</div>}
          {error && <div className="p-4 text-xs text-red-400">{error}</div>}
          {!loading && items.length === 0 && (
            <div className="p-6 text-center text-xs text-gray-400">
              No artifacts here. Ask the agent to save something (<code className="text-brand-300">save_to_artifact</code>) or use Create / Upload above.
            </div>
          )}
          {items.map((a) => {
            const Icon = iconForType(a.content_type)
            return (
              <div
                key={a.id}
                onClick={() => setActiveId(a.id)}
                draggable
                onDragStart={(e) => {
                  e.dataTransfer.setData('application/pantheon-artifact', JSON.stringify({
                    id: a.id,
                    project_id: a.project_id,
                    path: a.path,
                  }))
                  e.dataTransfer.effectAllowed = 'copyMove'
                }}
                className={`p-2 border-b border-gray-900 cursor-pointer hover:bg-gray-900 ${activeId === a.id ? 'bg-gray-900' : ''}`}
              >
                <div className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    aria-label={`Select ${displayTitle(a)}`}
                    checked={selected.has(a.id)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => toggleSelect(a.id)}
                    className="mt-0.5"
                  />
                  <Icon className="w-3.5 h-3.5 text-gray-400 mt-0.5 shrink-0" aria-hidden="true" />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium text-gray-200 flex items-center gap-1 min-w-0" title={a.title || a.path}>
                      {a.pinned && <Star className="w-3 h-3 text-amber-400 shrink-0" fill="currentColor" />}
                      <span className="truncate">{displayTitle(a)}</span>
                    </div>
                    <div className="text-xs text-gray-400 truncate" title={a.path}>{a.path}</div>
                    <div className="flex flex-wrap gap-1 mt-1 items-center">
                      {projectScope === 'all' && a.project_id && (
                        <span className="text-xs px-1.5 bg-brand-900 rounded text-brand-200">
                          {(projects.find((p) => p.id === a.project_id)?.name) || a.project_id}
                        </span>
                      )}
                      {(a.tags || []).map((t) => (
                        <span key={t} className="text-xs px-1 bg-gray-800 rounded text-gray-300">{t}</span>
                      ))}
                      <span className="text-xs text-gray-400">{formatBytes(a.size_bytes)}</span>
                      {a.updated_at && <Time value={a.updated_at} className="text-xs text-gray-400" />}
                    </div>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Right: detail */}
      <div className={"flex-1 min-w-0 overflow-hidden " + (activeId ? "" : "hidden md:block")}>
        {activeId ? (
          <ArtifactDetail
            id={activeId}
            onChanged={refresh}
            onClose={() => setActiveId(null)}
            onRequestMove={(mode) => setMoveModal({ ids: [activeId], mode })}
            onMissing={(missingId, e) => {
              setActiveId((cur) => (cur === missingId ? null : cur))
              setToast(e?.status === 404 || /not found/i.test(e?.message || '')
                ? 'That artifact was not found (deleted or moved). Showing the list.'
                : `Could not open the artifact: ${e?.message || 'error'}`)
            }}
          />
        ) : (
          <div className="h-full flex items-center justify-center text-xs text-gray-400">
            Select an artifact to view or edit
          </div>
        )}
      </div>

      {crossProjectMove && (
        <CrossProjectMoveConfirm
          info={crossProjectMove}
          projects={allProjects}
          onCancel={() => setCrossProjectMove(null)}
          onConfirm={async () => {
            await performMove(
              crossProjectMove.id,
              crossProjectMove.dest.project_id,
              crossProjectMove.dest.folder,
              crossProjectMove.mode,
              crossProjectMove.source
            )
            setCrossProjectMove(null)
          }}
        />
      )}
      {showWorkspaceImport && (
        <WorkspaceImportModal
          projectId={projectId}
          onClose={() => setShowWorkspaceImport(false)}
          onDone={async (r) => {
            await refresh()
            setToast(`Copied ${r.copied} file${r.copied === 1 ? '' : 's'} from the workspace` + (r.folder ? ` to ${r.folder}` : ''))
          }}
        />
      )}
      {pushModal && (
        <PushToGitHubModal
          projectId={projectId}
          pathPrefix={pushModal.pathPrefix}
          artifactIds={pushModal.artifactIds}
          label={pushModal.label}
          onClose={() => setPushModal(null)}
        />
      )}
      {moveModal && (
        <MoveModal
          ids={moveModal.ids}
          mode={moveModal.mode}
          projects={allProjects}
          foldersByProject={allFoldersByProject}
          currentProjectId={projectId === 'all' ? (allProjects[0]?.id || 'default') : projectId}
          onClose={() => setMoveModal(null)}
          onComplete={async (response) => {
            await refresh()
            const verb = moveModal.mode === 'duplicate' ? 'Duplicated' : 'Moved'
            const n = response.results?.length || 0
            setToast(`${verb} ${n} artifact${n === 1 ? '' : 's'}`)
            setSelected(new Set())
          }}
        />
      )}
      {toast && (
        <div className="fixed bottom-4 right-4 z-50 bg-gray-900 border border-gray-700 text-gray-200 text-xs rounded px-3 py-2 shadow-lg">
          {toast}
        </div>
      )}
      {showCreate && (
        <CreateArtifactModal
          projects={allProjects}
          currentProjectId={projectId}
          currentFolder={filterFolder}
          onClose={() => setShowCreate(false)}
          onComplete={async () => {
            setShowCreate(false)
            setToast('Artifact created successfully!')
            await refresh()
          }}
        />
      )}
      {showUpload && (
        <UploadArtifactModal
          projects={allProjects}
          currentProjectId={projectId}
          currentFolder={filterFolder}
          onClose={() => setShowUpload(false)}
          onComplete={async () => {
            setShowUpload(false)
            setToast('Artifact uploaded successfully!')
            await refresh()
          }}
        />
      )}
    </div>
  )
}


function ArtifactDetail({ id, onChanged, onClose, onRequestMove, onMissing }) {
  const [artifact, setArtifact] = React.useState(null)
  const [preview, setPreview] = React.useState(null)
  const [versions, setVersions] = React.useState([])
  const [loading, setLoading] = React.useState(true)
  const [view, setView] = React.useState('text')   // 'text' | 'preview'
  const [draft, setDraft] = React.useState('')
  const [saving, setSaving] = React.useState(false)
  const [showVersions, setShowVersions] = React.useState(false)
  const [editSummary, setEditSummary] = React.useState('')
  const [tagDraft, setTagDraft] = React.useState('')

  const load = async () => {
    setLoading(true)
    try {
      let a
      try {
        a = await artifactsApi.get(id)
      } catch (e) {
        // A stale/deleted id (e.g. a ?id= link): tell the parent, show the list.
        onMissing?.(id, e)
        return
      }
      setArtifact(a.data)
      setDraft(a.data.content || '')
      const [p, v] = await Promise.allSettled([artifactsApi.preview(id), artifactsApi.versions(id)])
      setPreview(p.status === 'fulfilled' ? p.value.data : { type: 'unsupported', reason: 'preview failed' })
      setVersions(v.status === 'fulfilled' ? (v.value.data.versions || []) : [])
    } finally { setLoading(false) }
  }

  useEffect(() => {
    load()
    setView('text') // default to text on each new artifact
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  if (loading || !artifact) return <div className="p-4 text-xs text-gray-400">Loading…</div>

  const ct = artifact.content_type || ''
  const isText = !!ct.match(/^(text\/|application\/(json|yaml|xml)|chat-export)/)
  const previewSupported = !!preview && preview.type !== 'unsupported' && preview.type !== 'text'
    || ct === 'text/markdown'   // markdown always supports rendered preview
  const Icon = iconForType(ct)
  const dirty = isText && draft !== (artifact.content || '')
  const versionNumber = versions.find((v) => v.id === artifact.current_version_id)?.version_number
    || Math.max(0, ...versions.map((v) => v.version_number || 0)) || versions.length
  const iconBtn = 'p-1 rounded text-gray-400 hover:text-gray-200 hover:bg-gray-800 shrink-0'

  const save = async () => {
    setSaving(true)
    try {
      await artifactsApi.update(id, { content: draft, edit_summary: editSummary || 'Edited via UI' })
      setEditSummary('')
      await load(); onChanged?.()
    } catch (e) {
      notify('error', 'Save failed: ' + e.message)
    } finally { setSaving(false) }
  }

  const togglePin = async () => {
    await artifactsApi.pin(id, !artifact.pinned); await load(); onChanged?.()
  }
  const remove = async () => {
    if (!confirm('Delete artifact?')) return
    await artifactsApi.delete(id); onChanged?.(); onClose?.()
  }
  const addTag = async () => {
    if (!tagDraft.trim()) return
    const newTags = Array.from(new Set([...(artifact.tags || []), tagDraft.trim()]))
    await artifactsApi.update(id, { tags: newTags })
    setTagDraft(''); await load(); onChanged?.()
  }
  const removeTag = async (t) => {
    const newTags = (artifact.tags || []).filter((x) => x !== t)
    await artifactsApi.update(id, { tags: newTags })
    await load(); onChanged?.()
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="border-b border-gray-800 px-3 sm:px-4 py-2 flex items-center gap-1 sm:gap-1.5">
        <Icon className="w-4 h-4 text-gray-400 shrink-0 mr-1" aria-hidden="true" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium flex items-center gap-2 min-w-0" title={artifact.title || artifact.path}>
            <span className="truncate">{displayTitle(artifact)}</span>
            {versionNumber > 0 && (
              <button
                onClick={() => setShowVersions(true)}
                title={`Version ${versionNumber} of ${versions.length} — open history`}
                className="shrink-0 text-xs px-1.5 py-0.5 rounded bg-gray-800 text-gray-300 hover:bg-gray-700"
              >
                v{versionNumber}
              </button>
            )}
          </div>
          <div className="text-xs text-gray-400 truncate" title={artifact.path}>
            {artifact.path} · {ct} · {formatBytes(artifact.size_bytes)}
            {artifact.updated_at && <> · <Time value={artifact.updated_at} /></>}
            {dirty && <span className="text-amber-300 ml-2">· unsaved</span>}
          </div>
        </div>
        <button onClick={togglePin} className={iconBtn + ' hover:text-amber-400'}
                aria-label={artifact.pinned ? 'Unpin' : 'Pin'} aria-pressed={!!artifact.pinned}
                title={artifact.pinned ? 'Unpin' : 'Pin (keeps it at the top of search)'}>
          <Star className={`w-4 h-4 ${artifact.pinned ? 'text-amber-400 fill-current' : ''}`} />
        </button>
        <button onClick={() => setShowVersions(!showVersions)} className={iconBtn}
                aria-label="Version history" aria-pressed={showVersions} title="Version history">
          <History className="w-4 h-4" />
        </button>
        <a href={artifactsApi.rawUrl(id)} download className={iconBtn}
           aria-label="Download original" title="Download original">
          <Download className="w-4 h-4" />
        </a>
        {onRequestMove && (
          <>
            <button
              onClick={() => onRequestMove('move')}
              className={iconBtn}
              aria-label="Move"
              title="Move to another folder / project"
            >
              <Move className="w-4 h-4" />
            </button>
            <button
              onClick={() => onRequestMove('duplicate')}
              className={iconBtn}
              aria-label="Duplicate"
              title="Make an independent copy in another folder / project"
            >
              <Copy className="w-4 h-4" />
            </button>
          </>
        )}
        <button onClick={remove} className={iconBtn + ' hover:text-red-400'} aria-label="Delete artifact" title="Delete">
          <Trash2 className="w-4 h-4" />
        </button>
        <button onClick={onClose} className={iconBtn} aria-label="Close" title="Close">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Tags row */}
      <div className="border-b border-gray-800 px-4 py-2 flex items-center gap-2 flex-wrap">
        {(artifact.tags || []).map((t) => (
          <span key={t} className="text-xs px-2 py-0.5 bg-gray-800 rounded flex items-center gap-1 text-gray-300">
            {t}
            <button onClick={() => removeTag(t)} className="hover:text-red-400" aria-label={`Remove tag ${t}`} title="Remove tag"><X className="w-3 h-3" /></button>
          </span>
        ))}
        <input
          type="text"
          placeholder="add tag…"
          aria-label="Add tag"
          value={tagDraft}
          onChange={(e) => setTagDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') addTag() }}
          className="text-xs bg-gray-900 border border-gray-800 rounded px-2 py-0.5 w-28"
        />
      </div>

      {/* Text/Preview tab bar (text artifacts only) */}
      {isText && previewSupported && (
        <div className="flex items-center gap-1 px-3 py-1.5 border-b border-gray-800 bg-gray-900/40">
          <button
            onClick={() => setView('text')}
            className={`flex items-center gap-1.5 px-2.5 py-1 text-xs rounded transition-colors ${
              view === 'text' ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-white hover:bg-gray-800/50'
            }`}
          >
            <Edit3 className="w-3 h-3" /> Text
          </button>
          <button
            onClick={() => setView('preview')}
            className={`flex items-center gap-1.5 px-2.5 py-1 text-xs rounded transition-colors ${
              view === 'preview' ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-white hover:bg-gray-800/50'
            }`}
          >
            <Eye className="w-3 h-3" /> Preview
          </button>
          {dirty && (
            <div className="ml-auto flex items-center gap-2">
              <input
                type="text"
                placeholder="Edit summary"
                value={editSummary}
                onChange={(e) => setEditSummary(e.target.value)}
                className="text-xs bg-gray-900 border border-gray-800 rounded px-2 py-0.5 w-44"
              />
              <button onClick={save} disabled={saving}
                      className="px-2 py-0.5 text-xs rounded bg-brand-700 hover:bg-brand-600 text-white flex items-center gap-1 disabled:opacity-50">
                <Save className="w-3 h-3" /> {saving ? 'Saving…' : 'Save'}
              </button>
              <button onClick={() => setDraft(artifact.content || '')}
                      className="px-2 py-0.5 text-xs rounded text-gray-400 hover:text-gray-200">
                Discard
              </button>
            </div>
          )}
        </div>
      )}

      {/* Body */}
      <div className="flex-1 overflow-hidden flex">
        <div className="flex-1 overflow-y-auto">
          {isText ? (
            view === 'preview' && previewSupported ? (
              <PreviewBody artifact={artifact} preview={preview} />
            ) : (
              <CoreEditor
                value={draft}
                onChange={setDraft}
                filename={artifact.path}
                contentType={ct}
                onSaveHotkey={dirty ? save : undefined}
                className="h-full"
              />
            )
          ) : (
            <PreviewBody artifact={artifact} preview={preview} />
          )}
        </div>
        {showVersions && (
          <VersionPanel
            artifactId={id}
            versions={versions}
            onRestore={async (n) => { await artifactsApi.restoreVersion(id, n); await load(); onChanged?.() }}
            onDiff={async (a, b) => {
              const res = await artifactsApi.diff(id, a, b)
              return res.data.diff
            }}
          />
        )}
      </div>
    </div>
  )
}


// Strip YAML frontmatter (--- delimited block at the start) so it
// doesn't render as prose. Also normalize a few common LLM/trafilatura
// markdown quirks: bold markers like **foo**bar (no trailing space)
// are invalid CommonMark and render as literal asterisks; we insert a
// space so the parser produces real <strong>. Same for *italic*word.
function cleanMarkdownForPreview(raw) {
  if (!raw) return ''
  let s = String(raw)
  // 1. Strip leading frontmatter block.
  if (s.startsWith('---\n') || s.startsWith('---\r\n')) {
    const m = s.match(/^---\r?\n[\s\S]*?\r?\n---\r?\n+/)
    if (m) s = s.slice(m[0].length)
  }
  // 2. Fix **bold**word -> **bold** word (closing ** must be followed by space or punctuation).
  s = s.replace(/(\*\*)([^*\s][^*]*?[^*\s])(\*\*)(\w)/g, '$1$2$3 $4')
  // Also handle the inverse: word**bold** with no leading space.
  s = s.replace(/(\w)(\*\*)([^*\s])/g, '$1 $2$3')
  return s
}

function SvgPreview({ svgContent, basename }) {
  const containerRef = useRef(null)
  const svgRef = useRef(null)
  const [ready, setReady] = useState(false)
  useEffect(() => {
    setReady(false)
    svgRef.current = null
    if (!containerRef.current) return
    // SVG artifacts can be agent/ingest-authored; strip scripts + handlers.
    containerRef.current.innerHTML = DOMPurify.sanitize(svgContent || '', {
      USE_PROFILES: { svg: true, svgFilters: true },
    })
    svgRef.current = containerRef.current.querySelector('svg')
    setReady(!!svgRef.current)
  }, [svgContent])
  return (
    <div className="relative w-full h-full">
      <div ref={containerRef} className="p-6 flex items-center justify-center" />
      {ready && (
        <div className="absolute top-3 right-3">
          <ExportMenu getSvgEl={() => svgRef.current} basename={basename || 'svg'} />
        </div>
      )}
    </div>
  )
}

function PreviewBody({ artifact, preview }) {
  const ct = artifact.content_type || ''

  if (!preview) return <div className="p-4 text-xs text-gray-400">Loading preview…</div>

  if (preview.type === 'text') {
    if (ct === 'text/markdown') {
      return (
        <div className="prose prose-invert max-w-3xl mx-auto p-6">
          <Markdown>
            {cleanMarkdownForPreview(preview.content || '')}
          </Markdown>
        </div>
      )
    }
    return <pre className="p-6 text-xs whitespace-pre-wrap font-mono text-gray-200">{preview.content}</pre>
  }
  if (preview.type === 'svg') {
    const basename = ((artifact.path || '').split('/').pop() || 'svg').replace(/\.[^.]+$/, '') || 'svg'
    return <SvgPreview svgContent={preview.content || ''} basename={basename} />
  }
  if (preview.type === 'mermaid') {
    const basename = ((artifact.path || '').split('/').pop() || 'diagram').replace(/\.[^.]+$/, '') || 'diagram'
    return (
      <div className="p-6 flex items-center justify-center">
        <Mermaid code={preview.content || ''} basename={basename} />
      </div>
    )
  }
  if (preview.type === 'image') {
    return <div className="p-6 flex items-center justify-center"><img src={preview.url} alt={artifact.title || ''} className="max-w-full max-h-full" /></div>
  }
  if (preview.type === 'pdf') {
    return <iframe src={preview.url} title="PDF" className="w-full h-full" />
  }
  if (preview.type === 'html') {
    return <div className="p-6 prose prose-invert max-w-none" dangerouslySetInnerHTML={{ __html: DOMPurify.sanitize(preview.content || '') }} />
  }
  if (preview.type === 'sheet') {
    return (
      <div className="p-6 space-y-6 overflow-auto">
        {(preview.sheets || []).map((sh) => (
          <div key={sh.name}>
            <h3 className="text-sm font-semibold mb-2">{sh.name}</h3>
            <table className="text-xs border border-gray-800">
              <tbody>
                {sh.rows.map((row, i) => (
                  <tr key={i} className="border-b border-gray-900">
                    {row.map((c, j) => <td key={j} className="px-2 py-1 border-r border-gray-900">{String(c)}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
    )
  }
  return (
    <div className="p-6 text-xs text-gray-400">
      Preview unavailable: {preview.reason || preview.type}.{' '}
      <a href={artifactsApi.rawUrl(artifact.id)} download className="text-brand-400 underline">Download original</a>
    </div>
  )
}


function CrossProjectMoveConfirm({ info, projects, onCancel, onConfirm }) {
  const sourceName = (projects.find((p) => p.id === info.source.project_id) || {}).name || info.source.project_id
  const destName = (projects.find((p) => p.id === info.dest.project_id) || {}).name || info.dest.project_id
  const basename = (info.source.path || '').split('/').pop()
  const verb = info.mode === 'duplicate' ? 'Duplicate' : 'Move'
  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center" onClick={onCancel}>
      <div className="bg-gray-950 border border-gray-800 rounded-lg p-4 max-w-md" onClick={(e) => e.stopPropagation()}>
        <div className="text-sm font-semibold text-gray-100 mb-2">
          {verb} "{basename}" from {sourceName} to {destName}?
        </div>
        <div className="text-xs text-gray-400 mb-4">
          {info.mode === 'move'
            ? `This removes the artifact and its memory (graph + embeddings) from ${sourceName}. ${destName}'s recall will gain it.`
            : `This creates an independent copy in ${destName}. The original in ${sourceName} stays put.`}
        </div>
        <div className="flex justify-end gap-2">
          <button onClick={onCancel} className="px-3 py-1 text-xs text-gray-300 hover:text-gray-100">Cancel</button>
          <button onClick={onConfirm} className="px-3 py-1 text-xs rounded bg-amber-700 hover:bg-amber-600 text-white">{verb}</button>
        </div>
      </div>
    </div>
  )
}


function VersionPanel({ artifactId, versions, onRestore, onDiff }) {
  const [diffA, setDiffA] = useState(null)
  const [diffB, setDiffB] = useState(null)
  const [diffText, setDiffText] = useState(null)
  const runDiff = async () => {
    try { setDiffText(await onDiff(diffA, diffB)) }
    catch (e) { notify('error', 'Diff failed: ' + e.message) }
  }
  return (
    <div className="w-64 sm:w-72 shrink-0 border-l border-gray-800 bg-gray-950 overflow-y-auto p-3 space-y-2 text-xs">
      <div className="font-semibold text-gray-300 flex items-center gap-1">
        <History className="w-3 h-3" /> Version history
      </div>
      {versions.map((v) => (
        <div key={v.id} className="border border-gray-800 rounded p-2 space-y-1">
          <div className="flex items-center justify-between">
            <span className="font-medium">v{v.version_number}</span>
            <Time value={v.created_at} className="text-gray-400" />
          </div>
          <div className="text-gray-400 truncate" title={v.edit_summary}>{v.edit_summary || '—'}</div>
          <div className="text-gray-400">{v.edited_by} · {formatBytes(v.size_bytes)}</div>
          <div className="flex gap-1 pt-1">
            <button onClick={() => setDiffA(v.version_number)} title="Diff from this version" aria-label={`Diff from v${v.version_number}`} className={`px-1 rounded ${diffA === v.version_number ? 'bg-brand-700 text-white' : 'bg-gray-800 hover:bg-gray-700'}`}>A</button>
            <button onClick={() => setDiffB(v.version_number)} title="Diff to this version" aria-label={`Diff to v${v.version_number}`} className={`px-1 rounded ${diffB === v.version_number ? 'bg-brand-700 text-white' : 'bg-gray-800 hover:bg-gray-700'}`}>B</button>
            <button onClick={() => onRestore(v.version_number)} className="ml-auto px-2 rounded bg-amber-900 hover:bg-amber-800 text-amber-100">Restore</button>
          </div>
        </div>
      ))}
      {diffA && diffB && diffA !== diffB && (
        <button
          onClick={runDiff}
          className="w-full px-2 py-1 rounded bg-brand-700 hover:bg-brand-600 text-white text-xs"
        >
          Diff v{diffA} → v{diffB}
        </button>
      )}
      {diffText !== null && (
        <div className="border border-gray-800 rounded">
          <div className="flex items-center justify-between px-2 py-1 border-b border-gray-800 text-gray-400">
            <span>Diff</span>
            <button onClick={() => setDiffText(null)} className="hover:text-gray-200">Close</button>
          </div>
          <pre className="p-2 max-h-96 overflow-auto whitespace-pre font-mono text-xs text-gray-300">{diffText || '(no differences)'}</pre>
        </div>
      )}
    </div>
  )
}


function CreateArtifactModal({ projects, currentProjectId, currentFolder, onClose, onComplete }) {
  const [projectId, setProjectId] = useState(currentProjectId === 'all' ? (projects[0]?.id || 'default') : currentProjectId)
  const [path, setPath] = useState(currentFolder ? `${currentFolder}/` : '')
  const [title, setTitle] = useState('')
  const [contentType, setContentType] = useState('text/markdown')
  const [content, setContent] = useState('')
  const [tagsInput, setTagsInput] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!path.trim()) {
      setError('Path is required')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const tags = tagsInput.split(',').map((t) => t.trim()).filter(Boolean)
      await artifactsApi.create({
        project_id: projectId,
        path: path.trim(),
        content,
        content_type: contentType,
        title: title.trim() || undefined,
        tags,
      })
      onComplete()
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-gray-950 border border-gray-800 rounded-lg p-5 w-full max-w-lg space-y-4 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center border-b border-gray-900 pb-2">
          <h3 className="text-sm font-semibold text-gray-100 flex items-center gap-1.5">
            <Plus className="w-4 h-4 text-brand-400" /> Create New Artifact
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-200" aria-label="Close" title="Close">
            <X className="w-4 h-4" />
          </button>
        </div>
        {error && <div className="text-xs text-red-400 bg-red-950/20 border border-red-900/50 p-2 rounded">{error}</div>}
        <form onSubmit={handleSubmit} className="space-y-3 text-xs">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-gray-400 mb-1">Project</label>
              <select
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                className="w-full bg-gray-900 border border-gray-850 rounded p-1.5 text-gray-200 focus:outline-none focus:border-brand-500"
              >
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-gray-400 mb-1">Content Type</label>
              <select
                value={contentType}
                onChange={(e) => setContentType(e.target.value)}
                className="w-full bg-gray-900 border border-gray-850 rounded p-1.5 text-gray-200 focus:outline-none focus:border-brand-500"
              >
                <option value="text/markdown">Markdown (.md)</option>
                <option value="text/plain">Plain Text (.txt)</option>
                <option value="text/x-python">Python (.py)</option>
                <option value="text/javascript">JavaScript (.js)</option>
                <option value="application/json">JSON (.json)</option>
              </select>
            </div>
          </div>
          <div>
            <label className="block text-gray-400 mb-1">Path (e.g. docs/notes.md)</label>
            <input
              type="text"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="filename.md or folder/filename.md"
              className="w-full bg-gray-900 border border-gray-850 rounded p-1.5 text-gray-200 focus:outline-none focus:border-brand-500"
              required
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-gray-400 mb-1">Title (optional)</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="User Friendly Title"
                className="w-full bg-gray-900 border border-gray-850 rounded p-1.5 text-gray-200 focus:outline-none focus:border-brand-500"
              />
            </div>
            <div>
              <label className="block text-gray-400 mb-1">Tags (optional, comma-separated)</label>
              <input
                type="text"
                value={tagsInput}
                onChange={(e) => setTagsInput(e.target.value)}
                placeholder="tag1, tag2"
                className="w-full bg-gray-900 border border-gray-850 rounded p-1.5 text-gray-200 focus:outline-none focus:border-brand-500"
              />
            </div>
          </div>
          <div>
            <label className="block text-gray-400 mb-1">Content</label>
            <textarea
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder="Artifact content here..."
              rows={8}
              className="w-full bg-gray-900 border border-gray-850 rounded p-1.5 text-gray-200 font-mono focus:outline-none focus:border-brand-500"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2 border-t border-gray-900">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-gray-400 hover:text-gray-200"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-1.5 rounded bg-brand-700 hover:bg-brand-600 text-white font-semibold disabled:opacity-50"
            >
              {submitting ? 'Creating...' : 'Create'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

function UploadArtifactModal({ projects, currentProjectId, currentFolder, onClose, onComplete }) {
  const [projectId, setProjectId] = useState(currentProjectId === 'all' ? (projects[0]?.id || 'default') : currentProjectId)
  const [file, setFile] = useState(null)
  const [path, setPath] = useState(currentFolder ? `${currentFolder}/` : '')
  const [title, setTitle] = useState('')
  const [tagsInput, setTagsInput] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState(null)

  const handleFileChange = (e) => {
    const selectedFile = e.target.files[0]
    setFile(selectedFile)
    if (selectedFile && !path) {
      setPath(currentFolder ? `${currentFolder}/${selectedFile.name}` : selectedFile.name)
    }
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!file) {
      setError('Please select a file')
      return
    }
    setSubmitting(true)
    setError(null)
    try {
      const tags = tagsInput.split(',').map((t) => t.trim()).filter(Boolean)
      const filePath = path.trim() || file.name
      await artifactsApi.upload(file, {
        project_id: projectId,
        path: filePath,
        title: title.trim() || undefined,
        tags,
      })
      onComplete()
    } catch (err) {
      setError(err.message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="fixed inset-0 z-50 bg-black/60 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-gray-950 border border-gray-800 rounded-lg p-5 w-full max-w-md space-y-4 shadow-2xl" onClick={(e) => e.stopPropagation()}>
        <div className="flex justify-between items-center border-b border-gray-900 pb-2">
          <h3 className="text-sm font-semibold text-gray-100 flex items-center gap-1.5">
            <Upload className="w-4 h-4 text-brand-400" /> Upload Artifact
          </h3>
          <button onClick={onClose} className="text-gray-400 hover:text-gray-200" aria-label="Close" title="Close">
            <X className="w-4 h-4" />
          </button>
        </div>
        {error && <div className="text-xs text-red-400 bg-red-950/20 border border-red-900/50 p-2 rounded">{error}</div>}
        <form onSubmit={handleSubmit} className="space-y-3 text-xs">
          <div>
            <label className="block text-gray-400 mb-1">Select File</label>
            <input
              type="file"
              onChange={handleFileChange}
              className="w-full bg-gray-900 border border-gray-850 rounded p-1.5 text-gray-200 file:bg-gray-800 file:border-0 file:text-white file:text-xs file:py-1 file:px-2 file:rounded file:cursor-pointer file:mr-2 hover:file:bg-gray-700"
              required
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-gray-400 mb-1">Project</label>
              <select
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                className="w-full bg-gray-900 border border-gray-850 rounded p-1.5 text-gray-200 focus:outline-none focus:border-brand-500"
              >
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>{p.name}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="block text-gray-400 mb-1">Title (optional)</label>
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="My Uploaded Doc"
                className="w-full bg-gray-900 border border-gray-850 rounded p-1.5 text-gray-200 focus:outline-none focus:border-brand-500"
              />
            </div>
          </div>
          <div>
            <label className="block text-gray-400 mb-1">Path in Artifacts (optional)</label>
            <input
              type="text"
              value={path}
              onChange={(e) => setPath(e.target.value)}
              placeholder="defaults to file name"
              className="w-full bg-gray-900 border border-gray-850 rounded p-1.5 text-gray-200 focus:outline-none focus:border-brand-500"
            />
          </div>
          <div>
            <label className="block text-gray-400 mb-1">Tags (optional, comma-separated)</label>
            <input
              type="text"
              value={tagsInput}
              onChange={(e) => setTagsInput(e.target.value)}
              placeholder="docs, pdf, report"
              className="w-full bg-gray-900 border border-gray-850 rounded p-1.5 text-gray-200 focus:outline-none focus:border-brand-500"
            />
          </div>
          <div className="flex justify-end gap-2 pt-2 border-t border-gray-900">
            <button
              type="button"
              onClick={onClose}
              className="px-3 py-1.5 text-gray-400 hover:text-gray-200"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={submitting}
              className="px-4 py-1.5 rounded bg-brand-700 hover:bg-brand-600 text-white font-semibold disabled:opacity-50"
            >
              {submitting ? 'Uploading...' : 'Upload'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
