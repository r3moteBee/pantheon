import React, { useState, useEffect } from 'react'
import { Search, Trash2, RefreshCw, ChevronDown, ChevronRight, Plus, X } from 'lucide-react'
import { useStore } from '../store'
import { memoryApi } from '../api/client'
import GraphView from './GraphView'

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

  useEffect(() => {
    if (activeProject?.id) loadData()
  }, [activeProject])

  const filteredNotes = notes.filter(n =>
    n.content.toLowerCase().includes(search.toLowerCase()) ||
    n.id.toLowerCase().includes(search.toLowerCase())
  )

  const filteredMessages = messages.filter(m =>
    m.content.toLowerCase().includes(search.toLowerCase()) ||
    m.role.toLowerCase().includes(search.toLowerCase())
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
          <Search className="w-4 h-4 absolute left-3 top-3 text-gray-500" />
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
          className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-gray-300 disabled:opacity-50"
        >
          <RefreshCw className="w-4 h-4" />
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
            <div key={msg.id} className="bg-gray-800 rounded-lg p-3 border border-gray-700">
              <div className="flex items-start justify-between mb-1">
                <div className="flex items-center gap-2">
                  <span className={`text-xs font-medium px-1.5 py-0.5 rounded ${
                    msg.role === 'user' ? 'bg-brand-600/20 text-brand-400' : 'bg-emerald-600/20 text-emerald-400'
                  }`}>
                    {msg.role}
                  </span>
                  <span className="text-xs text-gray-500">{msg.session_id?.slice(0, 8)}</span>
                  <span className="text-xs text-gray-600">{msg.timestamp}</span>
                </div>
                <button
                  onClick={() => deleteMessage(msg.id)}
                  className="ml-2 p-1 text-gray-500 hover:text-red-400 hover:bg-gray-700 rounded"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
              <p className="text-sm text-gray-300 line-clamp-3">{msg.content}</p>
            </div>
          ))}
          {filteredMessages.length === 0 && (
            <p className="text-center text-gray-600 py-8">No messages stored yet</p>
          )}
        </div>
      )}

      {view === 'notes' && (
        <div className="space-y-2">
          {filteredNotes.map(note => (
            <div key={note.id} className="bg-gray-800 rounded-lg p-3 border border-gray-700">
              <div className="flex items-start justify-between mb-2">
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-gray-500 font-mono">{note.id}</p>
                </div>
                <button
                  onClick={() => deleteNote(note.id)}
                  className="ml-2 p-1 text-gray-500 hover:text-red-400 hover:bg-gray-700 rounded"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
              <p className="text-sm text-gray-300 line-clamp-3">{note.content}</p>
            </div>
          ))}
          {filteredNotes.length === 0 && (
            <p className="text-center text-gray-600 py-8">No notes found</p>
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

  useEffect(() => {
    if (activeProject?.id) loadDocs()
  }, [activeProject])

  const filtered = docs.filter(d =>
    d.content.toLowerCase().includes(search.toLowerCase()) ||
    d.id.toLowerCase().includes(search.toLowerCase())
  )

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
          <Search className="w-4 h-4 absolute left-3 top-3 text-gray-500" />
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
          className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-gray-300 disabled:opacity-50"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      <div className="space-y-2">
        {filtered.map(doc => (
          <div key={doc.id} className="bg-gray-800 rounded-lg p-3 border border-gray-700">
            <div className="flex items-start justify-between mb-2">
              <div className="flex-1 min-w-0">
                <p className="text-xs text-gray-500 font-mono">{doc.id}</p>
                {doc.score !== undefined && (
                  <span className="inline-block mt-1 px-2 py-1 bg-brand-900 text-brand-200 text-xs rounded">
                    Score: {(doc.score * 100).toFixed(0)}%
                  </span>
                )}
              </div>
              <button
                onClick={() => deleteDoc(doc.id)}
                className="ml-2 p-1 text-gray-500 hover:text-red-400 hover:bg-gray-700 rounded"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
            <p className="text-sm text-gray-300 line-clamp-3">{doc.content}</p>
          </div>
        ))}
        {filtered.length === 0 && (
          <p className="text-center text-gray-600 py-8">No documents found</p>
        )}
      </div>
    </div>
  )
}

function GraphTab() {
  const [nodes, setNodes] = useState([])
  const [edges, setEdges] = useState([])
  const [loading, setLoading] = useState(false)
  const activeProject = useStore((s) => s.activeProject)
  const addNotification = useStore((s) => s.addNotification)

  const loadGraph = async () => {
    setLoading(true)
    try {
      const nodesRes = await memoryApi.listGraphNodes(activeProject?.id || 'default')
      const edgesRes = await memoryApi.listGraphEdges(activeProject?.id || 'default')
      setNodes(nodesRes.data.nodes || [])
      setEdges(edgesRes.data.edges || [])
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  useEffect(() => {
    if (activeProject?.id) loadGraph()
  }, [activeProject])

  const deleteNode = async (nodeId) => {
    try {
      await memoryApi.deleteGraphNode(nodeId, activeProject?.id || 'default')
      setNodes(nodes.filter(n => n.id !== nodeId))
      addNotification({ type: 'success', message: 'Node deleted' })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const deleteEdge = async (edgeId) => {
    try {
      await memoryApi.deleteGraphEdge(edgeId, activeProject?.id || 'default')
      setEdges(edges.filter(e => e.id !== edgeId))
      addNotification({ type: 'success', message: 'Edge deleted' })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <button
          onClick={loadGraph}
          disabled={loading}
          className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-gray-300 disabled:opacity-50 mb-4"
        >
          <RefreshCw className="w-4 h-4 inline mr-2" />
          Refresh Graph
        </button>

        <div>
          <h3 className="text-sm font-semibold text-gray-300 mb-2">Nodes ({nodes.length})</h3>
          <div className="space-y-2">
            {nodes.map(node => (
              <div key={node.id} className="bg-gray-800 rounded-lg p-3 border border-gray-700">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <p className="text-sm font-medium text-gray-200">{node.label}</p>
                    <span className="inline-block mt-1 px-2 py-1 bg-blue-900 text-blue-200 text-xs rounded">
                      {node.node_type}
                    </span>
                  </div>
                  <button
                    onClick={() => deleteNode(node.id)}
                    className="p-1 text-gray-500 hover:text-red-400 hover:bg-gray-700 rounded"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
            {nodes.length === 0 && (
              <p className="text-center text-gray-600 py-4">No nodes</p>
            )}
          </div>
        </div>
      </div>

      <div>
        <h3 className="text-sm font-semibold text-gray-300 mb-2">Edges ({edges.length})</h3>
        <div className="space-y-2">
          {edges.map(edge => (
            <div key={edge.id} className="bg-gray-800 rounded-lg p-3 border border-gray-700">
              <div className="flex items-center justify-between">
                <p className="text-sm text-gray-300">
                  <span className="font-medium">{edge.node_a_label}</span>
                  <span className="mx-2 text-gray-500">→</span>
                  <span className="text-purple-300 font-mono text-xs">[{edge.relationship}]</span>
                  <span className="mx-2 text-gray-500">→</span>
                  <span className="font-medium">{edge.node_b_label}</span>
                </p>
                <button
                  onClick={() => deleteEdge(edge.id)}
                  className="p-1 text-gray-500 hover:text-red-400 hover:bg-gray-700 rounded"
                >
                  <Trash2 className="w-3.5 h-3.5" />
                </button>
              </div>
            </div>
          ))}
          {edges.length === 0 && (
            <p className="text-center text-gray-600 py-4">No edges</p>
          )}
        </div>
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

  useEffect(() => {
    if (activeProject?.id) {
      loadNotes()
      loadSummary()
    }
  }, [activeProject])

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

  const formatDate = (iso) => {
    try {
      return new Date(iso).toLocaleString()
    } catch {
      return iso
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
                className="p-1 text-gray-500 hover:text-gray-300"
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
              {summary || <span className="italic text-gray-600">No project summary yet</span>}
            </div>
          )
        )}
      </div>

      {/* Notes Section */}
      <div>
        <div className="flex gap-2 mb-4">
          <div className="flex-1 relative">
            <Search className="w-4 h-4 absolute left-3 top-3 text-gray-500" />
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
            className="px-3 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-lg flex items-center gap-1"
          >
            <Plus className="w-4 h-4" />
            New
          </button>
          <button
            onClick={loadNotes}
            disabled={loading}
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
                    <ChevronDown className="w-4 h-4 text-gray-500 flex-shrink-0" />
                  ) : (
                    <ChevronRight className="w-4 h-4 text-gray-500 flex-shrink-0" />
                  )}
                  <span className="text-sm text-gray-300 truncate">{note.filename}</span>
                  <span className="text-xs text-gray-600 flex-shrink-0">{formatSize(note.size)}</span>
                </button>
                <div className="flex items-center gap-2 flex-shrink-0 ml-2">
                  <span className="text-xs text-gray-600">{formatDate(note.modified)}</span>
                  <button
                    onClick={() => deleteNote(note.filename)}
                    className="p-1 text-gray-500 hover:text-red-400 hover:bg-gray-700 rounded"
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
            <p className="text-center text-gray-600 py-8">
              {notes.length === 0 ? 'No archival notes yet' : 'No matching notes'}
            </p>
          )}
        </div>
      </div>
    </div>
  )
}

export default function MemoryBrowser({ embedded = false }) {
  const [activeTab, setActiveTab] = useState('episodic')

  const tabs = [
    { id: 'episodic', label: 'Episodic' },
    { id: 'semantic', label: 'Semantic' },
    { id: 'graph', label: 'Graph' },
    { id: 'archival', label: 'Archival' },
  ]

  return (
    <div className="flex flex-col h-full bg-gray-950">
      {!embedded && (
        <div className="px-6 py-4 bg-gray-900 border-b border-gray-800">
          <h1 className="text-xl font-bold text-gray-100">Memory Browser</h1>
        </div>
      )}

      {/* Tabs */}
      <div className="flex gap-0 border-b border-gray-800 overflow-x-auto">
        {tabs.map(tab => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={`px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
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
      <div className={"flex-1 " + (activeTab === 'graph' ? "overflow-hidden" : "overflow-y-auto p-6") + " scrollbar-thin"}>
        {activeTab === 'episodic' && <EpisodicTab />}
        {activeTab === 'semantic' && <SemanticTab />}
        {activeTab === 'graph' && <GraphView />}
        {activeTab === 'archival' && <ArchivalTab />}
      </div>
    </div>
  )
}
