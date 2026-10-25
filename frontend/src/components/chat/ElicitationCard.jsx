import React, { useEffect, useMemo, useState } from 'react'
import { AlertTriangle, Check, ExternalLink, MessageSquareWarning, XCircle } from 'lucide-react'
import {
  contentFromValues, describeUrl, fieldsFromSchema, initialValues, validateValues,
} from './elicitationFields'

// An MCP server asked for input mid-tool-call (spec 2026-07-28 elicitation).
// Shown under the streaming reply while the turn waits. Form mode renders
// the server's flat schema as fields; URL mode shows the full link and host
// and opens it only on an explicit click (never prefetched, never clickable
// text anywhere else). Submit = accept, Decline = decline, Dismiss = cancel.
//
// Props: elicitation {elicitationId, connection, tool, round, requests,
// timeoutS, errors?}, onAnswer(responses), onDecline(), onCancel().
export default function ElicitationCard({ elicitation, onAnswer, onDecline, onCancel }) {
  const { connection, tool, requests = [], round = 1, timeoutS, errors: serverErrors } = elicitation
  const fieldsByKey = useMemo(() => {
    const out = {}
    for (const r of requests) if (r.mode !== 'url') out[r.key] = fieldsFromSchema(r.requestedSchema)
    return out
  }, [requests])
  const [values, setValues] = useState(() => {
    const out = {}
    for (const [key, fields] of Object.entries(fieldsByKey)) out[key] = initialValues(fields)
    return out
  })
  const [localErrors, setLocalErrors] = useState({})
  const [opened, setOpened] = useState({})
  const [left, setLeft] = useState(timeoutS || 0)

  useEffect(() => {
    if (!timeoutS) return undefined
    setLeft(timeoutS)
    const t = setInterval(() => setLeft((s) => (s > 0 ? s - 1 : 0)), 1000)
    return () => clearInterval(t)
  }, [timeoutS, elicitation.elicitationId])

  const setValue = (key, name, v) => {
    setValues((prev) => ({ ...prev, [key]: { ...prev[key], [name]: v } }))
    setLocalErrors((prev) => (prev[key]?.[name] ? { ...prev, [key]: { ...prev[key], [name]: undefined } } : prev))
  }

  const submit = (e) => {
    e?.preventDefault?.()
    const errs = {}
    const responses = {}
    for (const r of requests) {
      if (r.mode === 'url') {
        responses[r.key] = { action: 'accept' }
        continue
      }
      const fields = fieldsByKey[r.key]
      const fieldErrors = validateValues(fields, values[r.key] || {})
      if (Object.keys(fieldErrors).length) errs[r.key] = fieldErrors
      else responses[r.key] = { action: 'accept', content: contentFromValues(fields, values[r.key] || {}) }
    }
    if (Object.keys(errs).length) { setLocalErrors(errs); return }
    onAnswer(responses)
  }

  const openUrl = (r) => {
    // The click is the consent: open in a new tab with no opener and no referrer.
    const w = window.open(r.url, '_blank', 'noopener,noreferrer')
    if (w) w.opener = null
    setOpened((prev) => ({ ...prev, [r.key]: true }))
  }

  const errorsFor = (key) => ({ ...(serverErrors?.[key] || {}), ...(localErrors[key] || {}) })
  const urlRequests = requests.filter((r) => r.mode === 'url')
  const allOpened = urlRequests.every((r) => opened[r.key])
  const minutes = Math.floor(left / 60)
  const seconds = String(left % 60).padStart(2, '0')

  return (
    <div className="flex justify-start mb-4" data-testid="elicitation-card">
      <form onSubmit={submit} className="w-full border border-sky-700/60 bg-sky-950/40 rounded-2xl px-4 py-3" noValidate>
        <div className="flex flex-wrap items-center gap-2 mb-2">
          <MessageSquareWarning className="w-4 h-4 text-sky-300" aria-hidden="true" />
          <span className="text-sm font-medium text-sky-200">
            MCP server <span className="font-mono">{connection}</span> needs your input
          </span>
          <span className="text-xs text-gray-400">
            for tool <span className="font-mono">{tool}</span>{round > 1 ? ` · round ${round}` : ''}
          </span>
          {timeoutS ? (
            <span className="ml-auto text-xs text-gray-500" aria-live="off">
              {left > 0 ? `${minutes}:${seconds} left` : 'expired'}
            </span>
          ) : null}
        </div>
        <p className="text-xs text-gray-400 mb-3">
          Your answers go to this server only; the agent is told which fields you answered, not what you typed.
        </p>

        {requests.map((r) => (
          <fieldset key={r.key} className="mb-3 last:mb-0 border-0 p-0 m-0 min-w-0">
            {r.message && <legend className="text-sm text-gray-100 mb-2 whitespace-pre-wrap">{r.message}</legend>}
            {r.mode === 'url'
              ? <UrlRequest request={r} opened={!!opened[r.key]} onOpen={() => openUrl(r)} />
              : <FormFields
                  requestKey={r.key}
                  fields={fieldsByKey[r.key]}
                  values={values[r.key] || {}}
                  errors={errorsFor(r.key)}
                  onChange={(name, v) => setValue(r.key, name, v)}
                />}
            {errorsFor(r.key)[''] && (
              <p className="text-xs text-red-300 mt-1" role="alert">{errorsFor(r.key)['']}</p>
            )}
          </fieldset>
        ))}

        <div className="flex flex-wrap gap-2 mt-3">
          <button
            type="submit"
            disabled={urlRequests.length > 0 && !allOpened}
            title={urlRequests.length > 0 && !allOpened ? 'Open the link first' : undefined}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap bg-sky-700 hover:bg-sky-600 disabled:opacity-50 disabled:hover:bg-sky-700 text-white transition-colors"
          >
            <Check className="w-3 h-3" aria-hidden="true" />
            {urlRequests.length > 0 && requests.length === urlRequests.length ? "I'm done, continue" : 'Submit'}
          </button>
          <button
            type="button"
            onClick={onDecline}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap bg-gray-700 hover:bg-gray-600 text-gray-200 transition-colors"
          >
            <XCircle className="w-3 h-3" aria-hidden="true" />
            Decline
          </button>
          <button
            type="button"
            onClick={onCancel}
            className="px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap text-gray-400 hover:text-gray-200 hover:bg-gray-800 transition-colors"
          >
            Dismiss
          </button>
        </div>
      </form>
    </div>
  )
}

