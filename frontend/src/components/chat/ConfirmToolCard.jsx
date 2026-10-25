import React, { useEffect, useRef, useState } from 'react'
import { ShieldAlert, Check, XCircle } from 'lucide-react'

// An MCP tool the server marks as changing or deleting data is about to run:
// the turn waits for Approve / Deny (tool annotations; human in the loop).
// Props: confirm {confirmId, connection, tool, arguments, title, description,
// annotations, timeoutS}, onAnswer(approve: boolean).
export default function ConfirmToolCard({ confirm, onAnswer }) {
  const ref = useRef(null)
  const [left, setLeft] = useState(confirm.timeoutS || 0)
  useEffect(() => { ref.current?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }) }, [confirm.confirmId])
  useEffect(() => {
    if (!confirm.timeoutS) return undefined
    setLeft(confirm.timeoutS)
    const t = setInterval(() => setLeft((s) => (s > 0 ? s - 1 : 0)), 1000)
    return () => clearInterval(t)
  }, [confirm.timeoutS, confirm.confirmId])
  const a = confirm.annotations || {}
  const flags = [
    a.destructiveHint === true && 'may delete or overwrite data',
    a.readOnlyHint === false && 'writes',
    a.idempotentHint === false && 'not safe to repeat',
    a.openWorldHint === true && 'reaches outside systems',
  ].filter(Boolean)
  const args = confirm.arguments && Object.keys(confirm.arguments).length ? JSON.stringify(confirm.arguments, null, 2) : null
  return (
    <div ref={ref} className="flex justify-start mb-4" data-testid="confirm-card">
      <div className="w-full border border-amber-700/60 bg-amber-950/30 rounded-2xl px-4 py-3">
        <div className="flex flex-wrap items-center gap-2 mb-1">
          <ShieldAlert className="w-4 h-4 text-amber-300" aria-hidden="true" />
          <span className="text-sm font-medium text-amber-200">
            Allow <span className="font-mono">{confirm.tool}</span> on MCP server <span className="font-mono">{confirm.connection}</span>?
          </span>
          {confirm.timeoutS ? (
            <span className="ml-auto text-xs text-gray-500">{left > 0 ? `${Math.floor(left / 60)}:${String(left % 60).padStart(2, '0')} left` : 'expired'}</span>
          ) : null}
        </div>
        <p className="text-xs text-gray-400 mb-2">
          The server marks this tool as one that changes data{flags.length ? ` (${flags.join(', ')})` : ''}. Nothing runs until you approve.
        </p>
        {confirm.description && <p className="text-sm text-gray-200 mb-2 whitespace-pre-wrap">{confirm.title ? <><b>{confirm.title}</b> — </> : null}{confirm.description}</p>}
        {args && (
          <pre className="text-xs text-gray-300 bg-gray-900/70 border border-gray-800 rounded-lg p-2 mb-2 overflow-x-auto max-h-48">{args}</pre>
        )}
        <div className="flex flex-wrap gap-2">
          <button type="button" onClick={() => onAnswer(true)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-amber-700 hover:bg-amber-600 text-white transition-colors">
            <Check className="w-3 h-3" aria-hidden="true" /> Approve
          </button>
          <button type="button" onClick={() => onAnswer(false)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-gray-700 hover:bg-gray-600 text-gray-200 transition-colors">
            <XCircle className="w-3 h-3" aria-hidden="true" /> Deny
          </button>
        </div>
      </div>
    </div>
  )
}
