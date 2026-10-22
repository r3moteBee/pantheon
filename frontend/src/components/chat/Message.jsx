import React from 'react'
import { Brain, Zap, Check, Copy, Bookmark, ThumbsUp, ThumbsDown } from 'lucide-react'
import Markdown from '../Markdown'
import { llmApi } from '../../api/client'
import { useStore } from '../../store'
import { formatClock, formatTitle, toDate } from '../../utils/time'
import JobStatusChips from './JobStatusChips'
import ToolCallBlock from './ToolCallBlock'
import { AttachmentPill } from './Attachments'
import useChatMarkdownComponents from './useChatMarkdownComponents'

function MessageActions({ content, onSaveMessage }) {
  const [copied, setCopied] = React.useState(false)
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content)
      setCopied(true)
      setTimeout(() => setCopied(false), 1500)
    } catch {}
  }
  return (
    <div className="mt-1 flex items-center gap-0.5 opacity-70 group-hover:opacity-100 focus-within:opacity-100 transition-opacity">
      <button
        type="button"
        onClick={handleCopy}
        className="p-1.5 text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors rounded"
        title={copied ? 'Copied' : 'Copy message'}
        aria-label={copied ? 'Copied' : 'Copy message'}
      >
        {copied ? <Check className="w-4 h-4 text-green-400" /> : <Copy className="w-4 h-4" />}
      </button>
      <button
        type="button"
        onClick={onSaveMessage}
        className="p-1.5 text-gray-400 hover:text-gray-100 hover:bg-gray-800 transition-colors rounded"
        title="Save message as artifact"
        aria-label="Save message as artifact"
      >
        <Bookmark className="w-4 h-4" />
      </button>
    </div>
  )
}

// Which task class / model answered (set by the per-turn chat router),
// with 👍/👎 that feed routing tuning (Settings → Routing tuning).
function RouteBadge({ route, rateable = false }) {
  const [rating, setRating] = React.useState(route?.rating || 0)
  if (!route || !route.task_class) return null
  const model = route.served_model || route.model
  const fellBack = route.served_model && route.model && route.served_model !== route.model
  const rate = async (value) => {
    const next = rating === value ? 0 : value
    setRating(next)
    try {
      await llmApi.routerFeedback(route.decision_id, next)
      route.rating = next
    } catch {
      setRating(rating)
    }
  }
  return (
    <span className='flex items-center gap-1 min-w-0'>
      <span
        className='text-xs px-1.5 py-0.5 rounded bg-gray-800 text-gray-300 border border-gray-700 truncate max-w-[14rem] sm:max-w-xs'
        title={`${route.rule}: ${route.reason}${fellBack ? ` — primary ${route.model} failed, answered by fallback` : ''}`}
      >
        {route.task_class}{model ? ` · ${model}` : ''}{fellBack ? ' ↩' : ''}
      </span>
      {rateable && route.decision_id && (
        <span className={`flex items-center ${rating ? '' : 'opacity-70 group-hover:opacity-100 focus-within:opacity-100'} transition-opacity`}>
          <button type='button' onClick={() => rate(1)} title='Good answer for this model' aria-label='Good answer'
            aria-pressed={rating > 0}
            className={`p-1 rounded hover:bg-gray-800 ${rating > 0 ? 'text-emerald-400' : 'text-gray-400 hover:text-gray-100'}`}>
            <ThumbsUp className='w-4 h-4' />
          </button>
          <button type='button' onClick={() => rate(-1)} title='Wrong model / bad answer' aria-label='Bad answer'
            aria-pressed={rating < 0}
            className={`p-1 rounded hover:bg-gray-800 ${rating < 0 ? 'text-red-400' : 'text-gray-400 hover:text-gray-100'}`}>
            <ThumbsDown className='w-4 h-4' />
          </button>
        </span>
      )}
    </span>
  )
}

// Bubble time: short clock, full local date/time with zone on hover.
function ClockTime({ value }) {
  const d = toDate(value)
  if (!d) return null
  return (
    <time dateTime={d.toISOString()} title={formatTitle(d)} className="text-xs text-gray-400 whitespace-nowrap">
      {formatClock(d)}
    </time>
  )
}

