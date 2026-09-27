import React, { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { mermaidMarkdownComponents } from './markdownComponents'

const REMARK_PLUGINS = [remarkGfm]

// The one markdown renderer. GFM (tables, task lists, strikethrough,
// autolinks) is always on, and ```mermaid fences render as diagrams via
// markdownComponents. Raw HTML in the source is NOT rendered (no
// rehype-raw), so agent/ingest-authored markdown can't inject markup;
// anything that must render HTML goes through DOMPurify elsewhere.
//
// `components` extends/overrides the default element renderers for a call
// site that needs different ones (e.g. chat resolves workspace:// links and
// images); the mermaid `pre` handler stays unless the override replaces it.
export default function Markdown({ children, className, components }) {
  const merged = useMemo(
    () => (components ? { ...mermaidMarkdownComponents, ...components } : mermaidMarkdownComponents),
    [components],
  )
  return (
    <ReactMarkdown remarkPlugins={REMARK_PLUGINS} className={className} components={merged}>
      {children}
    </ReactMarkdown>
  )
}
