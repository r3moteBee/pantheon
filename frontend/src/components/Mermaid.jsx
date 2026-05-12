import React, { useEffect, useRef, useState } from 'react'
import mermaid from 'mermaid'

let _initialized = false
function ensureInit() {
  if (_initialized) return
  mermaid.initialize({
    startOnLoad: false,
    theme: 'dark',
    securityLevel: 'strict',
    fontFamily: 'ui-sans-serif, system-ui, sans-serif',
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

export default function Mermaid({ code }) {
  const [svg, setSvg] = useState('')
  const [error, setError] = useState(null)
  const idRef = useRef(nextId())

  useEffect(() => {
    let cancelled = false
    ensureInit()
    setError(null)
    setSvg('')
    if (!code || !code.trim()) return
    mermaid
      .render(idRef.current, code)
      .then((result) => {
        if (!cancelled) setSvg(result.svg)
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

  if (!svg) {
    return (
      <div className="my-4 text-xs text-gray-500 italic">Rendering diagram…</div>
    )
  }

  return (
    <div
      className="my-4 flex justify-center overflow-x-auto rounded border border-gray-800 bg-gray-950/40 p-3"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  )
}
