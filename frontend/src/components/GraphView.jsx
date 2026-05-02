import React, { useState, useEffect, useMemo, useRef } from 'react'
import { Network, Search, X, AlertCircle, RefreshCw, ChevronRight } from 'lucide-react'
import ForceGraph from './ForceGraph'
import { memoryApi } from '../api/client'
import { useStore } from '../store'

/**
 * GraphView — wraps ForceGraph with a path-finder bar above the canvas
 * and a textual breadcrumb below. Lives inside the chat Memory tab.
 */
export default function GraphView() {
  const activeProject = useStore((s) => s.activeProject)
  const projectId = activeProject?.id || 'default'

  const [nodes, setNodes] = useState([])
  const [edges, setEdges] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [typeFilter, setTypeFilter] = useState('')
  const [view, setView] = useState('graph')  // 'graph' | 'list'
  const [listSearch, setListSearch] = useState('')

  // Path-finder state
  const [fromInput, setFromInput] = useState('')
  const [toInput, setToInput] = useState('')
  const [pathing, setPathing] = useState(false)
  const [pathResult, setPathResult] = useState(null)  // {found, path, edges, hops}
  const [pathError, setPathError] = useState(null)

  const refresh = async () => {
    setLoading(true); setError(null)
    try {
      const res = await memoryApi.graphFull(projectId, typeFilter || undefined, 500)
      setNodes(res.data?.nodes || [])
      setEdges(res.data?.edges || [])
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setLoading(false) }
  }

  useEffect(() => { refresh() }, [projectId, typeFilter])

  // Map to ForceGraph's expected props: entities/relationships
  const fgData = useMemo(() => {
    const idSet = new Set(nodes.map((n) => n.id))
    const rels = edges
      .map((e) => ({
        id: e.id,
        source_id: e.source || e.node_a_id,
        target_id: e.target || e.node_b_id,
        relationship_type: e.relationship,
        weight: e.weight || 0.5,
      }))
      .filter((r) => idSet.has(r.source_id) && idSet.has(r.target_id))
    // Compute degree for sizing
    const degree = new Map()
    for (const r of rels) {
      degree.set(r.source_id, (degree.get(r.source_id) || 0) + 1)
      degree.set(r.target_id, (degree.get(r.target_id) || 0) + 1)
    }
    const ents = nodes.map((n) => ({
      id: n.id,
      name: n.label,
      entity_type: n.node_type || n.type,
      degree: degree.get(n.id) || 0,
      attributes: n.metadata || n.attributes || {},
    }))
    return { entities: ents, relationships: rels }
  }, [nodes, edges])

  // Highlighted nodes/edges for path overlay
  const pathHighlight = useMemo(() => {
    if (!pathResult?.found) return null
    const nodeIds = new Set(pathResult.path.map((n) => n.id))
    const edgeIds = new Set(
      (pathResult.edges || []).flatMap((e) => [
        `${e.source}::${e.target}`, `${e.target}::${e.source}`,
      ])
    )
    return { nodeIds, edgeIds }
  }, [pathResult])

  // Suggestions for autocomplete inputs
  const suggest = (text) => {
    if (!text) return []
    const lower = text.toLowerCase()
    return nodes
      .filter((n) => (n.label || '').toLowerCase().includes(lower))
      .slice(0, 8)
  }
  const fromSuggestions = suggest(fromInput)
  const toSuggestions = suggest(toInput)

  const findPath = async () => {
    if (!fromInput.trim() || !toInput.trim()) return
    setPathing(true); setPathError(null); setPathResult(null)
    try {
      const res = await memoryApi.graphPath(projectId, fromInput.trim(), toInput.trim())
      setPathResult(res.data)
      if (!res.data?.found) {
        setPathError(`No defined relationship between "${fromInput}" and "${toInput}" in this project's graph.`)
      }
    } catch (e) {
      setPathError(e?.response?.data?.detail || e.message)
    } finally { setPathing(false) }
  }

  const clearPath = () => { setPathResult(null); setPathError(null) }

  return (
    <div className="h-full flex flex-col">
      {/* Path-finder bar */}
      <div className="border-b border-gray-800 bg-gray-950 px-3 py-2 flex items-center gap-2 flex-wrap">
        <Network className="w-4 h-4 text-brand-400" />
        <span className="text-xs text-gray-300 font-medium">Find path:</span>
        <NodeAutocomplete
          value={fromInput} onChange={setFromInput}
          suggestions={fromSuggestions}
          placeholder="From"
        />
        <ChevronRight className="w-3 h-3 text-gray-500" />
        <NodeAutocomplete
          value={toInput} onChange={setToInput}
          suggestions={toSuggestions}
          placeholder="To"
        />
        <button
          onClick={findPath}
          disabled={pathing || !fromInput.trim() || !toInput.trim()}
          className="px-3 py-1 text-xs rounded bg-brand-600 hover:bg-brand-500 text-white disabled:opacity-50"
        >
          {pathing ? 'Searching…' : 'Find Path'}
        </button>
        {pathResult && (
          <button onClick={clearPath} className="text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1">
            <X className="w-3 h-3" /> Clear
          </button>
        )}
        <span className="ml-auto" />
        <div className="flex items-center gap-0.5 bg-gray-900 border border-gray-800 rounded p-0.5">
          <button
            onClick={() => setView('graph')}
            className={`text-xs px-2 py-0.5 rounded ${view === 'graph' ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-gray-200'}`}
          >
            Graph
          </button>
          <button
            onClick={() => setView('list')}
            className={`text-xs px-2 py-0.5 rounded ${view === 'list' ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-gray-200'}`}
          >
            List
          </button>
        </div>
        <select
          value={typeFilter}
          onChange={(e) => setTypeFilter(e.target.value)}
          className="text-xs bg-gray-900 border border-gray-800 rounded px-2 py-1"
        >
          <option value="">All types</option>
          <option value="person">person</option>
          <option value="organization">organization</option>
          <option value="concept">concept</option>
          <option value="project">project</option>
          <option value="event">event</option>
          <option value="fact">fact</option>
          <option value="technology">technology</option>
          <option value="product">product</option>
        </select>
        <button onClick={refresh} className="text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1">
          <RefreshCw className="w-3 h-3" /> Refresh
        </button>
      </div>

      {pathError && (
        <div className="px-3 py-2 border-b border-amber-800 bg-amber-950 text-xs text-amber-300 flex items-center gap-2">
          <AlertCircle className="w-3 h-3" /> {pathError}
        </div>
      )}

      {/* Canvas (graph) or list */}
      <div className="flex-1 min-h-0 relative">
        {loading && (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-gray-500">
            Loading graph…
          </div>
        )}
        {error && (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-red-400">
            {error}
          </div>
        )}
        {!loading && !error && fgData.entities.length === 0 && (
          <div className="absolute inset-0 flex items-center justify-center text-xs text-gray-500 italic">
            This project's graph is empty. Save artifacts or chat — entities are extracted automatically.
          </div>
        )}
        {!loading && !error && view === 'graph' && fgData.entities.length > 0 && (
          <ForceGraph
            entities={fgData.entities}
            relationships={fgData.relationships}
            pathHighlight={pathHighlight}
          />
        )}
        {!loading && !error && view === 'list' && (
          <ListView
            nodes={nodes}
            edges={edges}
            search={listSearch}
            setSearch={setListSearch}
          />
        )}
      </div>

      {/* Path breadcrumb */}
      {pathResult?.found && (
        <div className="border-t border-gray-800 bg-gray-950 px-3 py-2 max-h-40 overflow-y-auto">
          <div className="text-xs text-gray-400 mb-1">
            Path: <span className="text-brand-300 font-medium">{pathResult.hops}</span> hop(s)
          </div>
          <div className="flex flex-wrap items-center gap-1 text-xs">
            {pathResult.path.map((n, i) => (
              <React.Fragment key={n.id}>
                <span className="px-2 py-0.5 rounded bg-brand-900 text-brand-100">{n.label}</span>
                {i < pathResult.path.length - 1 && (
                  <span className="text-gray-500">
                    — <span className="italic">{(pathResult.edges[i]?.relationship) || '(connected)'}</span> →
                  </span>
                )}
              </React.Fragment>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}


function NodeAutocomplete({ value, onChange, suggestions, placeholder }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    const onDoc = (e) => { if (ref.current && !ref.current.contains(e.target)) setOpen(false) }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [])
  return (
    <div className="relative" ref={ref}>
      <input
        type="text"
        value={value}
        onChange={(e) => { onChange(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        placeholder={placeholder}
        className="px-2 py-1 text-xs rounded bg-gray-900 border border-gray-800 w-40"
      />
      {open && suggestions.length > 0 && (
        <div className="absolute z-50 mt-1 w-56 max-h-56 overflow-y-auto bg-gray-900 border border-gray-700 rounded shadow-lg">
          {suggestions.map((s) => (
            <button
              key={s.id}
              onClick={() => { onChange(s.label); setOpen(false) }}
              className="w-full text-left text-xs px-2 py-1 hover:bg-gray-800 flex items-center gap-2"
            >
              <span className="text-gray-200">{s.label}</span>
              <span className="ml-auto text-[10px] text-gray-500">{s.node_type}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  )
}


function ListView({ nodes, edges, search, setSearch }) {
  const lower = (search || '').toLowerCase()
  const matchedNodes = nodes.filter((n) =>
    !lower
    || (n.label || '').toLowerCase().includes(lower)
    || (n.node_type || '').toLowerCase().includes(lower)
  )
  const matchedEdges = edges.filter((e) =>
    !lower
    || (e.relationship || '').toLowerCase().includes(lower)
    || (e.node_a_label || '').toLowerCase().includes(lower)
    || (e.node_b_label || '').toLowerCase().includes(lower)
  )

  // Group nodes by type
  const byType = {}
  for (const n of matchedNodes) {
    const t = n.node_type || 'other'
    ;(byType[t] = byType[t] || []).push(n)
  }
  const types = Object.keys(byType).sort()

  return (
    <div className="h-full overflow-y-auto p-4">
      <div className="mb-3 flex items-center gap-2">
        <Search className="w-3 h-3 text-gray-500" />
        <input
          type="text"
          placeholder="Filter nodes / edges by label or type"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 max-w-md text-xs bg-gray-900 border border-gray-800 rounded px-2 py-1"
        />
        <span className="text-[10px] text-gray-500">
          {matchedNodes.length} node{matchedNodes.length === 1 ? '' : 's'} ·
          {' '}{matchedEdges.length} edge{matchedEdges.length === 1 ? '' : 's'}
        </span>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Nodes column */}
        <div>
          <h3 className="text-xs font-semibold text-gray-400 uppercase mb-2">Nodes</h3>
          {types.length === 0 && <div className="text-xs text-gray-500 italic">No nodes match.</div>}
          {types.map((t) => (
            <div key={t} className="mb-3">
              <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">{t} ({byType[t].length})</div>
              <div className="space-y-1">
                {byType[t].map((n) => (
                  <div
                    key={n.id}
                    className="px-2 py-1 rounded border border-gray-800 bg-gray-900 text-xs flex items-center justify-between"
                  >
                    <span className="text-gray-200 truncate">{n.label}</span>
                    {n.metadata?.description && (
                      <span className="text-[10px] text-gray-500 truncate ml-2 max-w-xs">
                        {n.metadata.description}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            </div>
          ))}
        </div>

        {/* Edges column */}
        <div>
          <h3 className="text-xs font-semibold text-gray-400 uppercase mb-2">Edges</h3>
          {matchedEdges.length === 0 && (
            <div className="text-xs text-gray-500 italic">
              {edges.length === 0
                ? 'No edges in this project graph yet. Edges form when extraction finds explicit relationships in your content.'
                : 'No edges match the filter.'}
            </div>
          )}
          <div className="space-y-1">
            {matchedEdges.map((e) => (
              <div
                key={e.id}
                className="px-2 py-1 rounded border border-gray-800 bg-gray-900 text-xs flex items-center gap-2"
              >
                <span className="text-gray-200 truncate flex-1">{e.node_a_label}</span>
                <span className="text-brand-300 italic shrink-0">— {e.relationship} →</span>
                <span className="text-gray-200 truncate flex-1 text-right">{e.node_b_label}</span>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
