import React, { useState, useEffect, useMemo, useRef } from 'react'
import {
  FolderOpen, Folder, FileText, Code, Image as ImageIcon, FileSpreadsheet,
  Presentation, FileType, Star, Trash2, Plus, Upload, RefreshCw, Search,
  Save, Download, X, Tag, Edit3, Eye, History, Pin, MoreVertical, FileCode,
  PanelLeftClose, PanelLeftOpen,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import CodeMirror from '@uiw/react-codemirror'
import { markdown } from '@codemirror/lang-markdown'
import { python } from '@codemirror/lang-python'
import { javascript } from '@codemirror/lang-javascript'
import { artifactsApi } from '../api/client'
import { useStore } from '../store'

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

function formatBytes(n) {
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

function langExtension(ct) {
  if (ct === 'text/markdown') return [markdown()]
  if (ct === 'text/x-python') return [python()]
  if (ct === 'text/javascript' || ct === 'text/typescript') return [javascript({ jsx: true, typescript: ct === 'text/typescript' })]
  return []
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
  const [activeId, setActiveId] = useState(null)

  const refresh = async () => {
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
      setItems(list.data.artifacts || [])
      setFolders(foldersRes.data.folders || [])
      setTagCounts(tagsRes.data.tags || {})
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setLoading(false) }
  }

  useEffect(() => {
    const id = setTimeout(refresh, search ? 300 : 0)
    return () => clearTimeout(id)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, projectScope, filterFolder, filterTag, filterPinned, search, sort])

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
        alert('Export returned non-zip response: ' + (text || ct).slice(0, 200))
        return
      }
      const blob = res.data instanceof Blob ? res.data : new Blob([res.data], { type: 'application/zip' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url; a.download = 'artifacts.zip'; a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      alert('Export failed: ' + (e?.response?.data?.detail || e.message))
    }
  }

  return (
    <div className="h-full flex">
      {/* Left rail: folders + tags (collapsible; hidden on mobile when an artifact is open) */}
      <div className={
        (foldersCollapsed ? "w-10 " : "w-56 ") +
        "border-r border-gray-800 bg-gray-950 overflow-y-auto p-2 space-y-3 flex-shrink-0 " +
        (activeId ? "hidden md:block " : "")
      }>
        <div className={"flex " + (foldersCollapsed ? "justify-center" : "justify-end")}>
          <button
            onClick={() => setFoldersCollapsed(v => !v)}
            title={foldersCollapsed ? "Expand folders" : "Collapse folders"}
            className="text-gray-500 hover:text-gray-200 p-1 rounded"
          >
            {foldersCollapsed ? <PanelLeftOpen className="w-4 h-4" /> : <PanelLeftClose className="w-4 h-4" />}
          </button>
        </div>
        {!foldersCollapsed && (<>
        {!lockedProjectId && (
          <div>
            <div className="text-xs font-semibold text-gray-400 uppercase mb-2">
              Project scope
            </div>
            <div className="flex gap-1 mb-1 text-xs">
              <button
                onClick={() => setProjectScope('current')}
                className={`flex-1 px-2 py-1 rounded ${projectScope === 'current' ? 'bg-brand-600 text-white' : 'bg-gray-900 text-gray-400 hover:bg-gray-800'}`}
                title={activeProject?.name || 'default'}
              >
                {activeProject?.name || 'Default'}
              </button>
              <button
                onClick={() => setProjectScope('all')}
                className={`flex-1 px-2 py-1 rounded ${projectScope === 'all' ? 'bg-brand-600 text-white' : 'bg-gray-900 text-gray-400 hover:bg-gray-800'}`}
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
            className={`w-full text-left text-xs px-2 py-1 rounded ${!filterFolder ? 'bg-brand-600 text-white' : 'hover:bg-gray-900 text-gray-400'}`}
          >
            All
          </button>
          {folders.map((f) => (
            <button
              key={f}
              onClick={() => setFilterFolder(f)}
              className={`w-full text-left text-xs px-2 py-1 rounded flex items-center gap-1 ${
                filterFolder === f ? 'bg-brand-600 text-white' : 'hover:bg-gray-900 text-gray-400'
              }`}
              style={{ paddingLeft: 8 + (f.split('/').length - 1) * 12 }}
            >
              <Folder className="w-3 h-3" />
              {f.split('/').pop()}
            </button>
          ))}
        </div>

        <div>
          <div className="text-xs font-semibold text-gray-400 uppercase mb-2 flex items-center gap-1">
            <Tag className="w-3 h-3" /> Tags
          </div>
          <button
            onClick={() => setFilterTag(null)}
            className={`w-full text-left text-xs px-2 py-1 rounded ${!filterTag ? 'bg-brand-600 text-white' : 'hover:bg-gray-900 text-gray-400'}`}
          >
            All
          </button>
          {Object.entries(tagCounts).map(([t, n]) => (
            <button
              key={t}
              onClick={() => setFilterTag(t)}
              className={`w-full text-left text-xs px-2 py-1 rounded flex items-center justify-between ${
                filterTag === t ? 'bg-brand-600 text-white' : 'hover:bg-gray-900 text-gray-400'
              }`}
            >
              <span>{t}</span><span className="text-gray-600">{n}</span>
            </button>
          ))}
        </div>

        <div>
          <button
            onClick={() => setFilterPinned(!filterPinned)}
            className={`w-full text-left text-xs px-2 py-1 rounded flex items-center gap-1 ${
              filterPinned ? 'bg-brand-600 text-white' : 'hover:bg-gray-900 text-gray-400'
            }`}
          >
            <Star className="w-3 h-3" /> Pinned only
          </button>
        </div>
        </>)}
      </div>

      {/* Middle: list */}
      <div className={
        "min-w-0 border-r border-gray-800 flex flex-col " +
        (activeId
          ? "md:w-80 md:flex-shrink-0 hidden md:flex "
          : "flex-1 ")
      }>
        <div className="p-3 border-b border-gray-800 space-y-2">
          <div className="flex items-center gap-2">
            <Search className="w-3 h-3 text-gray-500" />
            <input
              type="text"
              placeholder="Search artifacts…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              className="flex-1 bg-gray-900 border border-gray-800 rounded px-2 py-1 text-xs"
            />
            <select
              value={sort}
              onChange={(e) => setSort(e.target.value)}
              className="bg-gray-900 border border-gray-800 rounded px-2 py-1 text-xs"
            >
              <option value="modified_desc">Modified ↓</option>
              <option value="modified_asc">Modified ↑</option>
              <option value="created_desc">Created ↓</option>
              <option value="title_asc">Title</option>
              <option value="size_desc">Size</option>
            </select>
            <button onClick={refresh} className="text-gray-400 hover:text-gray-200">
              <RefreshCw className="w-3 h-3" />
            </button>
          </div>
          {selected.size > 0 && (
            <div className="flex items-center gap-2 text-xs">
              <span className="text-gray-400">{selected.size} selected</span>
              <button onClick={handleBulkExport} className="px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 flex items-center gap-1">
                <Download className="w-3 h-3" /> Download zip
              </button>
              <button onClick={handleBulkDelete} className="px-2 py-1 rounded bg-red-900 hover:bg-red-800 flex items-center gap-1">
                <Trash2 className="w-3 h-3" /> Delete
              </button>
              <button onClick={() => setSelected(new Set())} className="text-gray-500 hover:text-gray-300">Clear</button>
            </div>
          )}
        </div>
        {/* Select-all row */}
        {items.length > 0 && (
          <div className="px-3 py-1.5 border-b border-gray-900 flex items-center gap-2 text-[10px] text-gray-400 bg-gray-950">
            <input
              type="checkbox"
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
          {loading && <div className="p-4 text-xs text-gray-500">Loading…</div>}
          {error && <div className="p-4 text-xs text-red-400">{error}</div>}
          {!loading && items.length === 0 && (
            <div className="p-6 text-center text-xs text-gray-500">
              No artifacts here. Save something to <code className="text-brand-300">save_to_artifact</code> in chat or create one below.
            </div>
          )}
          {items.map((a) => {
            const Icon = iconForType(a.content_type)
            return (
              <div
                key={a.id}
                onClick={() => setActiveId(a.id)}
                className={`p-2 border-b border-gray-900 cursor-pointer hover:bg-gray-900 ${activeId === a.id ? 'bg-gray-900' : ''}`}
              >
                <div className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    checked={selected.has(a.id)}
                    onClick={(e) => e.stopPropagation()}
                    onChange={() => toggleSelect(a.id)}
                    className="mt-0.5"
                  />
                  <Icon className="w-3.5 h-3.5 text-gray-500 mt-0.5" />
                  <div className="flex-1 min-w-0">
                    <div className="text-xs font-medium text-gray-200 truncate flex items-center gap-1">
                      {a.pinned && <Star className="w-3 h-3 text-amber-400" fill="currentColor" />}
                      {a.title || a.path}
                    </div>
                    <div className="text-[10px] text-gray-500 truncate">{a.path}</div>
                    <div className="flex flex-wrap gap-1 mt-1 items-center">
                      {projectScope === 'all' && a.project_id && (
                        <span className="text-[10px] px-1.5 bg-brand-900 rounded text-brand-300">
                          {(projects.find((p) => p.id === a.project_id)?.name) || a.project_id}
                        </span>
                      )}
                      {(a.tags || []).map((t) => (
                        <span key={t} className="text-[10px] px-1 bg-gray-800 rounded text-gray-400">{t}</span>
                      ))}
                      <span className="text-[10px] text-gray-600">{formatBytes(a.size_bytes)}</span>
                    </div>
                  </div>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Right: detail */}
      <div className="flex-1 min-w-0 overflow-hidden">
        {activeId ? (
          <ArtifactDetail
            id={activeId}
            onChanged={refresh}
            onClose={() => setActiveId(null)}
          />
        ) : (
          <div className="h-full flex items-center justify-center text-xs text-gray-500">
            Select an artifact to view or edit
          </div>
        )}
      </div>
    </div>
  )
}


function ArtifactDetail({ id, onChanged, onClose }) {
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
      const a = await artifactsApi.get(id)
      setArtifact(a.data)
      setDraft(a.data.content || '')
      const p = await artifactsApi.preview(id)
      setPreview(p.data)
      const v = await artifactsApi.versions(id)
      setVersions(v.data.versions || [])
    } finally { setLoading(false) }
  }

  useEffect(() => {
    load()
    setView('text') // default to text on each new artifact
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id])

  if (loading || !artifact) return <div className="p-4 text-xs text-gray-500">Loading…</div>

  const ct = artifact.content_type || ''
  const isText = !!ct.match(/^(text\/|application\/(json|yaml|xml)|chat-export)/)
  const previewSupported = !!preview && preview.type !== 'unsupported' && preview.type !== 'text'
    || ct === 'text/markdown'   // markdown always supports rendered preview
  const Icon = iconForType(ct)
  const dirty = isText && draft !== (artifact.content || '')

  const save = async () => {
    setSaving(true)
    try {
      await artifactsApi.update(id, { content: draft, edit_summary: editSummary || 'Edited via UI' })
      setEditSummary('')
      await load(); onChanged?.()
    } catch (e) {
      alert('Save failed: ' + (e?.response?.data?.detail || e.message))
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
      <div className="border-b border-gray-800 px-4 py-2 flex items-center gap-2">
        <Icon className="w-4 h-4 text-gray-500" />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium truncate">{artifact.title || artifact.path}</div>
          <div className="text-[10px] text-gray-500 truncate">
            {artifact.path} · {ct} · {formatBytes(artifact.size_bytes)} · v{versions.length}
            {dirty && <span className="text-amber-400 ml-2">· unsaved</span>}
          </div>
        </div>
        <button onClick={togglePin} className="text-gray-400 hover:text-amber-400" title="Pin">
          <Star className={`w-4 h-4 ${artifact.pinned ? 'text-amber-400 fill-current' : ''}`} />
        </button>
        <button onClick={() => setShowVersions(!showVersions)} className="text-gray-400 hover:text-gray-200" title="Version history">
          <History className="w-4 h-4" />
        </button>
        <a href={artifactsApi.rawUrl(id)} download className="text-gray-400 hover:text-gray-200" title="Download original">
          <Download className="w-4 h-4" />
        </a>
        <button onClick={remove} className="text-gray-400 hover:text-red-400" title="Delete">
          <Trash2 className="w-4 h-4" />
        </button>
        <button onClick={onClose} className="text-gray-400 hover:text-gray-200" title="Close">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Tags row */}
      <div className="border-b border-gray-800 px-4 py-2 flex items-center gap-2 flex-wrap">
        {(artifact.tags || []).map((t) => (
          <span key={t} className="text-[10px] px-2 py-0.5 bg-gray-800 rounded flex items-center gap-1 text-gray-300">
            {t}
            <button onClick={() => removeTag(t)} className="hover:text-red-400"><X className="w-3 h-3" /></button>
          </span>
        ))}
        <input
          type="text"
          placeholder="add tag…"
          value={tagDraft}
          onChange={(e) => setTagDraft(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') addTag() }}
          className="text-[10px] bg-gray-900 border border-gray-800 rounded px-2 py-0.5 w-24"
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
                      className="px-2 py-0.5 text-xs rounded bg-brand-600 hover:bg-brand-500 text-white flex items-center gap-1 disabled:opacity-50">
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
              <CodeMirror
                value={draft}
                height="100%"
                extensions={langExtension(ct)}
                onChange={(v) => setDraft(v)}
                theme="dark"
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
              alert(res.data.diff)
            }}
          />
        )}
      </div>
    </div>
  )
}


function PreviewBody({ artifact, preview }) {
  const ct = artifact.content_type || ''

  if (!preview) return <div className="p-4 text-xs text-gray-500">Loading preview…</div>

  if (preview.type === 'text') {
    if (ct === 'text/markdown') {
      return (
        <div className="prose prose-invert prose-sm max-w-none p-6">
          <ReactMarkdown remarkPlugins={[remarkGfm]}>{preview.content || ''}</ReactMarkdown>
        </div>
      )
    }
    return <pre className="p-6 text-xs whitespace-pre-wrap font-mono text-gray-200">{preview.content}</pre>
  }
  if (preview.type === 'svg') {
    return <div className="p-6 flex items-center justify-center" dangerouslySetInnerHTML={{ __html: preview.content }} />
  }
  if (preview.type === 'image') {
    return <div className="p-6 flex items-center justify-center"><img src={preview.url} alt={artifact.title || ''} className="max-w-full max-h-full" /></div>
  }
  if (preview.type === 'pdf') {
    return <iframe src={preview.url} title="PDF" className="w-full h-full" />
  }
  if (preview.type === 'html') {
    return <div className="p-6 prose prose-invert max-w-none" dangerouslySetInnerHTML={{ __html: preview.content }} />
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
    <div className="p-6 text-xs text-gray-500">
      Preview unavailable: {preview.reason || preview.type}.{' '}
      <a href={`/api/artifacts/${artifact.id}/raw`} download className="text-brand-400 underline">Download original</a>
    </div>
  )
}


function VersionPanel({ artifactId, versions, onRestore, onDiff }) {
  const [diffA, setDiffA] = useState(null)
  const [diffB, setDiffB] = useState(null)
  return (
    <div className="w-72 border-l border-gray-800 bg-gray-950 overflow-y-auto p-3 space-y-2 text-xs">
      <div className="font-semibold text-gray-300 flex items-center gap-1">
        <History className="w-3 h-3" /> Version history
      </div>
      {versions.map((v) => (
        <div key={v.id} className="border border-gray-800 rounded p-2 space-y-1">
          <div className="flex items-center justify-between">
            <span className="font-medium">v{v.version_number}</span>
            <span className="text-gray-500">{v.created_at?.slice(0, 16).replace('T', ' ')}</span>
          </div>
          <div className="text-gray-400 truncate" title={v.edit_summary}>{v.edit_summary || '—'}</div>
          <div className="text-gray-500">{v.edited_by} · {formatBytes(v.size_bytes)}</div>
          <div className="flex gap-1 pt-1">
            <button onClick={() => setDiffA(v.version_number)} className={`px-1 rounded ${diffA === v.version_number ? 'bg-brand-700 text-white' : 'bg-gray-800 hover:bg-gray-700'}`}>A</button>
            <button onClick={() => setDiffB(v.version_number)} className={`px-1 rounded ${diffB === v.version_number ? 'bg-brand-700 text-white' : 'bg-gray-800 hover:bg-gray-700'}`}>B</button>
            <button onClick={() => onRestore(v.version_number)} className="ml-auto px-2 rounded bg-amber-900 hover:bg-amber-800 text-amber-100">Restore</button>
          </div>
        </div>
      ))}
      {diffA && diffB && diffA !== diffB && (
        <button
          onClick={() => onDiff(diffA, diffB)}
          className="w-full px-2 py-1 rounded bg-brand-700 hover:bg-brand-600 text-white text-xs"
        >
          Diff v{diffA} → v{diffB}
        </button>
      )}
    </div>
  )
}
