import React, { useEffect, useState } from 'react'
import {
  Settings as SettingsIcon, Save, RefreshCw, Download, Trash2,
  AlertTriangle,
} from 'lucide-react'
import {
  projectsApi, personasApi, projectSettingsApi,
} from '../../api/client'
import { useStore } from '../../store'
import InfoTooltip from '../help/InfoTooltip'

const TONES   = ['focused', 'balanced', 'broad']
const SKILLS  = ['off', 'suggest', 'auto']

/**
 * Consolidated per-project settings page. Lives at the rightmost tab in
 * the chat tab strip. Folds in what used to be separate Personality and
 * MCP tabs, plus project metadata, lifecycle actions, and the chat
 * defaults that drive the chat-bar action icons.
 */
export default function ProjectSettingsPanel({ projectId }) {
  const setProjects = useStore((s) => s.setProjects)
  const setActiveProject = useStore((s) => s.setActiveProject)
  const addNotification = useStore((s) => s.addNotification)

  // Project metadata
  const [project, setProject] = useState(null)
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [metaDirty, setMetaDirty] = useState(false)

  // Chat defaults
  const [chatDefaults, setChatDefaults] = useState({
    persona: '', tone_weight: 'balanced',
    context_focus: 'balanced', skill_discovery: 'off',
  })
  const [chatDirty, setChatDirty] = useState(false)

  // Personas list
  const [personas, setPersonas] = useState([])


  // UI state
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const refresh = async () => {
    setLoading(true); setError(null)
    try {
      const [proj, settings, personasRes] = await Promise.all([
        projectsApi.get(projectId),
        projectSettingsApi.get(projectId),
        personasApi.list(),
      ])
      const p = proj.data
      setProject(p)
      setName(p.name || '')
      setDescription(p.description || '')
      setMetaDirty(false)

      const cd = settings.data
      setChatDefaults({
        persona:         cd.persona || '',
        tone_weight:     cd.tone_weight || 'balanced',
        context_focus:   cd.context_focus || 'balanced',
        skill_discovery: cd.skill_discovery || 'off',
      })
      setChatDirty(false)

      // personasApi.list() already unwraps r.data, so the response IS the
      // payload — it has .personas at the top level, not .data.personas.
      setPersonas(personasRes?.personas || personasRes?.data?.personas || [])
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setLoading(false) }
  }

  useEffect(() => { refresh() }, [projectId])

  const updateMeta = (k, v) => {
    if (k === 'name') setName(v)
    if (k === 'description') setDescription(v)
    setMetaDirty(true)
  }
  const updateChat = (k, v) => {
    setChatDefaults({ ...chatDefaults, [k]: v })
    setChatDirty(true)
  }

  const saveAll = async () => {
    setSaving(true); setError(null)
    try {
      if (metaDirty) {
        await projectsApi.update(projectId, name, description)
        // Update store
        const list = await projectsApi.list()
        setProjects(list.data?.projects || [])
        const updated = (list.data?.projects || []).find((p) => p.id === projectId)
        if (updated) setActiveProject(updated)
        setMetaDirty(false)
      }
      if (chatDirty) {
        await projectSettingsApi.update(projectId, chatDefaults)
        setChatDirty(false)
      }
      addNotification?.({ type: 'success', message: 'Project settings saved' })
    } catch (e) {
      setError(e?.response?.data?.detail || e.message)
    } finally { setSaving(false) }
  }

  const exportZip = async () => {
    try {
      const res = await projectsApi.exportProject(projectId)
      const url = URL.createObjectURL(res.data)
      const a = document.createElement('a')
      a.href = url; a.download = `${name || projectId}.zip`; a.click()
      URL.revokeObjectURL(url)
    } catch (e) {
      addNotification?.({ type: 'error', message: 'Export failed: ' + (e?.response?.data?.detail || e.message) })
    }
  }

  const deleteProject = async () => {
    if (projectId === 'default') {
      alert('The default project cannot be deleted.'); return
    }
    if (!confirm(`Permanently delete project "${name}" and all its data? This cannot be undone.`)) return
    if (!confirm('Are you sure? This includes artifacts, memory, conversations, and bound repos.')) return
    try {
      await projectsApi.delete(projectId)
      const list = await projectsApi.list()
      setProjects(list.data?.projects || [])
      const fallback = (list.data?.projects || []).find((p) => p.id === 'default')
        || (list.data?.projects || [])[0]
      if (fallback) setActiveProject(fallback)
      addNotification?.({ type: 'success', message: `Deleted project ${name}` })
    } catch (e) {
      addNotification?.({ type: 'error', message: 'Delete failed: ' + (e?.response?.data?.detail || e.message) })
    }
  }

  if (loading) return <div className="p-6 text-xs text-gray-500">Loading project settings…</div>

  return (
    <div className="h-full overflow-y-auto p-6">
      <div className="max-w-3xl mx-auto space-y-8">
        <div className="flex items-center justify-between">
          <h2 className="text-xl font-semibold flex items-center gap-2">
            <SettingsIcon className="w-5 h-5" /> Project settings
          </h2>
          <div className="flex items-center gap-2">
            <button onClick={refresh} className="text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1">
              <RefreshCw className="w-3 h-3" /> Reload
            </button>
            <button
              onClick={saveAll}
              disabled={saving || (!metaDirty && !chatDirty)}
              className="px-3 py-1.5 text-sm rounded bg-brand-600 hover:bg-brand-500 text-white flex items-center gap-1 disabled:opacity-40"
            >
              <Save className="w-3 h-3" /> {saving ? 'Saving…' : 'Save changes'}
            </button>
          </div>
        </div>
        {error && (
          <div className="p-3 rounded border border-red-700 bg-red-950 text-red-300 text-sm flex items-center gap-2">
            <AlertTriangle className="w-4 h-4" /> {error}
          </div>
        )}

        {/* Project metadata */}
        <Section title="Project">
          <Field label="Name">
            <input
              type="text" value={name}
              onChange={(e) => updateMeta('name', e.target.value)}
              className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-800 text-sm"
            />
          </Field>
          <Field label="Description">
            <textarea
              rows={2} value={description}
              onChange={(e) => updateMeta('description', e.target.value)}
              className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-800 text-sm"
              placeholder="What is this project for?"
            />
          </Field>
          {project && (
            <div className="text-[10px] text-gray-500">
              ID: <code className="text-gray-400">{project.id}</code>
              {project.created_at && <> · created {project.created_at.slice(0, 10)}</>}
              {project.updated_at && <> · updated {project.updated_at.slice(0, 10)}</>}
            </div>
          )}
        </Section>

        {/* Chat defaults */}
        <Section title="Chat defaults" hint="Drives the per-message toggles in the chat header.">
          <Field label="Default persona">
            <select
              value={chatDefaults.persona}
              onChange={(e) => updateChat('persona', e.target.value)}
              className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-800 text-sm"
            >
              <option value="">(none — use global identity)</option>
              {personas.map((p) => (
                <option key={p.id} value={p.id}>{p.name || p.id}</option>
              ))}
            </select>
          </Field>
          <div className="grid grid-cols-3 gap-3">
            <Field
              label="Tone weight"
              tooltip="How strongly the persona's tone colors responses. Use minimal for matter-of-fact answers, strong for fully in-character."
            >
              <select
                value={chatDefaults.tone_weight}
                onChange={(e) => updateChat('tone_weight', e.target.value)}
                className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-800 text-sm"
              >
                {TONES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
            <Field
              label="Context focus"
              tooltip="How tightly the agent stays on the current message vs. the wider conversation."
            >
              <select
                value={chatDefaults.context_focus}
                onChange={(e) => updateChat('context_focus', e.target.value)}
                className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-800 text-sm"
              >
                {TONES.map((t) => <option key={t} value={t}>{t}</option>)}
              </select>
            </Field>
            <Field
              label="Skill discovery"
              tooltip="Whether the agent auto-loads matching skills: off = manual /skill only; suggest = ask first; auto = load silently."
            >
              <select
                value={chatDefaults.skill_discovery}
                onChange={(e) => updateChat('skill_discovery', e.target.value)}
                className="w-full px-3 py-2 rounded bg-gray-900 border border-gray-800 text-sm"
              >
                {SKILLS.map((s) => <option key={s} value={s}>{s}</option>)}
              </select>
            </Field>
          </div>
        </Section>
        {/* Lifecycle */}
        <Section title="Export & clone">
          <button
            onClick={exportZip}
            className="px-3 py-2 text-sm rounded bg-gray-800 hover:bg-gray-700 flex items-center gap-2"
          >
            <Download className="w-4 h-4" /> Export ZIP
          </button>
          <p className="text-[11px] text-gray-500">
            Bundle this project's artifacts, memory, conversations, and metadata into a portable archive.
          </p>
        </Section>

        <Section title="Danger zone" tone="danger">
          <button
            onClick={deleteProject}
            disabled={projectId === 'default'}
            className="px-3 py-2 text-sm rounded bg-red-900 hover:bg-red-800 text-red-100 flex items-center gap-2 disabled:opacity-40"
          >
            <Trash2 className="w-4 h-4" /> Delete project
          </button>
          <p className="text-[11px] text-gray-500">
            Permanently removes the project, its artifacts, memory, and conversations.
            {projectId === 'default' && ' The default project cannot be deleted.'}
          </p>
        </Section>
      </div>
    </div>
  )
}


function Section({ title, hint, icon: Icon, tone, children }) {
  const ring = tone === 'danger'
    ? 'border-red-900/60'
    : 'border-gray-800'
  const titleColor = tone === 'danger' ? 'text-red-300' : 'text-gray-300'
  return (
    <section className={`p-4 rounded-lg border ${ring} bg-gray-950/40 space-y-3`}>
      <div>
        <h3 className={`text-sm font-semibold flex items-center gap-2 ${titleColor}`}>
          {Icon && <Icon className="w-4 h-4" />}
          {title}
        </h3>
        {hint && <p className="text-[11px] text-gray-500 mt-1">{hint}</p>}
      </div>
      {children}
    </section>
  )
}

function Field({ label, tooltip, children }) {
  return (
    <div>
      <label className="block text-xs text-gray-400 mb-1">
        {label}
        {tooltip && <InfoTooltip text={tooltip} />}
      </label>
      {children}
    </div>
  )
}
