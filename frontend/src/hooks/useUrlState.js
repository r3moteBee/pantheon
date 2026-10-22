import { useCallback } from 'react'
import { useSearchParams } from 'react-router-dom'

/**
 * A query-string-backed piece of UI state (UI review #15), e.g. the open
 * Settings section: `const [section, setSection] = useUrlParam('section', 'llms', { aliases: ['tab'] })`.
 *
 * - `aliases`: older parameter names still read (and dropped on the next write),
 *   so existing links such as /settings?tab=personality keep working.
 * - `allowed`: values outside this list fall back to the default.
 * - The default value is kept out of the URL; writes replace the history entry
 *   unless `push: true`.
 */
export function useUrlParam(name, fallback, { aliases = [], allowed = null, push = false } = {}) {
  const [params, setParams] = useSearchParams()
  let raw = params.get(name)
  for (const a of aliases) if (raw == null) raw = params.get(a)
  const value = raw != null && (!allowed || allowed.includes(raw)) ? raw : fallback

  const aliasKey = aliases.join(',')
  const setValue = useCallback((next) => {
    setParams((prev) => {
      const p = new URLSearchParams(prev)
      for (const a of aliasKey ? aliasKey.split(',') : []) p.delete(a)
      if (next == null || next === fallback) p.delete(name)
      else p.set(name, next)
      return p
    }, { replace: !push })
  }, [setParams, name, fallback, aliasKey, push])

  return [value, setValue]
}
