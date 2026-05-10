import React from 'react'
import { HelpCircle } from 'lucide-react'
import Tooltip from '../Tooltip'

/**
 * Inline `?` icon that shows a one-sentence hint on hover. Use next
 * to a form label or section heading for short clarifications. For
 * richer content (paragraphs, tables, links) use HelpDrawer instead.
 *
 * Wraps the existing Tooltip primitive so hint copy renders through
 * a portal and escapes overflow ancestors.
 */
export default function InfoTooltip({ text, placement = 'top', size = 14 }) {
  if (!text) return null
  return (
    <Tooltip label={text} placement={placement}>
      <button
        type="button"
        aria-label={text}
        onClick={(e) => { e.preventDefault(); e.stopPropagation() }}
        className="text-gray-500 hover:text-gray-300 inline-flex items-center align-middle ml-1"
      >
        <HelpCircle width={size} height={size} aria-hidden="true" />
      </button>
    </Tooltip>
  )
}
