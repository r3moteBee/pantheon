import React, { useState, useEffect } from 'react'
import { ChevronDown, ChevronRight, Zap, Brain, FileText, File, Loader2 } from 'lucide-react'
import SandboxedHtml from '../SandboxedHtml'
import Markdown from '../Markdown'
import { useStore } from '../../store'
import { filesApi, artifactsApi } from '../../api/client'

// Parse workspace:// path from show_file result
function parseShowFileResult(result) {
  if (!result) return null
  // Match [DISPLAY:workspace://path] format
  const displayMatch = result.match(/\[DISPLAY:workspace:\/\/([^\]]+)\]/)
  if (displayMatch) {
    const path = decodeURIComponent(displayMatch[1])
    const name = path.split('/').pop()
    return { caption: name, path }
  }
  // Legacy: match ![caption](workspace://path) or [caption](workspace://path)
  const mdMatch = result.match(/!?\[([^\]]*)\]\(workspace:\/\/([^)]+)\)/)
  if (mdMatch) return { caption: mdMatch[1], path: decodeURIComponent(mdMatch[2]) }
  return null
}

function FilePreview({ filePath, caption }) {
  const projectId = useStore((s) => s.activeProject?.id || 'default')
  const url = filesApi.viewUrl(filePath, projectId)
  const ext = (filePath || '').split('.').pop().toLowerCase()
  const imgExts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg']
  const textExts = ['md', 'markdown', 'txt', 'csv', 'json', 'yaml', 'yml']

  const [textContent, setTextContent] = useState(null)
  const [loading, setLoading] = useState(false)

  // Fetch text content for text-based files
  useEffect(() => {
    if (textExts.includes(ext)) {
      setLoading(true)
      fetch(url)
        .then(r => r.ok ? r.text() : Promise.reject('fetch failed'))
        .then(text => { setTextContent(text); setLoading(false) })
        .catch(() => { setTextContent(null); setLoading(false) })
    }
  }, [url, ext])

  if (imgExts.includes(ext)) {
    return (
      <div className="my-2">
        <img src={url} alt={caption || filePath} className="rounded-lg max-w-full max-h-96 border border-gray-700" />
        {caption && <div className="text-xs text-gray-400 mt-1">{caption}</div>}
      </div>
    )
  }
  if (ext === 'pdf') {
    return (
      <div className="my-2 rounded-lg overflow-hidden border border-gray-700">
        <iframe src={url} title={caption || filePath} className="w-full bg-white" style={{ height: '500px' }} />
        <div className="bg-gray-800 px-3 py-1.5 text-xs text-gray-400 flex items-center gap-2">
          <FileText className="w-3 h-3" /> {caption || filePath}
        </div>
      </div>
    )
  }
  if (ext === 'html' || ext === 'htm') {
    return (
      <div className="my-2 rounded-lg overflow-hidden border border-gray-700">
        <SandboxedHtml path={filePath} projectId={projectId} title={caption || filePath} className="w-full bg-white" style={{ height: '400px' }} />
        <div className="bg-gray-800 px-3 py-1.5 text-xs text-gray-400 flex items-center gap-2">
          <FileText className="w-3 h-3" /> {caption || filePath}
        </div>
      </div>
    )
  }
  if (textExts.includes(ext)) {
    return (
      <div className="my-2 rounded-lg overflow-hidden border border-gray-700">
        <div className="bg-gray-800 px-3 py-1.5 text-xs text-gray-400 flex items-center gap-2 border-b border-gray-700">
          <FileText className="w-3 h-3" /> {caption || filePath}
        </div>
        <div className="bg-gray-900 px-4 py-3 max-h-96 overflow-y-auto">
          {loading ? (
            <div className="text-gray-400 text-sm">Loading...</div>
          ) : textContent !== null ? (
            (ext === 'md' || ext === 'markdown') ? (
              <Markdown className="prose prose-invert prose-sm max-w-none">
                {textContent}
              </Markdown>
            ) : (
              <pre className="text-gray-300 text-xs whitespace-pre-wrap break-words font-mono">{textContent}</pre>
            )
          ) : (
            <div className="text-red-400 text-sm">Failed to load file</div>
          )}
        </div>
      </div>
    )
  }
  // Fallback: download link
  return (
    <div className="my-2">
      <a href={url} target="_blank" rel="noopener noreferrer"
        className="text-brand-400 hover:text-brand-300 underline inline-flex items-center gap-1 text-sm"
      >
        <File className="w-3.5 h-3.5" /> {caption || filePath}
      </a>
    </div>
  )
}

// generate_image results carry one [DISPLAY:artifact://<id>] per image
function parseArtifactDisplays(result) {
  if (!result) return []
  return [...result.matchAll(/\[DISPLAY:artifact:\/\/([0-9a-fA-F-]+)\]/g)].map((m) => m[1])
}

function GeneratedImages({ ids, caption }) {
  return (
    <div className="my-2 flex flex-wrap gap-2">
      {ids.map((id) => (
        <a key={id} href={artifactsApi.rawUrl(id)} target="_blank" rel="noopener noreferrer">
          <img
            src={artifactsApi.rawUrl(id)}
            alt={caption || 'generated image'}
            className="rounded-lg max-h-96 max-w-full border border-gray-700"
          />
        </a>
      ))}
      {caption && <div className="w-full text-xs text-gray-400">{caption}</div>}
    </div>
  )
}

