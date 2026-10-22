import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Check, ChevronDown, MoreHorizontal } from 'lucide-react'

/**
 * A row of tabs that never scrolls (UI review N2 / #2): the tabs that do not
 * fit go into a "More" menu instead of behind a horizontal scrollbar. The
 * active tab is always kept in the strip (the last fitting tab yields to it).
 *
 *   <TabStrip tabs={[{ id, label, icon }]} value={id} onChange={setId}
 *             variant="pill" | "underline" role="nav" | "tabs" label="Project sections" />
 *
 * - variant "pill": the compact chips of the workspace top bar (h-7).
 * - variant "underline": the section tablists of Settings / Skills / Connections.
 * - role "tabs" renders a WAI-ARIA tablist (role=tab + aria-selected);
 *   role "nav" renders a <nav> whose buttons carry aria-current="page".
 *
 * Widths come from a hidden copy of the full strip (so hidden tabs can still
 * be measured) and are re-read whenever the strip resizes.
 */
export default function TabStrip({ tabs, value, onChange, variant = 'underline', role = 'tabs', label, className = '' }) {
  const boxRef = useRef(null)
  const ghostRef = useRef(null)
  const [visible, setVisible] = useState(null) // null = everything fits

  const measure = useCallback(() => {
    const box = boxRef.current
    const ghost = ghostRef.current
    if (!box || !ghost) return
    const avail = box.clientWidth
    const nodes = [...ghost.children]
    const moreNode = nodes[nodes.length - 1]
    const widths = nodes.slice(0, -1).map((n) => n.getBoundingClientRect().width)
    const gap = parseFloat(getComputedStyle(ghost).columnGap) || 0
    const total = widths.reduce((a, w) => a + w, 0) + gap * Math.max(0, widths.length - 1)
    if (total <= avail + 0.5) { setVisible(null); return }
    // Fixed order (round 3 #1): the visible set never depends on which tab is
    // active, so tabs stay where muscle memory expects them. When the active
    // tab is in the overflow, the "More" button carries its name instead.
    const budget = avail - moreNode.getBoundingClientRect().width - gap
    const keep = new Set()
    let used = 0
    for (let i = 0; i < widths.length; i++) {
      if (used + (keep.size ? gap : 0) + widths[i] > budget) break
      keep.add(i); used += (keep.size > 1 ? gap : 0) + widths[i]
    }
    setVisible(keep)
  }, [tabs])

  useLayoutEffect(measure, [measure])
  useEffect(() => {
    const box = boxRef.current
    if (!box || typeof ResizeObserver === 'undefined') return
    const ro = new ResizeObserver(measure)
    ro.observe(box)
    // Web fonts arriving late change the label widths.
    document.fonts?.ready?.then(measure)
    return () => ro.disconnect()
  }, [measure])

  const shown = visible ? tabs.filter((_, i) => visible.has(i)) : tabs
  const hidden = visible ? tabs.filter((_, i) => !visible.has(i)) : []

  const isNav = role === 'nav'
  const Container = isNav ? 'nav' : 'div'
  const containerProps = isNav ? { 'aria-label': label } : { role: 'tablist', 'aria-label': label }
  const gapClass = variant === 'pill' ? 'gap-0.5' : 'gap-0'

  const renderTab = (t, { ghost = false } = {}) => {
    const Icon = t.icon
    const active = t.id === value
    const a11y = ghost
      ? { tabIndex: -1 }
      : isNav
        ? { 'aria-current': active ? 'page' : undefined }
        : { role: 'tab', 'aria-selected': active }
    return (
      <button
        key={t.id}
        type="button"
        onClick={ghost ? undefined : () => onChange(t.id)}
        className={tabClass(variant, active)}
        {...a11y}
      >
        {Icon && <Icon className={variant === 'pill' ? 'w-3 h-3' : 'w-3.5 h-3.5'} aria-hidden="true" />}
        {t.label}
      </button>
    )
  }

  return (
    <Container {...containerProps} className={`${className} flex items-center min-w-0`}>
      <div ref={boxRef} className={`relative flex-1 min-w-0 flex items-center overflow-hidden ${gapClass}`}>
        {shown.map((t) => renderTab(t))}
        {hidden.length > 0 && (
          <MoreMenu items={hidden} value={value} onChange={onChange} variant={variant} />
        )}
        {/* Measuring copy: every tab plus the "More" button, never shown. */}
        <div ref={ghostRef} aria-hidden="true"
          className={`absolute left-0 top-0 invisible pointer-events-none flex items-center whitespace-nowrap ${gapClass}`}>
          {tabs.map((t) => renderTab(t, { ghost: true }))}
          <button type="button" tabIndex={-1} className={tabClass(variant, false)}>
            <MoreHorizontal className="w-3.5 h-3.5" aria-hidden="true" /> More <ChevronDown className="w-3 h-3" aria-hidden="true" />
          </button>
        </div>
      </div>
    </Container>
  )
}

