import { useEffect, useState } from 'react'
import { llmApi } from '../../api/client'
import RoleMappingRow from './RoleMappingRow'

const ROLES = [
  { id: 'chat', label: 'Chat', description: 'Main agent loop' },
  { id: 'prefill', label: 'Prefill / fallback', description: 'Curation, summarization, secondary calls' },
  { id: 'vision', label: 'Vision', description: 'Image-aware completions (optional)' },
  { id: 'embed', label: 'Embeddings', description: 'Semantic memory + topic embeddings' },
  { id: 'rerank', label: 'Reranker', description: 'Optional re-ranker for retrieval' },
]

export default function RoleMapping({ refreshKey }) {
  const [endpoints, setEndpoints] = useState([])
  const [roles, setRolesState] = useState({})
  const [saving, setSaving] = useState(false)
  const [saveStatus, setSaveStatus] = useState('')

  const refresh = async () => {
    const [eps, rm] = await Promise.all([llmApi.listEndpoints(), llmApi.getRoles()])
    setEndpoints(eps)
    setRolesState(rm)
  }

  useEffect(() => {
    refresh()
  }, [refreshKey])

  const handleRowChange = (roleId, value) => {
    setRolesState((prev) => ({ ...prev, [roleId]: value }))
  }

  const handleSave = async () => {
    setSaving(true)
    setSaveStatus('')
    try {
      const payload = ROLES.map(({ id }) => ({
        role: id,
        endpoint: roles[id]?.endpoint || '',
        model: roles[id]?.model || '',
      }))
      await llmApi.setRoles(payload)
      setSaveStatus('Saved')
    } catch (e) {
      setSaveStatus(`Error: ${String(e?.message || e)}`)
    } finally {
      setSaving(false)
    }
  }

  return (
    <section className='space-y-2'>
      <header className='flex items-center justify-between'>
        <h3 className='text-sm font-semibold text-gray-200'>Role mapping</h3>
        <span className='text-xs text-gray-500'>{endpoints.length} endpoints available</span>
      </header>
      <div className='border border-gray-700 rounded-md bg-gray-900/40 px-3'>
        {ROLES.map(({ id, label, description }) => (
          <RoleMappingRow
            key={id}
            role={id}
            label={label}
            description={description}
            endpoints={endpoints}
            value={roles[id]}
            onChange={(v) => handleRowChange(id, v)}
          />
        ))}
      </div>
      <div className='flex items-center gap-3'>
        <button
          type='button'
          onClick={handleSave}
          disabled={saving}
          className='text-xs px-3 py-1 rounded bg-emerald-700 hover:bg-emerald-600 disabled:opacity-50'
        >
          {saving ? 'Saving…' : 'Save role mapping'}
        </button>
        {saveStatus && (
          <span className={`text-xs ${saveStatus.startsWith('Error') ? 'text-red-300' : 'text-emerald-300'}`}>
            {saveStatus}
          </span>
        )}
      </div>
    </section>
  )
}
