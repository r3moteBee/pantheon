import React, { useState, useRef, useLayoutEffect } from 'react'
import { createPortal } from 'react-dom'

/**
 * Hover tooltip that renders through a portal so it escapes any
 * overflow:hidden / overflow-x-auto / transform ancestor clipping.
 *
 * Uses fixed positioning anchored to the trigger's bounding rect,
 * recomputed when the tooltip becomes visible. Long labels wrap, and the
 * tooltip is kept inside the browser window near the edges.
 */
export default function Tooltip({ children, label, placement = 'bottom', delay = 200 }) {
  const [visible, setVisible] = useState(false)
  const [coords, setCoords] = useState({ left: 0, top: 0, ready: false })
  const triggerRef = useRef(null)
  const tipRef = useRef(null)
  const timerRef = useRef(null)

  const show = () => {
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setVisible(true), delay)
  }
  const hide = () => {
    clearTimeout(timerRef.current)
    setVisible(false)
    setCoords((c) => ({ ...c, ready: false }))
  }

  // Position after the tooltip has rendered (hidden) so its real size is
  // known: flip to the opposite side when the preferred one has no room,
  // then clamp into the viewport so it never runs off the window edge.
  useLayoutEffect(() => {
    if (!visible || !triggerRef.current || !tipRef.current) return
    const r = triggerRef.current.getBoundingClientRect()
    const t = tipRef.current.getBoundingClientRect()
    const vw = window.innerWidth
    const vh = window.innerHeight
    const gap = 6
    const margin = 8
    let side = placement
    if (side === 'top' && r.top - gap - t.height < margin) side = 'bottom'
    else if (side === 'bottom' && r.bottom + gap + t.height > vh - margin) side = 'top'
    else if (side === 'left' && r.left - gap - t.width < margin) side = 'right'
    else if (side === 'right' && r.right + gap + t.width > vw - margin) side = 'left'
    let left, top
    switch (side) {
      case 'top':
        left = r.left + r.width / 2 - t.width / 2
        top = r.top - gap - t.height
        break
      case 'left':
        left = r.left - gap - t.width
        top = r.top + r.height / 2 - t.height / 2
        break
      case 'right':
        left = r.right + gap
        top = r.top + r.height / 2 - t.height / 2
        break
      case 'bottom':
      default:
        left = r.left + r.width / 2 - t.width / 2
        top = r.bottom + gap
        break
    }
    left = Math.max(margin, Math.min(left, vw - margin - t.width))
    top = Math.max(margin, Math.min(top, vh - margin - t.height))
    setCoords({ left, top, ready: true })
  }, [visible, placement, label])

  return (
    <>
      <span
        ref={triggerRef}
        className="relative inline-flex"
        onMouseEnter={show}
        onMouseLeave={hide}
        onFocus={show}
        onBlur={hide}
      >
        {children}
      </span>
      {visible && label && createPortal(
        <span
          ref={tipRef}
          className="pointer-events-none rounded bg-gray-950 border border-gray-700 px-2 py-1 text-[11px] text-gray-200 shadow-lg"
          role="tooltip"
          style={{
            position: 'fixed',
            left: coords.left,
            top: coords.top,
            width: 'max-content',
            maxWidth: 'min(20rem, calc(100vw - 16px))',
            visibility: coords.ready ? 'visible' : 'hidden',
            zIndex: 9999,
          }}
        >
          {label}
        </span>,
        document.body
      )}
    </>
  )
}
