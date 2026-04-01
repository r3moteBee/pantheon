import React, { useState, useEffect, useCallback } from 'react'
import { Save, RefreshCw, Globe, Briefcase, AlertCircle, CheckCircle, RotateCcw, Info } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useStore } from '../store'
import { personalityApi } from '../api/client'

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
  // scope: 'global' = edit data/personality/*.md
  //        'project' = edit project override (falls back to global on read)
  const [scope, setScope] = useState('global')

  const [soulContent, setSoulContent]   = useState('')
  const [agentContent, setAgentContent] = useState('')
  const [isOverride, setIsOverride]     = useState(false)
  const [contentLoaded, setContentLoaded] = useState(false)  // true once a successful load has run
  const [loading, setLoading]   = useState(false)
  const [saving, setSaving]     = useState(false)
  const [resetting, setResetting] = useState(false)
  const [savedAt, setSavedAt]   = useState(null)

  const activeProject  = useStore((s) => s.activeProject)
  const addNotification = useStore((s) => s.addNotification)

  const projectId = scope === 'project' ? (activeProject?.id || 'default') : null

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
      if (scope === 'project') setIsOverride(true)
      addNotification({ type: 'success', message: 'Saved — takes effect on the next chat' })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setSaving(false)
  }

  // Reset to defaults: save the server-side template content to the live data dir.
  // We do this by fetching the status endpoint (which shows template content),
  // then saving the template value into the live slot.
  const resetToDefaults = async () => {
    if (!window.confirm('Reset to the built-in defaults? This will overwrite your current content.')) return
    setResetting(true)
    try {
      const statusRes = await personalityApi.status()
      const templates = statusRes.data.templates
      const soulTemplate  = templates['soul.md']?.preview   // Only a 120-char preview — use full API instead
      // Trigger the server to reload by saving the template-backed content
      // The backend already reads from the template when the live file is empty,
      // so just force-write what the backend would return right now.
      const freshSoul  = (await personalityApi.getSoul(null)).data.content
      const freshAgent = (await personalityApi.getAgent(null)).data.content
      await personalityApi.updateSoul(freshSoul, projectId)
      await personalityApi.updateAgent(freshAgent, projectId)
      setSoulContent(freshSoul)
      setAgentContent(freshAgent)
      if (scope === 'project') setIsOverride(true)
      setSavedAt(new Date())
      addNotification({ type: 'success', message: 'Reset to defaults complete' })
    } catch (err) {
      addNotification({ type: 'error', message: `Reset failed: ${err.message}` })
    }
    setResetting(false)
  }

  const agentName = extractAgentName(soulContent) || null

  const hasProject = activeProject && activeProject.id !== 'default'

  const currentContent = activeTab === 'soul' ? soulContent : agentContent
  const isEmpty = contentLoaded && !currentContent.trim()

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

      {/* Header */}
      <div className="px-6 py-4 bg-gray-900 border-b border-gray-800">
        <div className="flex items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-bold text-gray-100">
              Personality
              {agentName && (
                <span className="ml-2 text-brand-400">{agentName}</span>
              )}
            </h1>
            <p className="text-sm text-gray-500 mt-0.5">
              Configure the agent's identity and behavioral rules. Changes apply immediately on the next chat.
            </p>
          </div>

          {/* Scope switcher */}
          <div className="flex-shrink-0">
            <div className="flex rounded-lg overflow-hidden border border-gray-700 text-xs">
              <button
                onClick={() => setScope('global')}
                className={`flex items-center gap-1.5 px-3 py-2 transition-colors ${
                  scope === 'global'
                    ? 'bg-brand-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:text-white'
                }`}
              >
                <Globe className="w-3 h-3" />
                Global
              </button>
              <button
                onClick={() => setScope('project')}
                disabled={!hasProject}
                title={!hasProject ? 'Select a non-default project to create a project override' : ''}
                className={`flex items-center gap-1.5 px-3 py-2 transition-colors disabled:opacity-40 disabled:cursor-not-allowed ${
                  scope === 'project'
                    ? 'bg-brand-600 text-white'
                    : 'bg-gray-800 text-gray-400 hover:text-white'
                }`}
              >
                <Briefcase className="w-3 h-3" />
                {activeProject?.name || 'Project'}
              </button>
            </div>
          </div>
        </div>

        {/* Scope / override info banner */}
        {scope === 'global' && (
          <div className="mt-3 flex items-center gap-2 text-xs text-gray-500">
            <Globe className="w-3 h-3 flex-shrink-0" />
            Editing the <span className="text-gray-300 font-medium">global</span> personality used by all projects that don't have an override.
          </div>
        )}
        {scope === 'project' && !isOverride && (
          <div className="mt-3 flex items-center gap-2 px-3 py-2 bg-yellow-950/40 border border-yellow-800/40 rounded-lg text-xs text-yellow-400">
            <AlertCircle className="w-3 h-3 flex-shrink-0" />
            No project override exists yet — showing the global personality. Saving will create a project-specific override.
          </div>
        )}
        {scope === 'project' && isOverride && (
          <div className="mt-3 flex items-center gap-2 px-3 py-2 bg-brand-950/40 border border-brand-800/40 rounded-lg text-xs text-brand-400">
            <CheckCircle className="w-3 h-3 flex-shrink-0" />
            Editing <span className="font-medium">{activeProject?.name}</span>'s personality override. The global personality is not affected.
          </div>
        )}

        {/* Empty-file warning */}
        {isEmpty && !loading && (
          <div className="mt-3 flex items-center justify-between gap-3 px-3 py-2 bg-orange-950/40 border border-orange-800/40 rounded-lg text-xs text-orange-400">
            <span className="flex items-center gap-2">
              <Info className="w-3 h-3 flex-shrink-0" />
              The <span className="font-mono">{activeTabDef?.file}</span> file is empty on the server. Reset to restore the built-in defaults.
            </span>
            <button
              onClick={resetToDefaults}
              disabled={resetting}
              className="flex-shrink-0 flex items-center gap-1.5 px-3 py-1.5 bg-orange-700 hover:bg-orange-600 text-white rounded-md disabled:opacity-50"
            >
              <RotateCcw className={`w-3 h-3 ${resetting ? 'animate-spin' : ''}`} />
              {resetting ? 'Resetting…' : 'Reset to Defaults'}
            </button>
          </div>
        )}
      </div>

      {/* Tabs */}
      <div className="flex border-b border-gray-800 bg-gray-900">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
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

      {/* Editor + Preview */}
      <div className="flex-1 flex overflow-hidden min-h-0">

        {/* Editor pane */}
        <div className="flex-1 flex flex-col border-r border-gray-800 min-w-0">
          <textarea
            value={currentContent}
            onChange={(e) => {
              activeTab === 'soul'
                ? setSoulContent(e.target.value)
                : setAgentContent(e.target.value)
            }}
            disabled={loading}
            spellCheck={false}
            className="flex-1 w-full resize-none bg-gray-800 text-gray-100 p-4 text-sm font-mono leading-relaxed focus:outline-none focus:ring-1 focus:ring-inset focus:ring-brand-500 disabled:opacity-50 scrollbar-thin"
            placeholder={activeTabDef?.placeholder}
          />

          {/* Action bar */}
          <div className="px-4 py-3 bg-gray-900 border-t border-gray-800 flex items-center gap-2">
            <button
              onClick={loadPersonality}
              disabled={loading || saving}
              className="flex items-center gap-2 px-3 py-2 bg-gray-800 hover:bg-gray-700 text-gray-300 text-sm rounded-lg disabled:opacity-50"
            >
              <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin' : ''}`} />
              Reload
            </button>

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
              {saving ? 'Saving…' : `Save ${scope === 'project' ? 'Project Override' : 'Global'}`}
            </button>
          </div>
        </div>

        {/* Preview pane */}
        <div className="w-5/12 flex-shrink-0 flex flex-col bg-gray-900">
          <div className="px-4 py-3 border-b border-gray-800 flex items-center justify-between">
            <p className="text-sm font-medium text-gray-300">Preview</p>
            <p className="text-xs text-gray-600">{activeTabDef?.file}</p>
          </div>
          <div className="flex-1 overflow-y-auto scrollbar-thin p-5">
            {loading ? (
              <div className="flex items-center justify-center h-full">
                <RefreshCw className="w-5 h-5 text-gray-600 animate-spin" />
              </div>
            ) : isEmpty ? (
              <p className="text-sm text-gray-600 italic">No content — use "Reset to Defaults" to restore the built-in file.</p>
            ) : (
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                className="prose prose-invert prose-sm max-w-none"
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
        </div>
      </div>
    </div>
  )
}
