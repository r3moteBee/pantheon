import React from 'react'
import { FileText, File } from 'lucide-react'
import { useStore } from '../../store'
import { filesApi } from '../../api/client'

// Helper: resolve workspace:// paths to viewable URLs
const isWorkspacePath = (url) => url && url.startsWith('workspace://')
const workspaceFilePath = (url) => decodeURIComponent(url.replace(/^workspace:\/\//, ''))
const imageExts = /\.(png|jpe?g|gif|webp|bmp|svg)$/i
const pdfExt = /\.pdf$/i

// Defense in depth for zero-click exfiltration: the backend already turns
// external images in replies into text (agent/output_filter.py), but if one
// slips through, the browser must not fetch it. Only same-origin http(s)
// images load (relative paths, /api/... artifact and file URLs); anything
// else (other hosts, //host, data:, blob:) is shown as a link instead.
export const isSameOriginImage = (src) => {
  if (!src || typeof src !== 'string') return false
  try {
    const u = new URL(src, window.location.href)
    return (u.protocol === 'http:' || u.protocol === 'https:') && u.origin === window.location.origin
  } catch {
    return false
  }
}

const externalImageLabel = (src) => {
  try {
    const u = new URL(src, window.location.href)
    return u.protocol === 'http:' || u.protocol === 'https:' ? u.host : u.protocol.replace(/:$/, '') + ' URL'
  } catch {
    return 'external URL'
  }
}

// Chat's element overrides for the shared <Markdown>: workspace:// images
// and PDFs render inline, workspace:// links open the file, external links
// open in a new tab.
export default function useChatMarkdownComponents() {
  const projectId = useStore((s) => s.activeProject?.id || 'default')
  const resolveUrl = (wsPath) => {
    // Decode URI-encoded path from show_file tool, then let viewUrl re-encode
    const filePath = decodeURIComponent(wsPath.replace(/^workspace:\/\//, ''))
    return filesApi.viewUrl(filePath, projectId)
  }

  return {
    // Inline code only: fenced blocks are rendered by the shared <pre>
    // handler (markdownComponents.jsx -> CodeBlock) from this element's props.
    code: ({ node, className, children, ...props }) => (
      <code className={`bg-gray-700/80 px-1 py-0.5 rounded text-xs font-mono text-gray-100 ${className || ''}`} {...props}>{children}</code>
    ),
    img: ({ src, alt, node, ...props }) => {
      if (isWorkspacePath(src)) {
        const url = resolveUrl(src)
        const path = workspaceFilePath(src)
        if (pdfExt.test(path)) {
          return (
            <div className="my-3 rounded-lg overflow-hidden border border-gray-700">
              <embed src={url} type="application/pdf" className="w-full" style={{ height: '500px' }} />
              <div className="bg-gray-800 px-3 py-1.5 text-xs text-gray-400 flex items-center gap-2">
                <FileText className="w-3 h-3" /> {alt || path}
              </div>
            </div>
          )
        }
        if (imageExts.test(path)) {
          return (
            <div className="my-3">
              <img src={url} alt={alt || path} className="rounded-lg max-w-full max-h-96 border border-gray-700" />
              {alt && <div className="text-xs text-gray-400 mt-1">{alt}</div>}
            </div>
          )
        }
      }
      if (isSameOriginImage(src)) return <img src={src} alt={alt} {...props} />
      let isWeb = false
      try { isWeb = !!src && /^https?:$/.test(new URL(src, window.location.href).protocol) } catch { /* not a URL */ }
      const label = src ? `image not loaded: ${alt || 'image'} (${externalImageLabel(src)})` : `image not loaded: ${alt || 'image'}`
      return isWeb
        ? <a href={src} target="_blank" rel="noopener noreferrer nofollow" className="italic text-gray-400 underline">{label}</a>
        : <span className="italic text-gray-400">{label}</span>
    },
    a: ({ href, children, ...props }) => {
      if (isWorkspacePath(href)) {
        const url = resolveUrl(href)
        return (
          <a href={url} target="_blank" rel="noopener noreferrer"
            className="text-brand-400 hover:text-brand-300 underline inline-flex items-center gap-1"
            {...props}
          >
            <File className="w-3 h-3 inline" />
            {children}
          </a>
        )
      }
      return <a href={href} target="_blank" rel="noopener noreferrer" {...props}>{children}</a>
    },
  }
}
