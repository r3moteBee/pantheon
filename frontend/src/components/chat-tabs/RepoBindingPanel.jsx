import React, { useEffect, useState } from 'react'
import {
  Github, Check, X, RefreshCw, AlertTriangle, GitBranch, Loader,
} from 'lucide-react'
import { connectionsApi, projectRepoApi } from '../../api/client'

/**
 * Per-project repo binding flow.
 *
 * Step 1: Choose a connected GitHub account
 * Step 2: Live-fetch repos from that account, pick one
 * Step 3: Live-fetch branches for that repo, pick one
 * Step 4: Bind. Backend writes project_repo_bindings row.
 */
export default function RepoBindingPanel({ projectId }) {
  const [binding, setBinding] = useState(null)
  const [connections, setConnections] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  // Picker flow state
  const [picker, setPicker] = useState(null) // { connection, repos?, branches?, ... }

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

  useEffect(() => { refresh(); setPicker(null) }, [projectId])

  const unbind = async () => {
    if (!confirm('Unbind this project from its repo?')) return
    await projectRepoApi.unbind(projectId)
    await refresh()
  }

  const startPicker = async (conn) => {
    setPicker({ connection: conn, loading: true })
    try {
      const res = await connectionsApi.listConnectionRepos(conn.id)
      setPicker({ connection: conn, repos: res.data?.repos || [] })
    } catch (e) {
      setPicker({ connection: conn, error: e?.response?.data?.detail || e.message })
    }
  }

  const pickRepo = async (repo) => {
    const conn = picker.connection
    const [owner, repoName] = repo.full_name.split('/')
    setPicker({ ...picker, repo, branches: null, branchesLoading: true })
    try {
      const res = await connectionsApi.listConnectionBranches(conn.id, owner, repoName)
      setPicker({ ...picker, repo, branches: res.data?.branches || [], selectedBranch: repo.default_branch })
    } catch (e) {
      setPicker({ ...picker, repo, branchesError: e?.response?.data?.detail || e.message,
                  selectedBranch: repo.default_branch })
    }
  }

  const bind = async () => {
    if (!picker?.repo || !picker?.connection) return
    const [owner, repo] = picker.repo.full_name.split('/')
    const branch = picker.selectedBranch || picker.repo.default_branch || 'main'
    try {
      await projectRepoApi.bind(projectId, {
        connection_id: picker.connection.id,
        owner, repo, default_branch: branch,
      })
      setPicker(null)
      await refresh()
    } catch (e) {
      setPicker({ ...picker, bindError: e?.response?.data?.detail || e.message })
    }
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

      {/* Current binding card */}
      {binding ? (
        <div className="p-4 rounded-md border border-brand-700 bg-brand-950 mb-6">
          <div className="text-sm font-medium text-gray-100">
            Bound to <code className="text-brand-300">{binding.owner}/{binding.repo}</code>
          </div>
          <div className="text-xs text-gray-400 mt-1 flex items-center gap-2 flex-wrap">
            <span className="inline-flex items-center gap-1">
              <GitBranch className="w-3 h-3" /> {binding.default_branch}
            </span>
            {binding.account_login && <span>· via @{binding.account_login}</span>}
          </div>
          <div className="flex gap-2 mt-3">
            <a
              href={`https://github.com/${binding.owner}/${binding.repo}/tree/${binding.default_branch}`}
              target="_blank" rel="noreferrer"
              className="text-xs px-2 py-1 rounded bg-gray-800 hover:bg-gray-700 text-gray-300"
            >Open on GitHub</a>
            <button onClick={unbind} className="text-xs px-2 py-1 rounded bg-red-900 hover:bg-red-800 text-red-100">
              Unbind
            </button>
          </div>
        </div>
      ) : (
        <div className="p-4 rounded-md border border-gray-700 bg-gray-900 mb-6 text-sm text-gray-300">
          No repo bound. Pick a GitHub account below, browse its repos, and bind one.
        </div>
      )}

      {/* Connections list */}
      <h3 className="text-sm font-semibold text-gray-300 mb-2">Connected accounts</h3>
      {connections.length === 0 && (
        <div className="text-xs text-gray-500">
          No GitHub accounts connected.{' '}
          <a href="/connections" className="text-brand-400 underline">Add one</a> in Connections.
        </div>
      )}
      {connections.map((c) => {
        const isActivePicker = picker?.connection?.id === c.id
        return (
          <div key={c.id} className="mb-2 rounded-md border border-gray-800 bg-gray-900">
            <div className="p-3 flex items-center justify-between">
              <div>
                <div className="text-sm text-gray-200 flex items-center gap-2">
                  <Github className="w-4 h-4 text-brand-400" />
                  {c.account_login ? `@${c.account_login}` : 'GitHub'}
                </div>
                <div className="text-[10px] text-gray-500">
                  added {c.created_at?.slice(0, 10)}
                </div>
              </div>
              {isActivePicker ? (
                <button onClick={() => setPicker(null)} className="text-xs text-gray-400 hover:text-gray-200">
                  Cancel
                </button>
              ) : (
                <button
                  onClick={() => startPicker(c)}
                  className="text-xs px-2 py-1 rounded bg-brand-600 hover:bg-brand-500 text-white"
                >
                  Browse repos
                </button>
              )}
            </div>

            {/* Inline picker */}
            {isActivePicker && (
              <div className="border-t border-gray-800 p-3 space-y-3">
                {picker.error && (
                  <div className="text-xs text-red-400">{picker.error}</div>
                )}
                {picker.loading && (
                  <div className="text-xs text-gray-500 flex items-center gap-2">
                    <Loader className="w-3 h-3 animate-spin" /> Loading repos…
                  </div>
                )}
                {picker.repos && (
                  <RepoList
                    repos={picker.repos}
                    activeRepo={picker.repo}
                    onPick={pickRepo}
                  />
                )}
                {picker.repo && (
                  <BranchPicker
                    repo={picker.repo}
                    branches={picker.branches}
                    branchesLoading={picker.branchesLoading}
                    branchesError={picker.branchesError}
                    selectedBranch={picker.selectedBranch}
                    onSelectBranch={(b) => setPicker({ ...picker, selectedBranch: b })}
                  />
                )}
                {picker.repo && (
                  <div className="flex items-center justify-end gap-2 pt-2 border-t border-gray-800">
                    {picker.bindError && <span className="text-xs text-red-400 mr-auto">{picker.bindError}</span>}
                    <button
                      onClick={bind}
                      disabled={!picker.selectedBranch}
                      className="text-xs px-3 py-1.5 rounded bg-brand-600 hover:bg-brand-500 text-white disabled:opacity-50"
                    >
                      Bind {picker.repo.full_name} @ {picker.selectedBranch || '?'}
                    </button>
                  </div>
                )}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}


function RepoList({ repos, activeRepo, onPick }) {
  const [filter, setFilter] = useState('')
  const lower = filter.toLowerCase()
  const visible = repos.filter((r) =>
    !filter || r.full_name.toLowerCase().includes(lower)
        || (r.description || '').toLowerCase().includes(lower)
  )
  return (
    <div className="space-y-2">
      <input
        type="text"
        placeholder={`Filter ${repos.length} repos…`}
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        className="w-full px-2 py-1 text-xs bg-gray-800 border border-gray-700 rounded"
      />
      <div className="max-h-72 overflow-y-auto rounded border border-gray-800 divide-y divide-gray-800">
        {visible.length === 0 && (
          <div className="p-3 text-xs text-gray-500 italic">No repos match.</div>
        )}
        {visible.map((r) => (
          <button
            key={r.full_name}
            onClick={() => onPick(r)}
            className={`w-full text-left px-3 py-2 text-xs flex items-center justify-between hover:bg-gray-800 ${
              activeRepo?.full_name === r.full_name ? 'bg-brand-950' : ''
            }`}
          >
            <div>
              <div className="font-medium text-gray-200">{r.full_name}</div>
              {r.description && <div className="text-gray-500 text-[10px] truncate">{r.description}</div>}
            </div>
            <div className="text-gray-500 flex items-center gap-2">
              {r.private && <span className="px-1 bg-gray-800 rounded text-[10px]">private</span>}
              <span className="text-[10px]">{r.default_branch}</span>
              {activeRepo?.full_name === r.full_name && <Check className="w-3 h-3 text-brand-400" />}
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}


function BranchPicker({ repo, branches, branchesLoading, branchesError, selectedBranch, onSelectBranch }) {
  return (
    <div className="space-y-2 border-t border-gray-800 pt-3">
      <div className="text-xs text-gray-400 flex items-center gap-1">
        <GitBranch className="w-3 h-3" /> Branch for <code className="text-brand-300">{repo.full_name}</code>
      </div>
      {branchesLoading && (
        <div className="text-xs text-gray-500 flex items-center gap-2">
          <Loader className="w-3 h-3 animate-spin" /> Loading branches…
        </div>
      )}
      {branchesError && <div className="text-xs text-red-400">{branchesError}</div>}
      {branches && (
        <select
          value={selectedBranch || ''}
          onChange={(e) => onSelectBranch(e.target.value)}
          className="w-full px-2 py-1.5 text-xs bg-gray-800 border border-gray-700 rounded"
        >
          {!selectedBranch && <option value="">— select branch —</option>}
          {branches.map((b) => (
            <option key={b.name} value={b.name}>
              {b.name}{b.protected ? ' (protected)' : ''}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}
