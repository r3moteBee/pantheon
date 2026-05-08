import { useEffect, useState } from 'react'
import { llmApi } from '../../api/client'

export default function RoleMappingRow({
  role, label, description, endpoints, value, onChange,
}) {
  const [models, setModels] = useState([])
  const [probing, setProbing] = useState(false)
  const [probeError, setProbeError] = useState('')

  const selectedEndpoint = value?.endpoint || ''
  const selectedModel = value?.model || ''

  useEffect(() => {
    setModels([])
    setProbeError('')
  }, [selectedEndpoint])

  const fetchModels = async () => {
    if (!selectedEndpoint) return
    setProbing(true)
    setProbeError('')
    try {
      const r = await llmApi.probe({ endpoint_name: selectedEndpoint })
      if (r.ok) {
        setModels(r.models)
      } else {
        setProbeError(r.error || 'probe failed')
      }
    } finally {
      setProbing(false)
    }
  }

  return (
    <div className='grid grid-cols-12 gap-2 items-center py-2 border-b border-gray-800'>
      <div className='col-span-3'>
        <div className='text-sm text-gray-200'>{label}</div>
        <div className='text-xs text-gray-500'>{description}</div>
      </div>
      <div className='col-span-4'>
        <select
          value={selectedEndpoint}
          onChange={(e) => onChange({ endpoint: e.target.value, model: '' })}
          className='w-full bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm'
        >
          <option value=''>— unassigned —</option>
          {endpoints.map((ep) => (
            <option key={ep.name} value={ep.name}>{ep.name}</option>
          ))}
        </select>
      </div>
      <div className='col-span-5 flex gap-2'>
        {models.length > 0 ? (
          <select
            value={selectedModel}
            onChange={(e) => onChange({ endpoint: selectedEndpoint, model: e.target.value })}
            className='flex-1 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm'
          >
            <option value=''>— pick a model —</option>
            {models.map((m) => (
              <option key={m} value={m}>{m}</option>
            ))}
          </select>
        ) : (
          <input
            type='text'
            value={selectedModel}
            onChange={(e) => onChange({ endpoint: selectedEndpoint, model: e.target.value })}
            placeholder='model id'
            disabled={!selectedEndpoint}
            className='flex-1 bg-gray-800 border border-gray-600 rounded px-2 py-1 text-sm disabled:opacity-50'
          />
        )}
        <button
          type='button'
          onClick={fetchModels}
          disabled={!selectedEndpoint || probing}
          className='text-xs px-2 py-1 rounded bg-gray-700 hover:bg-gray-600 disabled:opacity-50'
        >
          {probing ? '…' : 'Fetch'}
        </button>
      </div>
      {probeError && (
        <div className='col-span-12 text-xs text-red-300 pl-3'>{probeError}</div>
      )}
    </div>
  )
}
