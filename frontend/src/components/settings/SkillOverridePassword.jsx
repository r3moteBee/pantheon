import React, { useState, useEffect } from 'react'
import { Save, Eye, EyeOff, Trash2, Check } from 'lucide-react'
import { useStore } from '../../store'
import { settingsApi } from '../../api/client'

export default function SkillOverridePassword() {
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [isSet, setIsSet] = useState(false)
  const [loading, setLoading] = useState(true)
  const [showPassword, setShowPassword] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  useEffect(() => {
    checkStatus()
  }, [])

  const checkStatus = async () => {
    try {
      const res = await settingsApi.listSecrets()
      const keys = res.data.keys || []
      setIsSet(keys.includes('skill_security_override_password'))
    } catch (err) {
      // ignore
    }
    setLoading(false)
  }

  const handleSave = async () => {
    if (!password.trim()) {
      addNotification({ type: 'error', message: 'Password cannot be empty' })
      return
    }
    if (password.length < 8) {
      addNotification({ type: 'error', message: 'Password must be at least 8 characters' })
      return
    }
    if (password !== confirmPassword) {
      addNotification({ type: 'error', message: 'Passwords do not match' })
      return
    }
    try {
      await settingsApi.setSecret('skill_security_override_password', password)
      setIsSet(true)
      setPassword('')
      setConfirmPassword('')
      addNotification({ type: 'success', message: 'Security override password saved to vault' })
    } catch (err) {
      addNotification({ type: 'error', message: `Failed to save: ${err.message}` })
    }
  }

  const handleRemove = async () => {
    try {
      await settingsApi.deleteSecret('skill_security_override_password')
      setIsSet(false)
      addNotification({ type: 'success', message: 'Security override password removed' })
    } catch (err) {
      addNotification({ type: 'error', message: `Failed to remove: ${err.message}` })
    }
  }

  return (
    <div>
      <h2 className="text-lg font-semibold text-gray-100 mb-1">Skill Security Override</h2>
      <p className="text-sm text-gray-500 mb-4">
        Set a password to allow force-enabling skills that failed a security scan.
        This password is stored encrypted in the vault and required each time an override is used.
      </p>

      {loading ? (
        <p className="text-xs text-gray-600">Loading...</p>
      ) : isSet ? (
        <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 space-y-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-2">
              <Check className="w-4 h-4 text-green-400" />
              <span className="text-sm text-green-400">Override password is configured</span>
            </div>
            <button
              onClick={handleRemove}
              className="flex items-center gap-1 px-2.5 py-1 text-xs rounded bg-red-900/30 text-red-400 hover:bg-red-900/50 transition-colors"
            >
              <Trash2 className="w-3 h-3" /> Remove
            </button>
          </div>
          <p className="text-xs text-gray-600">
            To change the password, remove the current one and set a new one.
          </p>
        </div>
      ) : (
        <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 space-y-3">
          <div className="space-y-2">
            <label className="block text-xs text-gray-400">New Override Password</label>
            <div className="relative">
              <input
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Minimum 8 characters"
                className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 pr-10"
              />
              <button
                type="button"
                onClick={() => setShowPassword(!showPassword)}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-300"
              >
                {showPassword ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
              </button>
            </div>
          </div>
          <div className="space-y-2">
            <label className="block text-xs text-gray-400">Confirm Password</label>
            <input
              type={showPassword ? 'text' : 'password'}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              placeholder="Re-enter password"
              className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
            />
          </div>
          <button
            onClick={handleSave}
            disabled={!password.trim() || !confirmPassword.trim()}
            className="flex items-center justify-center gap-2 px-3 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded disabled:opacity-50 w-full"
          >
            <Save className="w-4 h-4" /> Set Override Password
          </button>
        </div>
      )}
    </div>
  )
}
