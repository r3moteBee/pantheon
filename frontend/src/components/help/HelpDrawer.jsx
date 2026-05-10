import React, { useState } from 'react'
import { HelpCircle, ChevronDown, ChevronRight } from 'lucide-react'

/**
 * Collapsible inline help panel. Header bar is a clickable button
 * that toggles open/closed. Body renders arbitrary children — used
 * for tables, paragraphs, code snippets, external links.
 *
 * Optional storageKey: when set, open state persists to
 * localStorage[storageKey]. Without it, the drawer resets to
 * defaultOpen on each mount.
 *
 * Convention for storageKey: `help.<surface-name>` so namespaces
 * don't collide (e.g. `help.llm-providers`).
 */
function _readPersisted(storageKey, defaultOpen) {
  if (!storageKey) return defaultOpen
  try {
    const v = localStorage.getItem(storageKey)
    if (v === 'true') return true
    if (v === 'false') return false
    return defaultOpen
  } catch {
    return defaultOpen
  }
}

export default function HelpDrawer({
  title,
  children,
  defaultOpen = false,
  storageKey,
}) {
  const [open, setOpen] = useState(() => _readPersisted(storageKey, defaultOpen))

  const toggle = () => {
    const next = !open
    setOpen(next)
    if (storageKey) {
      try { localStorage.setItem(storageKey, String(next)) } catch {}
    }
  }

  const Chevron = open ? ChevronDown : ChevronRight

  return (
    <div className="border border-gray-700 rounded-md bg-gray-900/30 overflow-hidden">
      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="w-full flex items-center gap-2 px-3 py-2 text-sm text-gray-300 hover:bg-gray-800/50"
      >
        <Chevron className="w-4 h-4 text-gray-500" aria-hidden="true" />
        <HelpCircle className="w-4 h-4 text-gray-500" aria-hidden="true" />
        <span className="flex-1 text-left">{title}</span>
      </button>
      {open && (
        <div className="px-3 py-3 border-t border-gray-800">
          {children}
        </div>
      )}
    </div>
  )
}
