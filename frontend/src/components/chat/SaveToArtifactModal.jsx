import React from 'react'
import { Bookmark, Loader, Save } from 'lucide-react'
import { artifactsApi } from '../../api/client'

// Default artifact path for a saved chat reply:
// <project-slug>/responses/<date>-<slug of the first 60 chars>.md
export function defaultSavePath(content, projectName) {
  const stripped = content
    .slice(0, 60)
    .replace(/[`*_~#>]/g, '')
    .replace(/[^a-zA-Z0-9]+/g, '-')
    .toLowerCase()
    .replace(/^-+|-+$/g, '')
  const slug = stripped || 'response'
  const date = new Date().toISOString().slice(0, 10)
  const projName = projectName || 'default'
  const projSlug = projName.replace(/[^a-zA-Z0-9]+/g, '-').toLowerCase().replace(/^-+|-+$/g, '') || 'default'
  return `${projSlug}/responses/${date}-${slug}.md`
}

export default function SaveToArtifactModal({ defaultPath, content, projectId, onClose, onSaved }) {
  const [path, setPath] = React.useState(defaultPath)
  const [tagsInput, setTagsInput] = React.useState('from-chat')
  const [saving, setSaving] = React.useState(false)
  const [error, setError] = React.useState(null)

  const handleSave = async () => {
    setSaving(true); setError(null)
    try {
      const finalPath = path.endsWith('.md') ? path : `${path}.md`
      const tags = tagsInput
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean)
      const res = await artifactsApi.create({
        project_id: projectId,
        path: finalPath,
        content,
        content_type: 'text/markdown',
        tags,
        source: { kind: 'chat-message-save' },
      })
      onSaved?.(res.data)
    } catch (e) {
      setError(e.message || 'Save failed')
    } finally { setSaving(false) }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50" onClick={onClose}>
      <div
        className="bg-gray-900 border border-gray-700 rounded-lg shadow-xl w-full max-w-md p-4"
        onClick={(e) => e.stopPropagation()}
      >
        <h3 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
          <Bookmark className="w-4 h-4" /> Save as artifact
        </h3>
        <label className="block text-xs text-gray-400 mb-1">Path</label>
        <input
          autoFocus
          type="text"
          value={path}
          onChange={(e) => setPath(e.target.value)}
          placeholder="responses/note.md"
          className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-md text-white font-mono focus:outline-none focus:ring-1 focus:ring-brand-600"
          onKeyDown={(e) => { if (e.key === 'Enter') handleSave() }}
        />
        <label className="block text-xs text-gray-400 mt-3 mb-1">Tags (comma-separated)</label>
        <input
          type="text"
          value={tagsInput}
          onChange={(e) => setTagsInput(e.target.value)}
          className="w-full px-3 py-2 text-sm bg-gray-800 border border-gray-700 rounded-md text-white focus:outline-none focus:ring-1 focus:ring-brand-600"
        />
        <p className="text-[11px] text-gray-500 mt-2">
          {content.length.toLocaleString()} characters · markdown
        </p>
        {error && (
          <div className="mt-2 text-xs text-red-400">{error}</div>
        )}
        <div className="flex justify-end gap-2 mt-4">
          <button
            onClick={onClose}
            className="px-3 py-1.5 text-xs text-gray-400 hover:text-white"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={saving || !path.trim()}
            className="px-3 py-1.5 text-xs font-medium text-white bg-brand-600 hover:bg-brand-500 rounded-md disabled:opacity-50 flex items-center gap-1.5"
          >
            {saving ? <Loader className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>
      </div>
    </div>
  )
}
