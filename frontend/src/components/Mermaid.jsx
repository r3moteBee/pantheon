import React, { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'
import ExportMenu from './ExportMenu'

let _initialized = false
function ensureInit() {
  if (_initialized) return
  mermaid.initialize({
    startOnLoad: false,
    theme: 'dark',
    securityLevel: 'strict',
    fontFamily: 'ui-sans-serif, system-ui, sans-serif',
    // htmlLabels: false renders text as plain <text> instead of
    // <foreignObject> + <div xhtml>. This is what makes the SVG
    // self-contained — foreignObject breaks SVG viewers, taints
    // <canvas> on rasterize, and is unsupported by svg2pdf.js.
    flowchart: { htmlLabels: false, useMaxWidth: true },
    class: { htmlLabels: false },
    state: { htmlLabels: false },
    themeVariables: {
      background: '#0f172a',
      primaryColor: '#1f2937',
      primaryTextColor: '#e5e7eb',
      primaryBorderColor: '#374151',
      lineColor: '#9ca3af',
      secondaryColor: '#111827',
      tertiaryColor: '#1e293b',
    },
  })
  _initialized = true
}

let _idCounter = 0
function nextId() {
  _idCounter += 1
  return `mermaid-${Date.now().toString(36)}-${_idCounter}`
}

export default function Mermaid({ code, basename = 'mermaid-diagram' }) {
  const [error, setError] = useState(null)
  const [hasSvg, setHasSvg] = useState(false)
  const containerRef = useRef(null)
  const svgRef = useRef(null)
  const idRef = useRef(nextId())

  useEffect(() => {
    let cancelled = false
    ensureInit()
    setError(null)
    setHasSvg(false)
    svgRef.current = null
    if (containerRef.current) containerRef.current.innerHTML = ''
    if (!code || !code.trim()) return
    mermaid
      .render(idRef.current, code)
      .then((result) => {
        if (cancelled || !containerRef.current) return
        containerRef.current.innerHTML = result.svg
        svgRef.current = containerRef.current.querySelector('svg')
        setHasSvg(!!svgRef.current)
      })
      .catch((err) => {
        if (!cancelled) setError(err?.message || String(err))
      })
    return () => { cancelled = true }
  }, [code])

  if (error) {
    return (
      <div className="my-4 rounded border border-red-900/50 bg-red-950/30 p-3">
        <div className="text-xs font-semibold text-red-300 mb-1">
          Mermaid render error
        </div>
        <pre className="text-xs text-red-200 whitespace-pre-wrap mb-2">{error}</pre>
        <pre className="text-xs text-gray-400 whitespace-pre-wrap font-mono bg-gray-950/50 rounded p-2 overflow-x-auto">{code}</pre>
      </div>
    )
  }

  return (
    <div className="my-4 group relative rounded border border-gray-800 bg-gray-950/40 p-3">
      <div ref={containerRef} className="flex justify-center overflow-x-auto" />
      {!hasSvg && (
        <div className="text-xs text-gray-500 italic text-center">Rendering diagram…</div>
      )}
      {hasSvg && (
        <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 focus-within:opacity-100 transition">
          <ExportMenu getSvgEl={() => svgRef.current} basename={basename} />
        </div>
      )}
    </div>
  )
}
