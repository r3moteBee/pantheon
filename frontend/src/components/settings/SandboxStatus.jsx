import React, { useState, useEffect } from 'react'
import { RefreshCw, Server } from 'lucide-react'
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
  const tone = isFirecracker && status === 'healthy'
    ? 'border-green-700 bg-green-950'
    : status === 'degraded'
      ? 'border-amber-700 bg-amber-950'
      : 'border-gray-700 bg-gray-900'

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
          <Server className="w-4 h-4 text-brand-400" />
          Sandbox
        </h3>
        <button
          onClick={refresh}
          className="text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1"
        >
          <RefreshCw className="w-3 h-3" />
          Refresh
        </button>
      </div>
      {loading && <div className="text-xs text-gray-500">Loading…</div>}
      {error && <div className="text-xs text-red-400">{error}</div>}
      {!loading && health && (
        <div className={`border rounded-md p-3 text-xs ${tone}`}>
          <div className="flex items-center justify-between mb-2">
            <span className="font-medium text-gray-200">
              Backend: {backend}
            </span>
            <span className="text-gray-400">Status: {status}</span>
          </div>
          {health.note && (
            <div className="text-gray-400 text-xs mb-2">{health.note}</div>
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
          {!isFirecracker && (
            <div className="mt-3 border-t border-gray-700 pt-2 text-gray-300">
              Subprocess sandbox runs code on this host with no filesystem
              isolation. For real isolation, run{' '}
              <code className="text-brand-300">scripts/setup_firecracker.sh</code>{' '}
              on a Linux+KVM host and set{' '}
              <code className="text-brand-300">PANTHEON_SANDBOX=firecracker</code>.
            </div>
          )}
        </div>
      )}
    </div>
  )
}