const toolText = (toolCalls) => (toolCalls || [])
  .map((tc) => (typeof tc.result === 'string' ? tc.result : tc.result ? JSON.stringify(tc.result) : ''))
  .join('\n')

export default function Message({ msg, onSaveMessage }) {
  const isUser = msg.role === 'user'
  const mdComponents = useChatMarkdownComponents()
  const projectId = useStore((s) => s.activeProject?.id || 'default')
  const jobText = React.useMemo(
    () => (isUser ? '' : `${msg.content || ''}\n${toolText(msg.toolCalls)}`),
    [isUser, msg.content, msg.toolCalls],
  )
  return (
    <div className={`group flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}>
      <div className={isUser ? 'max-w-[85%] min-w-0' : 'w-full min-w-0'}>
        {!isUser && (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mb-1">
            <div className="w-6 h-6 rounded-full bg-brand-600 flex items-center justify-center" aria-hidden="true">
              <Brain className="w-3.5 h-3.5 text-white" />
            </div>
            <span className="text-xs text-gray-300">Agent</span>
            <RouteBadge route={msg.route} rateable />
            {msg.timestamp && <ClockTime value={msg.timestamp} />}
          </div>
        )}

        {/* Tool calls */}
        {msg.toolCalls && msg.toolCalls.length > 0 && (
          <div className="mb-2">
            {msg.toolCalls.map((tc, i) => (
              <ToolCallBlock key={i} toolCall={tc} />
            ))}
          </div>
        )}

        {/* Attachments */}
        {msg.attachments && msg.attachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mb-1.5">
            {msg.attachments.map((att, i) => (
              <AttachmentPill key={i} file={att} />
            ))}
          </div>
        )}

        {/* Message content */}
        <div
          className={`
            px-4 py-3 rounded-2xl text-sm leading-relaxed break-words
            ${isUser
              ? 'bg-brand-700 text-white rounded-br-sm'
              : 'bg-gray-800 text-gray-100 rounded-bl-sm'
            }
          `}
        >
          {isUser ? (
            <p className="whitespace-pre-wrap">{msg.content}</p>
          ) : (
            <Markdown className="prose prose-invert prose-sm max-w-none" components={mdComponents}>
              {msg.content}
            </Markdown>
          )}
        </div>

        {!isUser && <JobStatusChips text={jobText} projectId={projectId} />}

        {!isUser && msg.content && onSaveMessage && (
          <MessageActions content={msg.content} onSaveMessage={() => onSaveMessage(msg.content)} />
        )}

        {isUser && msg.timestamp && (
          <div className="flex justify-end mt-1">
            <ClockTime value={msg.timestamp} />
          </div>
        )}
      </div>
    </div>
  )
}

// The in-flight assistant turn: route badge, active skill, typing dots,
// tool calls so far and the streamed text.
export function StreamingMessage({ liveRoute, activeSkillBadge, toolCalls, content }) {
  const mdComponents = useChatMarkdownComponents()
  return (
    <div className="flex justify-start mb-4">
      <div className="w-full min-w-0">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 mb-1">
          <div className="w-6 h-6 rounded-full bg-brand-600 flex items-center justify-center" aria-hidden="true">
            <Brain className="w-3.5 h-3.5 text-white" />
          </div>
          <span className="text-xs text-gray-300">Agent</span>
          <RouteBadge route={liveRoute} />
          {activeSkillBadge && (
            <span className="flex items-center gap-1 text-xs px-1.5 py-0.5 rounded bg-brand-900 text-brand-200">
              <Zap className="w-3 h-3" aria-hidden="true" />
              {activeSkillBadge}
            </span>
          )}
          <div className="flex gap-1 ml-1" role="status" aria-label="Agent is replying">
            <div className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
            <div className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
            <div className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
          </div>
        </div>

        {toolCalls.map((tc, i) => (
          <ToolCallBlock key={i} toolCall={tc} />
        ))}

        {content && (
          <div className="px-4 py-3 rounded-2xl rounded-bl-sm bg-gray-800 text-gray-100 text-sm leading-relaxed break-words">
            <Markdown className="prose prose-invert prose-sm max-w-none" components={mdComponents} streaming>
              {content}
            </Markdown>
          </div>
        )}
      </div>
    </div>
  )
}