// One tool call in a message: show_file results render as a file preview,
// generate_image results as the images, everything else as a collapsible
// args/result block. The `context_loaded` banner ("corpus context loaded")
// is only ever rendered by the streaming turn: it is dropped from the
// finished message (useChatSocket.persistedToolCalls) because the backend
// doesn't store it, so history never has it either.
// A tool call waiting on an MCP background task (tasks extension): the
// server's state and message, how long it has run, and a Cancel that sends
// tasks/cancel. Gone once the result arrives.
function TaskStatusLine({ status, onCancel }) {
  const label = { working: 'Running on the server', input_required: 'Waiting for your input', cancelled: 'Cancelling' }[status.state] || status.state
  const minutes = Math.floor((status.elapsed || 0) / 60)
  const seconds = String((status.elapsed || 0) % 60).padStart(2, '0')
  const isProgress = status.kind === 'progress'
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-1.5 bg-gray-900 border-t border-gray-800 text-gray-300" role="status">
      <span className="flex items-center gap-1.5">
        <Loader2 className="w-3 h-3 animate-spin text-sky-400" aria-hidden="true" />
        <span>{isProgress ? 'In progress' : `Background task · ${label}`}</span>
      </span>
      {typeof status.percent === 'number' && (
        <span className="flex items-center gap-1.5">
          <span className="inline-block w-24 h-1.5 rounded bg-gray-700 overflow-hidden" aria-hidden="true">
            <span className="block h-full bg-sky-500" style={{ width: `${status.percent}%` }} />
          </span>
          <span className="text-gray-400">{status.percent}%</span>
        </span>
      )}
      {status.message && <span className="text-gray-400 truncate max-w-[60%]" title={status.message}>{status.message}</span>}
      <span className="text-gray-500">{minutes}:{seconds}</span>
      {!isProgress && onCancel && status.state !== 'cancelled' && (
        <button
          type="button"
          onClick={() => onCancel()}
          disabled={!!status.cancelling}
          className="ml-auto px-2 py-0.5 rounded border border-gray-700 hover:bg-gray-800 disabled:opacity-50 text-gray-300"
        >
          {status.cancelling ? 'Cancelling…' : 'Cancel task'}
        </button>
      )}
    </div>
  )
}

export default function ToolCallBlock({ toolCall, onCancel }) {
  const [expanded, setExpanded] = useState(false)
  const isContextLoad = toolCall.name === 'context_loaded'
  const isShowFile = toolCall.name === 'show_file'
  const showFileData = isShowFile ? parseShowFileResult(toolCall.result) : null

  // show_file with a successful result: render file preview, collapse the tool block
  if (isShowFile && showFileData) {
    return <FilePreview filePath={showFileData.path} caption={showFileData.caption} />
  }

  const imageIds = toolCall.name === 'generate_image' ? parseArtifactDisplays(toolCall.result) : []
  if (imageIds.length) {
    return <GeneratedImages ids={imageIds} caption={toolCall.args?.prompt} />
  }

  return (
    <div className={`my-2 border rounded-lg overflow-hidden text-xs ${isContextLoad ? 'border-brand-700' : 'border-gray-700'}`}>
      <button
        className={`w-full flex items-center gap-2 px-3 py-2 text-left ${isContextLoad ? 'bg-brand-900 hover:bg-brand-800' : 'bg-gray-800 hover:bg-gray-750'}`}
        onClick={() => setExpanded(!expanded)}
        aria-expanded={expanded}
      >
        {isContextLoad
          ? <Brain className="w-3 h-3 text-brand-400 flex-shrink-0" />
          : <Zap className="w-3 h-3 text-yellow-400 flex-shrink-0" />}
        <span className={`font-mono font-medium ${isContextLoad ? 'text-brand-300' : 'text-yellow-300'}`}>
          {isContextLoad
            ? `corpus context loaded (${toolCall.args?.sources || 0} results from ${(toolCall.args?.tiers || []).join(', ')})`
            : toolCall.name}
        </span>
        {expanded ? <ChevronDown className="w-3 h-3 ml-auto text-gray-400" /> : <ChevronRight className="w-3 h-3 ml-auto text-gray-400" />}
      </button>
      {toolCall.status && !toolCall.result && (
        <TaskStatusLine status={toolCall.status} onCancel={onCancel ? () => onCancel(toolCall.id) : null} />
      )}
      {expanded && (
        <div className="px-3 py-2 bg-gray-900 space-y-2">
          {!isContextLoad && toolCall.args && Object.keys(toolCall.args).length > 0 && (
            <div>
              <div className="text-gray-400 mb-1">Args:</div>
              <pre className="text-green-300 whitespace-pre-wrap break-all">
                {JSON.stringify(toolCall.args, null, 2)}
              </pre>
            </div>
          )}
          {toolCall.result && (
            <div>
              <div className="text-gray-400 mb-1">{isContextLoad ? 'Injected context:' : 'Result:'}</div>
              <pre className={`whitespace-pre-wrap break-all ${isContextLoad ? 'text-brand-200' : 'text-blue-300'}`}>{toolCall.result}</pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
