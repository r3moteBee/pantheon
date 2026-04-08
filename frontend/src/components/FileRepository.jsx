import React, { useState, useRef, useEffect, useCallback } from 'react'
import { ChevronRight, Download, Trash2, FolderPlus, Upload, File, Folder, ArrowLeft, RefreshCw, CheckCircle, AlertCircle, Pencil, Save, Archive } from 'lucide-react'
import { useStore } from '../store'
import { filesApi } from '../api/client'
import CoreEditor from './CoreEditor'

export default function FileRepository() {
  const [currentPath, setCurrentPath] = useState('')
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [fileContent, setFileContent] = useState(null)
  const [previewFile, setPreviewFile] = useState(null)
  const fileInputRef = useRef(null)
  const [folderName, setFolderName] = useState('')
  const [showNewFolder, setShowNewFolder] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [uploadStatus, setUploadStatus] = useState(null) // { count, errors }
  const [editing, setEditing] = useState(false)
  const [editContent, setEditContent] = useState('')
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [selected, setSelected] = useState(() => new Set())
  const [zipping, setZipping] = useState(false)

  const activeProject = useStore((s) => s.activeProject)
  const addNotification = useStore((s) => s.addNotification)

  const projectId = activeProject?.id || 'default'

  const loadFiles = async (path = '') => {
    setLoading(true)
    try {
      const res = await filesApi.list(projectId, path)
      const dirs  = (res.data.directories || []).map(d => ({ ...d, is_dir: true }))
      const files = (res.data.files      || []).map(f => ({ ...f, is_dir: false }))
      setItems([...dirs, ...files])
      setSelected(new Set())
      setCurrentPath(path)
      setFileContent(null)
      setPreviewFile(null)
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  useEffect(() => {
    loadFiles()
  }, [activeProject])

  // Clear upload status after a few seconds
  useEffect(() => {
    if (uploadStatus) {
      const timer = setTimeout(() => setUploadStatus(null), 4000)
      return () => clearTimeout(timer)
    }
  }, [uploadStatus])

  const getBreadcrumbs = () => {
    if (!currentPath) return []
    return currentPath.split('/').filter(p => p)
  }

  const navigateTo = (path) => loadFiles(path)

  const openFolder = (name) => {
    const newPath = currentPath ? `${currentPath}/${name}` : name
    navigateTo(newPath)
  }

  const goBack = () => {
    if (!currentPath) return
    const parts = currentPath.split('/')
    parts.pop()
    navigateTo(parts.join('/'))
  }

  const deleteItem = async (name, isFolder) => {
    if (!confirm(`Delete ${isFolder ? 'folder' : 'file'} "${name}"?`)) return
    const path = currentPath ? `${currentPath}/${name}` : name
    try {
      await filesApi.delete(path, projectId)
      addNotification({ type: 'success', message: 'Deleted successfully' })
      loadFiles(currentPath)
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const previewTextFile = async (name) => {
    const path = currentPath ? `${currentPath}/${name}` : name
    try {
      const res = await filesApi.read(path, projectId)
      setFileContent(res.data.content)
      setPreviewFile(name)
      setEditing(false)
      setDirty(false)
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const startEditing = () => {
    setEditContent(fileContent)
    setEditing(true)
    setDirty(false)
  }

  const cancelEditing = () => {
    if (dirty && !confirm('Discard unsaved changes?')) return
    setEditing(false)
    setDirty(false)
  }

  const saveFile = async () => {
    if (!previewFile) return
    const path = currentPath ? `${currentPath}/${previewFile}` : previewFile
    setSaving(true)
    try {
      await filesApi.write(path, editContent, projectId)
      setFileContent(editContent)
      setEditing(false)
      setDirty(false)
      addNotification({ type: 'success', message: 'File saved' })
      loadFiles(currentPath)
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setSaving(false)
  }

  const uploadFiles = useCallback(async (fileList) => {
    if (!fileList || fileList.length === 0) return

    setUploading(true)
    setUploadStatus(null)
    try {
      if (fileList.length === 1) {
        await filesApi.upload(fileList[0], projectId, currentPath)
      } else {
        await filesApi.uploadMultiple(Array.from(fileList), projectId, currentPath)
      }
      setUploadStatus({ count: fileList.length, errors: 0 })
      addNotification({ type: 'success', message: `${fileList.length} file${fileList.length > 1 ? 's' : ''} uploaded` })
      loadFiles(currentPath)
    } catch (err) {
      setUploadStatus({ count: 0, errors: fileList.length })
      addNotification({ type: 'error', message: err.message })
    }
    setUploading(false)
  }, [projectId, currentPath])

  const createFolder = async () => {
    if (!folderName.trim()) return
    const path = currentPath ? `${currentPath}/${folderName}` : folderName
    try {
      await filesApi.mkdir(path, projectId)
      addNotification({ type: 'success', message: 'Folder created' })
      setFolderName('')
      setShowNewFolder(false)
      loadFiles(currentPath)
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const isTextFile = (name) => {
    const exts = ['.txt', '.md', '.json', '.js', '.py', '.html', '.css', '.yml', '.yaml', '.ts', '.jsx', '.tsx', '.toml', '.cfg', '.ini', '.sh', '.env', '.log']
    return exts.some(ext => name.toLowerCase().endsWith(ext))
  }

  // Drag-and-drop handlers
  const handleDragOver = useCallback((e) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(true)
  }, [])

  const handleDragLeave = useCallback((e) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
  }, [])

  const handleDrop = useCallback((e) => {
    e.preventDefault()
    e.stopPropagation()
    setDragOver(false)
    const files = e.dataTransfer.files
    if (files?.length) uploadFiles(files)
  }, [uploadFiles])

  const getDownloadUrl = (name) => {
    const path = currentPath ? `${currentPath}/${name}` : name
    return filesApi.downloadUrl(path, projectId)
  }

  const toggleSelect = (name) => {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(name)) next.delete(name)
      else next.add(name)
      return next
    })
  }

  const allSelected = items.length > 0 && selected.size === items.length
  const someSelected = selected.size > 0 && !allSelected

  const toggleSelectAll = () => {
    if (allSelected) setSelected(new Set())
    else setSelected(new Set(items.map((i) => i.name)))
  }

  const downloadSelectedZip = async () => {
    if (selected.size === 0) return
    const paths = Array.from(selected).map((name) =>
      currentPath ? `${currentPath}/${name}` : name
    )
    setZipping(true)
    try {
      const res = await filesApi.downloadZip(paths, projectId)
      const blob = new Blob([res.data], { type: 'application/zip' })
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `${projectId}-files.zip`
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(url)
      addNotification({ type: 'success', message: `Downloaded ${selected.size} item${selected.size > 1 ? 's' : ''} as zip` })
    } catch (err) {
      addNotification({ type: 'error', message: err.message || 'Zip download failed' })
    }
    setZipping(false)
  }

  const formatSize = (bytes) => {
    if (!bytes) return ''
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  return (
    <div className="flex flex-col h-full bg-gray-950">
      {/* Header */}
      <div className="px-6 py-4 bg-gray-900 border-b border-gray-800">
        <h1 className="text-xl font-bold text-gray-100">File Repository</h1>
      </div>

      <div className="flex flex-1 overflow-hidden">
        {/* File browser — hidden while previewing a file so the preview/editor gets the full pane */}
        <div className={`flex-1 flex flex-col border-r border-gray-800 ${previewFile ? 'hidden' : ''}`}>
          {/* Breadcrumb and actions */}
          <div className="px-6 py-3 bg-gray-900 border-b border-gray-800 space-y-3">
            <div className="flex items-center gap-2">
              {currentPath && (
                <button
                  onClick={goBack}
                  className="p-1 hover:bg-gray-800 rounded"
                  title="Go back"
                >
                  <ArrowLeft className="w-4 h-4 text-gray-400" />
                </button>
              )}
              <div className="flex items-center gap-1 text-sm text-gray-400">
                <button
                  onClick={() => navigateTo('')}
                  className="hover:text-brand-300"
                >
                  /
                </button>
                {getBreadcrumbs().map((part, i, arr) => (
                  <React.Fragment key={i}>
                    <button
                      onClick={() => {
                        const path = arr.slice(0, i + 1).join('/')
                        navigateTo(path)
                      }}
                      className="text-brand-400 hover:text-brand-300"
                    >
                      {part}
                    </button>
                    {i < arr.length - 1 && <span>/</span>}
                  </React.Fragment>
                ))}
              </div>
            </div>

            {/* Action buttons */}
            <div className="flex items-center gap-2">
              <button
                onClick={() => fileInputRef.current?.click()}
                disabled={uploading}
                className="flex items-center gap-2 px-3 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-lg disabled:opacity-50"
              >
                <Upload className="w-4 h-4" />
                {uploading ? 'Uploading...' : 'Upload Files'}
              </button>
              <button
                onClick={() => setShowNewFolder(!showNewFolder)}
                className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-lg"
              >
                <FolderPlus className="w-4 h-4" />
                New Folder
              </button>
              {selected.size > 0 && (
                <button
                  onClick={downloadSelectedZip}
                  disabled={zipping}
                  className="flex items-center gap-2 px-3 py-2 bg-green-700 hover:bg-green-600 text-white text-sm rounded-lg disabled:opacity-50"
                  title="Download selected as zip"
                >
                  <Archive className="w-4 h-4" />
                  {zipping ? 'Zipping…' : `Download zip (${selected.size})`}
                </button>
              )}
              <button
                onClick={() => loadFiles(currentPath)}
                disabled={loading}
                className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-lg disabled:opacity-50"
              >
                <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
              </button>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                onChange={(e) => {
                  if (e.target.files?.length) {
                    uploadFiles(e.target.files)
                    e.target.value = ''   // reset so same files can be re-selected
                  }
                }}
                className="hidden"
              />

              {/* Upload status indicator */}
              {uploadStatus && (
                <div className={`flex items-center gap-1.5 text-xs px-2 py-1 rounded ${
                  uploadStatus.errors ? 'text-red-400 bg-red-900/30' : 'text-green-400 bg-green-900/30'
                }`}>
                  {uploadStatus.errors ? (
                    <><AlertCircle className="w-3.5 h-3.5" /> Upload failed</>
                  ) : (
                    <><CheckCircle className="w-3.5 h-3.5" /> {uploadStatus.count} file{uploadStatus.count > 1 ? 's' : ''} uploaded</>
                  )}
                </div>
              )}
            </div>

            {/* New folder form */}
            {showNewFolder && (
              <div className="flex gap-2">
                <input
                  type="text"
                  value={folderName}
                  onChange={(e) => setFolderName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') createFolder()
                    if (e.key === 'Escape') {
                      setShowNewFolder(false)
                      setFolderName('')
                    }
                  }}
                  placeholder="Folder name..."
                  className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
                  autoFocus
                />
                <button
                  onClick={createFolder}
                  className="px-3 py-2 bg-green-600 hover:bg-green-700 text-white text-sm rounded-lg"
                >
                  Create
                </button>
              </div>
            )}
          </div>

          {/* File list with drag-and-drop */}
          <div
            className={`flex-1 overflow-y-auto scrollbar-thin relative transition-colors ${
              dragOver ? 'bg-brand-900/20' : ''
            }`}
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
          >
            {/* Drag overlay */}
            {dragOver && (
              <div className="absolute inset-0 flex items-center justify-center bg-brand-900/30 border-2 border-dashed border-brand-500 rounded-lg z-10 pointer-events-none">
                <div className="text-center">
                  <Upload className="w-10 h-10 text-brand-400 mx-auto mb-2" />
                  <p className="text-brand-300 text-sm font-medium">Drop files here to upload</p>
                </div>
              </div>
            )}

            {items.length === 0 && !loading ? (
              <div
                className="flex flex-col items-center justify-center h-full text-gray-500 gap-3 cursor-pointer"
                onClick={() => fileInputRef.current?.click()}
              >
                <Upload className="w-8 h-8 text-gray-600" />
                <p>No files or folders</p>
                <p className="text-xs text-gray-600">Click to upload or drag and drop files here</p>
              </div>
            ) : (
              <div className="divide-y divide-gray-800">
                {/* Select-all header */}
                <div className="flex items-center gap-3 px-6 py-2 bg-gray-900/50 text-xs text-gray-500 sticky top-0 z-[1]">
                  <input
                    type="checkbox"
                    checked={allSelected}
                    ref={(el) => { if (el) el.indeterminate = someSelected }}
                    onChange={toggleSelectAll}
                    className="w-4 h-4 rounded border-gray-600 bg-gray-800 accent-brand-500 cursor-pointer"
                    title="Select all"
                  />
                  <span>
                    {selected.size > 0 ? `${selected.size} selected` : `${items.length} item${items.length === 1 ? '' : 's'}`}
                  </span>
                </div>
                {items.map((item) => (
                  <div key={item.name} className="flex items-center gap-3 px-6 py-3 hover:bg-gray-800 transition-colors group">
                    <input
                      type="checkbox"
                      checked={selected.has(item.name)}
                      onChange={() => toggleSelect(item.name)}
                      onClick={(e) => e.stopPropagation()}
                      className="w-4 h-4 rounded border-gray-600 bg-gray-800 accent-brand-500 cursor-pointer flex-shrink-0"
                      title="Select"
                    />
                    <button
                      onClick={() => item.is_dir ? openFolder(item.name) : (isTextFile(item.name) ? previewTextFile(item.name) : null)}
                      className="flex-1 flex items-center gap-3 min-w-0"
                    >
                      {item.is_dir ? (
                        <Folder className="w-4 h-4 text-blue-400 flex-shrink-0" />
                      ) : (
                        <File className="w-4 h-4 text-gray-400 flex-shrink-0" />
                      )}
                      <span className="text-sm text-gray-300 truncate">{item.name}</span>
                      {!item.is_dir && item.size != null && (
                        <span className="text-xs text-gray-600 flex-shrink-0">
                          {formatSize(item.size)}
                        </span>
                      )}
                    </button>

                    {/* Download button — always visible for files */}
                    {!item.is_dir && (
                      <a
                        href={getDownloadUrl(item.name)}
                        download
                        className="p-1.5 text-gray-500 hover:text-green-400 rounded hover:bg-gray-700"
                        title={`Download ${item.name}`}
                        onClick={(e) => e.stopPropagation()}
                      >
                        <Download className="w-4 h-4" />
                      </a>
                    )}

                    <button
                      onClick={() => deleteItem(item.name, item.is_dir)}
                      className="p-1.5 text-gray-500 hover:text-red-400 opacity-0 group-hover:opacity-100 rounded hover:bg-gray-700"
                      title="Delete"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Preview / Edit pane — full width, toggles between preview (default) and CoreEditor */}
        {previewFile && fileContent !== null && (
          <div className="flex-1 flex flex-col bg-gray-900 min-w-0">
            <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between gap-2">
              <div className="flex items-center gap-2 min-w-0">
                <button
                  onClick={() => {
                    if (dirty && !confirm('Discard unsaved changes?')) return
                    setPreviewFile(null)
                    setFileContent(null)
                    setEditing(false)
                    setDirty(false)
                  }}
                  className="p-1 text-gray-500 hover:text-gray-300 rounded hover:bg-gray-800 flex-shrink-0"
                  title="Back to files"
                >
                  <ArrowLeft className="w-4 h-4" />
                </button>
                <p className="text-sm font-medium text-gray-300 truncate">{previewFile}</p>
                <p className="text-xs text-gray-600 flex-shrink-0">{editing ? 'Editing' : 'Preview'}</p>
                {dirty && <span className="text-xs text-yellow-500 flex-shrink-0">unsaved</span>}
              </div>
              <div className="flex items-center gap-1 flex-shrink-0">
                {editing ? (
                  <>
                    <button
                      onClick={saveFile}
                      disabled={saving}
                      className="p-1 text-green-500 hover:text-green-400 rounded hover:bg-gray-800 disabled:opacity-50"
                      title="Save (Ctrl+S)"
                    >
                      <Save className="w-4 h-4" />
                    </button>
                    <button
                      onClick={cancelEditing}
                      className="p-1 text-gray-500 hover:text-gray-300 rounded hover:bg-gray-800"
                      title="Cancel editing"
                    >
                      <X className="w-4 h-4" />
                    </button>
                  </>
                ) : (
                  <>
                    {isTextFile(previewFile) && (
                      <button
                        onClick={startEditing}
                        className="p-1 text-gray-500 hover:text-brand-400 rounded hover:bg-gray-800"
                        title="Edit file"
                      >
                        <Pencil className="w-4 h-4" />
                      </button>
                    )}
                    <a
                      href={getDownloadUrl(previewFile)}
                      download
                      className="p-1 text-gray-500 hover:text-green-400 rounded hover:bg-gray-800"
                      title="Download"
                    >
                      <Download className="w-4 h-4" />
                    </a>
                  </>
                )}
              </div>
            </div>
            <div className="flex-1 overflow-y-auto scrollbar-thin">
              {editing ? (
                <div
                  className="w-full h-full"
                  onKeyDown={(e) => {
                    if (e.key === 'Escape') cancelEditing()
                  }}
                >
                  <CoreEditor
                    value={editContent}
                    filename={previewFile}
                    height="100%"
                    onChange={(val) => {
                      setEditContent(val)
                      setDirty(true)
                    }}
                    onSaveHotkey={saveFile}
                  />
                </div>
              ) : (
                <pre className="p-6 text-sm text-gray-300 whitespace-pre-wrap break-words max-w-4xl mx-auto">
                  {fileContent}
                </pre>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function X(props) {
  return <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" {...props}><path d="M18 6l-12 12M6 6l12 12"/></svg>
}
