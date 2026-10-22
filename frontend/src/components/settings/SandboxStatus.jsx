import React, { useState, useEffect } from 'react'
import { RefreshCw, Server, AlertTriangle } from 'lucide-react'
import { systemApi } from '../../api/client'

export default function SandboxStatus() {
  const [health, setHealth] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await systemApi.sandboxHealth()
      setHealth(res.data)
    } catch (e) {
      setError(e.message || 'Request failed')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { refresh() }, [])

  const backend = health?.backend || 'unknown'
  const status = health?.status || 'unknown'
  const isFirecracker = backend === 'firecracker'
  // No isolation = something the user should act on (UI review #5), so amber;
  // a healthy Firecracker sandbox is green; anything else neutral.
  const tone = !isFirecracker || status === 'degraded'
    ? 'border-amber-700/60 bg-amber-950/40'
    : status === 'healthy'
      ? 'border-green-800 bg-green-950/40'
      : 'border-gray-700 bg-gray-900'
  const statusBadge = status === 'healthy' && isFirecracker
    ? 'bg-green-900/60 text-green-200'
    : status === 'degraded'
      ? 'bg-amber-900/60 text-amber-200'
      : 'bg-gray-800 text-gray-300'

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Server className="w-4 h-4 text-brand-400" />
          Sandbox
        </h3>
        <button
          onClick={refresh}
          className="text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1 whitespace-nowrap"
        >
          <RefreshCw className="w-3 h-3" />
          Refresh
        </button>
      </div>
      {loading && <div className="text-xs text-gray-400">Loading…</div>}
      {error && <div className="text-xs text-red-400">{error}</div>}
      {!loading && health && (
        <div className={`border rounded-md p-3 text-xs ${tone}`}>
          <div className="flex flex-wrap items-center gap-2 mb-2">
            <span className="font-medium text-gray-200">Backend: {backend}</span>
            {!isFirecracker && (
              <span className="inline-flex items-center gap-1 whitespace-nowrap rounded-full bg-amber-900/70 px-2 py-0.5 font-medium text-amber-100">
                <AlertTriangle className="w-3 h-3" aria-hidden="true" /> No isolation
              </span>
            )}
            <span className={`ml-auto whitespace-nowrap rounded-full px-2 py-0.5 ${statusBadge}`}>
              Status: {status}
            </span>
          </div>
          {!isFirecracker ? (
            <p className="text-gray-200">
              The {backend} sandbox runs agent code directly on this host with no filesystem
              isolation. For real isolation, run{' '}
              <code className="text-amber-200">scripts/setup_firecracker.sh</code>{' '}
              on a Linux + KVM host and set{' '}
              <code className="text-amber-200">PANTHEON_SANDBOX=firecracker</code>.
            </p>
          ) : health.note && (
            <div className="text-gray-300 text-xs mb-2">{health.note}</div>
          )}
          {Array.isArray(health.issues) && health.issues.length > 0 && (
            <div className="mt-2 space-y-1">
              <div className="text-amber-300 font-medium">Issues:</div>
              {health.issues.map((iss, i) => (
                <div key={i} className="text-amber-200 pl-2">• {iss}</div>
              ))}
            </div>
          )}
          {health.kvm_available !== undefined && (
            <div className="mt-2 text-gray-400">
              KVM available: {health.kvm_available ? 'yes' : 'no'}
              {health.arch && ` · arch: ${health.arch}`}
            </div>
          )}
          {Array.isArray(health.rootfs_images) && health.rootfs_images.length > 0 && (
            <div className="mt-2 text-gray-400">
              Rootfs images: {health.rootfs_images.join(', ')}
            </div>
          )}
        </div>
      )}
    </div>
  )
}
