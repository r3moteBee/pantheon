import React, { useState, useRef, useCallback } from 'react'
import {
  Download, Search, Globe, Github, FileArchive, Upload, X,
  Loader2, ShieldCheck, ShieldX, AlertTriangle, ExternalLink,
  Package, CheckCircle2,
} from 'lucide-react'
import { skillsApi } from '../api/client'

// ── Hub icon helper ────────────────────────────────────────────────────────

function HubIcon({ hub, className = 'w-4 h-4' }) {
  switch (hub) {
    case 'github': return <Github className={className} />
    case 'local': return <FileArchive className={className} />
    default: return <Globe className={className} />
  }
}

// ── Search result card ─────────────────────────────────────────────────────

function SearchResultCard({ result, onImport, importing }) {
  return (
    <div className="border border-gray-700 rounded-lg p-3 hover:border-gray-600 transition-colors">
      <div className="flex items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <HubIcon hub={result.hub} className="w-3.5 h-3.5 text-gray-500 flex-shrink-0" />
            <span className="text-sm font-medium text-gray-200 truncate">{result.name}</span>
            {result.version && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-gray-700 text-gray-400">
                v{result.version}
              </span>
            )}
          </div>
          {result.description && (
            <p className="text-xs text-gray-400 mt-1 line-clamp-2">{result.description}</p>
          )}
          <div className="flex items-center gap-2 mt-1.5">
            {result.author && (
              <span className="text-[10px] text-gray-500">by {result.author}</span>
            )}
            {result.tags?.length > 0 && (
              <div className="flex gap-1">
                {result.tags.slice(0, 3).map((tag) => (
                  <span key={tag} className="text-[10px] px-1 py-0.5 rounded bg-gray-800 text-gray-500">
                    {tag}
                  </span>
                ))}
              </div>
            )}
          </div>
        </div>
        <button
          onClick={() => onImport(result)}
          disabled={importing}
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded bg-brand-600 hover:bg-brand-500 text-white disabled:opacity-50 disabled:cursor-not-allowed flex-shrink-0"
        >
          {importing ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
          Import
        </button>
      </div>
    </div>
  )
}

// ── Import result banner ───────────────────────────────────────────────────

