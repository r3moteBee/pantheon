import { Info, AlertTriangle } from 'lucide-react'

/** Help text vs real warnings (UI review #13): "info" is neutral blue, "warning" amber —
 *  only for something that needs attention. */
export default function InfoBox({ variant = 'info', title, children, className = '' }) {
  const warn = variant === 'warning'
  const Icon = warn ? AlertTriangle : Info
  const tone = warn
    ? 'border-amber-700/60 bg-amber-950/40 text-amber-100'
    : 'border-sky-800/50 bg-sky-950/30 text-gray-200'
  return (
    <div role={warn ? 'alert' : 'note'} className={`flex gap-2 rounded-lg border px-3 py-2.5 text-sm ${tone} ${className}`}>
      <Icon className={`mt-0.5 h-4 w-4 shrink-0 ${warn ? 'text-amber-400' : 'text-sky-400'}`} aria-hidden="true" />
      <div className="min-w-0">
        {title && <div className="font-medium">{title}</div>}
        <div className={title ? 'mt-0.5 text-gray-300' : ''}>{children}</div>
      </div>
    </div>
  )
}
