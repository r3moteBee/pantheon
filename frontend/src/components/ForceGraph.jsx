import React, { useRef, useEffect, useState } from 'react'
import * as d3 from 'd3'

/**
 * ForceGraph — d3-force directed graph visualization.
 *
 * d3 owns the SVG DOM (via useRef), React owns the container and props.
 * Supports zoom/pan, node dragging, click-to-select, type coloring,
 * degree-based sizing, edge weight thickness, and bridge node highlighting.
 */

// Entity type → color
const TYPE_COLORS = {
  person: '#60a5fa',       // blue-400
  organization: '#f472b6', // pink-400
  technology: '#34d399',   // emerald-400
  concept: '#a78bfa',      // violet-400
  place: '#fbbf24',        // amber-400
  product: '#fb923c',      // orange-400
  other: '#94a3b8',        // slate-400
}

function getColor(type) {
  return TYPE_COLORS[type?.toLowerCase()] || TYPE_COLORS.other
}

// Radius from degree: min 4, max 24, sqrt scale
function getRadius(degree) {
  return Math.max(4, Math.min(24, 4 + Math.sqrt(degree || 1) * 3))
}

/**
 * Brandes betweenness centrality — O(V*E), fine for <500 nodes.
 * Returns Map<nodeId, centrality>.
 */
function computeBetweenness(nodes, links) {
  const n = nodes.length
  if (n < 3) return new Map()

  const idIndex = new Map(nodes.map((nd, i) => [nd.id, i]))
  // Adjacency list
  const adj = Array.from({ length: n }, () => [])
  for (const l of links) {
    const si = idIndex.get(l.source?.id ?? l.source)
    const ti = idIndex.get(l.target?.id ?? l.target)
    if (si !== undefined && ti !== undefined) {
      adj[si].push(ti)
      adj[ti].push(si)
    }
  }

  const cb = new Float64Array(n) // centrality

  for (let s = 0; s < n; s++) {
    const stack = []
    const pred = Array.from({ length: n }, () => [])
    const sigma = new Float64Array(n)
    sigma[s] = 1
    const dist = new Int32Array(n).fill(-1)
    dist[s] = 0
    const queue = [s]
    let qi = 0

    while (qi < queue.length) {
      const v = queue[qi++]
      stack.push(v)
      for (const w of adj[v]) {
        if (dist[w] < 0) {
          dist[w] = dist[v] + 1
          queue.push(w)
        }
        if (dist[w] === dist[v] + 1) {
          sigma[w] += sigma[v]
          pred[w].push(v)
        }
      }
    }

    const delta = new Float64Array(n)
    while (stack.length) {
      const w = stack.pop()
      for (const v of pred[w]) {
        delta[v] += (sigma[v] / sigma[w]) * (1 + delta[w])
      }
      if (w !== s) cb[w] += delta[w]
    }
  }

  // Normalize
  const maxCb = Math.max(...cb) || 1
  const result = new Map()
  for (let i = 0; i < n; i++) {
    result.set(nodes[i].id, cb[i] / maxCb)
  }
  return result
}

const LABEL_HIDE_PX = 9      // labels smaller than this on screen are hidden (#23)
const LABEL_MAX_PX = 16
const PREWARM_TICKS = 160    // lay out off-screen, then show a settled graph

/**
 * Props: entities [{id,name,entity_type,degree,attributes}], relationships
 * [{id,source_id,target_id,relationship_type,weight}], selectedNode,
 * onSelectNode(node|null), highlightBridges, typeFilter, searchTerm,
 * pathHighlight {nodeIds:Set, edgeIds:Set}, duplicateIds (Set: dashed ring).
 * Size follows the container (ResizeObserver); the graph is fitted to the
 * view once the layout settles, and on the "Fit" button.
 */
