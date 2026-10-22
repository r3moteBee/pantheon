import React, { useLayoutEffect, useRef, useState } from 'react'
import { Trash2, ChevronDown, ChevronRight, Copy, Check } from 'lucide-react'
import Markdown from '../Markdown'
import Time from '../Time'
import { conversationTitle, sessionKind } from '../chat/ChatHistoryDrawer'

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
  // Semantic entries, by what wrote them (backend api/memory.py semantic_entry_summary).
  artifact: { label: 'artifact', cls: 'bg-sky-600/25 text-sky-200', title: 'A chunk of a project artifact, indexed automatically' },
  workspace: { label: 'workspace', cls: 'bg-teal-600/25 text-teal-200', title: 'A chunk of a workspace file the agent indexed' },
  image: { label: 'image', cls: 'bg-teal-600/25 text-teal-200', title: 'Description of an indexed image' },
  fact: { label: 'fact', cls: 'bg-amber-600/25 text-amber-200', title: 'A fact the agent extracted from a conversation or artifact' },
  preference: { label: 'preference', cls: 'bg-amber-600/25 text-amber-200', title: 'A preference the agent extracted about you' },
  topic: { label: 'topic', cls: 'bg-gray-700 text-gray-200', title: 'Topic summary node' },
  remembered: { label: 'remembered', cls: 'bg-brand-600/20 text-brand-300', title: 'Stored on request ("remember that…") or via the API' },
}

// Background sessions, by the id prefix the job handlers use (same set as the
// History drawer, which owns the canonical styles).
const SESSION_BADGES = {
  iteration: 'bg-violet-950 text-violet-200 border-violet-800',
  autonomous: 'bg-sky-950 text-sky-200 border-sky-800',
  coding: 'bg-emerald-950 text-emerald-200 border-emerald-800',
  research: 'bg-amber-950 text-amber-200 border-amber-800',
  telegram: 'bg-gray-800 text-gray-200 border-gray-700',
}

const COMPACT_MD = 'prose prose-invert prose-sm max-w-none text-gray-300 break-words ' +
  'prose-p:my-1 prose-headings:my-1.5 prose-headings:text-sm prose-ul:my-1 prose-ol:my-1 prose-li:my-0 ' +
  'prose-pre:my-1.5 prose-pre:text-xs prose-hr:my-2 prose-code:text-xs'

/** The semantic entry's normalized fields. Older backends return only the raw
 * metadata; derive the same shape client-side so the card still has a kind,
 * a time and a source. */
export function semanticSummary(item) {
  if (item.kind) return item
  const md = item.metadata || {}
  const src = String(md.source || ''), typ = String(md.type || '')
  let kind, path = null, title = null
  if (src === 'artifact' || md.artifact_id) { kind = 'artifact'; path = md.path; title = md.title }
  else if (md.fm_artifact_id || String(md.source_path || '').startsWith('artifact://')) {
    kind = 'artifact'; path = md.fm_artifact_path || md.source_path; title = md.fm_title || md.heading
  }
  else if (md.source_path || md.source_file || md.fm_artifact_path) {
    kind = typ === 'image_description' ? 'image' : 'workspace'
    path = md.source_path || md.fm_artifact_path || md.source_file; title = md.fm_title || md.heading
  }
  else if (src === 'auto_extraction' || typ === 'extracted_fact' || typ === 'user_preference') kind = typ === 'user_preference' ? 'preference' : 'fact'
  else if (md.kind === 'topic_node') kind = 'topic'
  else kind = src || 'remembered'
  return {
    ...item, kind,
    created_at: md.created_at || md.indexed_at || md.extracted_at || md.embedded_at || null,
    source_path: path || null, source_title: title || null, session_id: md.session_id || null,
  }
}

/** The session label for an episodic message: the conversation title (the
 * backend sends the stored title, else the first user message); job sessions
 * get the History drawer's kind-aware title ("Turn 1/5 execute · Name"). */
