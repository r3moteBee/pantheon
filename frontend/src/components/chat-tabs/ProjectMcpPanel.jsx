import React, { useEffect, useState } from 'react'
import { Plug, RefreshCw } from 'lucide-react'
import { projectMcpApi } from '../../api/client'

export default function ProjectMcpPanel({ projectId }) {
  const [servers, setServers] = useState([])
  const [loading, setLoading] = useState(true)

  const refresh = async () => {
    setLoading(true)
    try {
      const res = await projectMcpApi.list(projectId)
      setServers(res.data?.servers || [])
    } finally { setLoading(false) }
  }

  useEffect(() => { refresh() }, [projectId])

  const toggle = async (sv) => {
    await projectMcpApi.set(projectId, sv.server_id, !sv.enabled)
    setServers((xs) => xs.map((x) => x.server_id === sv.server_id ? { ...x, enabled: !x.enabled } : x))
  }

  return (
    <div className="h-full overflow-y-auto p-6 max-w-2xl mx-auto">
      <h2 className="text-lg font-semibold flex items-center gap-2 mb-4">
        <Plug className="w-5 h-5" /> MCP servers for this project
        <button onClick={refresh} className="ml-auto text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1">
          <RefreshCw className="w-3 h-3" /> Refresh
        </button>
      </h2>
      <p className="text-xs text-gray-500 mb-6">
        Per-project server enablement. Servers default to enabled.
        Manage server registrations themselves in <a className="text-brand-400 underline" href="/settings">Settings</a>.
      </p>
      {loading && <div className="text-xs text-gray-500">Loading…</div>}
      {!loading && servers.length === 0 && (
        <div className="text-sm text-gray-500 italic">No MCP servers connected.</div>
      )}
      <div className="space-y-2">
        {servers.map((sv) => (
          <div key={sv.server_id} className="p-3 rounded border border-gray-800 bg-gray-900 flex items-center justify-between">
            <div>
              <div className="text-sm font-medium text-gray-200">{sv.name || sv.server_id}</div>
              <div className="text-[10px] text-gray-500 font-mono">{sv.server_id}</div>
            </div>
            <button
              onClick={() => toggle(sv)}
              className={`relative inline-flex h-5 w-9 items-center rounded-full transition-colors ${
                sv.enabled ? 'bg-brand-600' : 'bg-gray-700'
              }`}
            >
              <span className={`inline-block h-4 w-4 transform rounded-full bg-white transition ${
                sv.enabled ? 'translate-x-4' : 'translate-x-1'
              }`} />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}
