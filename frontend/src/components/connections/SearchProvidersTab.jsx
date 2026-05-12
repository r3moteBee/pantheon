import React, { useState, useEffect } from 'react'
import { Search, Save } from 'lucide-react'
import { useStore } from '../../store'
import { settingsApi } from '../../api/client'
import HelpDrawer from '../help/HelpDrawer'

export default function SearchProvidersTab() {
  const [providers, setProviders] = useState([])
  const [usage, setUsage] = useState({ providers: [] })
  const [loading, setLoading] = useState(false)
  const [testing, setTesting] = useState(false)
  const [testQuery, setTestQuery] = useState('pantheon search test')
  const [testResult, setTestResult] = useState(null)
  const addNotification = useStore((s) => s.addNotification)

  const load = async () => {
    try {
      const res = await settingsApi.getSearchProviders()
      setProviders(res.data.providers || [])
      setUsage(res.data.usage || { providers: [] })
    } catch (err) {
      addNotification({ type: 'error', message: 'Failed to load search providers' })
    }
  }

  useEffect(() => { load() }, [])

  const updateField = (idx, field, value) => {
    const next = [...providers]
    next[idx] = { ...next[idx], [field]: value }
    setProviders(next)
  }

  const moveUp = (idx) => {
    if (idx === 0) return
    const next = [...providers]
    ;[next[idx - 1], next[idx]] = [next[idx], next[idx - 1]]
    setProviders(next)
  }

  const moveDown = (idx) => {
    if (idx === providers.length - 1) return
    const next = [...providers]
    ;[next[idx], next[idx + 1]] = [next[idx + 1], next[idx]]
    setProviders(next)
  }

  const removeProvider = (idx) => {
    setProviders(providers.filter((_, i) => i !== idx))
  }

  const uniqueName = (base) => {
    const existing = new Set(providers.map((p) => p.name))
    if (!existing.has(base)) return base
    const root = base.replace(/-(\d+)$/, '')
    let i = 2
    while (existing.has(`${root}-${i}`)) i += 1
    return `${root}-${i}`
  }

  const addProvider = () => {
    setProviders([
      ...providers,
      { name: uniqueName(`provider-${providers.length + 1}`), type: 'generic', url: '', api_key_vault_key: '',
        daily_limit: 0, monthly_limit: 0, rps: 0, enabled: true },
    ])
  }

  const duplicateProvider = (idx) => {
    const src = providers[idx]
    const newName = uniqueName(src.name)
    const suffix = newName.slice(src.name.replace(/-(\d+)$/, '').length) || `-${Date.now().toString().slice(-4)}`
    const newKey = src.api_key_vault_key
      ? `${src.api_key_vault_key.replace(/-(\d+)$/, '').replace(/_(\d+)$/, '')}${suffix.replace('-', '_')}`
      : ''
    const copy = {
      ...src,
      name: newName,
      api_key_vault_key: newKey,
      api_key: '',
    }
    const next = [...providers]
    next.splice(idx + 1, 0, copy)
    setProviders(next)
  }

  const save = async () => {
    setLoading(true)
    try {
      const payload = providers.map((p) => {
        const out = { ...p, daily_limit: parseInt(p.daily_limit) || 0,
                      monthly_limit: parseInt(p.monthly_limit) || 0,
                      rps: parseFloat(p.rps) || 0 }
        if (!out.api_key) delete out.api_key
        return out
      })
      await settingsApi.setSearchProviders(payload)
      addNotification({ type: 'success', message: 'Search providers saved' })
      setProviders((prev) => prev.map((p) => ({ ...p, api_key: '' })))
      await load()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  const reset = async (name, period) => {
    try {
      await settingsApi.resetSearchProvider(name, period)
      await load()
      addNotification({ type: 'success', message: `Reset ${period} usage for ${name}` })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const test = async () => {
    setTesting(true)
    setTestResult(null)
    try {
      const res = await settingsApi.testSearchChain(testQuery || 'pantheon search test')
      const raw = res.data.result || ''
      const tagMatch = raw.match(/^\[(searched|cached) via ([^\s\]]+)(?: — fallthrough: ([^\]]+))?\]\n?/)
      let providerUsed = null
      let fallthrough = []
      let body = raw
      if (tagMatch) {
        providerUsed = tagMatch[2]
        if (tagMatch[3]) fallthrough = tagMatch[3].split(';').map((s) => s.trim()).filter(Boolean)
        body = raw.slice(tagMatch[0].length)
      } else if (raw.startsWith('No search results')) {
        providerUsed = 'none'
        body = raw
      }
      setTestResult({ query: res.data.query, providerUsed, fallthrough, body, raw })
      setUsage(res.data.usage || { providers: [] })
      addNotification({
        type: providerUsed && providerUsed !== 'none' ? 'success' : 'error',
        message: providerUsed && providerUsed !== 'none'
          ? `Test ok — answered by ${providerUsed}`
          : 'Test failed — no provider returned results',
      })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
      setTestResult({ query: testQuery, providerUsed: null, fallthrough: [], body: `Error: ${err.message}`, raw: '' })
    }
    setTesting(false)
  }

  const usageFor = (name) => usage.providers?.find((u) => u.name === name) || {}

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto space-y-4">
        <div className="flex items-center gap-2">
          <Search className="w-5 h-5 text-gray-400" />
          <h2 className="text-xl font-semibold">Web search providers</h2>
        </div>

        <HelpDrawer title='About the search provider chain' storageKey='help.search-providers'>
          <p className='text-xs text-gray-400 mb-3'>
            Pantheon's <code className='text-amber-200/80'>web_search</code> tool
            tries providers <strong>top-to-bottom</strong> and falls through to
            the next one on any error, empty result set, or exhausted quota /
            rate limit. Quotas are tracked locally per provider — reset them at
            the start of a new billing cycle.
          </p>
          <table className='w-full text-xs mb-3'>
            <thead className='text-gray-500'>
              <tr>
                <th className='text-left font-normal pb-1 pr-3'>Type</th>
                <th className='text-left font-normal pb-1 pr-3'>Needs key?</th>
                <th className='text-left font-normal pb-1'>Notes</th>
              </tr>
            </thead>
            <tbody className='text-gray-300'>
              <tr className='border-t border-amber-900/30'>
                <td className='py-1.5 pr-3 align-top'>Brave</td>
                <td className='py-1.5 pr-3 align-top'>Yes</td>
                <td className='py-1.5 align-top'>Best result quality. Free tier at <a href='https://api.search.brave.com/app/keys' target='_blank' rel='noopener noreferrer' className='text-brand-400 hover:underline'>api.search.brave.com</a>.</td>
              </tr>
              <tr className='border-t border-amber-900/30'>
                <td className='py-1.5 pr-3 align-top'>SearXNG</td>
                <td className='py-1.5 pr-3 align-top'>No</td>
                <td className='py-1.5 align-top'>Self-hosted meta-search. Point URL at your instance.</td>
              </tr>
              <tr className='border-t border-amber-900/30'>
                <td className='py-1.5 pr-3 align-top'>DuckDuckGo</td>
                <td className='py-1.5 pr-3 align-top'>No</td>
                <td className='py-1.5 align-top'>Free fallback. Lower quality, no quota tracking needed.</td>
              </tr>
              <tr className='border-t border-amber-900/30'>
                <td className='py-1.5 pr-3 align-top'>Generic JSON</td>
                <td className='py-1.5 pr-3 align-top'>Optional</td>
                <td className='py-1.5 align-top'>Any HTTP endpoint returning a results array. URL + optional bearer key.</td>
              </tr>
            </tbody>
          </table>
          <p className='text-xs text-gray-400'>
            API keys are stored in the local vault keyed by the
            <code className='text-amber-200/80'> api_key vault key </code>
            field. The <strong>Test</strong> panel at the bottom lets you fire a
            real query and see which provider answered (plus a fallthrough
            trace if earlier ones failed).
          </p>
        </HelpDrawer>

        <p className="text-xs text-gray-500">
          Providers are tried top-to-bottom. The agent falls through to the next provider on
          any error, empty result set, or exhausted quota / rate limit. Per-provider quotas
          are tracked locally; reset them at the start of a new billing cycle.
        </p>

        <div className="space-y-3">
          {providers.map((p, idx) => {
            const u = usageFor(p.name)
            const monthlyPct = p.monthly_limit > 0 ? Math.min(100, (u.monthly_used || 0) / p.monthly_limit * 100) : 0
            const dailyPct = p.daily_limit > 0 ? Math.min(100, (u.daily_used || 0) / p.daily_limit * 100) : 0
            return (
              <div key={idx} className="bg-gray-900 border border-gray-700 rounded-lg p-3 space-y-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-gray-500 w-6">#{idx + 1}</span>
                  <input
                    type="text"
                    value={p.name}
                    onChange={(e) => updateField(idx, 'name', e.target.value)}
                    className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 w-32"
                    placeholder="name"
                  />
                  <select
                    value={p.type}
                    onChange={(e) => updateField(idx, 'type', e.target.value)}
                    className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                  >
                    <option value="brave">Brave</option>
                    <option value="searxng">SearXNG</option>
                    <option value="ddg">DuckDuckGo</option>
                    <option value="generic">Generic JSON</option>
                  </select>
                  <label className="flex items-center gap-1 text-xs text-gray-400">
                    <input
                      type="checkbox"
                      checked={p.enabled !== false}
                      onChange={(e) => updateField(idx, 'enabled', e.target.checked)}
                    />
                    enabled
                  </label>
                  <div className="flex-1" />
                  <button onClick={() => moveUp(idx)} className="text-xs px-2 py-1 text-gray-400 hover:text-white" title="Move up">↑</button>
                  <button onClick={() => moveDown(idx)} className="text-xs px-2 py-1 text-gray-400 hover:text-white" title="Move down">↓</button>
                  <button onClick={() => duplicateProvider(idx)} className="text-xs px-2 py-1 text-gray-400 hover:text-white" title="Duplicate row">⎘</button>
                  <button onClick={() => removeProvider(idx)} className="text-xs px-2 py-1 text-red-400 hover:text-red-300">remove</button>
                </div>

                {p.type !== 'ddg' && (
                  <div className="grid grid-cols-2 gap-2">
                    <input
                      type="text"
                      value={p.url || ''}
                      onChange={(e) => updateField(idx, 'url', e.target.value)}
                      placeholder="URL"
                      className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                    />
                    <input
                      type="text"
                      value={p.api_key_vault_key || ''}
                      onChange={(e) => updateField(idx, 'api_key_vault_key', e.target.value)}
                      placeholder="api_key vault key (e.g. brave_api_key)"
                      className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                    />
                    <input
                      type="password"
                      value={p.api_key || ''}
                      onChange={(e) => updateField(idx, 'api_key', e.target.value)}
                      placeholder={u.api_key_set ? '(key saved — leave blank to keep)' : 'API key'}
                      className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 col-span-2"
                    />
                  </div>
                )}

                <div className="grid grid-cols-3 gap-2">
                  <div>
                    <label className="block text-[10px] uppercase text-gray-500 mb-0.5">Daily limit</label>
                    <input
                      type="number"
                      value={p.daily_limit || 0}
                      onChange={(e) => updateField(idx, 'daily_limit', e.target.value)}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] uppercase text-gray-500 mb-0.5">Monthly limit</label>
                    <input
                      type="number"
                      value={p.monthly_limit || 0}
                      onChange={(e) => updateField(idx, 'monthly_limit', e.target.value)}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                    />
                  </div>
                  <div>
                    <label className="block text-[10px] uppercase text-gray-500 mb-0.5">Req / sec</label>
                    <input
                      type="number"
                      step="0.1"
                      value={p.rps || 0}
                      onChange={(e) => updateField(idx, 'rps', e.target.value)}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                    />
                  </div>
                </div>

                <div className="text-[11px] text-gray-400 space-y-1 pt-1">
                  <div className="flex items-center gap-2">
                    <span className="w-16">Daily:</span>
                    <span className="font-mono">{u.daily_used || 0}{p.daily_limit > 0 ? ` / ${p.daily_limit}` : ''}</span>
                    {p.daily_limit > 0 && (
                      <div className="flex-1 h-1 bg-gray-800 rounded overflow-hidden">
                        <div className="h-full bg-brand-500" style={{ width: `${dailyPct}%` }} />
                      </div>
                    )}
                    <button onClick={() => reset(p.name, 'daily')} className="text-[10px] text-gray-500 hover:text-gray-300">reset</button>
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="w-16">Monthly:</span>
                    <span className="font-mono">{u.monthly_used || 0}{p.monthly_limit > 0 ? ` / ${p.monthly_limit}` : ''}</span>
                    {p.monthly_limit > 0 && (
                      <div className="flex-1 h-1 bg-gray-800 rounded overflow-hidden">
                        <div className="h-full bg-brand-500" style={{ width: `${monthlyPct}%` }} />
                      </div>
                    )}
                    <button onClick={() => reset(p.name, 'monthly')} className="text-[10px] text-gray-500 hover:text-gray-300">reset</button>
                  </div>
                  <div className="flex items-center gap-3 text-gray-500">
                    <span>errors: {u.errors || 0}</span>
                    <span>skipped: {u.skipped || 0}</span>
                    {u.last_used && <span>last: {new Date(u.last_used).toLocaleString()}</span>}
                  </div>
                  {u.remote && (
                    <div className="mt-1 px-2 py-1 rounded bg-gray-800/60 border border-gray-700/60 text-[10px] text-gray-400 space-y-0.5">
                      <div className="text-amber-400/80 font-semibold uppercase tracking-wide">Brave-reported</div>
                      {u.remote.month_limit != null && (
                        <div>
                          Month: <span className="font-mono text-gray-200">{u.remote.month_used ?? '?'} / {u.remote.month_limit}</span>
                          {u.remote.month_remaining != null && <span> ({u.remote.month_remaining} left)</span>}
                        </div>
                      )}
                      {u.remote.second_limit != null && (
                        <div>
                          This second: <span className="font-mono text-gray-200">{u.remote.second_remaining ?? '?'} / {u.remote.second_limit}</span> remaining
                        </div>
                      )}
                      {u.remote.captured_at && (
                        <div className="text-gray-600">captured {new Date(u.remote.captured_at).toLocaleString()}</div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            )
          })}
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            onClick={addProvider}
            className="px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-xs text-gray-200 rounded border border-gray-700"
          >
            + Add provider
          </button>
          <button
            onClick={save}
            disabled={loading}
            className="flex items-center gap-2 px-4 py-1.5 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded disabled:opacity-50"
          >
            <Save className="w-4 h-4" />
            Save Provider Chain
          </button>
        </div>

        <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 space-y-2">
          <div className="flex items-center gap-2">
            <label className="text-xs text-gray-400 whitespace-nowrap">Test query:</label>
            <input
              type="text"
              value={testQuery}
              onChange={(e) => setTestQuery(e.target.value)}
              placeholder="e.g. latest news on HBM supply"
              className="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
              onKeyDown={(e) => { if (e.key === 'Enter') test() }}
            />
            <button
              onClick={test}
              disabled={testing}
              className="px-3 py-1 bg-gray-800 hover:bg-gray-700 text-sm text-gray-200 rounded border border-gray-700 disabled:opacity-50"
            >
              {testing ? 'Testing…' : 'Run test'}
            </button>
          </div>

          {testResult && (
            <div className="space-y-2 pt-1">
              <div className="flex items-center gap-2 text-xs">
                <span className="text-gray-500">Answered by:</span>
                {testResult.providerUsed && testResult.providerUsed !== 'none' ? (
                  <span className="px-2 py-0.5 rounded bg-green-900/40 border border-green-700 text-green-300 font-mono">
                    {testResult.providerUsed}
                  </span>
                ) : (
                  <span className="px-2 py-0.5 rounded bg-red-900/40 border border-red-700 text-red-300 font-mono">
                    none
                  </span>
                )}
              </div>

              {testResult.fallthrough.length > 0 && (
                <div className="text-xs text-gray-500">
                  <div className="mb-1">Fallthrough trace:</div>
                  <ul className="space-y-0.5 ml-2">
                    {testResult.fallthrough.map((line, i) => (
                      <li key={i} className="font-mono text-amber-400/80">• {line}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div>
                <div className="text-[10px] uppercase text-gray-500 mb-1">Result body</div>
                <pre className="text-[11px] text-gray-300 bg-black/40 border border-gray-800 rounded p-2 max-h-64 overflow-auto whitespace-pre-wrap font-mono">
                  {testResult.body || '(empty)'}
                </pre>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