function ImportResultBanner({ result, onDismiss }) {
  if (!result) return null

  const isSuccess = result.success && !result.quarantined
  const isQuarantined = result.success && result.quarantined
  const isFailed = !result.success

  return (
    <div
      className={`rounded-lg p-3 flex items-start gap-3 ${
        isSuccess
          ? 'bg-green-900/30 border border-green-800'
          : isQuarantined
          ? 'bg-amber-900/30 border border-amber-800'
          : 'bg-red-900/30 border border-red-800'
      }`}
    >
      {isSuccess && <CheckCircle2 className="w-4 h-4 text-green-400 flex-shrink-0 mt-0.5" />}
      {isQuarantined && <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0 mt-0.5" />}
      {isFailed && <ShieldX className="w-4 h-4 text-red-400 flex-shrink-0 mt-0.5" />}
      <div className="flex-1 min-w-0">
        <p className={`text-xs font-medium ${
          isSuccess ? 'text-green-300' : isQuarantined ? 'text-amber-300' : 'text-red-300'
        }`}>
          {isSuccess && `"${result.skill_name}" imported successfully`}
          {isQuarantined && `"${result.skill_name}" quarantined — scan failed`}
          {isFailed && `Import failed`}
        </p>
        <p className="text-[10px] text-gray-400 mt-0.5">{result.message}</p>
        {result.scan_risk != null && (
          <p className="text-[10px] text-gray-500 mt-0.5">
            Risk: {Math.round(result.scan_risk * 100)}% · {result.scan_findings} finding{result.scan_findings !== 1 ? 's' : ''}
          </p>
        )}
      </div>
      <button onClick={onDismiss} className="text-gray-500 hover:text-gray-300">
        <X className="w-3.5 h-3.5" />
      </button>
    </div>
  )
}

// ── Main SkillImporter component ───────────────────────────────────────────

export default function SkillImporter({ onClose, onImportComplete }) {
  const [activeTab, setActiveTab] = useState('search')
  const [searchQuery, setSearchQuery] = useState('')
  const [searchHub, setSearchHub] = useState('')
  const [searchResults, setSearchResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [importing, setImporting] = useState(null) // name of skill being imported
  const [importResult, setImportResult] = useState(null)
  const [githubUrl, setGithubUrl] = useState('')
  const [aiReview, setAiReview] = useState(true)
  const [dragOver, setDragOver] = useState(false)
  const fileInputRef = useRef(null)

  // ── Search ─────────────────────────────────────────────────────────────

  const handleSearch = useCallback(async () => {
    if (!searchQuery.trim()) return
    setSearching(true)
    setSearchResults([])
    try {
      const res = await skillsApi.searchHub(searchQuery.trim(), searchHub || null)
      setSearchResults(res.data?.results || [])
    } catch (e) {
      console.error('Hub search failed:', e)
    } finally {
      setSearching(false)
    }
  }, [searchQuery, searchHub])

  const handleSearchKeyDown = (e) => {
    if (e.key === 'Enter') handleSearch()
  }

  // ── Import from search result ──────────────────────────────────────────

  const handleImportResult = async (result) => {
    setImporting(result.name)
    setImportResult(null)
    try {
      const hub = result.hub || 'github'
      const source = result.download_url || result.url || result.name
      const res = await skillsApi.importSkill(source, hub, aiReview)
      setImportResult(res.data)
      if (res.data?.success) onImportComplete?.()
    } catch (e) {
      setImportResult({
        success: false,
        message: e.response?.data?.detail || e.message || 'Import failed',
      })
    } finally {
      setImporting(null)
    }
  }

  // ── Import from GitHub URL ─────────────────────────────────────────────

  const handleGithubImport = async () => {
    if (!githubUrl.trim()) return
    setImporting('github')
    setImportResult(null)
    try {
      const res = await skillsApi.importSkill(githubUrl.trim(), 'github', aiReview)
      setImportResult(res.data)
      if (res.data?.success) {
        setGithubUrl('')
        onImportComplete?.()
      }
    } catch (e) {
      setImportResult({
        success: false,
        message: e.response?.data?.detail || e.message || 'Import failed',
      })
    } finally {
      setImporting(null)
    }
  }

  // ── File upload ────────────────────────────────────────────────────────

  const handleFileUpload = async (file) => {
    if (!file) return
    const validExts = ['.zip', '.tar.gz', '.tgz']
    const name = file.name.toLowerCase()
    if (!validExts.some((ext) => name.endsWith(ext))) {
      setImportResult({
        success: false,
        message: `Unsupported file type. Accepted: ${validExts.join(', ')}`,
      })
      return
    }

    setImporting('upload')
    setImportResult(null)
    try {
      const res = await skillsApi.importUpload(file, aiReview)
      setImportResult(res.data)
      if (res.data?.success) onImportComplete?.()
    } catch (e) {
      setImportResult({
        success: false,
        message: e.response?.data?.detail || e.message || 'Upload failed',
      })
    } finally {
      setImporting(null)
    }
  }

  const handleDrop = (e) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer?.files?.[0]
    if (file) handleFileUpload(file)
  }

  const handleDragOver = (e) => {
    e.preventDefault()
    setDragOver(true)
  }

  // ── Tabs ───────────────────────────────────────────────────────────────

  const tabs = [
    { id: 'search', label: 'Search Hubs', icon: Search },
    { id: 'github', label: 'GitHub', icon: Github },
    { id: 'upload', label: 'Upload', icon: Upload },
  ]

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-xl shadow-xl max-w-2xl w-full max-h-[80vh] flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-5 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Download className="w-4 h-4 text-brand-400" />
          Import Skill
        </h2>
        <button onClick={onClose} className="text-gray-500 hover:text-gray-300">
          <X className="w-4 h-4" />
        </button>
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-800 px-5">
        {tabs.map((t) => {
          const Icon = t.icon
          return (
            <button
              key={t.id}
              onClick={() => setActiveTab(t.id)}
              className={`flex items-center gap-1.5 px-3 py-2 text-xs font-medium border-b-2 transition-colors ${
                activeTab === t.id
                  ? 'border-brand-400 text-brand-300'
                  : 'border-transparent text-gray-500 hover:text-gray-300'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          )
        })}
        {/* AI Review toggle */}
        <div className="ml-auto flex items-center gap-1.5 py-2">
          <span className="text-[10px] text-gray-500">AI Review</span>
          <button
            onClick={() => setAiReview(!aiReview)}
            className={`w-7 h-4 rounded-full flex items-center transition-colors ${
              aiReview ? 'bg-brand-600 justify-end' : 'bg-gray-700 justify-start'
            }`}
          >
            <span className="w-3 h-3 rounded-full bg-white mx-0.5" />
          </button>
        </div>
      </div>

      {/* Import result */}
      {importResult && (
        <div className="px-5 pt-3">
          <ImportResultBanner result={importResult} onDismiss={() => setImportResult(null)} />
        </div>
      )}

      {/* Content */}
      <div className="flex-1 overflow-y-auto px-5 py-4">
        {/* Search Hubs tab */}
        {activeTab === 'search' && (
          <div className="space-y-3">
            <div className="flex gap-2">
              <select
                value={searchHub}
                onChange={(e) => setSearchHub(e.target.value)}
                className="bg-gray-800 border border-gray-700 rounded-lg px-2 py-1.5 text-xs text-gray-300 outline-none"
              >
                <option value="">All hubs</option>
                <option value="github">GitHub</option>
              </select>
              <div className="flex-1 relative">
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={handleSearchKeyDown}
                  placeholder="Search for skills..."
                  className="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-xs text-gray-200 placeholder:text-gray-600 outline-none focus:border-brand-500"
                />
              </div>
              <button
                onClick={handleSearch}
                disabled={!searchQuery.trim() || searching}
                className="flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg bg-brand-600 hover:bg-brand-500 text-white disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {searching ? <Loader2 className="w-3 h-3 animate-spin" /> : <Search className="w-3 h-3" />}
                Search
              </button>
            </div>

            {searching && (
              <div className="flex items-center justify-center py-8 text-gray-500 text-xs">
                <Loader2 className="w-4 h-4 animate-spin mr-2" />
                Searching hubs...
              </div>
            )}

            {!searching && searchResults.length > 0 && (
              <div className="space-y-2">
                <p className="text-[10px] text-gray-500">{searchResults.length} result{searchResults.length !== 1 ? 's' : ''}</p>
                {searchResults.map((result, i) => (
                  <SearchResultCard
                    key={`${result.hub}-${result.name}-${i}`}
                    result={result}
                    onImport={handleImportResult}
                    importing={importing === result.name}
                  />
                ))}
              </div>
            )}

            {!searching && searchResults.length === 0 && searchQuery && (
              <div className="text-center py-8 text-gray-500 text-xs">
                No results. Try a different query or hub.
              </div>
            )}

            {!searchQuery && !searching && (
              <div className="text-center py-8 text-gray-600 text-xs">
                <Search className="w-6 h-6 mx-auto mb-2 opacity-40" />
                Search GitHub for skills to import
              </div>
            )}
          </div>
        )}

        {/* GitHub tab */}
        {activeTab === 'github' && (
          <div className="space-y-3">
            <p className="text-xs text-gray-400">
              Import a skill directly from a GitHub repository URL.
            </p>
            <div className="flex gap-2">
              <input
                type="text"
                value={githubUrl}
                onChange={(e) => setGithubUrl(e.target.value)}
                placeholder="https://github.com/user/repo or user/repo"
                className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-xs text-gray-200 placeholder:text-gray-600 outline-none focus:border-brand-500"
                onKeyDown={(e) => e.key === 'Enter' && handleGithubImport()}
              />
              <button
                onClick={handleGithubImport}
                disabled={!githubUrl.trim() || importing === 'github'}
                className="flex items-center gap-1.5 px-4 py-2 text-xs font-medium rounded-lg bg-brand-600 hover:bg-brand-500 text-white disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {importing === 'github' ? (
                  <Loader2 className="w-3 h-3 animate-spin" />
                ) : (
                  <Download className="w-3 h-3" />
                )}
                Import
              </button>
            </div>
            <div className="text-[10px] text-gray-600 space-y-1">
              <p>Supported formats: Pantheon (skill.json), SKILL.md, MCP tools, or plain repos with a README.</p>
              <p>The repo is downloaded as a zip, scanned, and installed. Failed scans result in quarantine.</p>
            </div>
          </div>
        )}

        {/* Upload tab */}
        {activeTab === 'upload' && (
          <div className="space-y-3">
            <p className="text-xs text-gray-400">
              Upload a skill package (.zip or .tar.gz).
            </p>
            <div
              onDrop={handleDrop}
              onDragOver={handleDragOver}
              onDragLeave={() => setDragOver(false)}
              onClick={() => fileInputRef.current?.click()}
              className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors ${
                dragOver
                  ? 'border-brand-400 bg-brand-900/10'
                  : 'border-gray-700 hover:border-gray-600'
              }`}
            >
              {importing === 'upload' ? (
                <div className="flex flex-col items-center gap-2">
                  <Loader2 className="w-8 h-8 text-brand-400 animate-spin" />
                  <p className="text-xs text-gray-400">Importing and scanning...</p>
                </div>
              ) : (
                <div className="flex flex-col items-center gap-2">
                  <Upload className="w-8 h-8 text-gray-600" />
                  <p className="text-xs text-gray-400">
                    Drop a file here or <span className="text-brand-400">click to browse</span>
                  </p>
                  <p className="text-[10px] text-gray-600">Accepts .zip and .tar.gz</p>
                </div>
              )}
              <input
                ref={fileInputRef}
                type="file"
                accept=".zip,.tar.gz,.tgz"
                className="hidden"
                onChange={(e) => handleFileUpload(e.target.files?.[0])}
              />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
