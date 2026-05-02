import React, { useState, useRef } from 'react'

/**
 * Lightweight hover tooltip. Shows after a short delay (200ms by default),
 * positioned relative to the wrapped child. Themed to match the rest of
 * the chrome rather than relying on the slow/ugly browser-native title.
 */
export default function Tooltip({ children, label, placement = 'bottom', delay = 200 }) {
  const [visible, setVisible] = useState(false)
  const timerRef = useRef(null)

  const onEnter = () => {
    clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => setVisible(true), delay)
  }
  const onLeave = () => {
    clearTimeout(timerRef.current)
    setVisible(false)
  }

  const positionClass = {
    top:    'bottom-full mb-1',
    bottom: 'top-full mt-1',
    left:   'right-full mr-1',
    right:  'left-full ml-1',
  }[placement] || 'top-full mt-1'

  return (
    <span className="relative inline-flex" onMouseEnter={onEnter} onMouseLeave={onLeave} onFocus={onEnter} onBlur={onLeave}>
      {children}
      {visible && label && (
        <span
          className={`pointer-events-none absolute z-50 ${positionClass} left-1/2 -translate-x-1/2 whitespace-nowrap rounded bg-gray-950 border border-gray-700 px-2 py-1 text-[11px] text-gray-200 shadow-lg`}
          role="tooltip"
        >
          {label}
        </span>
      )}
    </span>
  )
}