export function sessionLabel(msg) {
  const kind = sessionKind(msg.session_id)
  if (kind) {
    return conversationTitle({ session_id: msg.session_id, first_user_message: msg.session_first_user_message || msg.session_title || '' })
  }
  return msg.session_title || null
}

function CopyId({ id, what }) {
  const [done, setDone] = useState(false)
  const copy = async () => {
    try { await navigator.clipboard.writeText(id); setDone(true); setTimeout(() => setDone(false), 1200) } catch {}
  }
  return (
    <button
      type="button"
      onClick={copy}
      aria-label={`Copy ${what} id`}
      title={`${id}\nClick to copy`}
      className="p-0.5 text-gray-400 hover:text-gray-200 rounded shrink-0"
    >
      {done ? <Check className="w-3.5 h-3.5 text-emerald-300" /> : <Copy className="w-3.5 h-3.5" />}
    </button>
  )
}

/** One memory entry (episodic message/note or semantic chunk): badge, source,
 * formatted time, compact rendered Markdown, click to expand. */
export default function EpisodicEntry({ kind = 'message', item: rawItem, onDelete }) {
  const item = kind === 'semantic' ? semanticSummary(rawItem) : rawItem
  const [expanded, setExpanded] = useState(false)
  const role = kind === 'note' ? 'note'
    : kind === 'semantic' ? item.kind
    : (isJobPrompt(item) ? 'task' : (item.role === 'user' ? 'user' : item.role === 'assistant' ? 'assistant' : null))
  const badge = BADGES[role] || { label: role || item.role || 'other', cls: 'bg-gray-700 text-gray-200', title: '' }
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
  const sKind = kind === 'message' ? sessionKind(item.session_id) : null
  const sLabel = kind === 'message' ? sessionLabel(item) : null
  const what = kind === 'note' ? 'note' : kind === 'semantic' ? 'entry' : 'message'

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
        {sKind && (
          <span className={`text-xs px-1.5 py-0.5 rounded border whitespace-nowrap ${SESSION_BADGES[sKind]}`} title={`${sKind} job session`}>
            {sKind}
          </span>
        )}
        {kind === 'message' && item.session_id ? (
          <span className="text-xs text-gray-400 truncate min-w-0" title={`Session ${item.session_id}`}>
            {sLabel || <span className="font-mono">{item.session_id.slice(0, 8)}</span>}
          </span>
        ) : null}
        {kind === 'semantic' && (item.source_title || item.source_path) ? (
          <span className="text-xs text-gray-400 truncate min-w-0" title={item.source_path || item.source_title}>
            {item.source_title || item.source_path}
          </span>
        ) : null}
        {kind === 'semantic' && !item.source_title && !item.source_path && item.session_id ? (
          <span className="text-xs text-gray-400 truncate min-w-0" title={`Extracted from ${item.session_id}`}>
            {item.session_id.startsWith('artifact:') ? 'from an artifact' : 'from a conversation'}
          </span>
        ) : null}
        {kind === 'semantic' && item.seq ? (
          // Several entries from one source (e.g. snapshots of a task ledger) look
          // alike until expanded (round 3 #3): number them oldest to newest.
          <span className="text-xs px-1.5 py-0.5 rounded bg-gray-700 text-gray-200 whitespace-nowrap"
            title={`Entry ${item.seq} of ${item.seqTotal} from this source, oldest first`}>
            {item.seq}/{item.seqTotal}
          </span>
        ) : null}
        {ts && <Time value={ts} relative={!item.seq} seconds={!!item.seq} className="text-xs text-gray-400 whitespace-nowrap" />}
        {typeof item.score === 'number' && (
          <span className="text-xs px-1.5 py-0.5 bg-brand-900 text-brand-200 rounded whitespace-nowrap">
            Score: {(item.score * 100).toFixed(0)}%
          </span>
        )}
        <span className="flex-1" />
        {item.id && <CopyId id={item.id} what={what} />}
        <button
          type="button"
          onClick={() => onDelete(item.id)}
          aria-label={`Delete ${what}`}
          title={`Delete ${what}`}
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
