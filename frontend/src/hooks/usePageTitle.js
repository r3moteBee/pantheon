import { useEffect } from 'react'

/**
 * Sets document.title from the most specific part first (UI review #22):
 *   usePageTitle('Marble Maze Game', 'Tasks')  ->  "Marble Maze Game · Tasks — Pantheon"
 * Empty parts are skipped. Pass `false` as the first argument to skip
 * (e.g. a page embedded in another one that owns the title).
 */
export default function usePageTitle(...parts) {
  const enabled = parts[0] !== false
  const title = parts.filter((p) => p && p !== true).join(' · ')
  useEffect(() => {
    if (!enabled) return
    document.title = title ? `${title} — Pantheon` : 'Pantheon'
  }, [enabled, title])
}
