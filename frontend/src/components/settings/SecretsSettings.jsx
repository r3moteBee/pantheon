import React, { useState, useEffect } from 'react'
import { Trash2, Plus } from 'lucide-react'
import { useStore } from '../../store'
import { settingsApi } from '../../api/client'

export default function SecretsSettings() {
  const [secrets, setSecrets] = useState([])
  const [newKey, setNewKey] = useState('')
  const [newValue, setNewValue] = useState('')
  const [loading, setLoading] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  useEffect(() => {
    loadSecrets()
  }, [])

  const loadSecrets = async () => {
    setLoading(true)
    try {
      const res = await settingsApi.listSecrets()
      // API returns { keys: ["key1", "key2"], count: N }
      const keys = res.data.keys || []
      setSecrets(keys.map((k) => ({ key: k })))
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  const addSecret = async () => {
    if (!newKey.trim() || !newValue.trim()) return
    // Normalize to lowercase — the backend vault lookups use lowercase keys
    const normalizedKey = newKey.trim().toLowerCase()
    try {
      await settingsApi.setSecret(normalizedKey, newValue)
      setNewKey('')
      setNewValue('')
      addNotification({ type: 'success', message: `Secret "${normalizedKey}" saved` })
      loadSecrets()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const deleteSecret = async (key) => {
    if (!confirm(`Delete secret "${key}"?`)) return
    try {
      await settingsApi.deleteSecret(key)
      addNotification({ type: 'success', message: 'Secret deleted' })
      loadSecrets()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  return (
    <div className="space-y-4">
      <h3 className="text-sm font-semibold text-gray-200">API Secrets & Keys</h3>

      {/* Add new secret */}
      <div className="bg-gray-800 rounded-lg p-4 border border-gray-700">
        <div className="space-y-3">
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-2">Key Name</label>
            <input
              type="text"
              value={newKey}
              onChange={(e) => setNewKey(e.target.value)}
              placeholder="telegram_bot_token"
              className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
            />
          </div>
          <div>
            <label className="block text-xs font-medium text-gray-400 mb-2">Value</label>
            <input
              type="password"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              placeholder="***"
              className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
            />
          </div>
          <button
            onClick={addSecret}
            disabled={loading || !newKey.trim() || !newValue.trim()}
            className="w-full flex items-center justify-center gap-2 px-3 py-2 bg-green-600 hover:bg-green-700 text-white text-sm rounded disabled:opacity-50"
          >
            <Plus className="w-4 h-4" />
            Add Secret
          </button>
        </div>
      </div>

      {/* Secrets list */}
      <div className="space-y-2">
        {secrets.length === 0 ? (
          <p className="text-sm text-gray-600 text-center py-4">No secrets configured</p>
        ) : (
          secrets.map((secret) => (
            <div key={secret.key} className="bg-gray-800 rounded-lg p-3 border border-gray-700 flex items-center justify-between">
              <div className="flex-1 min-w-0">
                <p className="text-sm font-mono text-gray-300">{secret.key}</p>
                <span className="text-xs text-gray-600">************</span>
              </div>
              <button
                onClick={() => deleteSecret(secret.key)}
                className="ml-2 p-1 text-gray-500 hover:text-red-400 hover:bg-gray-700 rounded"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))
        )}
      </div>
    </div>
  )
}