export default function ForceGraph({
  entities = [],
  relationships = [],
  selectedNode,
  onSelectNode,
  highlightBridges = false,
  typeFilter = null,
  searchTerm = '',
  pathHighlight = null,
  duplicateIds = null,
}) {
  const wrapRef = useRef(null)
  const svgRef = useRef(null)
  const simRef = useRef(null)
  const zoomRef = useRef(null)
  const sizeRef = useRef({ width: 800, height: 600 })
  const kRef = useRef(1)
  const fitRef = useRef(() => {})
  const labelRef = useRef(() => {})
  const onSelectRef = useRef(onSelectNode)
  onSelectRef.current = onSelectNode
  const [size, setSize] = useState({ width: 800, height: 600 })
  const [betweenness, setBetweenness] = useState(new Map())

  // Track the container size; refit when it changes.
  useEffect(() => {
    const el = wrapRef.current
    if (!el) return
    const ro = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect
      if (width > 0 && height > 0) setSize({ width: Math.round(width), height: Math.round(height) })
    })
    ro.observe(el)
    return () => ro.disconnect()
  }, [])
  useEffect(() => {
    sizeRef.current = size
    fitRef.current(false)
  }, [size])

  // Build nodes/links from props
  const { nodes, links } = React.useMemo(() => {
    const filteredEntities = entities.filter((e) => {
      if (typeFilter && e.entity_type !== typeFilter) return false
      return true
    })
    const entityIds = new Set(filteredEntities.map((e) => e.id))

    const nodes = filteredEntities.map((e) => ({
      id: e.id,
      name: e.name,
      entity_type: e.entity_type,
      degree: e.degree || 0,
      attributes: e.attributes || {},
    }))

    const links = relationships
      .filter((r) => entityIds.has(r.source_id) && entityIds.has(r.target_id))
      .map((r) => ({
        source: r.source_id,
        target: r.target_id,
        type: r.relationship_type,
        weight: r.weight || 0.5,
        id: r.id,
      }))

    return { nodes, links }
  }, [entities, relationships, typeFilter])

  // Hubs keep their label at low zoom: the top ~15% by degree (min degree 3).
  const hubIds = React.useMemo(() => {
    const sorted = [...nodes].sort((a, b) => b.degree - a.degree)
    const n = Math.max(1, Math.ceil(nodes.length * 0.15))
    return new Set(sorted.slice(0, n).filter((d) => d.degree >= 3).map((d) => d.id))
  }, [nodes])

  // Compute betweenness when data changes
  useEffect(() => {
    if (nodes.length > 2 && nodes.length < 500) {
      // Defer to avoid blocking render
      const timer = setTimeout(() => {
        setBetweenness(computeBetweenness(nodes, links))
      }, 100)
      return () => clearTimeout(timer)
    } else {
      setBetweenness(new Map())
    }
  }, [nodes, links])

  // Search highlighting
  const searchMatches = React.useMemo(() => {
    if (!searchTerm) return new Set()
    const lower = searchTerm.toLowerCase()
    return new Set(
      nodes.filter((n) => n.name.toLowerCase().includes(lower)).map((n) => n.id)
    )
  }, [nodes, searchTerm])

  // Main d3 effect
  useEffect(() => {
    if (!svgRef.current || nodes.length === 0) return

    const svg = d3.select(svgRef.current)
    svg.selectAll('*').remove()

    const g = svg.append('g')

    // Labels keep a readable on-screen size: they grow with zoom up to
    // LABEL_MAX_PX and hide below LABEL_HIDE_PX (hubs, the selection and
    // hovered/search/path nodes excepted, see labelRef).
    const applyLabels = () => {
      const k = kRef.current
      g.selectAll('text.node-label')
        .attr('font-size', (d) => Math.min((d.degree > 5 ? 12 : 11) * k, LABEL_MAX_PX) / k)
        .attr('display', (d) => {
          const px = (d.degree > 5 ? 12 : 11) * k
          return px >= LABEL_HIDE_PX || d._forceLabel ? null : 'none'
        })
    }
    labelRef.current = applyLabels

    // Zoom
    const zoom = d3.zoom()
      .scaleExtent([0.1, 8])
      .on('zoom', (event) => {
        g.attr('transform', event.transform)
        if (event.transform.k !== kRef.current) { kRef.current = event.transform.k; applyLabels() }
      })
    svg.call(zoom).on('dblclick.zoom', null)
    zoomRef.current = zoom

    // Arrow markers for directed edges
    svg.append('defs').append('marker')
      .attr('id', 'arrow')
      .attr('viewBox', '0 -5 10 10')
      .attr('refX', 20)
      .attr('refY', 0)
      .attr('markerWidth', 6)
      .attr('markerHeight', 6)
      .attr('orient', 'auto')
      .append('path')
      .attr('d', 'M0,-5L10,0L0,5')
      .attr('fill', '#4b5563')

    const { width, height } = sizeRef.current
    // Simulation: settles quickly and stops (alphaMin) instead of drifting.
    const simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id((d) => d.id).distance(80).strength(0.3))
      .force('charge', d3.forceManyBody().strength(-180).distanceMax(320))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('x', d3.forceX(width / 2).strength(0.04))
      .force('y', d3.forceY(height / 2).strength(0.04))
      .force('collision', d3.forceCollide().radius((d) => getRadius(d.degree) + 6))
      .alphaMin(0.02)
      .alphaDecay(0.05)
      .stop()
    // Lay out before the first paint so the graph appears settled.
    simulation.tick(PREWARM_TICKS)
    simRef.current = simulation

    // Links
    const link = g.append('g')
      .selectAll('line')
      .data(links)
      .join('line')
      .attr('class', 'link')
      .attr('stroke', '#475569')
      .attr('stroke-opacity', 0.6)
      .attr('stroke-width', (d) => Math.max(1, d.weight * 3))
      .attr('marker-end', 'url(#arrow)')

    // Edge labels (hidden by default, shown on hover)
    const edgeLabel = g.append('g')
      .selectAll('text')
      .data(links)
      .join('text')
      .attr('text-anchor', 'middle')
      .attr('fill', '#cbd5e1')
      .attr('font-size', '9px')
      .attr('opacity', 0)
      .attr('pointer-events', 'none')
      .text((d) => d.type)

    // Node groups
    const node = g.append('g')
      .selectAll('g')
      .data(nodes)
      .join('g')
      .style('cursor', 'pointer')
      .call(drag(simulation))

    // Bridge glow (behind circle)
    node.append('circle')
      .attr('r', (d) => getRadius(d.degree) + 4)
      .attr('fill', 'none')
      .attr('stroke', '#fbbf24')
      .attr('stroke-width', 2)
      .attr('opacity', 0)
      .attr('class', 'bridge-glow')

    // Possible-duplicate ring (dashed)
    node.append('circle')
      .attr('r', (d) => getRadius(d.degree) + 3)
      .attr('fill', 'none')
      .attr('stroke', '#fb923c')
      .attr('stroke-width', 1.5)
      .attr('stroke-dasharray', '3 2')
      .attr('opacity', 0)
      .attr('class', 'dup-ring')

    // Main circle
    node.append('circle')
      .attr('r', (d) => getRadius(d.degree))
      .attr('fill', (d) => getColor(d.entity_type))
      .attr('stroke', '#1f2937')
      .attr('stroke-width', 1.5)
      .attr('class', 'main-circle')

    // Labels
    node.append('text')
      .attr('class', 'node-label')
      .attr('dx', (d) => getRadius(d.degree) + 4)
      .attr('dy', '0.35em')
      .attr('fill', '#e5e7eb')
      .attr('paint-order', 'stroke')
      .attr('stroke', '#030712')
      .attr('stroke-width', 3)
      .attr('stroke-linejoin', 'round')
      .text((d) => d.name.length > 24 ? d.name.slice(0, 22) + '…' : d.name)
      .append('title').text((d) => d.name)

    // Click handler
    node.on('click', (event, d) => {
      event.stopPropagation()
      onSelectRef.current?.(d)
    })

    // Hover: show connected edges + labels
    node.on('mouseenter', (event, d) => {
      link
        .attr('stroke', (l) =>
          l.source.id === d.id || l.target.id === d.id ? '#60a5fa' : '#475569'
        )
        .attr('stroke-opacity', (l) =>
          l.source.id === d.id || l.target.id === d.id ? 1 : 0.2
        )
      edgeLabel.attr('opacity', (l) =>
        l.source.id === d.id || l.target.id === d.id ? 1 : 0
      )
      d3.select(event.currentTarget).select('text.node-label').attr('display', null)
    })

    node.on('mouseleave', () => {
      link.attr('stroke', '#475569').attr('stroke-opacity', 0.6)
      edgeLabel.attr('opacity', 0)
      applyLabels()
    })

    // Click background to deselect
    svg.on('click', () => onSelectRef.current?.(null))

    const draw = () => {
      link
        .attr('x1', (d) => d.source.x)
        .attr('y1', (d) => d.source.y)
        .attr('x2', (d) => d.target.x)
        .attr('y2', (d) => d.target.y)

      edgeLabel
        .attr('x', (d) => (d.source.x + d.target.x) / 2)
        .attr('y', (d) => (d.source.y + d.target.y) / 2)

      node.attr('transform', (d) => `translate(${d.x},${d.y})`)
    }
    simulation.on('tick', draw)

    // Fit the nodes' bounding box (plus label room) into the view.
    const fit = (animate = true) => {
      const { width: w, height: h } = sizeRef.current
      if (!nodes.length || !w || !h) return
      let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity
      for (const d of nodes) {
        const r = getRadius(d.degree)
        x0 = Math.min(x0, d.x - r); y0 = Math.min(y0, d.y - r)
        x1 = Math.max(x1, d.x + r + 90); y1 = Math.max(y1, d.y + r)  // 90: label room
      }
      const pad = 24
      const bw = Math.max(x1 - x0, 1), bh = Math.max(y1 - y0, 1)
      const scale = Math.max(0.1, Math.min((w - pad * 2) / bw, (h - pad * 2) / bh, 2))
      const t = d3.zoomIdentity
        .translate(w / 2 - (x0 + bw / 2) * scale, h / 2 - (y0 + bh / 2) * scale)
        .scale(scale)
      ;(animate ? svg.transition().duration(400) : svg).call(zoom.transform, t)
    }
    fitRef.current = fit
    draw()
    fit(false)
    applyLabels()

    return () => {
      simulation.stop()
    }
  }, [nodes, links])

  // Update visual states reactively (selection, bridges, search, path, duplicates)
  useEffect(() => {
    if (!svgRef.current) return
    const svg = d3.select(svgRef.current)
    const selId = selectedNode?.id
    const neighbours = new Set()
    if (selId) {
      for (const l of links) {
        const s = l.source?.id ?? l.source, t = l.target?.id ?? l.target
        if (s === selId) neighbours.add(t)
        if (t === selId) neighbours.add(s)
      }
    }

    // Labels forced visible at any zoom
    svg.selectAll('text.node-label').each((d) => {
      d._forceLabel = hubIds.has(d.id) || d.id === selId || neighbours.has(d.id)
        || searchMatches.has(d.id) || !!pathHighlight?.nodeIds?.has(d.id)
    })
    labelRef.current()

    // Selected node highlight
    svg.selectAll('.main-circle')
      .attr('stroke', (d) =>
        selId === d.id ? '#fff' : searchMatches.has(d.id) ? '#fbbf24' : '#1f2937'
      )
      .attr('stroke-width', (d) =>
        selId === d.id ? 3 : searchMatches.has(d.id) ? 2.5 : 1.5
      )

    // Bridge glow
    svg.selectAll('.bridge-glow')
      .attr('opacity', (d) =>
        highlightBridges && (betweenness.get(d.id) || 0) > 0.3 ? 0.7 : 0
      )

    svg.selectAll('.dup-ring')
      .attr('opacity', (d) => (duplicateIds?.has(d.id) ? 0.9 : 0))

    // Path highlight overlay — fade non-path nodes/links, gold-stroke path elements
    if (pathHighlight && pathHighlight.nodeIds) {
      const nodeIds = pathHighlight.nodeIds
      const edgeIds = pathHighlight.edgeIds || new Set()
      svg.selectAll('.main-circle')
        .attr('opacity', (d) => nodeIds.has(d.id) ? 1.0 : 0.25)
      svg.selectAll('.node-label')
        .attr('opacity', (d) => nodeIds.has(d.id) ? 1.0 : 0.25)
      svg.selectAll('line.link')
        .attr('opacity', (l) => {
          const sid = l.source?.id ?? l.source
          const tid = l.target?.id ?? l.target
          const key1 = `${sid}::${tid}`, key2 = `${tid}::${sid}`
          return (edgeIds.has(key1) || edgeIds.has(key2)) ? 1.0 : 0.1
        })
        .attr('stroke', (l) => {
          const sid = l.source?.id ?? l.source
          const tid = l.target?.id ?? l.target
          const key1 = `${sid}::${tid}`, key2 = `${tid}::${sid}`
          return (edgeIds.has(key1) || edgeIds.has(key2)) ? '#fbbf24' : '#475569'
        })
    } else {
      svg.selectAll('.main-circle').attr('opacity', 1.0)
      svg.selectAll('.node-label').attr('opacity', 1.0)
      svg.selectAll('line.link').attr('opacity', 1).attr('stroke', '#475569')
    }
  }, [nodes, links, hubIds, selectedNode, highlightBridges, betweenness, searchMatches, pathHighlight, duplicateIds])

  const zoomBy = (factor) => {
    if (!svgRef.current || !zoomRef.current) return
    d3.select(svgRef.current).transition().duration(200).call(zoomRef.current.scaleBy, factor)
  }

  return (
    <div ref={wrapRef} className="relative w-full h-full min-h-[320px]">
      {nodes.length === 0 ? (
        <div className="absolute inset-0 flex items-center justify-center text-gray-400 text-sm">
          No graph data to visualize
        </div>
      ) : (
        <>
          <svg
            ref={svgRef}
            width={size.width}
            height={size.height}
            className="absolute inset-0 bg-gray-950"
            role="img"
            aria-label={`Knowledge graph: ${nodes.length} entities, ${links.length} relationships`}
          />
          <div className="absolute bottom-3 left-3 flex items-center gap-1 bg-gray-900/90 border border-gray-700 rounded-md p-0.5 shadow">
            <button type="button" onClick={() => fitRef.current(true)} title="Fit the whole graph in view"
                    className="px-2 py-1 text-xs text-gray-200 hover:bg-gray-800 rounded whitespace-nowrap">
              Fit
            </button>
            <button type="button" onClick={() => zoomBy(1.4)} aria-label="Zoom in" title="Zoom in"
                    className="px-2 py-1 text-xs text-gray-200 hover:bg-gray-800 rounded">+</button>
            <button type="button" onClick={() => zoomBy(1 / 1.4)} aria-label="Zoom out" title="Zoom out"
                    className="px-2 py-1 text-xs text-gray-200 hover:bg-gray-800 rounded">−</button>
          </div>
        </>
      )}
    </div>
  )
}

function drag(simulation) {
  return d3.drag()
    .on('start', (event, d) => {
      if (!event.active) simulation.alphaTarget(0.3).restart()
      d.fx = d.x
      d.fy = d.y
    })
    .on('drag', (event, d) => {
      d.fx = event.x
      d.fy = event.y
    })
    .on('end', (event, d) => {
      // alphaTarget 0: the simulation cools down and stops (alphaMin) again.
      if (!event.active) simulation.alphaTarget(0)
      d.fx = null
      d.fy = null
    })
}
