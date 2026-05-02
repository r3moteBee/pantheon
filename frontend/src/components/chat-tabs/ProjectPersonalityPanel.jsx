import React, { useEffect, useState } from 'react'
import { User as UserIcon, Save, RefreshCw } from 'lucide-react'
import { projectSettingsApi } from '../../api/client'

const TONES = ['focused', 'balanced', 'broad']
const SKILLS = ['off', 'auto', 'always']

export default function ProjectPersonalityPanel({ projectId }) {
  const [s, setS] = useState({ persona: '', tone_weight: 'balanced', context_focus: 'balanced', skill_discovery: 'off' })
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [savedAt, setSavedAt] = useState(null)

  const load = async () => {
    setLoading(true)
    try {
      const res = await projectSettingsApi.get(projectId)
      setS({
        persona: res.data?.persona || '',
        tone_weight: res.data?.tone_weight || 'balanced',
        context_focus: res.data?.context_focus || 'balanced',
        skill_discovery: res.data?.skill_discovery || 'off',
      })
      setDirty(false)
    } finally { setLoading(false) }
  }

  useEffect(() => { load() }, [projectId])

  const update = (key, val) => { setS({ ...s, [key]: val }); setDirty(true) }

  const save = async () => {
    setSaving(true)
    try {
      await projectSettingsApi.update(projectId, s)
      setDirty(false)
      setSavedAt(new Date().toISOString())
    } finally { setSaving(false) }
  }

  return (
    <div className="h-full overflow-y-auto p-6 max-w-2xl mx-auto">
      <h2 className="text-lg font-semibold flex items-center gap-2 mb-4">
        <UserIcon className="w-5 h-5" /> Personality
        <button onClick={load} className="ml-auto text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1">
          <RefreshCw className="w-3 h-3" /> Reload
        </button>
      </h2>
      <p className="text-xs text-gray-500 mb-6">
        These are this project's chat defaults. The agent's global identity (soul / agent.md)
        lives in <a className="text-brand-400 underline" href="/settings">Settings → Personality</a>.
      </p>

      {loading ? (
        <div className="text-xs text-gray-500">Loading…</div>
      ) : (
        <div className="space-y-5 text-sm">
          <div>
            <label className="block text-xs text-gray-400 mb-1">Default persona</label>
            <input
              type="text" value={s.persona} onChange={(e) => update('persona', e.target.value)}
              placeholder="(none) — use global identity"
              className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-800"
            />
            <p className="text-[10px] text-gray-500 mt-1">Persona ID from the Personas page (e.g. 'zeus', 'apollo'), or leave blank.</p>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Tone weight</label>
            <select value={s.tone_weight} onChange={(e) => update('tone_weight', e.target.value)}
                    className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-800">
              {TONES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Context focus</label>
            <select value={s.context_focus} onChange={(e) => update('context_focus', e.target.value)}
                    className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-800">
              {TONES.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
            <p className="text-[10px] text-gray-500 mt-1">
              focused = aggressive recency boost · balanced = mild · broad = pure relevance
            </p>
          </div>
          <div>
            <label className="block text-xs text-gray-400 mb-1">Skill discovery</label>
            <select value={s.skill_discovery} onChange={(e) => update('skill_discovery', e.target.value)}
                    className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-800">
              {SKILLS.map((t) => <option key={t} value={t}>{t}</option>)}
            </select>
          </div>
          <div className="flex items-center gap-3 pt-2">
            <button
              onClick={save} disabled={!dirty || saving}
              className="px-3 py-1.5 text-sm rounded bg-brand-600 hover:bg-brand-500 text-white flex items-center gap-1 disabled:opacity-50"
            >
              <Save className="w-3 h-3" /> {saving ? 'Saving…' : 'Save'}
            </button>
            {!dirty && savedAt && (
              <span className="text-xs text-gray-500">Saved {new Date(savedAt).toLocaleTimeString()}</span>
            )}
            {dirty && <span className="text-xs text-amber-400">Unsaved changes</span>}
          </div>
        </div>
      )}
    </div>
  )
}
