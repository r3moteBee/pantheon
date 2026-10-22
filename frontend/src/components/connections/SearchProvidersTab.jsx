import React, { useState, useEffect } from 'react'
import { Save, ArrowUp, ArrowDown, Copy, Trash2 } from 'lucide-react'
import { useStore } from '../../store'
import { settingsApi } from '../../api/client'
import HelpDrawer from '../help/HelpDrawer'
import Time from '../Time'
import { primaryButton, secondaryButton } from '../PageHeader'

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
    const p = providers[idx]
    if (!confirm(`Remove the "${p.name || `#${idx + 1}`}" provider from the chain? (Takes effect when you save.)`)) return
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

  const addPreset = (type) => {
    const presets = {
      brave: { name: 'brave', type: 'brave', url: 'https://api.search.brave.com/res/v1/web/search', api_key_vault_key: 'brave_api_key' },
      tavily: { name: 'tavily', type: 'tavily', url: 'https://api.tavily.com/search', api_key_vault_key: 'tavily_api_key' },
      google: { name: 'google', type: 'google', url: 'https://www.googleapis.com/customsearch/v1?cx=YOUR_CX_ID', api_key_vault_key: 'google_api_key' },
      bing: { name: 'bing', type: 'bing', url: 'https://api.bingmicrosoft.com/v7.0/search', api_key_vault_key: 'bing_api_key' },
      wikipedia: { name: 'wikipedia', type: 'wikipedia', url: 'https://en.wikipedia.org/w/api.php', api_key_vault_key: '' },
      searxng: { name: 'searxng', type: 'searxng', url: 'http://localhost:8888', api_key_vault_key: '' },
      ddg: { name: 'ddg', type: 'ddg', url: '', api_key_vault_key: '' },
      generic: { name: 'generic', type: 'generic', url: '', api_key_vault_key: '' }
    }
    const selected = presets[type]
    if (!selected) return

    setProviders([
      ...providers,
      {
        name: uniqueName(selected.name),
        type: selected.type,
        url: selected.url,
        api_key_vault_key: selected.api_key_vault_key,
        daily_limit: 0,
        monthly_limit: 0,
        rps: 0,
        enabled: true
      }
    ])
    addNotification({ type: 'success', message: `Added ${selected.name} search provider preset` })
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
    // Rendered inside the Connections page, which provides the page header
    // (title), the scroll container, the gutter and the max width for every
    // tab (UI review N1) - same inner layout as MCPConnections: a plain block,
    // no own padding, max-width or page title.
    <div className="space-y-4">
      <HelpDrawer title='About the search provider chain' storageKey='help.search-providers'>
        <p className='text-xs text-gray-400 mb-3'>
          Click a <strong>provider type on the left</strong> (or the <strong>+ Add</strong> button on the right) to append it to the search chain.
          Pantheon's <code className='text-sky-200'>web_search</code> tool
          tries providers <strong>top-to-bottom</strong> and falls through to
          the next one on any error, empty result set, or exhausted quota /
          rate limit. Quotas are tracked locally per provider — reset them at
          the start of a new billing cycle.
        </p>
        <table className='w-full text-xs mb-3'>
          <thead className='text-gray-400'>
            <tr>
              <th className='text-left font-normal pb-1 pr-3'>Type</th>
              <th className='text-left font-normal pb-1 pr-3'>Needs key?</th>
              <th className='text-left font-normal pb-1 pr-3'>Notes</th>
              <th className='text-right font-normal pb-1'>Action</th>
            </tr>
          </thead>
          <tbody className='text-gray-300'>
            <tr className='border-t border-sky-900/40'>
              <td className='py-1.5 pr-3 align-top'>
                <button
                  type='button'
                  onClick={() => addPreset('brave')}
                  className='text-left text-brand-400 hover:underline focus:underline font-semibold'
                  title='Pre-fill and add Brave preset to chain'
                >
                  Brave
                </button>
              </td>
              <td className='py-1.5 pr-3 align-top'>Yes</td>
              <td className='py-1.5 pr-3 align-top'>Best result quality. Free tier at <a href='https://api.search.brave.com/app/keys' target='_blank' rel='noopener noreferrer' className='text-brand-400 hover:underline'>api.search.brave.com</a>.</td>
              <td className='py-1.5 text-right align-top'>
                <button onClick={() => addPreset('brave')} className='text-brand-400 hover:text-brand-300 hover:underline'>+ Add</button>
              </td>
            </tr>
            <tr className='border-t border-sky-900/40'>
              <td className='py-1.5 pr-3 align-top'>
                <button
                  type='button'
                  onClick={() => addPreset('tavily')}
                  className='text-left text-brand-400 hover:underline focus:underline font-semibold'
                  title='Pre-fill and add Tavily preset to chain'
                >
                  Tavily
                </button>
              </td>
              <td className='py-1.5 pr-3 align-top'>Yes</td>
              <td className='py-1.5 pr-3 align-top'>Agent-optimized web search. Free and paid tiers at <a href='https://tavily.com' target='_blank' rel='noopener noreferrer' className='text-brand-400 hover:underline'>tavily.com</a>.</td>
              <td className='py-1.5 text-right align-top'>
                <button onClick={() => addPreset('tavily')} className='text-brand-400 hover:text-brand-300 hover:underline'>+ Add</button>
              </td>
            </tr>
            <tr className='border-t border-sky-900/40'>
              <td className='py-1.5 pr-3 align-top'>
                <button
                  type='button'
                  onClick={() => addPreset('google')}
                  className='text-left text-brand-400 hover:underline focus:underline font-semibold'
                  title='Pre-fill and add Google Custom Search preset to chain'
                >
                  Google
                </button>
              </td>
              <td className='py-1.5 pr-3 align-top'>Yes</td>
              <td className='py-1.5 pr-3 align-top'>Custom Search JSON API. Needs a key and Custom Search Engine ID (CX) parameter in the URL query string.</td>
              <td className='py-1.5 text-right align-top'>
                <button onClick={() => addPreset('google')} className='text-brand-400 hover:text-brand-300 hover:underline'>+ Add</button>
              </td>
            </tr>
            <tr className='border-t border-sky-900/40'>
              <td className='py-1.5 pr-3 align-top'>
                <button
                  type='button'
                  onClick={() => addPreset('bing')}
                  className='text-left text-brand-400 hover:underline focus:underline font-semibold'
                  title='Pre-fill and add Bing Search preset to chain'
                >
                  Bing
                </button>
              </td>
              <td className='py-1.5 pr-3 align-top'>Yes</td>
              <td className='py-1.5 pr-3 align-top'>Bing Search API. Requires Ocp-Apim-Subscription-Key credentials.</td>
              <td className='py-1.5 text-right align-top'>
                <button onClick={() => addPreset('bing')} className='text-brand-400 hover:text-brand-300 hover:underline'>+ Add</button>
              </td>
            </tr>
            <tr className='border-t border-sky-900/40'>
              <td className='py-1.5 pr-3 align-top'>
                <button
                  type='button'
                  onClick={() => addPreset('wikipedia')}
                  className='text-left text-brand-400 hover:underline focus:underline font-semibold'
                  title='Pre-fill and add Wikipedia preset to chain'
                >
                  Wikipedia
                </button>
              </td>
              <td className='py-1.5 pr-3 align-top'>No</td>
              <td className='py-1.5 pr-3 align-top'>Factual encyclopedic lookup. Wikipedia MediaWiki search API.</td>
              <td className='py-1.5 text-right align-top'>
                <button onClick={() => addPreset('wikipedia')} className='text-brand-400 hover:text-brand-300 hover:underline'>+ Add</button>
              </td>
            </tr>
            <tr className='border-t border-sky-900/40'>
              <td className='py-1.5 pr-3 align-top'>
                <button
                  type='button'
                  onClick={() => addPreset('searxng')}
                  className='text-left text-brand-400 hover:underline focus:underline font-semibold'
                  title='Pre-fill and add SearXNG preset to chain'
                >
                  SearXNG
                </button>
              </td>
              <td className='py-1.5 pr-3 align-top'>No</td>
              <td className='py-1.5 pr-3 align-top'>Self-hosted meta-search. Point URL at your instance.</td>
              <td className='py-1.5 text-right align-top'>
                <button onClick={() => addPreset('searxng')} className='text-brand-400 hover:text-brand-300 hover:underline'>+ Add</button>
              </td>
            </tr>
            <tr className='border-t border-sky-900/40'>
              <td className='py-1.5 pr-3 align-top'>
                <button
                  type='button'
                  onClick={() => addPreset('ddg')}
                  className='text-left text-brand-400 hover:underline focus:underline font-semibold'
                  title='Pre-fill and add DuckDuckGo preset to chain'
                >
                  DuckDuckGo
                </button>
              </td>
              <td className='py-1.5 pr-3 align-top'>No</td>
              <td className='py-1.5 pr-3 align-top'>Free fallback. Lower quality, no quota tracking needed.</td>
              <td className='py-1.5 text-right align-top'>
                <button onClick={() => addPreset('ddg')} className='text-brand-400 hover:text-brand-300 hover:underline'>+ Add</button>
              </td>
            </tr>
            <tr className='border-t border-sky-900/40'>
              <td className='py-1.5 pr-3 align-top'>
                <button
                  type='button'
                  onClick={() => addPreset('generic')}
                  className='text-left text-brand-400 hover:underline focus:underline font-semibold'
                  title='Pre-fill and add Generic JSON preset to chain'
                >
                  Generic JSON
                </button>
              </td>
              <td className='py-1.5 pr-3 align-top'>Optional</td>
              <td className='py-1.5 pr-3 align-top'>Any HTTP endpoint returning a results array. URL + optional bearer key.</td>
              <td className='py-1.5 text-right align-top'>
                <button onClick={() => addPreset('generic')} className='text-brand-400 hover:text-brand-300 hover:underline'>+ Add</button>
              </td>
            </tr>
          </tbody>
        </table>
        <p className='text-xs text-gray-400 mb-3'>
          API keys are stored in the local vault keyed by the
          <code className='text-sky-200'> api_key vault key </code>
          field. The <strong>Test</strong> panel at the bottom lets you fire a
          real query and see which provider answered (plus a fallthrough
          trace if earlier ones failed).
        </p>
        <div className='text-xs border-t border-sky-900/40 pt-3 text-gray-300'>
          <strong>Note on Brave Search vs. MCP:</strong> You can configure Brave Search here or as an MCP server. Setting it up here integrates it directly into Pantheon's fast, built-in search fallthrough chain without running external processes. Connecting it as an MCP server, however, grants the agent access to Brave's advanced capabilities like local maps metadata and image search.
        </div>
      </HelpDrawer>

      <p className="text-xs text-gray-400">
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
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-xs text-gray-300 w-6" title="Position in the fallback chain">#{idx + 1}</span>
                <input
                  type="text"
                  value={p.name}
                  onChange={(e) => updateField(idx, 'name', e.target.value)}
                  className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 w-32"
                  placeholder="name"
                  aria-label={`Provider #${idx + 1} name`}
                />
                <select
                  aria-label={`Provider #${idx + 1} type`}
                  value={p.type}
                  onChange={(e) => {
                    const type = e.target.value
                    const defaults = {
                      brave: { url: 'https://api.search.brave.com/res/v1/web/search', api_key_vault_key: 'brave_api_key' },
                      searxng: { url: 'http://localhost:8888', api_key_vault_key: '' },
                      ddg: { url: '', api_key_vault_key: '' },
                      tavily: { url: 'https://api.tavily.com/search', api_key_vault_key: 'tavily_api_key' },
                      google: { url: 'https://www.googleapis.com/customsearch/v1?cx=YOUR_CX_ID', api_key_vault_key: 'google_api_key' },
                      bing: { url: 'https://api.bingmicrosoft.com/v7.0/search', api_key_vault_key: 'bing_api_key' },
                      wikipedia: { url: 'https://en.wikipedia.org/w/api.php', api_key_vault_key: '' },
                      generic: { url: '', api_key_vault_key: '' }
                    }
                    const def = defaults[type] || {}
                    const next = [...providers]
                    next[idx] = { ...next[idx], type, ...def }
                    setProviders(next)
                  }}
                  className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                >
                  <option value="brave">Brave</option>
                  <option value="searxng">SearXNG</option>
                  <option value="ddg">DuckDuckGo</option>
                  <option value="tavily">Tavily</option>
                  <option value="google">Google Custom Search</option>
                  <option value="bing">Bing Search</option>
                  <option value="wikipedia">Wikipedia</option>
                  <option value="generic">Generic JSON</option>
                </select>
                <label className="flex items-center gap-1 text-xs text-gray-300">
                  <input
                    type="checkbox"
                    checked={p.enabled !== false}
                    onChange={(e) => updateField(idx, 'enabled', e.target.checked)}
                  />
                  enabled
                </label>
                <div className="flex-1" />
                <div className="flex items-center gap-0.5">
                  <button onClick={() => moveUp(idx)} disabled={idx === 0}
                          className="p-1.5 rounded text-gray-300 hover:text-white hover:bg-gray-800 disabled:opacity-40"
                          title="Move up (tried earlier)" aria-label={`Move ${p.name || 'provider'} up`}>
                    <ArrowUp className="w-3.5 h-3.5" aria-hidden="true" />
                  </button>
                  <button onClick={() => moveDown(idx)} disabled={idx === providers.length - 1}
                          className="p-1.5 rounded text-gray-300 hover:text-white hover:bg-gray-800 disabled:opacity-40"
                          title="Move down (tried later)" aria-label={`Move ${p.name || 'provider'} down`}>
                    <ArrowDown className="w-3.5 h-3.5" aria-hidden="true" />
                  </button>
                  <button onClick={() => duplicateProvider(idx)}
                          className="p-1.5 rounded text-gray-300 hover:text-white hover:bg-gray-800"
                          title="Duplicate this provider (e.g. a second API key)" aria-label={`Duplicate ${p.name || 'provider'}`}>
                    <Copy className="w-3.5 h-3.5" aria-hidden="true" />
                  </button>
                  <span className="mx-1 h-4 w-px bg-gray-700" aria-hidden="true" />
                  <button onClick={() => removeProvider(idx)}
                          className="p-1.5 rounded text-gray-400 hover:text-red-300 hover:bg-red-950/50"
                          title="Remove from chain" aria-label={`Remove ${p.name || 'provider'}`}>
                    <Trash2 className="w-3.5 h-3.5" aria-hidden="true" />
                  </button>
                </div>
              </div>

              {p.type !== 'ddg' && (
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
                  <div className="sm:col-span-2">
                    <label htmlFor={`sp-url-${idx}`} className="block text-xs text-gray-300 mb-0.5">URL</label>
                    <input
                      id={`sp-url-${idx}`}
                      type="text"
                      value={p.url || ''}
                      onChange={(e) => updateField(idx, 'url', e.target.value)}
                      title={p.url || ''}
                      className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 font-mono"
                    />
                  </div>
                  {p.type !== 'wikipedia' && (
                    <div>
                      <label htmlFor={`sp-vk-${idx}`} className="block text-xs text-gray-300 mb-0.5">Vault key name</label>
                      <input
                        id={`sp-vk-${idx}`}
                        type="text"
                        value={p.api_key_vault_key || ''}
                        onChange={(e) => updateField(idx, 'api_key_vault_key', e.target.value)}
                        placeholder="e.g. brave_api_key"
                        className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 font-mono"
                      />
                    </div>
                  )}
                  {p.type !== 'wikipedia' && (
                    <div>
                      <label htmlFor={`sp-key-${idx}`} className="block text-xs text-gray-300 mb-0.5">
                        API key {u.api_key_set && <span className="text-green-300">(saved)</span>}
                      </label>
                      <input
                        id={`sp-key-${idx}`}
                        type="password"
                        value={p.api_key || ''}
                        onChange={(e) => updateField(idx, 'api_key', e.target.value)}
                        placeholder={u.api_key_set ? 'leave blank to keep the saved key' : 'paste to store in the vault'}
                        className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                      />
                    </div>
                  )}
                </div>
              )}

              <div className="grid grid-cols-3 gap-2">
                <p className="col-span-3 -mb-1 text-xs text-gray-400">Limits: 0 = unlimited.</p>
                <div>
                  <label htmlFor={`sp-d-${idx}`} className="block text-xs text-gray-300 mb-0.5">Daily limit</label>
                  <input
                    id={`sp-d-${idx}`}
                    type="number"
                    min={0}
                    value={p.daily_limit || 0}
                    onChange={(e) => updateField(idx, 'daily_limit', e.target.value)}
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                  />
                </div>
                <div>
                  <label htmlFor={`sp-m-${idx}`} className="block text-xs text-gray-300 mb-0.5">Monthly limit</label>
                  <input
                    id={`sp-m-${idx}`}
                    type="number"
                    min={0}
                    value={p.monthly_limit || 0}
                    onChange={(e) => updateField(idx, 'monthly_limit', e.target.value)}
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                  />
                </div>
                <div>
                  <label htmlFor={`sp-r-${idx}`} className="block text-xs text-gray-300 mb-0.5">Requests / sec</label>
                  <input
                    id={`sp-r-${idx}`}
                    type="number"
                    min={0}
                    step="0.1"
                    value={p.rps || 0}
                    onChange={(e) => updateField(idx, 'rps', e.target.value)}
                    className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100"
                  />
                </div>
              </div>

              <div className="text-xs text-gray-400 space-y-1 pt-1">
                <div className="flex items-center gap-2">
                  <span className="w-16">Daily:</span>
                  <span className="font-mono">{u.daily_used || 0}{p.daily_limit > 0 ? ` / ${p.daily_limit}` : ''}</span>
                  {p.daily_limit > 0 && (
                    <div className="flex-1 h-1 bg-gray-800 rounded overflow-hidden">
                      <div className="h-full bg-brand-500" style={{ width: `${dailyPct}%` }} />
                    </div>
                  )}
                  <button onClick={() => reset(p.name, 'daily')} className="text-xs text-gray-300 hover:text-white underline decoration-dotted" aria-label={`Reset daily usage for ${p.name}`}>reset</button>
                </div>
                <div className="flex items-center gap-2">
                  <span className="w-16">Monthly:</span>
                  <span className="font-mono">{u.monthly_used || 0}{p.monthly_limit > 0 ? ` / ${p.monthly_limit}` : ''}</span>
                  {p.monthly_limit > 0 && (
                    <div className="flex-1 h-1 bg-gray-800 rounded overflow-hidden">
                      <div className="h-full bg-brand-500" style={{ width: `${monthlyPct}%` }} />
                    </div>
                  )}
                  <button onClick={() => reset(p.name, 'monthly')} className="text-xs text-gray-300 hover:text-white underline decoration-dotted" aria-label={`Reset monthly usage for ${p.name}`}>reset</button>
                </div>
                <div className="flex flex-wrap items-center gap-3 text-gray-400">
                  <span className={u.errors > 0 ? 'rounded bg-red-950/60 px-1.5 text-red-300' : ''}
                        title={u.errors > 0 ? 'Calls to this provider that errored (the chain fell through to the next one)' : undefined}>
                    errors: {u.errors || 0}
                  </span>
                  <span>skipped: {u.skipped || 0}</span>
                  {u.last_used && <span>last: <Time value={u.last_used} /></span>}
                </div>
                {u.remote && (
                  <div className="mt-1 px-2 py-1 rounded bg-gray-800/60 border border-gray-700/60 text-xs text-gray-400 space-y-0.5">
                    <div className="text-gray-300 font-semibold uppercase tracking-wide">Brave-reported</div>
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
                      <div className="text-gray-400">captured <Time value={u.remote.captured_at} /></div>
                    )}
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>

      <div className="flex flex-wrap gap-2">
        <button onClick={addProvider} className={secondaryButton}>
          + Add provider
        </button>
        <button onClick={save} disabled={loading} className={primaryButton}>
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
              <span className="text-gray-400">Answered by:</span>
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
              <div className="text-xs text-gray-400">
                <div className="mb-1">Fallthrough trace:</div>
                <ul className="space-y-0.5 ml-2">
                  {testResult.fallthrough.map((line, i) => (
                    <li key={i} className="font-mono text-amber-300">• {line}</li>
                  ))}
                </ul>
              </div>
            )}

            <div>
              <div className="text-xs uppercase text-gray-400 mb-1">Result body</div>
              <pre className="text-xs text-gray-300 bg-black/40 border border-gray-800 rounded p-2 max-h-64 overflow-auto whitespace-pre-wrap font-mono">
                {testResult.body || '(empty)'}
              </pre>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
