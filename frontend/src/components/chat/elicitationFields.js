// MCP elicitation forms (spec 2026-07-28): the server's `requestedSchema` is
// a flat object of primitive fields. These helpers turn it into form fields,
// coerce the typed input back into JSON values and validate before sending
// (the backend validates again; this is for inline feedback).

const INPUT_TYPES = { email: 'email', uri: 'url', date: 'date', 'date-time': 'datetime-local' }

const constOptions = (list) =>
  Array.isArray(list) && list.length > 0 && list.every((o) => o && typeof o === 'object' && 'const' in o)
    ? list.map((o) => ({ value: o.const, title: o.title ?? String(o.const) }))
    : null

const enumOptions = (list) =>
  Array.isArray(list) ? list.map((v) => ({ value: v, title: String(v) })) : null

// One field descriptor per schema property, in schema order.
export function fieldsFromSchema(schema) {
  const props = (schema && schema.properties) || {}
  const required = new Set(Array.isArray(schema?.required) ? schema.required : [])
  return Object.entries(props).map(([name, prop]) => {
    const p = prop && typeof prop === 'object' ? prop : {}
    const base = {
      name,
      title: p.title || name,
      description: p.description || '',
      required: required.has(name),
      default: p.default,
    }
    const single = constOptions(p.oneOf) || enumOptions(p.enum)
    if (single) return { ...base, kind: 'select', options: single }
    if (p.type === 'array') {
      const multi = constOptions(p.items?.anyOf) || enumOptions(p.items?.enum)
      if (multi) return { ...base, kind: 'multiselect', options: multi, minItems: p.minItems, maxItems: p.maxItems }
    }
    if (p.type === 'boolean') return { ...base, kind: 'boolean' }
    if (p.type === 'number' || p.type === 'integer') {
      return { ...base, kind: 'number', integer: p.type === 'integer', min: p.minimum, max: p.maximum }
    }
    if (p.type === 'string') {
      return {
        ...base, kind: 'text', inputType: INPUT_TYPES[p.format] || 'text', format: p.format,
        minLength: p.minLength, maxLength: p.maxLength,
      }
    }
    // Outside the spec's subset: a free-text JSON box, sent as written.
    return { ...base, kind: 'json' }
  })
}

// Initial form values: defaults where the schema gives them.
export function initialValues(fields) {
  const out = {}
  for (const f of fields) {
    if (f.default !== undefined) out[f.name] = f.default
    else if (f.kind === 'boolean') out[f.name] = false
    else if (f.kind === 'multiselect') out[f.name] = []
    else out[f.name] = ''
  }
  return out
}

// Form values -> the `content` object: typed, empty optional fields omitted.
export function contentFromValues(fields, values) {
  const content = {}
  for (const f of fields) {
    const v = values[f.name]
    if (v === '' || v === undefined || v === null) continue
    if (f.kind === 'number') {
      const n = typeof v === 'number' ? v : Number(v)
      if (!Number.isNaN(n)) content[f.name] = n
      continue
    }
    if (f.kind === 'json') {
      try { content[f.name] = JSON.parse(v) } catch { content[f.name] = v }
      continue
    }
    if (f.kind === 'multiselect' && Array.isArray(v) && v.length === 0) continue
    content[f.name] = v
  }
  return content
}

const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/

// Client-side check mirroring backend/mcp_client/elicitation.validate_content.
export function validateValues(fields, values) {
  const errors = {}
  for (const f of fields) {
    const raw = values[f.name]
    const empty = raw === '' || raw === undefined || raw === null || (Array.isArray(raw) && raw.length === 0)
    if (empty) {
      if (f.required) errors[f.name] = 'Required'
      continue
    }
    if (f.kind === 'number') {
      const n = Number(raw)
      if (Number.isNaN(n)) errors[f.name] = 'Must be a number'
      else if (f.integer && !Number.isInteger(n)) errors[f.name] = 'Must be a whole number'
      else if (f.min !== undefined && n < f.min) errors[f.name] = `Must be at least ${f.min}`
      else if (f.max !== undefined && n > f.max) errors[f.name] = `Must be at most ${f.max}`
    } else if (f.kind === 'text') {
      const s = String(raw)
      if (f.minLength !== undefined && s.length < f.minLength) errors[f.name] = `At least ${f.minLength} characters`
      else if (f.maxLength !== undefined && s.length > f.maxLength) errors[f.name] = `At most ${f.maxLength} characters`
      else if (f.format === 'email' && !EMAIL_RE.test(s)) errors[f.name] = 'Must be an e-mail address'
      else if (f.format === 'uri' && !/^[a-z][a-z0-9+.-]*:/i.test(s)) errors[f.name] = 'Must be a URL'
    } else if (f.kind === 'multiselect') {
      if (f.minItems !== undefined && raw.length < f.minItems) errors[f.name] = `Pick at least ${f.minItems}`
      else if (f.maxItems !== undefined && raw.length > f.maxItems) errors[f.name] = `Pick at most ${f.maxItems}`
    } else if (f.kind === 'json') {
      try { JSON.parse(raw) } catch { errors[f.name] = 'Must be valid JSON' }
    }
  }
  return errors
}

// For URL-mode requests: what to show so the person can judge the link
// (full URL, highlighted host, a warning for punycode / non-https).
export function describeUrl(url) {
  try {
    const u = new URL(url)
    const host = u.hostname
    const warnings = []
    if (host.split('.').some((label) => label.startsWith('xn--'))) warnings.push('This address uses encoded (punycode) characters; look at it closely.')
    if (u.protocol !== 'https:') warnings.push('This link is not HTTPS.')
    if (u.username || u.password) warnings.push('This link embeds a username; the real site is ' + host + '.')
    return { ok: true, host, protocol: u.protocol, href: u.href, warnings }
  } catch {
    return { ok: false, host: '', protocol: '', href: String(url || ''), warnings: ['This is not a valid link.'] }
  }
}
