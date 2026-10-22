import React, { useEffect, useState } from 'react'
import { Check, Copy } from 'lucide-react'

// Plain text of react-markdown's rendered children (strings, arrays, elements).
export function nodeText(children) {
  if (children == null || children === false) return ''
  if (typeof children === 'string' || typeof children === 'number') return String(children)
  if (Array.isArray(children)) return children.map(nodeText).join('')
  if (children.props) return nodeText(children.props.children)
  return ''
}

let highlighterPromise = null
const loadHighlighter = () => {
  if (!highlighterPromise) highlighterPromise = import('./codeHighlight').catch(() => null)
  return highlighterPromise
}
// Cheap check before downloading the grammars.
const HIGHLIGHTABLE = /^(js|jsx|mjs|cjs|javascript|ts|typescript|tsx|py|python|python3|json|jsonc|css|scss|html|htm|xml|svg|vue)$/i

export function CopyButton({ text, label = 'Copy code', className = '' }) {
  const [copied, setCopied] = useState(false)
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch { /* clipboard blocked */ }
  }
  return (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? 'Copied' : label}
      title={copied ? 'Copied' : label}
      className={`inline-flex items-center gap-1 rounded px-1.5 py-1 text-xs whitespace-nowrap text-gray-300 hover:text-white hover:bg-gray-700 ${className}`}
    >
      {copied ? <Check className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4" />}
      <span>{copied ? 'Copied' : 'Copy'}</span>
    </button>
  )
}

// A fenced code block: language label, copy button, lazy syntax highlighting
// for the common languages (others render plain).
export default function CodeBlock({ code, lang }) {
  const [segments, setSegments] = useState(null)
  useEffect(() => {
    setSegments(null)
    if (!lang || !HIGHLIGHTABLE.test(lang)) return
    let alive = true
    loadHighlighter().then((m) => {
      if (!alive || !m) return
      try { setSegments(m.highlight(code, lang)) } catch { /* parse error: stay plain */ }
    })
    return () => { alive = false }
  }, [code, lang])

  return (
    <div className="not-prose my-3 rounded-lg border border-gray-700/70 bg-gray-900 overflow-hidden">
      <div className="flex items-center justify-between gap-2 border-b border-gray-800 pl-3 pr-1 py-0.5">
        <span className="text-xs text-gray-400 font-mono truncate">{lang || 'text'}</span>
        <CopyButton text={code} />
      </div>
      <pre className="m-0 p-3 overflow-x-auto bg-transparent text-xs leading-relaxed scrollbar-thin">
        <code className="font-mono text-gray-100">
          {segments
            ? segments.map((s, i) => (s.cls ? <span key={i} className={s.cls}>{s.text}</span> : s.text))
            : code}
        </code>
      </pre>
    </div>
  )
}
