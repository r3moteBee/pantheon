// One timestamp format for the whole UI (UI review 2026-10-21 #10): local time,
// relative for recent moments, the absolute local time (with zone) on hover.
// Accepts ISO strings (with or without zone/microseconds), epoch seconds or ms, Date.

export function toDate(ts) {
  if (ts == null || ts === '') return null
  if (ts instanceof Date) return isNaN(ts) ? null : ts
  if (typeof ts === 'number') return new Date(ts < 1e12 ? ts * 1000 : ts)
  let s = String(ts).trim()
  if (/^\d+(\.\d+)?$/.test(s)) return toDate(Number(s))
  // "2026-10-22 02:46" / "2026-10-22T02:46:00" without a zone is UTC on the backend.
  s = s.replace(' ', 'T').replace(/(\.\d{3})\d+/, '$1')
  if (!/[zZ]|[+-]\d\d:?\d\d$/.test(s)) s += 'Z'
  const d = new Date(s)
  return isNaN(d) ? null : d
}

const pad = (n) => String(n).padStart(2, '0')

/** Absolute local time: "Oct 22, 2026, 10:46 PM" (seconds with {seconds: true}). */
export function formatAbsolute(ts, { seconds = false } = {}) {
  const d = toDate(ts)
  if (!d) return ''
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', ...(seconds ? { second: '2-digit' } : {}),
  })
}

/** The hover text: absolute local time with seconds and the time zone. */
export function formatTitle(ts) {
  const d = toDate(ts)
  if (!d) return ''
  return d.toLocaleString(undefined, {
    year: 'numeric', month: 'short', day: 'numeric',
    hour: 'numeric', minute: '2-digit', second: '2-digit', timeZoneName: 'short',
  })
}

/** "just now", "5 min ago", "2 h ago", "yesterday 9:12 PM", else the absolute date. */
export function formatRelative(ts, now = new Date()) {
  const d = toDate(ts)
  if (!d) return ''
  const diff = (now - d) / 1000
  if (diff < 0) {
    // future (scheduled runs): "in 5 min", "in 3 h", else absolute
    const f = -diff
    if (f < 60) return 'in under a minute'
    if (f < 3600) return `in ${Math.round(f / 60)} min`
    if (f < 86400) return `in ${Math.round(f / 3600)} h`
    return formatAbsolute(d)
  }
  if (diff < 45) return 'just now'
  if (diff < 3600) return `${Math.max(1, Math.round(diff / 60))} min ago`
  if (diff < 6 * 3600) return `${Math.round(diff / 3600)} h ago`
  const today = new Date(now); today.setHours(0, 0, 0, 0)
  const time = d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
  if (d >= today) return `today ${time}`
  const yest = new Date(today); yest.setDate(yest.getDate() - 1)
  if (d >= yest) return `yesterday ${time}`
  return formatAbsolute(d)
}

/** Default: relative when recent, absolute otherwise. Use with formatTitle() as the hover. */
export function formatTimestamp(ts, { relative = true, seconds = false } = {}) {
  return relative ? formatRelative(ts) : formatAbsolute(ts, { seconds })
}

/** Short clock time for chat bubbles: "10:46 PM". */
export function formatClock(ts) {
  const d = toDate(ts)
  if (!d) return ''
  return d.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' })
}

export const _test = { pad }