function tabClass(variant, active) {
  if (variant === 'pill') {
    return `flex h-7 flex-shrink-0 items-center gap-1.5 px-2.5 text-xs rounded-md transition-colors whitespace-nowrap ${
      active ? 'bg-gray-800 text-white' : 'text-gray-400 hover:text-gray-200 hover:bg-gray-900'
    }`
  }
  return `flex flex-shrink-0 items-center gap-1.5 px-4 py-2 text-xs font-medium whitespace-nowrap border-b-2 transition-colors ${
    active ? 'border-brand-400 text-brand-300' : 'border-transparent text-gray-400 hover:text-gray-200'
  }`
}

/** The overflow menu: the tabs that did not fit, as a menu beneath a "More" button. */
function MoreMenu({ items, value, onChange, variant }) {
  const btnRef = useRef(null)
  const menuRef = useRef(null)
  const [open, setOpen] = useState(false)
  const [coords, setCoords] = useState({ left: 0, top: 0 })
  const activeHidden = items.find((t) => t.id === value) || null
  const ActiveIcon = activeHidden?.icon

  useLayoutEffect(() => {
    if (!open || !btnRef.current) return
    const r = btnRef.current.getBoundingClientRect()
    const width = 224
    setCoords({ left: Math.max(8, Math.min(r.left, window.innerWidth - width - 8)), top: r.bottom + 4 })
  }, [open])

  useEffect(() => {
    if (!open) return
    const onDown = (e) => {
      if (btnRef.current?.contains(e.target) || menuRef.current?.contains(e.target)) return
      setOpen(false)
    }
    const onKey = (e) => { if (e.key === 'Escape') { setOpen(false); btnRef.current?.focus() } }
    document.addEventListener('mousedown', onDown)
    document.addEventListener('keydown', onKey)
    setTimeout(() => menuRef.current?.querySelector('[role=menuitemradio]')?.focus(), 0)
    return () => {
      document.removeEventListener('mousedown', onDown)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label={(activeHidden ? `${activeHidden.label} (current section). ` : '') + `More sections (${items.map((t) => t.label).join(', ')})`}
        title={activeHidden ? `${activeHidden.label} - current section. More sections` : 'More sections'}
        className={tabClass(variant, open || !!activeHidden)}
      >
        {activeHidden
          ? <>{ActiveIcon && <ActiveIcon className={variant === 'pill' ? 'w-3 h-3' : 'w-3.5 h-3.5'} aria-hidden="true" />}{activeHidden.label}</>
          : <><MoreHorizontal className="w-3.5 h-3.5" aria-hidden="true" /> More</>}
        <ChevronDown className="w-3 h-3" aria-hidden="true" />
      </button>
      {open && createPortal(
        <div
          ref={menuRef}
          role="menu"
          aria-label="More sections"
          className="w-56 rounded-md bg-gray-900 border border-gray-700 shadow-xl py-1"
          style={{ position: 'fixed', left: coords.left, top: coords.top, zIndex: 9999 }}
        >
          {items.map((t) => {
            const Icon = t.icon
            const active = t.id === value
            return (
              <button
                key={t.id}
                type="button"
                role="menuitemradio"
                aria-checked={active}
                onClick={() => { setOpen(false); onChange(t.id) }}
                className={`w-full text-left px-2 py-1.5 text-xs flex items-center gap-2 hover:bg-gray-800 ${active ? 'text-white font-medium' : 'text-gray-300'}`}
              >
                <Check className={`w-3 h-3 flex-shrink-0 ${active ? 'text-brand-400' : 'text-transparent'}`} aria-hidden="true" />
                {Icon && <Icon className="w-3.5 h-3.5 flex-shrink-0 text-gray-400" aria-hidden="true" />}
                <span className="truncate">{t.label}</span>
              </button>
            )
          })}
        </div>,
        document.body
      )}
    </>
  )
}
