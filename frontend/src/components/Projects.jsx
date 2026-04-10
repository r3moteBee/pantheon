import React, { useState, useEffect } from 'react'
import { Trash2, Plus, Check, RefreshCw, Calendar, User } from 'lucide-react'
import { useStore } from '../store'
import { projectsApi, personasApi } from '../api/client'
import { ExportButton, ImportButton } from './ProjectPortability'

function CreateProjectForm({ onProjectCreated, personas }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [id, setId] = useState('')
  const [personaId, setPersonaId] = useState('pan')
  const [loading, setLoading] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  const createProject = async () => {
    if (!name.trim()) return
    setLoading(true)
    try {
      const res = await projectsApi.create(name, description, id || undefined)
      const projectId = res.data?.id || id
      // Apply persona if one was selected
      if (personaId && projectId) {
        try {
          await personasApi.apply(personaId, projectId)
        } catch (e) {
          console.warn('Failed to apply persona:', e)
        }
      }
      setName('')
      setDescription('')
      setId('')
      setPersonaId('pan')
      addNotification({ type: 'success', message: 'Project created' })
      onProjectCreated()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 space-y-3">
      <h3 className="text-sm font-semibold text-gray-200">Create New Project</h3>
      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Project name..."
        className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
      />
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Description (optional)..."
        rows={2}
        className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 resize-none"
      />
      <input
        type="text"
        value={id}
        onChange={(e) => setId(e.target.value)}
        placeholder="Custom ID (optional)..."
        className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
      />

      {/* Persona selector */}
      <div>
        <label className="block text-xs text-gray-400 mb-1">Agent Persona</label>
        <select
          value={personaId}
          onChange={(e) => setPersonaId(e.target.value)}
          className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
        >
          <option value="">None (use global personality)</option>
          {personas.map((p) => (
            <option key={p.id} value={p.id}>
              {p.icon} {p.name} — {p.tagline}
            </option>
          ))}
        </select>
      </div>

      <button
        onClick={createProject}
        disabled={loading || !name.trim()}
        className="w-full px-3 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded disabled:opacity-50"
      >
        {loading ? 'Creating...' : 'Create Project'}
      </button>
    </div>
  )
}

function ProjectCard({ project, isActive, onSetActive, onDelete, personas, onRefresh }) {
  const [deleting, setDeleting] = useState(false)
  const [changingPersona, setChangingPersona] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  const deleteProject = async () => {
    if (isActive) {
      addNotification({ type: 'error', message: 'Cannot delete active project' })
      return
    }
    if (!confirm(`Delete project "${project.name}"? This cannot be undone.`)) return

    setDeleting(true)
    try {
      await projectsApi.delete(project.id)
      addNotification({ type: 'success', message: 'Project deleted' })
      onDelete()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setDeleting(false)
  }

  const changePersona = async (newPersonaId) => {
    if (!newPersonaId) return
    setChangingPersona(true)
    try {
      await personasApi.apply(newPersonaId, project.id)
      addNotification({ type: 'success', message: `Persona applied to ${project.name}` })
      if (onRefresh) onRefresh()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setChangingPersona(false)
  }

  const formatDate = (dateStr) => {
    try {
      return new Date(dateStr).toLocaleDateString()
    } catch {
      return dateStr
    }
  }

  const currentPersona = personas.find((p) => p.id === project.persona_id)

  return (
    <div
      className={`
        rounded-lg border-2 p-4 transition-all
        ${isActive
          ? 'bg-brand-900 border-brand-600'
          : 'bg-gray-800 border-gray-700 hover:border-gray-600'
        }
      `}
    >
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <h3 className="font-semibold text-gray-100">{project.name}</h3>
            {isActive && (
              <span className="inline-flex items-center gap-1 px-2 py-1 bg-green-900 text-green-200 text-xs rounded-full">
                <div className="w-1.5 h-1.5 rounded-full bg-green-400" />
                Active
              </span>
            )}
          </div>
          {project.description && (
            <p className="text-sm text-gray-400 mt-1 line-clamp-2">{project.description}</p>
          )}
        </div>
      </div>

      <div className="space-y-2 mb-4">
        <div className="text-xs text-gray-500">
          <span className="font-mono">ID: {project.id}</span>
        </div>
        {project.created_at && (
          <div className="text-xs text-gray-600 flex items-center gap-1">
            <Calendar className="w-3 h-3" />
            Created {formatDate(project.created_at)}
          </div>
        )}
      </div>

      {/* Persona selector */}
      <div className="mb-3">
        <label className="block text-xs text-gray-500 mb-1 flex items-center gap-1">
          <User className="w-3 h-3" /> Persona
        </label>
        <select
          value={project.persona_id || ''}
          onChange={(e) => changePersona(e.target.value)}
          disabled={changingPersona}
          className="w-full bg-gray-900 border border-gray-600 rounded px-2 py-1.5 text-xs text-gray-200 focus:outline-none focus:border-brand-500 disabled:opacity-50"
        >
          <option value="">None (global personality)</option>
          {personas.map((p) => (
            <option key={p.id} value={p.id}>
              {p.icon} {p.name}
            </option>
          ))}
        </select>
      </div>

      <div className="flex gap-2">
        {!isActive && (
          <button
            onClick={onSetActive}
            className="flex-1 px-3 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded transition-colors"
          >
            <Check className="w-3.5 h-3.5 inline mr-2" />
            Set Active
          </button>
        )}
        <ExportButton project={project} />
        <button
          onClick={deleteProject}
          disabled={isActive || deleting}
          className="px-3 py-2 bg-gray-700 hover:bg-red-900 disabled:opacity-50 text-gray-300 hover:text-red-200 text-sm rounded transition-colors"
        >
          <Trash2 className="w-3.5 h-3.5" />
        </button>
      </div>
    </div>
  )
}

export default function Projects() {
  const [loading, setLoading] = useState(false)
  const [personas, setPersonas] = useState([])
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

  return (
    <div className="flex flex-col h-full bg-gray-950">
      {/* Header */}
      <div className="px-6 py-4 bg-gray-900 border-b border-gray-800 flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-100">Projects</h1>
        <div className="flex items-center gap-2">
          <ImportButton onImported={loadProjects} />
          <button
            onClick={loadProjects}
            disabled={loading}
            className="p-2 text-gray-400 hover:text-gray-300 disabled:opacity-50"
          >
            <RefreshCw className="w-4 h-4" />
          </button>
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto scrollbar-thin p-6">
        <div className="max-w-4xl mx-auto space-y-6">
          <CreateProjectForm onProjectCreated={loadProjects} personas={personas} />

          <div>
            <h2 className="text-sm font-semibold text-gray-400 mb-4">All Projects</h2>
            {projects.length === 0 ? (
              <div className="text-center py-12">
                <p className="text-gray-600 mb-4">No projects yet</p>
                <p className="text-sm text-gray-700">Create a new project to get started</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                {projects.map((project) => (
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
