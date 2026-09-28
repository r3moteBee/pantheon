import React, { useState, useEffect } from 'react'
import { Plus, RefreshCw, Trash2, Globe } from 'lucide-react'
import { skillsApi } from '../../api/client'

export default function SkillHubsSettings() {
  const [registries, setRegistries] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [showForm, setShowForm] = useState(false)
  const [editingId, setEditingId] = useState(null)
  const [form, setForm] = useState({
    id: '', url: '', display_name: '', auth_type: 'none', bearer_token: '',
  })
  const [busy, setBusy] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const { data } = await skillsApi.listRegistries()
      setRegistries(data.registries || [])
      setError('')
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const resetForm = () => {
    setForm({ id: '', url: '', display_name: '', auth_type: 'none', bearer_token: '' })
    setEditingId(null)
    setShowForm(false)
  }

  const startEdit = (r) => {
    setEditingId(r.id)
    setForm({
      id: r.id,
      url: r.url,
      display_name: r.display_name || '',
      auth_type: r.auth?.type || 'none',
      bearer_token: '',
    })
    setShowForm(true)
  }

  const submit = async () => {
    setBusy(true)
    setError('')
    try {
      const payload = {
        url: form.url,
        display_name: form.display_name || null,
        auth_type: form.auth_type,
        bearer_token: form.bearer_token || null,
      }
      if (editingId) {
        await skillsApi.updateRegistry(editingId, payload)
      } else {
        await skillsApi.createRegistry({ id: form.id, ...payload })
      }
      resetForm()
      await load()
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (id) => {
    if (!confirm(`Remove skill registry "${id}"?`)) return
    try {
      await skillsApi.deleteRegistry(id)
      await load()
    } catch (e) {
      setError(e.message)
    }
  }

  return (
    <div>
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-sm font-semibold text-gray-200 flex items-center gap-2">
            <Globe className="w-4 h-4" /> Skill Registry Hubs
          </h2>
          <p className="text-xs text-gray-500 mt-1">
            Configure private skill registries that implement the{' '}
            <span className="text-gray-400">Pantheon Skill Registry Protocol v1.0</span>.
            They become searchable from Skills → Import.
          </p>
        </div>
        {!showForm && (
          <button
            onClick={() => setShowForm(true)}
            className="flex items-center gap-1 px-3 py-1.5 text-xs rounded bg-brand-600 hover:bg-brand-500 text-white"
          >
            <Plus className="w-3.5 h-3.5" /> Add Hub
          </button>
        )}
      </div>

      {error && (
        <div className="mb-3 p-2 rounded border border-red-800 bg-red-900/30 text-xs text-red-300">
          {error}
        </div>
      )}

      {showForm && (
        <div className="mb-4 p-4 rounded border border-gray-800 bg-gray-900/40 space-y-3">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1">ID</label>
              <input
                type="text"
                value={form.id}
                disabled={!!editingId}
                onChange={(e) => setForm({ ...form, id: e.target.value })}
                placeholder="acme"
                className="w-full px-2 py-1.5 text-xs bg-gray-950 border border-gray-800 rounded text-gray-200 disabled:opacity-50"
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Display Name</label>
              <input
                type="text"
                value={form.display_name}
                onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                placeholder="Acme Internal Skills"
                className="w-full px-2 py-1.5 text-xs bg-gray-950 border border-gray-800 rounded text-gray-200"
              />
            </div>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">URL</label>
            <input
              type="text"
              value={form.url}
              onChange={(e) => setForm({ ...form, url: e.target.value })}
              placeholder="https://skills.acme.internal"
              className="w-full px-2 py-1.5 text-xs bg-gray-950 border border-gray-800 rounded text-gray-200"
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div>
              <label className="block text-xs text-gray-400 mb-1">Auth</label>
              <select
                value={form.auth_type}
                onChange={(e) => setForm({ ...form, auth_type: e.target.value })}
                className="w-full px-2 py-1.5 text-xs bg-gray-950 border border-gray-800 rounded text-gray-200"
              >
                <option value="none">None</option>
                <option value="bearer">Bearer Token</option>
              </select>
            </div>
            {form.auth_type === 'bearer' && (
              <div>
                <label className="block text-xs text-gray-400 mb-1">
                  Bearer Token {editingId && <span className="text-gray-600">(leave blank to keep)</span>}
                </label>
                <input
                  type="password"
                  value={form.bearer_token}
                  onChange={(e) => setForm({ ...form, bearer_token: e.target.value })}
                  className="w-full px-2 py-1.5 text-xs bg-gray-950 border border-gray-800 rounded text-gray-200"
                />
              </div>
            )}
          </div>
          <div className="flex gap-2 justify-end">
            <button
              onClick={resetForm}
              className="px-3 py-1.5 text-xs rounded border border-gray-800 text-gray-300 hover:bg-gray-900"
            >
              Cancel
            </button>
            <button
              onClick={submit}
              disabled={busy || !form.url || (!editingId && !form.id)}
              className="px-3 py-1.5 text-xs rounded bg-brand-600 hover:bg-brand-500 text-white disabled:opacity-50"
            >
              {editingId ? 'Save' : 'Add'}
            </button>
          </div>
        </div>
      )}

      {loading ? (
        <div className="text-xs text-gray-500">Loading…</div>
      ) : registries.length === 0 ? (
        <div className="p-4 rounded border border-dashed border-gray-800 text-xs text-gray-500 text-center">
          No hubs available.
        </div>
      ) : (
        <div className="space-y-2">
          {registries.map((r) => (
            <div key={r.id} className="p-3 rounded border border-gray-800 bg-gray-900/40 flex items-center justify-between">
              <div className="min-w-0">
                <div className="text-sm text-gray-200 truncate">
                  {r.display_name || r.id}
                  <span className="ml-2 text-xs text-gray-600">[{r.id}]</span>
                  {r.builtin && (
                    <span className="ml-2 px-1.5 py-0.5 text-[10px] rounded bg-gray-800 text-gray-400 uppercase">Built-in</span>
                  )}
                  {r.builtin && !r.searchable && (
                    <span className="ml-1 px-1.5 py-0.5 text-[10px] rounded bg-gray-800 text-gray-500">Not searchable</span>
                  )}
                </div>
                {r.url && <div className="text-xs text-gray-500 truncate">{r.url}</div>}
                {!r.builtin && (
                  <div className="text-xs text-gray-600 mt-0.5">
                    Auth: {r.auth?.type}
                    {r.auth?.type === 'bearer' && (
                      <span className="ml-1">{r.auth?.token_set ? '✓' : '(missing token)'}</span>
                    )}
                  </div>
                )}
              </div>
              {!r.builtin && (
                <div className="flex gap-1 ml-3">
                  <button
                    onClick={() => startEdit(r)}
                    className="p-1.5 text-gray-400 hover:text-gray-200"
                    title="Edit"
                  >
                    <RefreshCw className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => remove(r.id)}
                    className="p-1.5 text-gray-400 hover:text-red-400"
                    title="Remove"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
