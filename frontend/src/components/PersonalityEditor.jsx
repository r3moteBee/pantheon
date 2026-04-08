import React, { useState, useEffect, useCallback } from 'react'
import { Save, RefreshCw, Globe, AlertCircle, CheckCircle, RotateCcw, Info, ChevronDown, BookOpen, Search, Pencil, Eye } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useStore } from '../store'
import { personalityApi, personasApi, projectsApi } from '../api/client'
import CoreEditor from './CoreEditor'

// Extract the first heading or "You are X" line from soul.md as the agent name
function extractAgentName(content) {
  if (!content) return null
  const headingMatch = content.match(/^#\s+(.+)/m)
  if (headingMatch) return headingMatch[1].replace(/^(The Soul of|Soul of)\s+/i, '').trim()
  const youAreMatch = content.match(/^You are ([A-Za-z0-9_-]+)/m)
  if (youAreMatch) return youAreMatch[1]
  return null
}

export default function PersonalityEditor() {
  const [activeTab, setActiveTab] = useState('soul')
  const [viewMode, setViewMode] = useState('preview')  // 'preview' | 'edit'

  // Project selection — null means global
  const [selectedProjectId, setSelectedProjectId] = useState(null)
  const [projects, setProjects] = useState([])
  const [projectSearch, setProjectSearch] = useState('')

  // Persona selection
  const [personas, setPersonas] = useState([])
  const [selectedPersonaId, setSelectedPersonaId] = useState('')
  const [applyingPersona, setApplyingPersona] = useState(false)

  // Save as Persona modal
  const [showSaveAsPersona, setShowSaveAsPersona] = useState(false)
  const [savePersonaName, setSavePersonaName] = useState('')
  const [savePersonaTagline, setSavePersonaTagline] = useState('')
  const [savingAsPersona, setSavingAsPersona] = useState(false)

  // Content state
  const [soulContent, setSoulContent]   = useState('')
  const [agentContent, setAgentContent] = useState('')
  const [isOverride, setIsOverride]     = useState(false)
  const [contentLoaded, setContentLoaded] = useState(false)
  const [loading, setLoading]   = useState(false)
  const [saving, setSaving]     = useState(false)
  const [resetting, setResetting] = useState(false)
  const [savedAt, setSavedAt]   = useState(null)

  const addNotification = useStore((s) => s.addNotification)

  const projectId = selectedProjectId // null = global

  // Load projects list
  useEffect(() => {
    const load = async () => {
      try {
        const res = await projectsApi.list()
        setProjects(res.data.projects || [])
      } catch (e) {
        console.warn('Failed to load projects:', e)
      }
    }
    load()
  }, [])

  // Load personas list
  useEffect(() => {
    const load = async () => {
      try {
        const data = await personasApi.list()
        setPersonas(data.personas || [])
      } catch (e) {
        console.warn('Failed to load personas:', e)
      }
    }
    load()
  }, [])

  // Try to detect which persona is active for the selected project
  useEffect(() => {
    if (!selectedProjectId) {
      setSelectedPersonaId('')
      return
    }
    const proj = projects.find((p) => p.id === selectedProjectId)
    setSelectedPersonaId(proj?.persona_id || '')
  }, [selectedProjectId, projects])

  const loadPersonality = useCallback(async () => {
    setLoading(true)
    setContentLoaded(false)
    try {
      const [soulRes, agentRes] = await Promise.all([
        personalityApi.getSoul(projectId),
        personalityApi.getAgent(projectId),
      ])
      setSoulContent(soulRes.data.content  ?? '')
      setAgentContent(agentRes.data.content ?? '')
      setIsOverride(soulRes.data.is_override ?? false)
      setContentLoaded(true)
    } catch (err) {
      addNotification({ type: 'error', message: `Failed to load personality: ${err.message}` })
    }
    setLoading(false)
  }, [projectId, addNotification])

  useEffect(() => {
    loadPersonality()
  }, [loadPersonality])

  const save = async () => {
    setSaving(true)
    try {
      if (activeTab === 'soul') {
        await personalityApi.updateSoul(soulContent, projectId)
      } else {
        await personalityApi.updateAgent(agentContent, projectId)
      }
      setSavedAt(new Date())
      if (projectId) setIsOverride(true)
      addNotification({ type: 'success', message: 'Saved — takes effect on the next chat' })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setSaving(false)
  }

  // Apply a persona — copies its soul content into the current project
  const applyPersona = async (personaId) => {
    if (!personaId) return
    if (!projectId) {
      addNotification({ type: 'error', message: 'Select a project first — personas are applied to projects, not global.' })
      return
    }
    setApplyingPersona(true)
    try {
      await personasApi.apply(personaId, projectId)
      setSelectedPersonaId(personaId)
      // Refresh projects metadata (persona_id stored there)
      const res = await projectsApi.list()
      setProjects(res.data.projects || [])
      // Reload the editor content
      await loadPersonality()
      const persona = personas.find((p) => p.id === personaId)
      addNotification({ type: 'success', message: `Applied "${persona?.name || personaId}" — soul content copied to project` })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setApplyingPersona(false)
  }

  // Reset to global defaults
  const resetToDefaults = async () => {
    if (!window.confirm('Reset to the built-in defaults? This will overwrite your current content.')) return
    setResetting(true)
    try {
      const freshSoul  = (await personalityApi.getSoul(null)).data.content
      const freshAgent = (await personalityApi.getAgent(null)).data.content
      await personalityApi.updateSoul(freshSoul, projectId)
      await personalityApi.updateAgent(freshAgent, projectId)
      setSoulContent(freshSoul)
      setAgentContent(freshAgent)
      if (projectId) setIsOverride(true)
      setSavedAt(new Date())
      addNotification({ type: 'success', message: 'Reset to defaults complete' })
    } catch (err) {
      addNotification({ type: 'error', message: `Reset failed: ${err.message}` })
    }
    setResetting(false)
  }

  // Save current soul content as a new persona in the library
  const saveAsPersona = async () => {
    if (!savePersonaName.trim()) return
    setSavingAsPersona(true)
    try {
      const agentName = extractAgentName(soulContent)
      await personasApi.create({
        name: savePersonaName.trim(),
        tagline: savePersonaTagline.trim() || `Custom persona${agentName ? ` based on ${agentName}` : ''}`,
        description: `User-created persona${agentName ? ` derived from ${agentName}` : ''}.`,
        icon: '🎭',
        traits: [],
        best_for: '',
        soul: soulContent,
      })
      // Refresh personas list
      const data = await personasApi.list()
      setPersonas(data.personas || [])
      setShowSaveAsPersona(false)
      setSavePersonaName('')
      setSavePersonaTagline('')
      addNotification({ type: 'success', message: 'Persona saved to library' })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setSavingAsPersona(false)
  }

  const agentName = extractAgentName(soulContent) || null
  const currentContent = activeTab === 'soul' ? soulContent : agentContent
  const isEmpty = contentLoaded && !currentContent.trim()

  const scopeLabel = projectId ? projects.find((p) => p.id === projectId)?.name || projectId : 'Global'

  // Filter projects for the dropdown search
  const filteredProjects = projectSearch
    ? projects.filter((p) =>
        p.name.toLowerCase().includes(projectSearch.toLowerCase()) ||
        p.id.toLowerCase().includes(projectSearch.toLowerCase())
      )
    : projects

  const TABS = [
    {
      id: 'soul',
      label: 'Identity & Values',
      file: 'soul.md',
      placeholder: "Define who the agent is — name, purpose, values, and core commitments.\n\nWrite in Markdown. This file shapes the agent's personality in every conversation.",
    },
    {
      id: 'agent',
      label: 'Behavior & Instructions',
      file: 'agent.md',
      placeholder: "Write detailed behavioral rules, response format preferences, and task-specific instructions.\n\nWrite in Markdown. This supplements the identity file with operational guidance.",
    },
  ]
  const activeTabDef = TABS.find((t) => t.id === activeTab)

  return (
    <div className="flex flex-col h-full bg-gray-950">

      {/* Header with selectors */}
      <div className="px-6 py-4 bg-gray-900 border-b border-gray-800">
        <div className="flex items-start justify-between gap-4 mb-3">
          <div>
            <h1 className="text-xl font-bold text-gray-100">
              Personality
              {agentName && (
                <span className="ml-2 text-brand-400">{agentName}</span>
              )}
            </h1>
            <p className="text-sm text-gray-500 mt-0.5">
              Configure the agent's identity and behavioral rules. Changes apply on the next chat.
            </p>
          </div>
        </div>

        {/* Project + Persona selectors row */}
        <div className="flex items-end gap-3 flex-wrap">
          {/* Project selector */}
          <div className="flex-1 min-w-[200px] max-w-[300px]">
            <label className="block text-xs text-gray-400 mb-1">
              <Globe className="w-3 h-3 inline mr-1" />
              Scope
            </label>
            <select
              value={selectedProjectId || '__global__'}
              onChange={(e) => setSelectedProjectId(e.target.value === '__global__' ? null : e.target.value)}
              className="w-full bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
            >
              <option value="__global__">Global (all projects)</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.persona_id ? `${personas.find((pe) => pe.id === p.persona_id)?.icon || '🎭'} ` : ''}{p.name}
                </option>
              ))}
            </select>
          </div>

          {/* Persona selector — only when a project is selected */}
          {projectId && (
            <div className="flex-1 min-w-[200px] max-w-[300px]">
              <label className="block text-xs text-gray-400 mb-1">
                <BookOpen className="w-3 h-3 inline mr-1" />
                Apply Persona
              </label>
              <div className="flex gap-1.5">
                <select
                  value={selectedPersonaId}
                  onChange={(e) => setSelectedPersonaId(e.target.value)}
                  disabled={applyingPersona}
                  className="flex-1 bg-gray-800 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 disabled:opacity-50"
                >
                  <option value="">Choose a persona...</option>
                  {personas.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.icon} {p.name} — {p.tagline}
                    </option>
                  ))}
                </select>
                <button
                  onClick={() => applyPersona(selectedPersonaId)}
                  disabled={!selectedPersonaId || applyingPersona}
                  title="Apply persona — copies its soul content to this project"
                  className="px-3 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded disabled:opacity-50 flex-shrink-0"
                >
                  {applyingPersona ? '...' : 'Apply'}
                </button>
              </div>
            </div>
          )}

          {/* Reset to defaults */}
          <button
            onClick={resetToDefaults}
            disabled={resetting}
            title="Reset to built-in global defaults"
            className="flex items-center gap-1.5 px-3 py-2 bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-gray-200 text-sm rounded border border-gray-700 disabled:opacity-50"
          >
            <RotateCcw className={`w-3.5 h-3.5 ${resetting ? 'animate-spin' : ''}`} />
            Reset
          </button>
        </div>

        {/* Context banners */}
        {!projectId && (
          <div className="mt-3 flex items-center gap-2 text-xs text-gray-500">
            <Globe className="w-3 h-3 flex-shrink-0" />
            Editing the <span className="text-gray-300 font-medium">global</span> personality used by all projects without an override.
          </div>
        )}
        {projectId && !isOverride && (
          <div className="mt-3 flex items-center gap-2 px-3 py-2 bg-yellow-950/40 border border-yellow-800/40 rounded-lg text-xs text-yellow-400">
            <AlertCircle className="w-3 h-3 flex-shrink-0" />
            No project override exists yet — showing the global personality. Saving or applying a persona will create a project-specific copy.
          </div>
        )}
        {projectId && isOverride && (
          <div className="mt-3 flex items-center gap-2 px-3 py-2 bg-brand-950/40 border border-brand-800/40 rounded-lg text-xs text-brand-400">
            <CheckCircle className="w-3 h-3 flex-shrink-0" />
            Editing <span className="font-medium">{scopeLabel}</span>'s personality. Edits stay local to this project.
          </div>
        )}

        {/* Empty-file warning */}
        {isEmpty && !loading && (
          <div className="mt-3 flex items-center justify-between gap-3 px-3 py-2 bg-orange-950/40 border border-orange-800/40 rounded-lg text-xs text-orange-400">
            <span className="flex items-center gap-2">
              <Info className="w-3 h-3 flex-shrink-0" />
              The <span className="font-mono">{activeTabDef?.file}</span> file is empty. Use Reset or apply a persona to populate it.
            </span>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-800 bg-gray-900">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => { setActiveTab(tab.id); setViewMode('preview') }}
            className={`px-5 py-3 text-sm font-medium border-b-2 transition-colors ${
              activeTab === tab.id
                ? 'border-brand-500 text-brand-400'
                : 'border-transparent text-gray-400 hover:text-gray-300'
            }`}
          >
            <span>{tab.label}</span>
            <span className="ml-2 text-xs font-mono text-gray-600">{tab.file}</span>
          </button>
        ))}
      </div>

      {/* Single full-width pane — toggles between preview and editor */}
      <div className="flex-1 flex flex-col overflow-hidden min-h-0 bg-gray-900">

        {/* Mode header */}
        <div className="px-4 py-2 border-b border-gray-800 flex items-center justify-between flex-shrink-0">
          <div className="flex items-center gap-2">
            <p className="text-sm font-medium text-gray-300">
              {viewMode === 'edit' ? 'Editing' : 'Preview'}
            </p>
            <p className="text-xs text-gray-600">{activeTabDef?.file}</p>
          </div>
          <button
            onClick={() => setViewMode(viewMode === 'edit' ? 'preview' : 'edit')}
            disabled={loading}
            className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs rounded-md disabled:opacity-50"
            title={viewMode === 'edit' ? 'Back to preview' : 'Edit content'}
          >
            {viewMode === 'edit' ? (
              <><Eye className="w-3.5 h-3.5" /> Preview</>
            ) : (
              <><Pencil className="w-3.5 h-3.5" /> Edit</>
            )}
          </button>
        </div>

        {/* Content */}
        <div className="flex-1 overflow-hidden min-h-0">
          {viewMode === 'edit' ? (
            <CoreEditor
              value={currentContent}
              onChange={(val) => {
                activeTab === 'soul'
                  ? setSoulContent(val)
                  : setAgentContent(val)
              }}
              language="markdown"
              editable={!loading}
              height="100%"
            />
          ) : (
            <div className="h-full overflow-y-auto scrollbar-thin p-6">
              {loading ? (
                <div className="flex items-center justify-center h-full">
                  <RefreshCw className="w-5 h-5 text-gray-600 animate-spin" />
                </div>
              ) : isEmpty ? (
                <p className="text-sm text-gray-600 italic">No content — apply a persona or use Reset to populate, then click Edit to author it.</p>
              ) : (
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  className="prose prose-invert prose-sm max-w-3xl mx-auto"
                  components={{
                    code: ({ node, inline, className, children, ...props }) => {
                      if (inline) {
                        return (
                          <code className="bg-gray-800 px-1 py-0.5 rounded text-xs font-mono" {...props}>
                            {children}
                          </code>
                        )
                      }
                      return (
                        <pre className="bg-gray-950 rounded-lg p-3 overflow-x-auto">
                          <code className="text-green-300 text-xs font-mono" {...props}>{children}</code>
                        </pre>
                      )
                    },
                  }}
                >
                  {currentContent}
                </ReactMarkdown>
              )}
            </div>
          )}
        </div>

        {/* Action bar */}
        <div className="px-4 py-3 bg-gray-900 border-t border-gray-800 flex items-center gap-2 flex-shrink-0">
          <button
            onClick={loadPersonality}
            disabled={loading || saving}
            className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-lg disabled:opacity-50"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
            Reload
          </button>

          {activeTab === 'soul' && soulContent.trim() && (
            <button
              onClick={() => {
                setSavePersonaName(agentName || '')
                setSavePersonaTagline('')
                setShowSaveAsPersona(true)
              }}
              className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-lg"
            >
              <BookOpen className="w-4 h-4" />
              Save as Persona
            </button>
          )}

          {savedAt && (
            <span className="text-xs text-gray-600 ml-1">
              Saved {savedAt.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
            </span>
          )}

          <button
            onClick={save}
            disabled={loading || saving || !currentContent.trim()}
            className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-lg disabled:opacity-50 ml-auto"
          >
            <Save className="w-4 h-4" />
            {saving ? 'Saving…' : `Save ${projectId ? 'Project' : 'Global'}`}
          </button>
        </div>
      </div>

      {/* Save as Persona modal */}
      {showSaveAsPersona && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowSaveAsPersona(false)}>
          <div className="bg-gray-800 rounded-lg border border-gray-700 p-5 w-full max-w-md mx-4 space-y-3" onClick={(e) => e.stopPropagation()}>
            <h3 className="text-sm font-semibold text-gray-200">Save as Persona</h3>
            <p className="text-xs text-gray-500">
              Save the current soul content as a new persona template in your library. This creates a snapshot — future edits here won't change the saved persona.
            </p>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Persona Name</label>
              <input
                type="text"
                value={savePersonaName}
                onChange={(e) => setSavePersonaName(e.target.value)}
                placeholder="My Custom Agent"
                className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
                autoFocus
              />
            </div>
            <div>
              <label className="block text-xs text-gray-400 mb-1">Tagline (optional)</label>
              <input
                type="text"
                value={savePersonaTagline}
                onChange={(e) => setSavePersonaTagline(e.target.value)}
                placeholder="Short description..."
                className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setShowSaveAsPersona(false)}
                className="px-3 py-2 bg-gray-700 hover:bg-gray-600 text-gray-300 text-sm rounded"
              >
                Cancel
              </button>
              <button
                onClick={saveAsPersona}
                disabled={savingAsPersona || !savePersonaName.trim()}
                className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded disabled:opacity-50"
              >
                <BookOpen className="w-4 h-4" />
                {savingAsPersona ? 'Saving...' : 'Save to Library'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