function UrlRequest({ request, opened, onOpen }) {
  const info = describeUrl(request.url)
  return (
    <div className="rounded-lg border border-gray-700 bg-gray-900/60 p-3">
      <div className="text-xs text-gray-400 mb-1">The server wants you to open this link in your browser:</div>
      <div className="font-mono text-xs text-gray-200 break-all select-all" aria-label="Full link">{info.href}</div>
      {info.ok && (
        <div className="text-xs text-gray-400 mt-1">
          Site: <span className="font-semibold text-sky-200">{info.host}</span>
        </div>
      )}
      {info.warnings.map((w) => (
        <div key={w} className="flex items-start gap-1.5 text-xs text-amber-300 mt-1.5" role="alert">
          <AlertTriangle className="w-3.5 h-3.5 shrink-0 mt-0.5" aria-hidden="true" />
          <span>{w}</span>
        </div>
      ))}
      <div className="mt-2 flex items-center gap-2">
        <button
          type="button"
          onClick={onOpen}
          disabled={!info.ok}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium whitespace-nowrap bg-gray-700 hover:bg-gray-600 disabled:opacity-50 text-gray-100 transition-colors"
        >
          <ExternalLink className="w-3 h-3" aria-hidden="true" />
          {opened ? 'Open again' : 'Open link in a new tab'}
        </button>
        {opened && <span className="text-xs text-gray-400">Finish there, then continue below.</span>}
      </div>
    </div>
  )
}

const inputClass = 'w-full rounded-lg bg-gray-900 border border-gray-700 focus:border-sky-500 focus:outline-none px-2.5 py-1.5 text-sm text-gray-100 placeholder-gray-500'

