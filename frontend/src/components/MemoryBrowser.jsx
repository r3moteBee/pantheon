import React, { useState, useEffect, useRef, useMemo } from 'react'
import { toDate } from '../utils/time'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { Search, Trash2, RefreshCw, ChevronDown, ChevronRight, Plus, X } from 'lucide-react'
import { useStore } from '../store'
import { memoryApi } from '../api/client'
import GraphView from './GraphView'
import Time from './Time'
import InfoBox from './InfoBox'
import EpisodicEntry, { semanticSummary, sessionLabel } from './memory/EpisodicEntry'

// Load once per project id. The effects used to depend on the activeProject
// object, which Layout replaces (id-only -> full record) right after mount,
// and React StrictMode re-runs mount effects in dev: both refetched (#26).
// The ref survives StrictMode's simulated remount, so one load per id.
function useLoadPerProject(projectId, load) {
  const loadedFor = useRef(null)
  useEffect(() => {
    if (!projectId || loadedFor.current === projectId) return
    loadedFor.current = projectId
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId])
}

function EpisodicTab() {
  const [notes, setNotes] = useState([])
  const [messages, setMessages] = useState([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [view, setView] = useState('messages')
  const activeProject = useStore((s) => s.activeProject)
  const addNotification = useStore((s) => s.addNotification)

  const loadData = async () => {
    setLoading(true)
    const pid = activeProject?.id || 'default'
    try {
      const [notesRes, msgsRes] = await Promise.all([
        memoryApi.listNotes(pid),
        memoryApi.listMessages(pid, 50),
      ])
      setNotes(notesRes.data.notes || [])
      setMessages(msgsRes.data.messages || [])
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  useLoadPerProject(activeProject?.id, loadData)

  const filteredNotes = notes.filter(n =>
    n.content.toLowerCase().includes(search.toLowerCase()) ||
    n.id.toLowerCase().includes(search.toLowerCase())
  )

  const filteredMessages = messages.filter(m =>
    [m.content, m.role, m.session_id, sessionLabel(m)].some((v) => (v || '').toLowerCase().includes(search.toLowerCase()))
  )

  const deleteNote = async (noteId) => {
    try {
      await memoryApi.deleteNote(noteId)
      setNotes(notes.filter(n => n.id !== noteId))
      addNotification({ type: 'success', message: 'Note deleted' })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const deleteMessage = async (messageId) => {
    try {
      await memoryApi.deleteMessage(messageId)
      setMessages(messages.filter(m => m.id !== messageId))
      addNotification({ type: 'success', message: 'Message deleted' })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <div className="flex-1 relative">
          <Search className="w-4 h-4 absolute left-3 top-3 text-gray-400" aria-hidden="true" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search..."
            className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
          />
        </div>
        <button
          onClick={loadData}
          disabled={loading}
          aria-label="Reload episodic memory"
          title="Reload"
          className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-gray-300 disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      {/* Sub-tabs: Messages vs Notes */}
      <div className="flex gap-1 bg-gray-800/50 rounded-lg p-0.5">
        <button
          onClick={() => setView('messages')}
          className={`flex-1 py-1.5 px-3 text-xs font-medium rounded-md transition-colors ${
            view === 'messages' ? 'bg-gray-700 text-gray-100' : 'text-gray-400 hover:text-gray-300'
          }`}
        >
          Messages ({messages.length})
        </button>
        <button
          onClick={() => setView('notes')}
          className={`flex-1 py-1.5 px-3 text-xs font-medium rounded-md transition-colors ${
            view === 'notes' ? 'bg-gray-700 text-gray-100' : 'text-gray-400 hover:text-gray-300'
          }`}
        >
          Notes ({notes.length})
        </button>
      </div>

      {view === 'messages' && (
        <div className="space-y-2">
          {filteredMessages.map(msg => (
            <EpisodicEntry key={msg.id} item={msg} onDelete={deleteMessage} />
          ))}
          {filteredMessages.length === 0 && !loading && (
            <p className="text-center text-gray-400 py-8">
              {messages.length === 0 ? 'No messages stored yet' : 'No messages match the search'}
            </p>
          )}
        </div>
      )}

      {view === 'notes' && (
        <div className="space-y-2">
          {filteredNotes.map(note => (
            <EpisodicEntry key={note.id} kind="note" item={note} onDelete={deleteNote} />
          ))}
          {filteredNotes.length === 0 && !loading && (
            <p className="text-center text-gray-400 py-8">
              {notes.length === 0
                ? 'No episodic notes yet. The agent adds one when you ask it to remember something about this conversation.'
                : 'No notes match the search'}
            </p>
          )}
        </div>
      )}
    </div>
  )
}

function SemanticTab() {
  const [docs, setDocs] = useState([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const activeProject = useStore((s) => s.activeProject)
  const addNotification = useStore((s) => s.addNotification)

  const loadDocs = async () => {
    setLoading(true)
    try {
      const res = await memoryApi.listSemantic(activeProject?.id || 'default', 100)
      setDocs(res.data.items || [])
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  useLoadPerProject(activeProject?.id, loadDocs)

  // Entries from one source (snapshots of a task ledger, chunks of one file)
  // look alike until expanded (round 3 #3): number them oldest to newest
  // (seq/seqTotal) and the card shows the exact time instead of "2 h ago".
  const numbered = useMemo(() => {
    const groups = new Map()
    for (const d of docs) {
      const s = semanticSummary(d)
      const key = s.source_title || s.source_path || s.session_id
      if (!key) continue
      const k = `${s.kind}|${key}`
      if (!groups.has(k)) groups.set(k, [])
      groups.get(k).push(d)
    }
    const seq = new Map()
    for (const list of groups.values()) {
      if (list.length < 2) continue
      const sorted = [...list].sort((a, b) => (toDate(semanticSummary(a).created_at) || 0) - (toDate(semanticSummary(b).created_at) || 0))
      sorted.forEach((d, i) => seq.set(d.id, { seq: i + 1, seqTotal: sorted.length }))
    }
    return docs.map((d) => (seq.has(d.id) ? { ...d, ...seq.get(d.id) } : d))
  }, [docs])

  const q = search.toLowerCase()
  const filtered = numbered.filter(d => {
    const s = semanticSummary(d)
    return [d.content, d.id, s.kind, s.source_path, s.source_title].some((v) => (v || '').toLowerCase().includes(q))
  })

  const deleteDoc = async (docId) => {
    try {
      await memoryApi.deleteSemantic(docId, activeProject?.id || 'default')
      setDocs(docs.filter(d => d.id !== docId))
      addNotification({ type: 'success', message: 'Document deleted' })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <div className="flex-1 relative">
          <Search className="w-4 h-4 absolute left-3 top-3 text-gray-400" aria-hidden="true" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search documents..."
            className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
          />
        </div>
        <button
          onClick={loadDocs}
          disabled={loading}
          aria-label="Reload semantic memory"
          title="Reload"
          className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-gray-300 disabled:opacity-50"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
        </button>
      </div>

      <div className="space-y-2">
        {filtered.map(doc => (
          <EpisodicEntry key={doc.id} kind="semantic" item={doc} onDelete={deleteDoc} />
        ))}
        {filtered.length === 0 && !loading && docs.length > 0 && (
          <p className="text-center text-gray-400 py-8">No documents match the search</p>
        )}
        {docs.length === 0 && !loading && <SemanticEmpty />}
      </div>
    </div>
  )
}

function ArchivalTab() {
  const [notes, setNotes] = useState([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [expandedNote, setExpandedNote] = useState(null)
  const [noteContent, setNoteContent] = useState('')
  const [showNewNote, setShowNewNote] = useState(false)
  const [newNoteText, setNewNoteText] = useState('')
  const [creating, setCreating] = useState(false)
  const [summary, setSummary] = useState('')
  const [showSummary, setShowSummary] = useState(false)
  const [editingSummary, setEditingSummary] = useState(false)
  const [summaryDraft, setSummaryDraft] = useState('')
  const activeProject = useStore((s) => s.activeProject)
  const addNotification = useStore((s) => s.addNotification)
  const projectId = activeProject?.id || 'default'

  const loadNotes = async () => {
    setLoading(true)
    try {
      const res = await memoryApi.listArchivalNotes(projectId)
      setNotes(res.data.notes || [])
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  const loadSummary = async () => {
    try {
      const res = await memoryApi.getArchivalSummary(projectId)
      setSummary(res.data.content || '')
    } catch (err) {
      // Summary may not exist yet — that's fine
      setSummary('')
    }
  }

  useLoadPerProject(activeProject?.id, () => { loadNotes(); loadSummary() })

  const filtered = notes.filter(n =>
    n.filename.toLowerCase().includes(search.toLowerCase()) ||
    n.path?.toLowerCase().includes(search.toLowerCase())
  )

  const readNote = async (filename) => {
    if (expandedNote === filename) {
      setExpandedNote(null)
      setNoteContent('')
      return
    }
    try {
      const res = await memoryApi.readArchivalNote(filename, projectId)
      setNoteContent(res.data.content || '')
      setExpandedNote(filename)
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const deleteNote = async (filename) => {
    if (!confirm(`Delete archival note "${filename}"?`)) return
    try {
      await memoryApi.deleteArchivalNote(filename, projectId)
      setNotes(notes.filter(n => n.filename !== filename))
      if (expandedNote === filename) {
        setExpandedNote(null)
        setNoteContent('')
      }
      addNotification({ type: 'success', message: 'Note deleted' })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const createNote = async () => {
    if (!newNoteText.trim()) return
    setCreating(true)
    try {
      await memoryApi.createArchivalNote(newNoteText.trim(), projectId)
      addNotification({ type: 'success', message: 'Note created' })
      setNewNoteText('')
      setShowNewNote(false)
      loadNotes()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setCreating(false)
  }

  const saveSummary = async () => {
    try {
      await memoryApi.updateArchivalSummary(summaryDraft, projectId)
      setSummary(summaryDraft)
      setEditingSummary(false)
      addNotification({ type: 'success', message: 'Project summary updated' })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const formatSize = (bytes) => {
    if (!bytes) return ''
    if (bytes < 1024) return `${bytes} B`
    return `${(bytes / 1024).toFixed(1)} KB`
  }

  return (
    <div className="space-y-6">
      {/* Project Summary Section */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 p-4">
        <div className="flex items-center justify-between mb-2">
          <h3 className="text-sm font-semibold text-gray-300">Project Summary</h3>
          <div className="flex gap-1">
            {editingSummary ? (
              <>
                <button
                  onClick={saveSummary}
                  className="px-2 py-1 text-xs bg-green-600 hover:bg-green-700 text-white rounded"
                >
                  Save
                </button>
                <button
                  onClick={() => setEditingSummary(false)}
                  className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 rounded"
                >
                  Cancel
                </button>
              </>
            ) : (
              <button
                onClick={() => {
                  if (!showSummary) {
                    setShowSummary(true)
                  } else {
                    setSummaryDraft(summary)
                    setEditingSummary(true)
                  }
                }}
                className="px-2 py-1 text-xs bg-gray-700 hover:bg-gray-600 text-gray-300 rounded"
              >
                {showSummary ? 'Edit' : 'Show'}
              </button>
            )}
            {showSummary && !editingSummary && (
              <button
                onClick={() => setShowSummary(false)}
                aria-label="Hide project summary"
                title="Hide"
                className="p-1 text-gray-400 hover:text-gray-200"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            )}
          </div>
        </div>
        {showSummary && (
          editingSummary ? (
            <textarea
              value={summaryDraft}
              onChange={(e) => setSummaryDraft(e.target.value)}
              className="w-full h-32 bg-gray-900 border border-gray-600 rounded p-3 text-sm text-gray-300 font-mono resize-y focus:outline-none focus:border-brand-500"
              placeholder="Write a project summary..."
            />
          ) : (
            <div className="text-sm text-gray-400 whitespace-pre-wrap">
              {summary || <span className="italic text-gray-400">No project summary yet</span>}
            </div>
          )
        )}
      </div>

      {/* Notes Section */}
      <div>
        <div className="flex gap-2 mb-4">
          <div className="flex-1 relative">
            <Search className="w-4 h-4 absolute left-3 top-3 text-gray-400" aria-hidden="true" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search archival notes..."
              className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
            />
          </div>
          <button
            onClick={() => setShowNewNote(!showNewNote)}
            className="px-3 py-2 bg-brand-700 hover:bg-brand-600 text-white text-sm rounded-lg flex items-center gap-1 whitespace-nowrap"
          >
            <Plus className="w-4 h-4" />
            New
          </button>
          <button
            onClick={loadNotes}
            disabled={loading}
            aria-label="Reload archival notes"
            title="Reload"
            className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-gray-300 disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>

        {/* New note form */}
        {showNewNote && (
          <div className="bg-gray-800 rounded-lg border border-gray-700 p-3 mb-4">
            <textarea
              value={newNoteText}
              onChange={(e) => setNewNoteText(e.target.value)}
              placeholder="Write your note..."
              className="w-full h-24 bg-gray-900 border border-gray-600 rounded p-3 text-sm text-gray-300 resize-y focus:outline-none focus:border-brand-500 mb-2"
              autoFocus
            />
            <div className="flex justify-end gap-2">
              <button
                onClick={() => { setShowNewNote(false); setNewNoteText('') }}
                className="px-3 py-1.5 text-sm bg-gray-700 hover:bg-gray-600 text-gray-300 rounded"
              >
                Cancel
              </button>
              <button
                onClick={createNote}
                disabled={creating || !newNoteText.trim()}
                className="px-3 py-1.5 text-sm bg-brand-600 hover:bg-brand-700 text-white rounded disabled:opacity-50"
              >
                {creating ? 'Saving...' : 'Save Note'}
              </button>
            </div>
          </div>
        )}

        {/* Notes list */}
        <div className="space-y-2">
          {filtered.map(note => (
            <div key={note.filename} className="bg-gray-800 rounded-lg border border-gray-700">
              <div className="flex items-center justify-between p-3">
                <button
                  onClick={() => readNote(note.filename)}
                  className="flex-1 flex items-center gap-2 text-left min-w-0"
                >
                  {expandedNote === note.filename ? (
                    <ChevronDown className="w-4 h-4 text-gray-400 flex-shrink-0" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-gray-400 flex-shrink-0" />
                  )}
                  <span className="text-sm text-gray-300 truncate" title={note.filename}>{note.filename}</span>
                  <span className="text-xs text-gray-400 flex-shrink-0">{formatSize(note.size)}</span>
                </button>
                <div className="flex items-center gap-2 flex-shrink-0 ml-2">
                  <Time value={note.modified} className="text-xs text-gray-400 whitespace-nowrap" />
                  <button
                    onClick={() => deleteNote(note.filename)}
                    aria-label={`Delete ${note.filename}`}
                    title="Delete note"
                    className="p-1 text-gray-400 hover:text-red-400 hover:bg-gray-700 rounded"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
              {expandedNote === note.filename && (
                <div className="border-t border-gray-700 p-3">
                  <pre className="text-xs text-gray-300 whitespace-pre-wrap break-words font-mono">
                    {noteContent}
                  </pre>
                </div>
              )}
            </div>
          ))}
          {filtered.length === 0 && (
            <p className="text-center text-gray-400 py-8">
              {notes.length === 0 ? 'No archival notes yet' : 'No matching notes'}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

const MEMORY_TABS = [
  { id: 'episodic', label: 'Episodic' },
  { id: 'semantic', label: 'Semantic' },
  { id: 'graph', label: 'Graph' },
  { id: 'archival', label: 'Archival' },
]

// Empty Semantic tab: say what fills it and where to go (#24).
function SemanticEmpty() {
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  // Inside the project workspace the tabs are a ?tab= param; standalone, use /artifacts.
  const openArtifacts = () => {
    if (searchParams.has('tab')) {
      const next = new URLSearchParams(searchParams)
      next.set('tab', 'artifacts'); next.delete('mem')
      setSearchParams(next)
    } else {
      navigate('/artifacts')
    }
  }
  return (
    <InfoBox title="No semantic memory in this project yet">
      <p>
        Semantic memory holds searchable, embedded chunks of this project's artifacts and indexed
        workspace files, plus facts you asked the agent to remember. Recall searches it on every chat turn.
      </p>
      <ul className="list-disc pl-5 mt-1.5 space-y-0.5">
        <li>Save, create or upload a text artifact; it's indexed automatically.</li>
        <li>Put files in the project workspace and ask the agent to <em>"index the workspace"</em>.</li>
        <li>Ask the agent to <em>"remember in semantic memory that…"</em>.</li>
      </ul>
      <button
        type="button"
        onClick={openArtifacts}
        className="mt-2.5 px-3 py-1.5 text-xs font-medium rounded-md bg-brand-700 hover:bg-brand-600 text-white whitespace-nowrap"
      >
        Open Artifacts
      </button>
    </InfoBox>
  )
}

export default function MemoryBrowser({ embedded = false }) {
  // The sub-tab lives in the URL (?mem=graph) so reload and links keep it (#15).
  const [searchParams, setSearchParams] = useSearchParams()
  const memParam = searchParams.get('mem')
  const activeTab = MEMORY_TABS.some((t) => t.id === memParam) ? memParam : 'episodic'
  const setActiveTab = (id) => {
    const next = new URLSearchParams(searchParams)
    if (id === 'episodic') next.delete('mem'); else next.set('mem', id)
    setSearchParams(next, { replace: true })
  }
  const tabs = MEMORY_TABS

  return (
    <div className="flex flex-col h-full bg-gray-950" data-area="memory">
      {!embedded && (
        <div className="px-6 py-4 bg-gray-900 border-b border-gray-800">
          <h1 className="text-xl font-bold text-gray-100">Memory Browser</h1>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-0 border-b border-gray-800 overflow-x-auto" role="tablist" aria-label="Memory tiers">
        {tabs.map(tab => (
          <button
            key={tab.id}
            role="tab"
            aria-selected={activeTab === tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors whitespace-nowrap ${
              activeTab === tab.id
                ? 'border-brand-500 text-brand-400'
                : 'border-transparent text-gray-400 hover:text-gray-300'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Content */}
      <div className={"flex-1 min-h-0 " + (activeTab === 'graph' ? "overflow-hidden" : "overflow-y-auto p-3 sm:p-6") + " scrollbar-thin"}>
        {activeTab === 'episodic' && <EpisodicTab />}
        {activeTab === 'semantic' && <SemanticTab />}
        {activeTab === 'graph' && <GraphView />}
        {activeTab === 'archival' && <ArchivalTab />}
      </div>
    </div>
  )
}
