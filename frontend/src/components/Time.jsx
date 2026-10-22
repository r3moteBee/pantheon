import { formatTimestamp, formatTitle, toDate } from '../utils/time'

/** <Time value={ts} /> — the one way to show a timestamp (relative/absolute + hover). */
export default function Time({ value, relative = true, seconds = false, className = '' }) {
  const d = toDate(value)
  if (!d) return null
  return (
    <time dateTime={d.toISOString()} title={formatTitle(d)} className={className}>
      {formatTimestamp(d, { relative, seconds })}
    </time>
  )
}
