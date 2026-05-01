import React, { useState, useEffect } from 'react'
import { Github, Trash2, Plus, RefreshCw, Check, AlertTriangle, Eye, EyeOff } from 'lucide-react'
import { sourcesApi } from '../api/client'
import { useStore } from '../store'

export default function SourcesPage() {
  const activeProject = useStore((s) => s.activeProject)
  const projectId = activeProject?.id || 'default'
  const [connections, setConnections] = useState([])
  const [loading, setLoading] = useState(true)
  const [showAdd, setShowAdd] = useState(false)
  const [error, setError] = useState(null)

  // Add-flow state
  const [token, setToken] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [repos, setRepos] = useState(null)
  const [picking, setPicking] = useState(false)
  const [selectedRepo, setSelectedRepo] = useState(null)
  const [adding, setAdding] = useState(false)

  const refresh = async () => {
    setLoading(true)
    try {
      const res = await sourcesApi.listGitHub(projectId)
      setConnections(res.data.connections || [])
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setLoading(false) }
  }

  useEffect(() => { refresh() }, [projectId])

  const handlePickRepo = async () => {
    setError(null)
    if (!token.trim()) { setError('Paste a GitHub PAT first.'); return }
    setPicking(true)
    try {
      const res = await sourcesApi.listRepos(token.trim())
      setRepos(res.data.repos || [])
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setPicking(false) }
  }

  const handleAdd = async () => {
    if (!selectedRepo || !token.trim()) return
    setAdding(true); setError(null)
    try {
      await sourcesApi.createGitHub(projectId, token.trim(), selectedRepo.full_name, selectedRepo.default_branch)
      setShowAdd(false); setToken(''); setRepos(null); setSelectedRepo(null)
      await refresh()
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setAdding(false) }
  }

  const handleDelete = async (id) => {
    if (!confirm('Remove this connection?')) return
    await sourcesApi.deleteGitHub(id)
    await refresh()
  }

  return (
    <div className="h-full overflow-y-auto p-6 max-w-3xl mx-auto">
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-xl font-semibold flex items-center gap-2">
          <Github className="w-5 h-5" /> Connected Sources
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
              <Plus className="w-3 h-3" /> Add GitHub
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="mb-4 p-3 rounded-md border border-red-700 bg-red-950 text-red-300 text-sm flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" /> {error}
        </div>
      )}

      {showAdd && (
        <div className="mb-6 p-4 rounded-lg border border-gray-700 bg-gray-900 space-y-3">
          <div className="text-sm font-semibold">Add a GitHub repository</div>
          <p className="text-xs text-gray-400">
            Paste a GitHub Personal Access Token (classic or fine-grained) with{' '}
            <code className="text-brand-300">repo</code> scope. The token is encrypted in
            the local secrets vault. Generate one at{' '}
            <code className="text-brand-300">github.com/settings/tokens</code>.
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
            <button
              onClick={handlePickRepo}
              disabled={picking || !token.trim()}
              className="px-3 py-2 rounded-md bg-gray-800 hover:bg-gray-700 text-sm border border-gray-700 disabled:opacity-50"
            >
              {picking ? 'Loading…' : 'List repos'}
            </button>
          </div>

          {repos && (
            <div className="border border-gray-700 rounded-md max-h-72 overflow-y-auto">
              {repos.length === 0 && <div className="p-3 text-xs text-gray-500">No repos visible to this token.</div>}
              {repos.map((r) => (
                <button
                  key={r.full_name}
                  onClick={() => setSelectedRepo(r)}
                  className={`w-full text-left px-3 py-2 border-b border-gray-800 last:border-b-0 hover:bg-gray-800 text-xs flex items-center justify-between ${
                    selectedRepo?.full_name === r.full_name ? 'bg-brand-950' : ''
                  }`}
                >
                  <div>
                    <div className="font-medium text-gray-200">{r.full_name}</div>
                    {r.description && <div className="text-gray-500">{r.description}</div>}
                  </div>
                  <div className="text-gray-500 flex items-center gap-2">
                    {r.private && <span className="px-1 bg-gray-800 rounded">private</span>}
                    <span>{r.default_branch}</span>
                    {selectedRepo?.full_name === r.full_name && <Check className="w-3 h-3 text-brand-400" />}
                  </div>
                </button>
              ))}
            </div>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <button
              onClick={() => { setShowAdd(false); setToken(''); setRepos(null); setSelectedRepo(null) }}
              className="px-3 py-1.5 text-xs rounded-md text-gray-400 hover:text-gray-200"
            >
              Cancel
            </button>
            <button
              onClick={handleAdd}
              disabled={!selectedRepo || adding}
              className="px-3 py-1.5 text-xs rounded-md bg-brand-600 hover:bg-brand-500 text-white disabled:opacity-50"
            >
              {adding ? 'Adding…' : 'Add connection'}
            </button>
          </div>
        </div>
      )}

      {loading && <div className="text-xs text-gray-500">Loading…</div>}
      {!loading && connections.length === 0 && !showAdd && (
        <div className="text-sm text-gray-500 italic">
          No GitHub connections yet for project <span className="text-gray-400">{projectId}</span>. Add one to enable github_* tools in chat.
        </div>
      )}

      {!loading && connections.length > 0 && (
        <div className="space-y-2">
          {connections.map((c) => (
            <div key={c.id} className="p-3 rounded-md border border-gray-700 bg-gray-900 flex items-center justify-between">
              <div className="text-sm">
                <div className="font-medium text-gray-200 flex items-center gap-2">
                  <Github className="w-4 h-4 text-brand-400" />
                  {c.full_name}
                  {c.status === 'error' && <AlertTriangle className="w-4 h-4 text-amber-400" />}
                </div>
                <div className="text-xs text-gray-500">
                  branch: {c.default_branch} · added {c.created_at?.slice(0, 10)}
                  {c.account_login && ` · as @${c.account_login}`}
                  {c.last_used_at && ` · last used ${c.last_used_at.slice(0, 10)}`}
                </div>
                {c.status === 'error' && c.error_message && (
                  <div className="text-xs text-amber-400 mt-1">{c.error_message}</div>
                )}
              </div>
              <button
                onClick={() => handleDelete(c.id)}
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
