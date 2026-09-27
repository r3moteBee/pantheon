import React from 'react'
import { FileText, File } from 'lucide-react'
import { useStore } from '../../store'
import { filesApi } from '../../api/client'

// Helper: resolve workspace:// paths to viewable URLs
const isWorkspacePath = (url) => url && url.startsWith('workspace://')
const workspaceFilePath = (url) => decodeURIComponent(url.replace(/^workspace:\/\//, ''))
const imageExts = /\.(png|jpe?g|gif|webp|bmp|svg)$/i
const pdfExt = /\.pdf$/i

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
    code: ({ node, inline, className, children, ...props }) => {
      if (inline) {
        return <code className="bg-gray-700 px-1 py-0.5 rounded text-xs font-mono" {...props}>{children}</code>
      }
      return (
        <pre className="bg-gray-900 rounded-lg p-3 overflow-x-auto">
          <code className="text-green-300 text-xs font-mono" {...props}>{children}</code>
        </pre>
      )
    },
    img: ({ src, alt, ...props }) => {
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
      return <img src={src} alt={alt} {...props} />
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
