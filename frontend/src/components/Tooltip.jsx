import React, { useState, useRef, useLayoutEffect } from 'react'
import { createPortal } from 'react-dom'

/**
 * Hover tooltip that renders through a portal so it escapes any
 * overflow:hidden / overflow-x-auto / transform ancestor clipping.
 *
 * Uses fixed positioning anchored to the trigger's bounding rect,
 * recomputed when the tooltip becomes visible.
 */
export default function Tooltip({ children, label, placement = 'bottom', delay = 200 }) {
  const [visible, setVisible] = useState(false)
  const [coords, setCoords] = useState({ left: 0, top: 0 })
  const triggerRef = useRef(null)
  const timerRef = useRef(null)

  const show = () => {
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setVisible(true), delay)
  }
  const hide = () => {
    clearTimeout(timerRef.current)
    setVisible(false)
  }

  useLayoutEffect(() => {
    if (!visible || !triggerRef.current) return
    const r = triggerRef.current.getBoundingClientRect()
    const gap = 6
    let left, top
    switch (placement) {
      case 'top':
        left = r.left + r.width / 2
        top = r.top - gap
        break
      case 'left':
        left = r.left - gap
        top = r.top + r.height / 2
        break
      case 'right':
        left = r.right + gap
        top = r.top + r.height / 2
        break
      case 'bottom':
      default:
        left = r.left + r.width / 2
        top = r.bottom + gap
        break
    }
    setCoords({ left, top })
  }, [visible, placement])

  const transformByPlacement = {
    top: 'translate(-50%, -100%)',
    bottom: 'translate(-50%, 0)',
    left: 'translate(-100%, -50%)',
    right: 'translate(0, -50%)',
  }[placement] || 'translate(-50%, 0)'

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
          className="pointer-events-none whitespace-nowrap rounded bg-gray-950 border border-gray-700 px-2 py-1 text-[11px] text-gray-200 shadow-lg"
          role="tooltip"
          style={{
            position: 'fixed',
            left: coords.left,
            top: coords.top,
            transform: transformByPlacement,
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
