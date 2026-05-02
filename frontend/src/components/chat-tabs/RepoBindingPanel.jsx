import React, { useEffect, useState } from 'react'
import { Github, Check, X, RefreshCw, AlertTriangle } from 'lucide-react'
import { connectionsApi, projectRepoApi } from '../../api/client'

export default function RepoBindingPanel({ projectId }) {
  const [binding, setBinding] = useState(null)
  const [connections, setConnections] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [picking, setPicking] = useState(null)  // connection_id whose repos we're listing
  const [pickingRepos, setPickingRepos] = useState([])

  const refresh = async () => {
    setLoading(true); setError(null)
    try {
      const [b, c] = await Promise.all([
        projectRepoApi.get(projectId),
        connectionsApi.list(),
      ])
      setBinding(b.data?.binding || null)
      setConnections(c.data?.connections || [])
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setLoading(false) }
  }

  useEffect(() => { refresh() }, [projectId])

  const unbind = async () => {
    if (!confirm('Unbind this project from its repo?')) return
    await projectRepoApi.unbind(projectId)
    await refresh()
  }

  const startPicking = (conn) => {
    setPicking(conn.id)
    if (conn.full_name) {
      setPickingRepos([{ full_name: conn.full_name, default_branch: conn.default_branch }])
    } else {
      setPickingRepos([])
    }
  }

  const bindFromConnection = async (conn, repoFullName, defaultBranch) => {
    if (!repoFullName) return
    const [owner, repo] = repoFullName.split('/')
    await projectRepoApi.bind(projectId, {
      connection_id: conn.id, owner, repo, default_branch: defaultBranch || 'main',
    })
    setPicking(null)
    setPickingRepos([])
    await refresh()
  }

  return (
    <div className="h-full overflow-y-auto p-6 max-w-3xl mx-auto">
      <h2 className="text-lg font-semibold flex items-center gap-2 mb-4">
        <Github className="w-5 h-5" /> Repository
        <button onClick={refresh} className="ml-auto text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1">
          <RefreshCw className="w-3 h-3" /> Refresh
        </button>
      </h2>
      {loading && <div className="text-xs text-gray-500">Loading…</div>}
      {error && (
        <div className="mb-4 p-3 rounded-md border border-red-700 bg-red-950 text-red-300 text-sm flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" /> {error}
        </div>
      )}
      {binding ? (
        <div className="p-4 rounded-md border border-brand-700 bg-brand-950">
          <div className="text-sm font-medium text-gray-100">
            Bound to <code className="text-brand-300">{binding.owner}/{binding.repo}</code>
          </div>
          <div className="text-xs text-gray-400 mt-1">
            Branch: {binding.default_branch}
            {binding.account_login && ` · via @${binding.account_login}`}
          </div>
          <div className="flex gap-2 mt-3">
            <a
              href={`https://github.com/${binding.owner}/${binding.repo}`}
              target="_blank" rel="noreferrer"
              className="text-xs px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300"
            >Open on GitHub</a>
            <button onClick={unbind} className="text-xs px-2 py-1 rounded bg-red-900 hover:bg-red-800 text-red-100">
              Unbind
            </button>
          </div>
        </div>
      ) : (
        <div className="p-4 rounded-md border border-gray-700 bg-gray-900 mb-4 text-sm text-gray-300">
          No repo bound to this project. Pick from a connected GitHub account below, or
          <a href="/connections" className="text-brand-400 underline ml-1">add a new connection</a>.
        </div>
      )}

      <h3 className="text-sm font-semibold text-gray-300 mt-6 mb-2">Connected accounts</h3>
      {connections.length === 0 && (
        <div className="text-xs text-gray-500">
          No GitHub connections. <a href="/connections" className="text-brand-400 underline">Add one</a> to bind a repo.
        </div>
      )}
      {connections.map((c) => (
        <div key={c.id} className="p-3 mb-2 rounded-md border border-gray-800 bg-gray-900">
          <div className="flex items-center justify-between">
            <div>
              <div className="text-sm text-gray-200">
                {c.account_login ? `@${c.account_login}` : 'GitHub'}
                {c.full_name && <span className="text-gray-500"> · {c.full_name}</span>}
              </div>
              <div className="text-[10px] text-gray-500">added {c.created_at?.slice(0,10)}</div>
            </div>
            {picking === c.id ? (
              <button onClick={() => { setPicking(null); setPickingRepos([]) }} className="text-xs text-gray-400 hover:text-gray-200">
                Cancel
              </button>
            ) : (
              <button
                onClick={() => startPicking(c)}
                className="text-xs px-2 py-1 rounded bg-brand-600 hover:bg-brand-500 text-white"
              >
                Use this account
              </button>
            )}
          </div>
          {picking === c.id && (
            <div className="mt-2 border-t border-gray-800 pt-2 space-y-1">
              {pickingRepos.length === 0 ? (
                <div className="text-xs text-gray-500 italic">
                  This connection has no default repo.{' '}
                  <a href="/connections" className="text-brand-400 underline">Edit it</a> to add one.
                </div>
              ) : (
                pickingRepos.map((r) => (
                  <button
                    key={r.full_name}
                    onClick={() => bindFromConnection(c, r.full_name, r.default_branch)}
                    className="w-full text-left text-xs px-2 py-1 rounded hover:bg-gray-800 flex items-center gap-2"
                  >
                    <Check className="w-3 h-3 text-brand-400" />
                    <span>{r.full_name}</span>
                    <span className="text-gray-500 ml-auto">{r.default_branch}</span>
                  </button>
                ))
              )}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
