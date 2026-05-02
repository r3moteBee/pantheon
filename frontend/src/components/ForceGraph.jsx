import React, { useRef, useEffect, useCallback, useState } from 'react'
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

export default function ForceGraph({
  entities = [],
  relationships = [],
  selectedNode,
  onSelectNode,
  highlightBridges = false,
  typeFilter = null,
  searchTerm = '',
  width = 800,
  height = 600,
  pathHighlight = null,  // {nodeIds: Set, edgeIds: Set} — fade non-path elements
}) {
  const svgRef = useRef(null)
  const simRef = useRef(null)
  const [betweenness, setBetweenness] = useState(new Map())

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

    // Zoom
    const zoom = d3.zoom()
      .scaleExtent([0.1, 8])
      .on('zoom', (event) => g.attr('transform', event.transform))
    svg.call(zoom)

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

    // Simulation
    const simulation = d3.forceSimulation(nodes)
      .force('link', d3.forceLink(links).id((d) => d.id).distance(80).strength(0.3))
      .force('charge', d3.forceManyBody().strength(-150).distanceMax(300))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide().radius((d) => getRadius(d.degree) + 2))

    simRef.current = simulation

    // Links
    const link = g.append('g')
      .selectAll('line')
      .data(links)
      .join('line')
      .attr('stroke', '#374151')
      .attr('stroke-opacity', 0.6)
      .attr('stroke-width', (d) => Math.max(1, d.weight * 3))
      .attr('marker-end', 'url(#arrow)')

    // Edge labels (hidden by default, shown on hover)
    const edgeLabel = g.append('g')
      .selectAll('text')
      .data(links)
      .join('text')
      .attr('text-anchor', 'middle')
      .attr('fill', '#6b7280')
      .attr('font-size', '8px')
      .attr('opacity', 0)
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

    // Main circle
    node.append('circle')
      .attr('r', (d) => getRadius(d.degree))
      .attr('fill', (d) => getColor(d.entity_type))
      .attr('stroke', '#1f2937')
      .attr('stroke-width', 1.5)
      .attr('class', 'main-circle')

    // Labels
    node.append('text')
      .attr('dx', (d) => getRadius(d.degree) + 4)
      .attr('dy', '0.35em')
      .attr('fill', '#d1d5db')
      .attr('font-size', (d) => (d.degree > 5 ? '11px' : '9px'))
      .text((d) => d.name.length > 20 ? d.name.slice(0, 18) + '…' : d.name)

    // Click handler
    node.on('click', (event, d) => {
      event.stopPropagation()
      onSelectNode?.(d)
    })

    // Hover: show connected edges + labels
    node.on('mouseenter', (event, d) => {
      link
        .attr('stroke', (l) =>
          l.source.id === d.id || l.target.id === d.id ? '#60a5fa' : '#374151'
        )
        .attr('stroke-opacity', (l) =>
          l.source.id === d.id || l.target.id === d.id ? 1 : 0.2
        )
      edgeLabel.attr('opacity', (l) =>
        l.source.id === d.id || l.target.id === d.id ? 1 : 0
      )
    })

    node.on('mouseleave', () => {
      link.attr('stroke', '#374151').attr('stroke-opacity', 0.6)
      edgeLabel.attr('opacity', 0)
    })

    // Click background to deselect
    svg.on('click', () => onSelectNode?.(null))

    // Tick
    simulation.on('tick', () => {
      link
        .attr('x1', (d) => d.source.x)
        .attr('y1', (d) => d.source.y)
        .attr('x2', (d) => d.target.x)
        .attr('y2', (d) => d.target.y)

      edgeLabel
        .attr('x', (d) => (d.source.x + d.target.x) / 2)
        .attr('y', (d) => (d.source.y + d.target.y) / 2)

      node.attr('transform', (d) => `translate(${d.x},${d.y})`)
    })

    // Auto-fit after initial settle
    simulation.on('end', () => {
      const bounds = g.node().getBBox()
      if (bounds.width > 0 && bounds.height > 0) {
        const padding = 40
        const scale = Math.min(
          (width - padding * 2) / bounds.width,
          (height - padding * 2) / bounds.height,
          1.5
        )
        const tx = width / 2 - (bounds.x + bounds.width / 2) * scale
        const ty = height / 2 - (bounds.y + bounds.height / 2) * scale
        svg.transition().duration(500).call(
          zoom.transform,
          d3.zoomIdentity.translate(tx, ty).scale(scale)
        )
      }
    })

    return () => {
      simulation.stop()
    }
  }, [nodes, links, width, height])

  // Update visual states reactively (selection, bridges, search)
  useEffect(() => {
    if (!svgRef.current) return
    const svg = d3.select(svgRef.current)

    // Selected node highlight
    svg.selectAll('.main-circle')
      .attr('stroke', (d) =>
        selectedNode?.id === d.id ? '#fff' : searchMatches.has(d.id) ? '#fbbf24' : '#1f2937'
      )
      .attr('stroke-width', (d) =>
        selectedNode?.id === d.id ? 3 : searchMatches.has(d.id) ? 2.5 : 1.5
      )

    // Bridge glow
    svg.selectAll('.bridge-glow')
      .attr('opacity', (d) =>
        highlightBridges && (betweenness.get(d.id) || 0) > 0.3 ? 0.7 : 0
      )

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
      svg.selectAll('line.link').attr('opacity', 0.6).attr('stroke', '#475569')
    }
  }, [selectedNode, highlightBridges, betweenness, searchMatches, pathHighlight])

  if (nodes.length === 0) {
    return (
      <div
        className="flex items-center justify-center text-gray-500 text-sm"
        style={{ width, height }}
      >
        No graph data to visualize
      </div>
    )
  }

  return (
    <svg
      ref={svgRef}
      width={width}
      height={height}
      style={{ width: '100%', height: '100%', minHeight: 400 }}
      className="bg-gray-950 rounded-lg border border-gray-800"
    />
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
      if (!event.active) simulation.alphaTarget(0)
      d.fx = null
      d.fy = null
    })
}