function FormFields({ requestKey, fields, values, errors, onChange }) {
  if (!fields.length) return <p className="text-xs text-gray-500">No details needed, just confirm.</p>
  return (
    <div className="grid gap-3">
      {fields.map((f) => {
        const id = `elicit-${requestKey}-${f.name}`
        const err = errors[f.name]
        const label = (
          <label htmlFor={id} className="block text-xs font-medium text-gray-300 mb-1">
            {f.title}{f.required && <span className="text-sky-300" aria-hidden="true"> *</span>}
            {f.required && <span className="sr-only"> (required)</span>}
          </label>
        )
        const help = f.description && <p className="text-xs text-gray-500 mt-1">{f.description}</p>
        const error = err && <p id={`${id}-err`} className="text-xs text-red-300 mt-1" role="alert">{err}</p>
        const aria = { 'aria-invalid': !!err, 'aria-describedby': err ? `${id}-err` : undefined }
        const v = values[f.name]
        if (f.kind === 'boolean') {
          return (
            <div key={f.name}>
              <label htmlFor={id} className="flex items-center gap-2 text-sm text-gray-200">
                <input id={id} type="checkbox" checked={!!v} onChange={(e) => onChange(f.name, e.target.checked)} className="accent-sky-500" {...aria} />
                {f.title}
              </label>
              {help}{error}
            </div>
          )
        }
        if (f.kind === 'select') {
          return (
            <div key={f.name}>
              {label}
              <select id={id} value={v ?? ''} onChange={(e) => onChange(f.name, e.target.value)} className={inputClass} {...aria}>
                <option value="">{f.required ? 'Choose…' : '(none)'}</option>
                {f.options.map((o) => <option key={String(o.value)} value={o.value}>{o.title}</option>)}
              </select>
              {help}{error}
            </div>
          )
        }
        if (f.kind === 'multiselect') {
          const chosen = Array.isArray(v) ? v : []
          const toggle = (val) => onChange(f.name, chosen.includes(val) ? chosen.filter((x) => x !== val) : [...chosen, val])
          return (
            <div key={f.name} role="group" aria-labelledby={`${id}-lbl`}>
              <div id={`${id}-lbl`} className="text-xs font-medium text-gray-300 mb-1">
                {f.title}{f.required && <span className="text-sky-300"> *</span>}
              </div>
              <div className="flex flex-wrap gap-x-4 gap-y-1">
                {f.options.map((o) => (
                  <label key={String(o.value)} className="flex items-center gap-1.5 text-sm text-gray-200">
                    <input type="checkbox" checked={chosen.includes(o.value)} onChange={() => toggle(o.value)} className="accent-sky-500" />
                    {o.title}
                  </label>
                ))}
              </div>
              {help}{error}
            </div>
          )
        }
        if (f.kind === 'number') {
          return (
            <div key={f.name}>
              {label}
              <input id={id} type="number" inputMode={f.integer ? 'numeric' : 'decimal'} step={f.integer ? 1 : 'any'}
                     min={f.min} max={f.max} value={v ?? ''} onChange={(e) => onChange(f.name, e.target.value)}
                     className={inputClass} {...aria} />
              {help}{error}
            </div>
          )
        }
        if (f.kind === 'json') {
          return (
            <div key={f.name}>
              {label}
              <textarea id={id} rows={3} value={typeof v === 'string' ? v : JSON.stringify(v ?? '', null, 2)}
                        onChange={(e) => onChange(f.name, e.target.value)} placeholder="JSON value"
                        className={`${inputClass} font-mono`} {...aria} />
              {help}{error}
            </div>
          )
        }
        return (
          <div key={f.name}>
            {label}
            <input id={id} type={f.inputType} value={v ?? ''} minLength={f.minLength} maxLength={f.maxLength}
                   onChange={(e) => onChange(f.name, e.target.value)} className={inputClass} {...aria} />
            {help}{error}
          </div>
        )
      })}
    </div>
  )
}
