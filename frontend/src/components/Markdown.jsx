import React, { useMemo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { mermaidMarkdownComponents } from './markdownComponents'
import { splitFrontmatter } from './chat/frontmatter'
import MetadataCard from './chat/MetadataCard'

const REMARK_PLUGINS = [remarkGfm]

// The one markdown renderer. GFM (tables, task lists, strikethrough,
// autolinks) is always on, and ```mermaid fences render as diagrams via
// markdownComponents. Raw HTML in the source is NOT rendered (no
// rehype-raw), so agent/ingest-authored markdown can't inject markup;
// anything that must render HTML goes through DOMPurify elsewhere.
//
// YAML frontmatter blocks (`---` / key: value lines / `---`, leading or
// embedded) are cut out by chat/frontmatter.js and shown as a compact
// MetadataCard; left to markdown, the closing `---` would turn every key
// line into one giant setext H2 (UI review N3). A lone `---` is still a
// horizontal rule. `streaming` keeps a block whose closing `---` hasn't
// arrived yet from flashing as a heading.
//
// `components` extends/overrides the default element renderers for a call
// site that needs different ones (e.g. chat resolves workspace:// links and
// images); the mermaid `pre` handler stays unless the override replaces it.
export default function Markdown({ children, className, components, streaming = false }) {
  const merged = useMemo(
    () => (components ? { ...mermaidMarkdownComponents, ...components } : mermaidMarkdownComponents),
    [components],
  )
  const text = typeof children === 'string' ? children : (children == null ? '' : String(children))
  const segments = useMemo(() => splitFrontmatter(text, { streaming }), [text, streaming])

  if (segments.length === 1 && segments[0].type === 'markdown') {
    return (
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} className={className} components={merged}>
        {text}
      </ReactMarkdown>
    )
  }
  return (
    <div className={className}>
      {segments.map((seg, i) => (
        seg.type === 'frontmatter'
          ? <MetadataCard key={i} fields={seg.fields} partial={seg.partial} />
          : <ReactMarkdown key={i} remarkPlugins={REMARK_PLUGINS} components={merged}>{seg.text}</ReactMarkdown>
      ))}
    </div>
  )
}
