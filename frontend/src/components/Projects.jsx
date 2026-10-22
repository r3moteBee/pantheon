import React, { useState, useEffect, useMemo } from 'react'
import { Trash2, Check, RefreshCw, Calendar, User, Plus, X, Search, Pencil } from 'lucide-react'
import { useStore } from '../store'
import { projectsApi, personasApi, personalityApi } from '../api/client'
import { ExportButton, ImportButton } from './ProjectPortability'
import DeleteProjectModal from './DeleteProjectModal'
import PageHeader, { primaryButton, secondaryButton } from './PageHeader'
import Time from './Time'

function CreateProjectForm({ onProjectCreated, onCancel, personas }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [id, setId] = useState('')
  const [personaId, setPersonaId] = useState('')
  const [loading, setLoading] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  const createProject = async () => {
    if (!name.trim()) return
    setLoading(true)
    try {
      const res = await projectsApi.create(name, description, id || undefined)
      const projectId = res.data?.id || id
      // Apply a personality preset if one was chosen (default: follow global)
      if (personaId && projectId) {
        try {
          await personasApi.apply(personaId, projectId)
        } catch (e) {
          addNotification({ type: 'error', message: `Project created, but the preset wasn't applied: ${e.message}` })
        }
      }
      setName('')
      setDescription('')
      setId('')
      setPersonaId('')
      addNotification({ type: 'success', message: 'Project created' })
      onProjectCreated()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  const field = 'w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:border-brand-500'
  return (
    <form
      onSubmit={(e) => { e.preventDefault(); createProject() }}
      className="bg-gray-800 rounded-lg p-4 border border-gray-700 space-y-3"
    >
      <h2 className="text-sm font-semibold text-gray-200">New project</h2>
      <div>
        <label htmlFor="np-name" className="block text-xs text-gray-300 mb-1">Name</label>
        <input id="np-name" type="text" value={name} autoFocus
               onChange={(e) => setName(e.target.value)} placeholder="e.g. Kitchen remodel" className={field} />
      </div>
      <div>
        <label htmlFor="np-desc" className="block text-xs text-gray-300 mb-1">Description (optional)</label>
        <textarea id="np-desc" value={description} onChange={(e) => setDescription(e.target.value)}
                  rows={2} className={`${field} resize-none`} />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div>
          <label htmlFor="np-id" className="block text-xs text-gray-300 mb-1">Custom ID (optional)</label>
          <input id="np-id" type="text" value={id} onChange={(e) => setId(e.target.value)}
                 placeholder="generated if empty" className={field} />
        </div>
        <div>
          <label htmlFor="np-persona" className="block text-xs text-gray-300 mb-1">Personality preset</label>
          <select id="np-persona" value={personaId} onChange={(e) => setPersonaId(e.target.value)} className={field}>
            <option value="">Global personality (recommended)</option>
            {personas.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} — {p.tagline}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="flex gap-2">
        <button type="submit" disabled={loading || !name.trim()} className={primaryButton}>
          {loading ? 'Creating...' : 'Create project'}
        </button>
        <button type="button" onClick={onCancel} className={secondaryButton}>Cancel</button>
      </div>
    </form>
  )
}

function ProjectCard({ project, isActive, onSetActive, onDelete, personas, onRefresh }) {
  const [changingPersona, setChangingPersona] = useState(false)
  // The personality used to be a live <select> on every card, so a stray
  // click re-styled a project (UI review #33). Now: shown as text, changed
  // behind an explicit "Change" with Save / Cancel.
  const [editingPersona, setEditingPersona] = useState(false)
  const [personaDraft, setPersonaDraft] = useState(project.persona_id || '')
  const addNotification = useStore((s) => s.addNotification)

  const [confirmingDelete, setConfirmingDelete] = useState(false)

  const deleteProject = () => {
    if (isActive) {
      addNotification({ type: 'error', message: 'Cannot delete active project' })
      return
    }
    setConfirmingDelete(true)
  }

  const onDeleted = () => {
    setConfirmingDelete(false)
    addNotification({ type: 'success', message: `Project "${project.name}" deleted` })
    onDelete()
  }

  const changePersona = async (newPersonaId) => {
    setChangingPersona(true)
    try {
      if (newPersonaId) {
        await personasApi.apply(newPersonaId, project.id)
        addNotification({ type: 'success', message: `Preset applied to ${project.name}` })
      } else {
        await personalityApi.reset(project.id)
        addNotification({ type: 'success', message: `${project.name} now follows the global personality` })
      }
      setEditingPersona(false)
      if (onRefresh) onRefresh()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setChangingPersona(false)
  }

  const personaName = project.persona_id
    ? (personas.find((p) => p.id === project.persona_id)?.name || project.persona_id)
    : 'Global personality'

  return (
    <div
      className={`
        flex flex-col rounded-lg border-2 p-4 transition-all
        ${isActive
          ? 'bg-gray-800 border-brand-500'
          : 'bg-gray-800 border-gray-700 hover:border-gray-600'
        }
      `}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1 min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="font-semibold text-gray-100 break-words min-w-0">{project.name}</h3>
            {isActive && (
              <span className="inline-flex items-center gap-1 whitespace-nowrap px-2 py-0.5 bg-green-900 text-green-100 text-xs rounded-full">
                <span className="w-1.5 h-1.5 rounded-full bg-green-400" aria-hidden="true" />
                Active
              </span>
            )}
          </div>
          {project.description && (
            <p className="text-sm text-gray-300 mt-1 line-clamp-2">{project.description}</p>
          )}
        </div>
      </div>

      <div className="space-y-1 mb-3 text-xs text-gray-400">
        <div className="truncate" title={project.id}>
          <span className="font-mono">ID: {project.id}</span>
        </div>
        {project.created_at && (
          <div className="flex items-center gap-1">
            <Calendar className="w-3 h-3" aria-hidden="true" />
            Created <Time value={project.created_at} relative={false} />
          </div>
        )}
      </div>

      {/* Personality preset */}
      <div className="mb-3 text-xs">
        {!editingPersona ? (
          <div className="flex items-center gap-2 text-gray-300">
            <User className="w-3 h-3 text-gray-400" aria-hidden="true" />
            <span className="text-gray-400">Personality:</span>
            <span className="truncate">{personaName}</span>
            <button
              type="button"
              onClick={() => { setPersonaDraft(project.persona_id || ''); setEditingPersona(true) }}
              className="ml-auto inline-flex items-center gap-1 whitespace-nowrap text-gray-300 hover:text-white"
              aria-label={`Change personality of ${project.name}`}
            >
              <Pencil className="w-3 h-3" aria-hidden="true" /> Change
            </button>
          </div>
        ) : (
          <div className="space-y-2">
            <label htmlFor={`persona-${project.id}`} className="block text-gray-300">Personality preset</label>
            <select
              id={`persona-${project.id}`}
              value={personaDraft}
              onChange={(e) => setPersonaDraft(e.target.value)}
              disabled={changingPersona}
              className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1.5 text-xs text-gray-200 focus:border-brand-500 disabled:opacity-50"
            >
              <option value="">Global personality</option>
              {personas.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <div className="flex gap-2">
              <button
                type="button"
                onClick={() => changePersona(personaDraft)}
                disabled={changingPersona || personaDraft === (project.persona_id || '')}
                className={primaryButton}
              >
                {changingPersona ? 'Saving…' : 'Save'}
              </button>
              <button type="button" onClick={() => setEditingPersona(false)} className={secondaryButton}>
                Cancel
              </button>
            </div>
          </div>
        )}
      </div>

      <div className="mt-auto flex gap-2">
        {!isActive && (
          <button onClick={onSetActive} className={`${secondaryButton} flex-1 justify-center`}>
            <Check className="w-3.5 h-3.5" aria-hidden="true" />
            Set active
          </button>
        )}
        <ExportButton project={project} />
        <button
          onClick={deleteProject}
          disabled={isActive}
          title={isActive ? 'Switch to another project to delete this one' : 'Delete project'}
          aria-label={`Delete project ${project.name}`}
          className="px-3 py-2 bg-gray-700 hover:bg-red-900 disabled:opacity-50 text-gray-300 hover:text-red-200 text-sm rounded transition-colors"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
      {confirmingDelete && (
        <DeleteProjectModal project={project} onClose={() => setConfirmingDelete(false)} onDeleted={onDeleted} />
      )}
    </div>
  )
}

const SORTS = {
  name: { label: 'Name', fn: (a, b) => (a.name || a.id).localeCompare(b.name || b.id) },
  newest: { label: 'Newest first', fn: (a, b) => String(b.created_at || '').localeCompare(String(a.created_at || '')) },
  oldest: { label: 'Oldest first', fn: (a, b) => String(a.created_at || '').localeCompare(String(b.created_at || '')) },
}

export default function Projects() {
  const [loading, setLoading] = useState(false)
  const [personas, setPersonas] = useState([])
  const [showCreate, setShowCreate] = useState(false)
  const [query, setQuery] = useState('')
  const [sort, setSort] = useState('newest')
  const projects = useStore((s) => s.projects)
  const activeProject = useStore((s) => s.activeProject)
  const setProjects = useStore((s) => s.setProjects)
  const setActiveProject = useStore((s) => s.setActiveProject)
  const addNotification = useStore((s) => s.addNotification)

  const loadProjects = async () => {
    setLoading(true)
    try {
      const res = await projectsApi.list()
      const projectList = res.data.projects || []
      setProjects(projectList)
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  const loadPersonas = async () => {
    try {
      const data = await personasApi.list()
      setPersonas(data.personas || [])
    } catch (err) {
      console.warn('Failed to load personas:', err)
    }
  }

  useEffect(() => {
    loadProjects()
    loadPersonas()
  }, [])

  const setActive = async (project) => {
    setActiveProject(project)
    addNotification({ type: 'success', message: `Switched to ${project.name}` })
  }

  // Active project pinned first, then the chosen sort; the filter matches name, id, description.
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase()
    const list = projects.filter((p) => !q || [p.name, p.id, p.description].some((v) => (v || '').toLowerCase().includes(q)))
    list.sort(SORTS[sort].fn)
    const i = list.findIndex((p) => p.id === activeProject?.id)
    if (i > 0) list.unshift(list.splice(i, 1)[0])
    return list
  }, [projects, query, sort, activeProject?.id])

  return (
    <div className="flex flex-col h-full bg-gray-950">
      <PageHeader
        title="Projects"
        description="Each project has its own chat history, memory, artifacts, tasks and repository."
        actions={
          <>
            <button onClick={() => setShowCreate((v) => !v)} aria-expanded={showCreate}
                    className={showCreate ? secondaryButton : primaryButton}>
              {showCreate ? <X className="w-3.5 h-3.5" aria-hidden="true" /> : <Plus className="w-3.5 h-3.5" aria-hidden="true" />}
              {showCreate ? 'Close' : 'New project'}
            </button>
            <ImportButton onImported={loadProjects} />
            <button
              onClick={loadProjects}
              disabled={loading}
              title="Refresh"
              aria-label="Refresh projects"
              className="p-2 text-gray-400 hover:text-gray-200 disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            </button>
          </>
        }
      />

      {/* Content */}
      <div className="flex-1 overflow-y-auto scrollbar-thin p-4 sm:p-6">
        <div className="max-w-5xl mx-auto space-y-6">
          {showCreate && (
            <CreateProjectForm
              onProjectCreated={() => { setShowCreate(false); loadProjects() }}
              onCancel={() => setShowCreate(false)}
              personas={personas}
            />
          )}

          <div>
            <div className="mb-4 flex flex-wrap items-center gap-2">
              <h2 className="mr-auto text-sm font-semibold text-gray-300">
                All projects <span className="font-normal text-gray-400">({projects.length})</span>
              </h2>
              <div className="relative">
                <Search className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-gray-400" aria-hidden="true" />
                <input
                  type="search"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  placeholder="Filter projects"
                  aria-label="Filter projects"
                  className="w-48 rounded border border-gray-700 bg-gray-900 py-1.5 pl-8 pr-2 text-sm text-gray-100 focus:border-brand-500"
                />
              </div>
              <select
                value={sort}
                onChange={(e) => setSort(e.target.value)}
                aria-label="Sort projects"
                className="rounded border border-gray-700 bg-gray-900 px-2 py-1.5 text-sm text-gray-200"
              >
                {Object.entries(SORTS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
              </select>
            </div>
            {projects.length === 0 ? (
              <div className="text-center py-12">
                <p className="text-gray-300 mb-2">No projects yet</p>
                <p className="text-sm text-gray-400">Use “New project” to get started.</p>
              </div>
            ) : shown.length === 0 ? (
              <p className="py-8 text-center text-sm text-gray-400">No projects match “{query}”.</p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {shown.map((project) => (
                  <ProjectCard
                    key={project.id}
                    project={project}
                    isActive={activeProject?.id === project.id}
                    onSetActive={() => setActive(project)}
                    onDelete={loadProjects}
                    personas={personas}
                    onRefresh={loadProjects}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
