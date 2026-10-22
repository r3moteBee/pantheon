/** Shared page header (UI review #17): title, optional description, right-aligned actions. */
export default function PageHeader({ title, description, actions, children, className = '' }) {
  return (
    <div className={`flex flex-wrap items-start justify-between gap-3 border-b border-gray-800 px-6 py-4 ${className}`}>
      <div className="min-w-0">
        <h1 className="text-lg font-semibold text-gray-100">{title}</h1>
        {description && <p className="mt-0.5 text-sm text-gray-400">{description}</p>}
        {children}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}

/** The one primary button style. */
export const primaryButton =
  'inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg bg-brand-700 px-3 py-1.5 text-sm font-medium text-white hover:bg-brand-600 disabled:opacity-50'
export const secondaryButton =
  'inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg border border-gray-700 bg-gray-800 px-3 py-1.5 text-sm text-gray-200 hover:bg-gray-750 disabled:opacity-50'
