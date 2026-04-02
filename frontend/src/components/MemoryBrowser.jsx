import React, { useState, useEffect } from 'react'
import { Search, Trash2, RefreshCw, ChevronDown, ChevronRight, Plus, X } from 'lucide-react'
import { useStore } from '../store'
import { memoryApi } from '../api/client'

function EpisodicTab() {
  const [notes, setNotes] = useState([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const activeProject = useStore((s) => s.activeProject)
  const addNotification = useStore((s) => s.addNotification)

  const loadNotes = async () => {
    setLoading(true)
    try {
      const res = await memoryApi.listNotes(activeProject?.id || 'default')
      setNotes(res.data.notes || [])
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  useEffect(() => {
    if (activeProject?.id) loadNotes()
  }, [activeProject])

  const filtered = notes.filter(n =>
    n.content.toLowerCase().includes(search.toLowerCase()) ||
    n.id.toLowerCase().includes(search.toLowerCase())
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

  return (
    <div className="space-y-4">
      <div className="flex gap-2">
        <div className="flex-1 relative">
          <Search className="w-4 h-4 absolute left-3 top-3 text-gray-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search notes..."
            className="w-full bg-gray-800 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
          />
        </div>
        <button
          onClick={loadNotes}
          disabled={loading}
          className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-gray-300 disabled:opacity-50"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      <div className="space-y-2">
        {filtered.map(note => (
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
        {filtered.length === 0 && (
          <p className="text-center text-gray-600 py-8">No notes found</p>
        )}
      </div>
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
  return (
    <div className="text-center py-8">
      <p className="text-gray-500">Archival memory management coming soon</p>
    </div>
  )
}

export default function MemoryBrowser() {
  const [activeTab, setActiveTab] = useState('episodic')

  const tabs = [
    { id: 'episodic', label: 'Episodic' },
    { id: 'semantic', label: 'Semantic' },
    { id: 'graph', label: 'Graph' },
    { id: 'archival', label: 'Archival' },
  ]

  return (
    <div className="flex flex-col h-full bg-gray-950">
      {/* Header */}
      <div className="px-6 py-4 bg-gray-900 border-b border-gray-800">
        <h1 className="text-xl font-bold text-gray-100">Memory Browser</h1>
      </div>

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
      <div className="flex-1 overflow-y-auto p-6 scrollbar-thin">
        {activeTab === 'episodic' && <EpisodicTab />}
        {activeTab === 'semantic' && <SemanticTab />}
        {activeTab === 'graph' && <GraphTab />}
        {activeTab === 'archival' && <ArchivalTab />}
      </div>
    </div>
  )
}
