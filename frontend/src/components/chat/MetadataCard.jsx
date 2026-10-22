import React, { useState } from 'react'
import { ChevronDown, ChevronRight, Tag } from 'lucide-react'

const clip = (s, n) => (s.length > n ? s.slice(0, n - 1).trimEnd() + '…' : s)

// A YAML-frontmatter block (see frontmatter.js) rendered as a compact,
// collapsed key/value card instead of the giant setext heading markdown
// would make of it. `partial` = the block is still streaming in.
export default function MetadataCard({ fields, partial = false }) {
  const [open, setOpen] = useState(false)
  const count = fields.length
  const preview = fields.slice(0, 3).map(([k, v]) => `${k}: ${clip(v.replace(/\s+/g, ' ') || '—', 32)}`).join(' · ')
  const label = `Metadata, ${count} field${count === 1 ? '' : 's'}${partial ? ', still loading' : ''}`
  return (
    <div className="not-prose my-2 rounded-md border border-gray-700 bg-gray-900/70 text-xs">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        aria-label={label}
        title={label}
        className="w-full flex items-center gap-2 px-2.5 py-1.5 text-left text-gray-300 hover:bg-gray-800/70 rounded-md"
      >
        {open ? <ChevronDown className="w-3.5 h-3.5 flex-shrink-0 text-gray-400" aria-hidden="true" /> : <ChevronRight className="w-3.5 h-3.5 flex-shrink-0 text-gray-400" aria-hidden="true" />}
        <Tag className="w-3.5 h-3.5 flex-shrink-0 text-gray-400" aria-hidden="true" />
        <span className="font-medium whitespace-nowrap">Metadata</span>
        <span className="min-w-0 truncate font-mono text-gray-400">{preview}{partial ? ' …' : ''}</span>
        {count > 3 && <span className="ml-auto whitespace-nowrap text-gray-400">+{count - 3} more</span>}
      </button>
      {open && (
        <dl className="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-3 gap-y-1 px-3 pb-2 pt-1 border-t border-gray-800">
          {fields.map(([k, v], i) => (
            <React.Fragment key={`${k}-${i}`}>
              <dt className="font-mono text-gray-400 whitespace-nowrap">{k}</dt>
              <dd className="text-gray-200 whitespace-pre-wrap break-words">{v || <span className="text-gray-400">—</span>}</dd>
            </React.Fragment>
          ))}
        </dl>
      )}
    </div>
  )
}
