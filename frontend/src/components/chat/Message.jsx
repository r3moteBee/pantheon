import React from 'react'
import { Brain, Zap, Check, Copy, Bookmark, ThumbsUp, ThumbsDown } from 'lucide-react'
import Markdown from '../Markdown'
import { llmApi } from '../../api/client'
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
    <div className="mt-1 flex items-center gap-0.5 opacity-60 lg:opacity-0 lg:group-hover:opacity-100 transition-opacity">
      <button
        onClick={handleCopy}
        className="p-1.5 text-gray-500 hover:text-gray-300 transition-colors rounded"
        title="Copy message"
        aria-label="Copy message"
      >
        {copied ? <Check className="w-3 h-3 text-green-400" /> : <Copy className="w-3 h-3" />}
      </button>
      <button
        onClick={onSaveMessage}
        className="p-1.5 text-gray-500 hover:text-gray-300 transition-colors rounded"
        title="Save message as artifact"
        aria-label="Save message as artifact"
      >
        <Bookmark className="w-3 h-3" />
      </button>
    </div>
  )
}

// Which task class / model answered (set by the per-turn chat router),
// with 👍/👎 that feed routing tuning (Settings → Routing tuning).
export function RouteBadge({ route, rateable = false }) {
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
    <span className='flex items-center gap-1'>
      <span
        className='text-[10px] px-1.5 py-0.5 rounded bg-gray-800 text-gray-400 border border-gray-700'
        title={`${route.rule}: ${route.reason}${fellBack ? ` — primary ${route.model} failed, answered by fallback` : ''}`}
      >
        {route.task_class}{model ? ` · ${model}` : ''}{fellBack ? ' ↩' : ''}
      </span>
      {rateable && route.decision_id && (
        <span className={`flex items-center ${rating ? '' : 'opacity-60 lg:opacity-0 lg:group-hover:opacity-100'} transition-opacity`}>
          <button onClick={() => rate(1)} title='Good answer for this model' aria-label='Good answer'
            className={`p-0.5 rounded ${rating > 0 ? 'text-emerald-400' : 'text-gray-500 hover:text-gray-300'}`}>
            <ThumbsUp className='w-3 h-3' />
          </button>
          <button onClick={() => rate(-1)} title='Wrong model / bad answer' aria-label='Bad answer'
            className={`p-0.5 rounded ${rating < 0 ? 'text-red-400' : 'text-gray-500 hover:text-gray-300'}`}>
            <ThumbsDown className='w-3 h-3' />
          </button>
        </span>
      )}
    </span>
  )
}

export default function Message({ msg, onSaveMessage }) {
  const isUser = msg.role === 'user'
  const mdComponents = useChatMarkdownComponents()
  return (
    <div className={`group flex ${isUser ? 'justify-end' : 'justify-start'} mb-4`}>
      <div className={`max-w-3xl ${isUser ? 'order-2' : 'order-1'}`}>
        {!isUser && (
          <div className="flex items-center gap-2 mb-1">
            <div className="w-6 h-6 rounded-full bg-brand-600 flex items-center justify-center">
              <Brain className="w-3.5 h-3.5 text-white" />
            </div>
            <span className="text-xs text-gray-500">Agent</span>
            <RouteBadge route={msg.route} rateable />
            {msg.timestamp && (
              <span className="text-xs text-gray-600">{new Date(msg.timestamp).toLocaleTimeString()}</span>
            )}
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
            px-4 py-3 rounded-2xl text-sm leading-relaxed
            ${isUser
              ? 'bg-brand-600 text-white rounded-br-sm'
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

        {!isUser && msg.content && onSaveMessage && (
          <MessageActions content={msg.content} onSaveMessage={() => onSaveMessage(msg.content)} />
        )}

        {isUser && msg.timestamp && (
          <div className="flex justify-end mt-1">
            <span className="text-xs text-gray-600">{new Date(msg.timestamp).toLocaleTimeString()}</span>
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
      <div className="max-w-3xl">
        <div className="flex items-center gap-2 mb-1">
          <div className="w-6 h-6 rounded-full bg-brand-600 flex items-center justify-center">
            <Brain className="w-3.5 h-3.5 text-white" />
          </div>
          <span className="text-xs text-gray-500">Agent</span>
          <RouteBadge route={liveRoute} />
          {activeSkillBadge && (
            <span className="flex items-center gap-1 text-[10px] px-1.5 py-0.5 rounded bg-brand-900 text-brand-300">
              <Zap className="w-2.5 h-2.5" />
              {activeSkillBadge}
            </span>
          )}
          <div className="flex gap-1 ml-1">
            <div className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-bounce" style={{ animationDelay: '0ms' }} />
            <div className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-bounce" style={{ animationDelay: '150ms' }} />
            <div className="w-1.5 h-1.5 bg-brand-400 rounded-full animate-bounce" style={{ animationDelay: '300ms' }} />
          </div>
        </div>

        {toolCalls.map((tc, i) => (
          <ToolCallBlock key={i} toolCall={tc} />
        ))}

        {content && (
          <div className="px-4 py-3 rounded-2xl rounded-bl-sm bg-gray-800 text-gray-100 text-sm leading-relaxed">
            <Markdown className="prose prose-invert prose-sm max-w-none" components={mdComponents}>
              {content}
            </Markdown>
          </div>
        )}
      </div>
    </div>
  )
}
