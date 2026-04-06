import React, { useState, useEffect } from 'react'
import { Plus, Edit3, Trash2, Save, X, Lock, Copy } from 'lucide-react'
import { useStore } from '../store'
import { personasApi } from '../api/client'

function PersonaCard({ persona, onEdit, onDelete, onClone }) {
  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-4 hover:border-gray-600 transition-colors">
      <div className="flex items-start justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-2xl">{persona.icon}</span>
          <div>
            <h3 className="font-semibold text-gray-100 text-sm">{persona.name}</h3>
            <p className="text-xs text-gray-500">{persona.tagline}</p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {persona.is_bundled && (
            <span className="text-xs text-gray-600 flex items-center gap-1" title="Bundled — read only">
              <Lock className="w-3 h-3" />
            </span>
          )}
          {persona.is_default && (
            <span className="px-1.5 py-0.5 text-[10px] bg-brand-900 text-brand-300 rounded">Default</span>
          )}
        </div>
      </div>

      <p className="text-xs text-gray-400 mb-2 line-clamp-2">{persona.description}</p>

      {persona.traits?.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-3">
          {persona.traits.map((t) => (
            <span key={t} className="px-1.5 py-0.5 text-[10px] bg-gray-700 text-gray-300 rounded">
              {t}
            </span>
          ))}
        </div>
      )}

      <div className="text-xs text-gray-600 mb-3">{persona.best_for}</div>

      <div className="flex gap-1.5">
        {persona.is_bundled ? (
          <button
            onClick={() => onClone(persona)}
            className="flex items-center gap-1 px-2 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs rounded transition-colors"
          >
            <Copy className="w-3 h-3" /> Clone
          </button>
        ) : (
          <>
            <button
              onClick={() => onEdit(persona)}
              className="flex items-center gap-1 px-2 py-1.5 bg-gray-700 hover:bg-gray-600 text-gray-300 text-xs rounded transition-colors"
            >
              <Edit3 className="w-3 h-3" /> Edit
            </button>
            <button
              onClick={() => onDelete(persona)}
              className="flex items-center gap-1 px-2 py-1.5 bg-gray-700 hover:bg-red-900 text-gray-300 hover:text-red-200 text-xs rounded transition-colors"
            >
              <Trash2 className="w-3 h-3" /> Delete
            </button>
          </>
        )}
      </div>
    </div>
  )
}

