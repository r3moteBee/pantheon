import React, { useState, useEffect } from 'react'
import { Eye, EyeOff, Check, RefreshCw, RotateCw, Shield, AlertTriangle, Terminal, Copy } from 'lucide-react'
import { useStore } from '../../store'
import { systemApi } from '../../api/client'

export default function SystemUpdate() {
  const [status, setStatus] = useState('checking') // 'checking', 'idle', 'updating', 'success', 'error'
  const [updateInfo, setUpdateInfo] = useState(null)
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [confirmChecked, setConfirmChecked] = useState(false)
  const [error, setError] = useState(null)
  const [reconnecting, setReconnecting] = useState(false)
  const [reconnectAttempts, setReconnectAttempts] = useState(0)
  const addNotification = useStore((s) => s.addNotification)

  const check = async () => {
    setStatus('checking')
    setError(null)
    try {
      const res = await systemApi.checkUpdate()
      setUpdateInfo(res.data)
      setStatus('idle')
    } catch (err) {
      setError(err.message || 'Failed to check for updates.')
      setStatus('error')
    }
  }

  useEffect(() => {
    check()
  }, [])

  const pollServer = (startTime) => {
    let attempts = 0
    const interval = setInterval(async () => {
      attempts++
      setReconnectAttempts(attempts)
      try {
        await systemApi.sandboxHealth()
        clearInterval(interval)
        setReconnecting(false)
        setStatus('success')
        addNotification({ type: 'success', message: 'Pantheon updated and server restarted successfully!' })
      } catch (e) {
        if (attempts > 60) {
          clearInterval(interval)
          setReconnecting(false)
          setError('Update initiated, but backend failed to reconnect. Please refresh the page manually or check server logs.')
          setStatus('error')
        }
      }
    }, 3000)
  }

  const handleUpdate = async () => {
    setError(null)
    setStatus('updating')
    try {
      const payload = {}
      if (updateInfo?.auth_enabled) {
        payload.password = password
      } else {
        payload.confirm = confirmChecked
      }

      const res = await systemApi.executeUpdate(payload)
      
      if (res.data?.success) {
        if (res.data?.mode === 'docker') {
          setUpdateInfo(prev => ({
            ...prev,
            dockerInstructions: res.data.message,
            isDocker: true
          }))
          setStatus('idle')
        } else {
          setReconnecting(true)
          setReconnectAttempts(0)
          setTimeout(() => {
            pollServer(Date.now())
          }, 5000)
        }
      } else {
        setError(res.data?.message || 'Update failed to initiate.')
        setStatus('idle')
      }
    } catch (err) {
      setError(err.message || 'Failed to execute update.')
      setStatus('idle')
    }
  }

  if (status === 'checking') {
    return (
      <div className="flex flex-col items-center justify-center py-12 space-y-4">
        <RotateCw className="w-8 h-8 text-brand-400 animate-spin" />
        <p className="text-sm text-gray-400 font-medium">Checking for updates...</p>
      </div>
    )
  }

  if (status === 'updating' || reconnecting) {
    return (
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-6 max-w-lg mx-auto text-center space-y-6">
        <div className="relative flex justify-center">
          <RefreshCw className="w-12 h-12 text-brand-400 animate-spin" />
        </div>
        <div className="space-y-2">
          <h3 className="text-lg font-semibold text-gray-100">Updating Pantheon...</h3>
          <p className="text-sm text-gray-400">
            Applying updates, running frontend build, and restarting services. This typically takes 15–30 seconds.
          </p>
        </div>
        <div className="bg-gray-850/40 rounded-lg p-4 border border-gray-800">
          <div className="flex items-center justify-between text-xs text-gray-500">
            <span>Status:</span>
            <span className="text-brand-300 font-medium font-mono">
              {reconnectAttempts > 0 ? `Reconnecting... (Attempt ${reconnectAttempts})` : 'Scheduling restart...'}
            </span>
          </div>
          <div className="mt-3 w-full bg-gray-800 rounded-full h-1.5 overflow-hidden">
            <div 
              className="bg-brand-500 h-1.5 rounded-full transition-all duration-500" 
              style={{ width: `${Math.min(100, (reconnectAttempts / 15) * 100)}%` }}
            />
          </div>
        </div>
      </div>
    )
  }

  if (status === 'success') {
    return (
      <div className="bg-gray-900 border border-green-800/30 rounded-lg p-6 max-w-lg mx-auto text-center space-y-6">
        <div className="flex justify-center">
          <div className="p-3 bg-green-950/80 rounded-full border border-green-700/50">
            <Check className="w-10 h-10 text-green-400" />
          </div>
        </div>
        <div className="space-y-2">
          <h3 className="text-lg font-semibold text-gray-100">Pantheon Up to Date!</h3>
          <p className="text-sm text-gray-400">
            The updater successfully downloaded latest changes, rebuilt dependencies, and restarted the server.
          </p>
        </div>
        <button
          onClick={() => window.location.reload()}
          className="px-6 py-2 bg-green-600 hover:bg-green-700 text-white text-sm font-medium rounded-lg transition-colors"
        >
          Reload Interface
        </button>
      </div>
    )
  }

  const isUpToDate = updateInfo && !updateInfo.update_available
  const isDocker = updateInfo?.isDocker || false

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-semibold text-gray-200 mb-1 flex items-center gap-1.5">
          <RefreshCw className="w-4 h-4 text-brand-400" /> System Updater
        </h3>
        <p className="text-xs text-gray-500">
          Check for updates, pull changes from the git upstream branch, compile assets, and automatically restart backend services.
        </p>
      </div>

      {error && (
        <div className="bg-red-950/30 border border-red-900/50 text-red-300 rounded-lg p-4 text-xs flex gap-2">
          <AlertTriangle className="w-4 h-4 flex-shrink-0 text-red-400 mt-0.5" />
          <div>{error}</div>
        </div>
      )}

      {isUpToDate && !isDocker ? (
        <div className="bg-gray-800/20 border border-gray-700/40 rounded-lg p-6 text-center space-y-4">
          <Check className="w-8 h-8 text-green-400 mx-auto" />
          <div className="space-y-1">
            <p className="text-sm font-medium text-gray-200">You are on the latest version</p>
            <p className="text-xs text-gray-500">{updateInfo?.message || 'Pantheon is up to date.'}</p>
          </div>
          <button
            onClick={check}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 border border-gray-700 rounded text-xs text-gray-300 transition-colors"
          >
            <RotateCw className="w-3 h-3" /> Check Again
          </button>
        </div>
      ) : (
        <div className="space-y-6">
          {updateInfo?.update_available && (
            <div className="bg-amber-950/20 border border-amber-900/40 rounded-lg p-4 space-y-3">
              <div className="flex items-center gap-2 text-amber-400 text-sm font-medium">
                <AlertTriangle className="w-4 h-4" />
                Update Available: {updateInfo.message}
              </div>
              
              {updateInfo.commits && updateInfo.commits.length > 0 && (
                <div className="space-y-2">
                  <span className="text-xs font-semibold text-gray-400">Commits to be applied:</span>
                  <div className="bg-gray-950/80 border border-gray-800 rounded p-3 text-xs font-mono text-gray-300 max-h-40 overflow-y-auto scrollbar-thin space-y-1">
                    {updateInfo.commits.map((commit, idx) => (
                      <div key={idx} className="truncate">
                        {commit}
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {isDocker && updateInfo?.dockerInstructions ? (
            <div className="bg-gray-900 border border-gray-850 rounded-lg p-4 space-y-4">
              <div className="flex items-center gap-2 text-brand-400 text-sm font-semibold">
                <Terminal className="w-4 h-4" /> Docker Environment Detected
              </div>
              <p className="text-xs text-gray-400 leading-relaxed">
                Pantheon is running inside a Docker container. In containerized environments, direct filesystem updates are not possible. Please run these commands in your host terminal to pull and rebuild:
              </p>
              <div className="relative">
                <pre className="bg-gray-950/85 border border-gray-800 rounded p-3 text-xs font-mono text-gray-350 overflow-x-auto leading-relaxed select-all">
                  {updateInfo.dockerInstructions}
                </pre>
                <button
                  onClick={() => {
                    navigator.clipboard.writeText(updateInfo.dockerInstructions)
                    addNotification({ type: 'success', message: 'Commands copied to clipboard!' })
                  }}
                  className="absolute top-2 right-2 p-1.5 bg-gray-800/80 hover:bg-gray-700/80 border border-gray-700 text-gray-300 rounded transition-colors text-[10px] flex items-center gap-1 font-sans"
                >
                  <Copy className="w-3 h-3" /> Copy
                </button>
              </div>
            </div>
          ) : (
            <div className="bg-gray-800/20 border border-gray-700/40 rounded-lg p-4 space-y-4">
              <div className="flex items-start gap-2.5">
                <Shield className="w-5 h-5 text-amber-500 flex-shrink-0 mt-0.5" />
                <div className="space-y-1">
                  <h4 className="text-xs font-semibold text-gray-200">Security Authorization Required</h4>
                  <p className="text-[11px] text-gray-400 leading-relaxed">
                    Executing an update runs bash shell scripts locally to pull code and rebuild assets.
                    {updateInfo?.auth_enabled 
                      ? ' Enter your administration password to proceed.'
                      : ' Please confirm that you wish to execute the update.'
                    }
                  </p>
                </div>
              </div>

              <div className="border-t border-gray-700/50 pt-4">
                {updateInfo?.auth_enabled ? (
                  <div className="space-y-2">
                    <label className="block text-xs font-medium text-gray-400">Admin Password</label>
                    <div className="relative">
                      <input
                        type={showPassword ? 'text' : 'password'}
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                        placeholder="••••••••••••"
                        className="w-full bg-gray-950 border border-gray-700 rounded-lg pl-3 pr-10 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500"
                      />
                      <button
                        type="button"
                        onClick={() => setShowPassword(!showPassword)}
                        className="absolute inset-y-0 right-0 pr-3 flex items-center text-gray-400 hover:text-gray-250 transition-colors"
                      >
                        {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
                      </button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-start gap-2">
                    <input
                      type="checkbox"
                      id="confirm-update"
                      checked={confirmChecked}
                      onChange={(e) => setConfirmChecked(e.target.checked)}
                      className="mt-1 rounded bg-gray-950 border-gray-700 text-brand-500 focus:ring-brand-500 cursor-pointer"
                    />
                    <label htmlFor="confirm-update" className="text-xs text-gray-355 select-none cursor-pointer leading-normal">
                      I understand that executing the update pulls changes from upstream and restarts the server process.
                    </label>
                  </div>
                )}
              </div>

              <div className="flex gap-3 pt-2">
                <button
                  onClick={handleUpdate}
                  disabled={updateInfo?.auth_enabled ? !password : !confirmChecked}
                  className="flex-1 flex items-center justify-center gap-1.5 px-4 py-2 bg-brand-600 hover:bg-brand-700 disabled:opacity-50 text-white text-sm font-medium rounded-lg transition-colors"
                >
                  <RefreshCw className="w-4 h-4" /> Apply Update & Restart
                </button>
                <button
                  onClick={check}
                  className="px-4 py-2 bg-gray-800 hover:bg-gray-750 border border-gray-700 text-gray-300 text-sm font-medium rounded-lg transition-colors"
                >
                  Cancel
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
