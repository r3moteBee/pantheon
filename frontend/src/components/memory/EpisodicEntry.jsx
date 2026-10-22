import React, { useLayoutEffect, useRef, useState } from 'react'
import { Trash2, ChevronDown, ChevronRight } from 'lucide-react'
import Markdown from '../Markdown'
import Time from '../Time'

// Prompts the job handlers store as role=user so a task's session replays in
// chat (autonomous_task, iteration_loop). They are harness instructions, not
// something the user said. Mirrors backend memory/episodic.py is_job_prompt.
const JOB_PROMPT_RE = /^You are running (an autonomous task|turn \d+ of \d+)/

export function isJobPrompt(msg) {
  const kind = String(msg?.metadata?.kind || '')
  if (kind.endsWith('_prompt')) return true
  return msg?.role === 'user' && JOB_PROMPT_RE.test(msg?.content || '')
}

const BADGES = {
  user: { label: 'user', cls: 'bg-brand-600/20 text-brand-300', title: 'Something you said' },
  assistant: { label: 'assistant', cls: 'bg-emerald-600/20 text-emerald-300', title: 'The agent\'s reply' },
  task: { label: 'task', cls: 'bg-violet-600/25 text-violet-200', title: 'Instructions a background job sent to the agent (not typed by you)' },
  note: { label: 'note', cls: 'bg-gray-700 text-gray-200', title: 'Episodic note' },
}

const COMPACT_MD = 'prose prose-invert prose-sm max-w-none text-gray-300 break-words ' +
  'prose-p:my-1 prose-headings:my-1.5 prose-headings:text-sm prose-ul:my-1 prose-ol:my-1 prose-li:my-0 ' +
  'prose-pre:my-1.5 prose-pre:text-xs prose-hr:my-2 prose-code:text-xs'

/** One episodic message or note: compact rendered Markdown, click to expand. */
export default function EpisodicEntry({ kind = 'message', item, onDelete }) {
  const [expanded, setExpanded] = useState(false)
  const role = kind === 'note' ? 'note' : (isJobPrompt(item) ? 'task' : (item.role === 'user' ? 'user' : item.role === 'assistant' ? 'assistant' : null))
  const badge = BADGES[role] || { label: item.role || 'other', cls: 'bg-gray-700 text-gray-200', title: '' }
  const content = item.content || ''
  // "Long" = the rendered Markdown overflows the collapsed height (measured).
  const bodyRef = useRef(null)
  const [long, setLong] = useState(false)
  useLayoutEffect(() => {
    const el = bodyRef.current
    if (!el || expanded) return
    const check = () => setLong(el.scrollHeight > el.clientHeight + 4)
    check()
    const ro = new ResizeObserver(check)
    ro.observe(el)
    return () => ro.disconnect()
  }, [content, expanded])
  const ts = item.timestamp || item.created_at || item.updated_at

  const toggle = (e) => {
    // Let links and text selection inside the rendered Markdown work.
    if (e.target.closest('a, button')) return
    if (window.getSelection?.().toString()) return
    if (long) setExpanded((v) => !v)
  }

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700">
      <div className="flex items-center gap-2 px-3 pt-2.5 pb-1 min-w-0">
        {long ? (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
            aria-label={expanded ? 'Collapse entry' : 'Expand entry'}
            title={expanded ? 'Collapse' : 'Expand'}
            className="-ml-1 p-0.5 text-gray-400 hover:text-gray-200 rounded"
          >
            {expanded ? <ChevronDown className="w-3.5 h-3.5" /> : <ChevronRight className="w-3.5 h-3.5" />}
          </button>
        ) : null}
        <span title={badge.title} className={`text-xs font-medium px-1.5 py-0.5 rounded whitespace-nowrap ${badge.cls}`}>
          {badge.label}
        </span>
        {kind === 'note' ? (
          <span className="text-xs text-gray-400 font-mono truncate min-w-0">{item.id}</span>
        ) : item.session_id ? (
          <span className="text-xs text-gray-400 font-mono truncate min-w-0" title={`Session ${item.session_id}`}>
            {item.session_id.slice(0, 8)}
          </span>
        ) : null}
        {ts && <Time value={ts} className="text-xs text-gray-400 whitespace-nowrap" />}
        <span className="flex-1" />
        <button
          type="button"
          onClick={() => onDelete(item.id)}
          aria-label={kind === 'note' ? 'Delete note' : 'Delete message'}
          title={kind === 'note' ? 'Delete note' : 'Delete message'}
          className="p-1 text-gray-400 hover:text-red-400 hover:bg-gray-700 rounded shrink-0"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
      <div
        ref={bodyRef}
        onClick={toggle}
        className={`relative px-3 pb-2.5 ${long ? 'cursor-pointer' : ''} ${expanded ? '' : 'max-h-28 overflow-hidden'}`}
      >
        <Markdown className={COMPACT_MD}>{content}</Markdown>
        {long && !expanded && (
          <div className="pointer-events-none absolute inset-x-0 bottom-0 h-10 bg-gradient-to-t from-gray-800 to-transparent" />
        )}
      </div>
      {long && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="w-full text-left px-3 pb-2 -mt-1 text-xs text-gray-400 hover:text-gray-200"
        >
          {expanded ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  )
}