function PersonaEditor({ persona, onSave, onCancel }) {
  const [name, setName] = useState(persona?.name || '')
  const [tagline, setTagline] = useState(persona?.tagline || '')
  const [description, setDescription] = useState(persona?.description || '')
  const [icon, setIcon] = useState(persona?.icon || '🎭')
  const [traits, setTraits] = useState(persona?.traits?.join(', ') || '')
  const [bestFor, setBestFor] = useState(persona?.best_for || '')
  const [soul, setSoul] = useState(persona?.soul || '')
  const [saving, setSaving] = useState(false)

  const handleSave = async () => {
    if (!name.trim()) return
    setSaving(true)
    try {
      await onSave({
        name: name.trim(),
        tagline,
        description,
        icon,
        traits: traits.split(',').map((t) => t.trim()).filter(Boolean),
        best_for: bestFor,
        soul,
      })
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 p-5 space-y-3">
      <div className="flex items-center justify-between mb-1">
        <h3 className="text-sm font-semibold text-gray-200">
          {persona?.id ? 'Edit Persona' : 'Create New Persona'}
        </h3>
        <button onClick={onCancel} className="text-gray-500 hover:text-gray-300">
          <X className="w-4 h-4" />
        </button>
      </div>

      <div className="grid grid-cols-[auto_1fr] gap-3">
        <div>
          <label className="block text-xs text-gray-400 mb-1">Icon</label>
          <input
            type="text"
            value={icon}
            onChange={(e) => setIcon(e.target.value)}
            className="w-14 bg-gray-900 border border-gray-600 rounded px-2 py-2 text-center text-lg focus:outline-none focus:border-brand-500"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Persona name..."
            className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
          />
        </div>
      </div>

      <div>
        <label className="block text-xs text-gray-400 mb-1">Tagline</label>
        <input
          type="text"
          value={tagline}
          onChange={(e) => setTagline(e.target.value)}
          placeholder="Short tagline..."
          className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
        />
      </div>

      <div>
        <label className="block text-xs text-gray-400 mb-1">Description</label>
        <textarea
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          placeholder="What this persona is like..."
          rows={2}
          className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 resize-none"
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-xs text-gray-400 mb-1">Traits (comma-separated)</label>
          <input
            type="text"
            value={traits}
            onChange={(e) => setTraits(e.target.value)}
            placeholder="curious, patient, thorough"
            className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
          />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Best For</label>
          <input
            type="text"
            value={bestFor}
            onChange={(e) => setBestFor(e.target.value)}
            placeholder="Research, coding, analysis..."
            className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
          />
        </div>
      </div>

      <div>
        <label className="block text-xs text-gray-400 mb-1">Soul (personality prompt — Markdown)</label>
        <textarea
          value={soul}
          onChange={(e) => setSoul(e.target.value)}
          placeholder="# The Soul of [Name]\n\nYou are..."
          rows={10}
          className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 font-mono focus:outline-none focus:border-brand-500 resize-y"
        />
      </div>

      <div className="flex justify-end gap-2 pt-1">
        <button
          onClick={onCancel}
          className="px-3 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm rounded"
        >
          Cancel
        </button>
        <button
          onClick={handleSave}
          disabled={saving || !name.trim()}
          className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded disabled:opacity-50"
        >
          <Save className="w-4 h-4" />
          {saving ? 'Saving...' : 'Save Persona'}
        </button>
      </div>
    </div>
  )
}

export default function PersonaLibrary() {
  const [personas, setPersonas] = useState([])
  const [loading, setLoading] = useState(false)
  const [editingPersona, setEditingPersona] = useState(null) // null = list view, {} = create, {id:...} = edit
  const addNotification = useStore((s) => s.addNotification)

  const loadPersonas = async () => {
    setLoading(true)
    try {
      const data = await personasApi.list()
      setPersonas(data.personas || [])
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  useEffect(() => {
    loadPersonas()
  }, [])

  const handleSave = async (data) => {
    try {
      if (editingPersona?.id && !editingPersona?.is_bundled) {
        await personasApi.update(editingPersona.id, data)
        addNotification({ type: 'success', message: 'Persona updated' })
      } else {
        await personasApi.create(data)
        addNotification({ type: 'success', message: 'Persona created' })
      }
      setEditingPersona(null)
      loadPersonas()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const handleDelete = async (persona) => {
    if (!confirm(`Delete persona "${persona.name}"? This cannot be undone.`)) return
    try {
      await personasApi.delete(persona.id)
      addNotification({ type: 'success', message: 'Persona deleted' })
      loadPersonas()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const handleClone = (persona) => {
    setEditingPersona({
      name: `${persona.name} (Custom)`,
      tagline: persona.tagline,
      description: persona.description,
      icon: persona.icon,
      traits: persona.traits,
      best_for: persona.best_for,
      soul: persona.soul,
    })
  }

  // Split into bundled and custom
  const bundled = personas.filter((p) => p.is_bundled)
  const custom = personas.filter((p) => !p.is_bundled)

  if (editingPersona !== null) {
    return (
      <div className="p-6 max-w-3xl mx-auto">
        <PersonaEditor
          persona={editingPersona}
          onSave={handleSave}
          onCancel={() => setEditingPersona(null)}
        />
      </div>
    )
  }

  return (
    <div className="p-6">
      <div className="max-w-5xl mx-auto">
        {/* Header */}
        <div className="flex items-center justify-between mb-6">
          <div>
            <h2 className="text-lg font-bold text-gray-100">Persona Library</h2>
            <p className="text-xs text-gray-500 mt-0.5">
              Prebuilt and custom agent personalities. Apply personas to projects for tailored behavior.
            </p>
          </div>
          <button
            onClick={() => setEditingPersona({})}
            className="flex items-center gap-2 px-3 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded"
          >
            <Plus className="w-4 h-4" />
            New Persona
          </button>
        </div>

        {/* Custom Personas */}
        {custom.length > 0 && (
          <div className="mb-8">
            <h3 className="text-xs font-semibold text-gray-400 mb-3 uppercase tracking-wider">Custom Personas</h3>
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {custom.map((p) => (
                <PersonaCard
                  key={p.id}
                  persona={p}
                  onEdit={setEditingPersona}
                  onDelete={handleDelete}
                  onClone={handleClone}
                />
              ))}
            </div>
          </div>
        )}

        {/* Bundled Personas */}
        <div>
          <h3 className="text-xs font-semibold text-gray-400 mb-3 uppercase tracking-wider">
            Bundled Personas — Greek Pantheon
          </h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
            {bundled.map((p) => (
              <PersonaCard
                key={p.id}
                persona={p}
                onEdit={setEditingPersona}
                onDelete={handleDelete}
                onClone={handleClone}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
