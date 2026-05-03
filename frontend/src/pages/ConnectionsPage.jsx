import React, { useState, useEffect } from 'react'
import { Github, Plug, Trash2, Plus, RefreshCw, Eye, EyeOff, AlertTriangle } from 'lucide-react'
import { connectionsApi } from '../api/client'
import MCPConnections from '../components/MCPConnections'

const TABS = [
  { id: 'github', label: 'GitHub', icon: Github },
  { id: 'mcp',    label: 'MCP servers', icon: Plug },
]

export default function ConnectionsPage() {
  const [tab, setTab] = useState('github')

  return (
    <div className="h-full flex flex-col">
      <div className="border-b border-gray-800 px-6 pt-4 flex items-center gap-1">
        {TABS.map((t) => {
          const Icon = t.icon
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-4 py-2 text-xs font-medium border-b-2 transition-colors ${
                tab === t.id
                  ? 'border-brand-400 text-brand-300'
                  : 'border-transparent text-gray-500 hover:text-gray-300'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          )
        })}
      </div>
      <div className="flex-1 overflow-hidden">
        {tab === 'github' && <GitHubAccountsTab />}
        {tab === 'mcp'    && <MCPTab />}
      </div>
    </div>
  )
}


function GitHubAccountsTab() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showAdd, setShowAdd] = useState(false)
  const [token, setToken] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [adding, setAdding] = useState(false)

  const refresh = async () => {
    setLoading(true); setError(null)
    try {
      const res = await connectionsApi.list()
      setItems(res.data.connections || [])
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setLoading(false) }
  }
  useEffect(() => { refresh() }, [])

  const addConnection = async () => {
    if (!token.trim()) return
    setAdding(true); setError(null)
    try {
      // System-wide GitHub connection — no repo binding here. Repo selection
      // happens per-project on the chat Repository tab.
      await connectionsApi.create({ token: token.trim() })
      setShowAdd(false); setToken('')
      await refresh()
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setAdding(false) }
  }

  const remove = async (id) => {
    if (!confirm('Remove this account? Any project bindings using it will be cleared.')) return
    await connectionsApi.delete(id)
    await refresh()
  }

  return (
    <div className="h-full overflow-y-auto p-6 max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-2">
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <Github className="w-5 h-5" /> GitHub accounts
        </h2>
        <div className="flex gap-2">
          <button onClick={refresh} className="text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1">
            <RefreshCw className="w-3 h-3" /> Refresh
          </button>
          {!showAdd && (
            <button
              onClick={() => setShowAdd(true)}
              className="text-xs px-3 py-1.5 rounded-md bg-brand-600 hover:bg-brand-500 text-white flex items-center gap-1"
            >
              <Plus className="w-3 h-3" /> Add account
            </button>
          )}
        </div>
      </div>
      <p className="text-xs text-gray-500 mb-4">
        System-wide PATs. Bind a specific repo to a project on the chat
        <span className="text-gray-300"> Repository </span>tab.
      </p>

      {error && (
        <div className="mb-4 p-3 rounded-md border border-red-700 bg-red-950 text-red-300 text-sm flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" /> {error}
        </div>
      )}

      {showAdd && (
        <div className="mb-6 p-4 rounded-lg border border-gray-700 bg-gray-900 space-y-3">
          <div className="text-sm font-semibold">Add GitHub account</div>
          <p className="text-xs text-gray-400">
            Personal Access Token (classic or fine-grained) with{' '}
            <code className="text-brand-300">repo</code> scope. Encrypted in
            the local secrets vault.
          </p>
          <div className="flex gap-2">
            <input
              type={showToken ? 'text' : 'password'}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="github_pat_…"
              className="flex-1 px-3 py-2 rounded-md bg-gray-800 border border-gray-700 text-sm font-mono"
            />
            <button
              onClick={() => setShowToken(!showToken)}
              className="px-2 text-gray-500 hover:text-gray-300"
              type="button"
            >
              {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button
              onClick={() => { setShowAdd(false); setToken('') }}
              className="px-3 py-1.5 text-xs rounded-md text-gray-400 hover:text-gray-200"
            >
              Cancel
            </button>
            <button
              onClick={addConnection}
              disabled={!token.trim() || adding}
              className="px-3 py-1.5 text-xs rounded-md bg-brand-600 hover:bg-brand-500 text-white disabled:opacity-50"
            >
              {adding ? 'Verifying…' : 'Add account'}
            </button>
          </div>
        </div>
      )}

      {loading && <div className="text-xs text-gray-500">Loading…</div>}
      {!loading && items.length === 0 && !showAdd && (
        <div className="text-sm text-gray-500 italic">
          No GitHub accounts connected. Add one to enable repo binding on projects.
        </div>
      )}

      {!loading && items.length > 0 && (
        <div className="space-y-2">
          {items.map((c) => (
            <div key={c.id} className="p-3 rounded-md border border-gray-700 bg-gray-900 flex items-center justify-between">
              <div className="text-sm">
                <div className="font-medium text-gray-200 flex items-center gap-2">
                  <Github className="w-4 h-4 text-brand-400" />
                  {c.account_login ? `@${c.account_login}` : 'GitHub'}
                  {c.full_name && <span className="text-gray-500">· default repo: {c.full_name}</span>}
                  {c.status === 'error' && <AlertTriangle className="w-4 h-4 text-amber-400" />}
                </div>
                <div className="text-xs text-gray-500">
                  added {c.created_at?.slice(0, 10)}
                  {c.last_used_at && ` · last used ${c.last_used_at.slice(0, 10)}`}
                  {c.project_id && ` · legacy per-project (${c.project_id})`}
                </div>
                {c.status === 'error' && c.error_message && (
                  <div className="text-xs text-amber-400 mt-1">{c.error_message}</div>
                )}
              </div>
              <button
                onClick={() => remove(c.id)}
                className="text-gray-500 hover:text-red-400"
                title="Remove"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}


function MCPTab() {
  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-4xl mx-auto">
        <h2 className="text-xl font-semibold flex items-center gap-2 mb-4">
          <Plug className="w-5 h-5" /> MCP servers
        </h2>
        <p className="text-xs text-gray-500 mb-4">
          System-wide MCP server registrations. Toggle which servers a project
          uses on the chat <span className="text-gray-300">Project Settings</span> tab.
        </p>
        <MCPConnections />
      </div>
    </div>
  )
}
