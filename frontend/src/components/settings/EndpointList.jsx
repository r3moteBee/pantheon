import { useEffect, useState } from 'react'
import { llmApi } from '../../api/client'
import EndpointCard from './EndpointCard'
import AddEndpointForm from './AddEndpointForm'

export default function EndpointList({ onChange }) {
  const [endpoints, setEndpoints] = useState([])
  const [loading, setLoading] = useState(true)

  const refresh = async () => {
    setLoading(true)
    try {
      setEndpoints(await llmApi.listEndpoints())
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, [])

  const handleChange = () => {
    refresh()
    onChange?.()
  }

  return (
    <section className='space-y-3'>
      <header className='flex items-center justify-between'>
        <h3 className='text-sm font-semibold text-gray-200'>Endpoints</h3>
        <span className='text-xs text-gray-500'>
          {loading ? '…' : `${endpoints.length} configured`}
        </span>
      </header>
      <div className='space-y-2'>
        {endpoints.map((e) => (
          <EndpointCard key={e.name} endpoint={e} onChange={handleChange} />
        ))}
        {!loading && endpoints.length === 0 && (
          <div className='text-xs text-gray-500 italic'>
            No endpoints yet. Add one below.
          </div>
        )}
      </div>
      <AddEndpointForm onSaved={handleChange} />
    </section>
  )
}
