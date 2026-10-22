import React from 'react'
import { X, ArrowRight, ArrowLeft, Copy as CopyIcon } from 'lucide-react'
import Time from '../Time'

const HIDDEN_META = new Set(['description', 'extracted_at', 'entity_type'])

/** Side panel for a clicked graph entity: type, description, edges, likely duplicates (#23). */
export default function NodeDetailPanel({ node, nodes, edges, duplicates, onSelect, onClose }) {
  const meta = node.metadata || node.attributes || {}
  const byId = new Map(nodes.map((n) => [n.id, n]))
  const out = [], inc = []
  for (const e of edges) {
    const a = e.source || e.node_a_id, b = e.target || e.node_b_id
    if (a === node.id) out.push({ e, other: b, label: e.node_b_label || byId.get(b)?.label })
    else if (b === node.id) inc.push({ e, other: a, label: e.node_a_label || byId.get(a)?.label })
  }
  const extra = Object.entries(meta).filter(([k, v]) => !HIDDEN_META.has(k) && v != null && v !== '' && typeof v !== 'object')
  const type = node.node_type || node.type || meta.entity_type || 'other'

  const EdgeRow = ({ dir, item }) => (
    <li>
      <button
        type="button"
        onClick={() => onSelect(item.other)}
        className="w-full text-left flex items-center gap-1.5 px-2 py-1 rounded hover:bg-gray-800 min-w-0"
        title={`Open ${item.label || item.other}`}
      >
        {dir === 'out'
          ? <ArrowRight className="w-3 h-3 text-gray-400 shrink-0" aria-label="outgoing" />
          : <ArrowLeft className="w-3 h-3 text-gray-400 shrink-0" aria-label="incoming" />}
        <span className="italic text-brand-300 shrink-0">{item.e.relationship || 'related'}</span>
        <span className="text-gray-200 truncate">{item.label || item.other}</span>
      </button>
    </li>
  )

  return (
    <aside
      aria-label={`Details for ${node.label}`}
      className="absolute top-0 right-0 bottom-0 z-20 w-full sm:w-80 bg-gray-900/95 backdrop-blur border-l border-gray-700 shadow-2xl flex flex-col"
    >
      <div className="flex items-start gap-2 px-4 py-3 border-b border-gray-800">
        <div className="flex-1 min-w-0">
          <h3 className="text-sm font-semibold text-gray-100 break-words">{node.label}</h3>
          <span className="inline-block mt-1 text-xs px-1.5 py-0.5 rounded bg-gray-800 text-gray-300">{type}</span>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close details"
          title="Close"
          className="p-1 rounded text-gray-400 hover:text-gray-200 hover:bg-gray-800"
        >
          <X className="w-4 h-4" />
        </button>
      </div>
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-4 text-xs">
        <section>
          <h4 className="font-semibold text-gray-400 uppercase mb-1">Description</h4>
          <p className="text-gray-200 text-sm">{meta.description || <span className="italic text-gray-400">No description</span>}</p>
          {(meta.extracted_at || extra.length > 0) && (
            <dl className="mt-2 grid grid-cols-[auto,1fr] gap-x-2 gap-y-0.5 text-gray-400">
              {meta.extracted_at && (<><dt>Extracted</dt><dd className="text-gray-300"><Time value={meta.extracted_at} /></dd></>)}
              {extra.map(([k, v]) => (
                <React.Fragment key={k}><dt>{k}</dt><dd className="text-gray-300 break-all">{String(v)}</dd></React.Fragment>
              ))}
            </dl>
          )}
        </section>

        {duplicates.length > 0 && (
          <section className="rounded-md border border-orange-800/60 bg-orange-950/30 p-2.5">
            <h4 className="font-semibold text-orange-200 flex items-center gap-1 mb-1">
              <CopyIcon className="w-3 h-3" aria-hidden="true" /> Possible duplicates
            </h4>
            <ul className="space-y-0.5 mb-2">
              {duplicates.map((d) => (
                <li key={d.id}>
                  <button type="button" onClick={() => onSelect(d.id)} className="text-gray-100 underline decoration-dotted hover:text-white text-left">
                    {d.label}
                  </button>
                  <span className="text-gray-400"> {d.exact ? '(same name)' : '(similar name)'}</span>
                </li>
              ))}
            </ul>
            <p className="text-gray-300">
              Merging happens through the agent's <code className="text-orange-200">merge_topics</code> tool, e.g.
              ask in chat: <em>"merge the topic '{duplicates[0].label}' into '{node.label}'"</em>.
              It rewrites the edges onto one node and removes the other.
            </p>
          </section>
        )}

        <section>
          <h4 className="font-semibold text-gray-400 uppercase mb-1">Relationships ({out.length + inc.length})</h4>
          {out.length + inc.length === 0 ? (
            <p className="italic text-gray-400">No edges.</p>
          ) : (
            <ul className="-mx-2">
              {out.map((item) => <EdgeRow key={'o' + item.e.id} dir="out" item={item} />)}
              {inc.map((item) => <EdgeRow key={'i' + item.e.id} dir="in" item={item} />)}
            </ul>
          )}
        </section>
      </div>
    </aside>
  )
}
