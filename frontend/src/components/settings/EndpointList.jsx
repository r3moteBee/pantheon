import { useEffect, useState } from 'react'
import { llmApi } from '../../api/client'
import EndpointCard from './EndpointCard'
import AddEndpointForm from './AddEndpointForm'
import HelpDrawer from '../help/HelpDrawer'
import { LLM_PROVIDERS, providerNameToSlug } from '../help/llmProviders'

export default function EndpointList({ onChange }) {
  const [endpoints, setEndpoints] = useState([])
  const [loading, setLoading] = useState(true)
  const [prefill, setPrefill] = useState(null)

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

  const useProvider = (p) => {
    setPrefill({
      name: providerNameToSlug(p.name),
      base_url: p.base_url,
      api_type: p.api_type,
    })
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
      <HelpDrawer title='Common LLM providers' storageKey='help.llm-providers'>
        <p className='text-xs text-gray-400 mb-3'>
          Click <strong>Use this</strong> to pre-fill the form below. You'll
          still need to paste an API key from the provider's signup page.
        </p>
        <div className='overflow-x-auto'>
          <table className='w-full text-xs'>
            <thead className='text-gray-500'>
              <tr>
                <th className='text-left font-normal pb-1 pr-3'>Provider</th>
                <th className='text-left font-normal pb-1 pr-3'>Base URL</th>
                <th className='text-left font-normal pb-1 pr-3'>API type</th>
                <th className='text-left font-normal pb-1 pr-3'>Get a key</th>
                <th className='pb-1'></th>
              </tr>
            </thead>
            <tbody className='text-gray-300'>
              {LLM_PROVIDERS.map((p) => (
                <tr key={p.name} className='border-t border-gray-800'>
                  <td className='py-1.5 pr-3'>{p.name}</td>
                  <td className='py-1.5 pr-3'>
                    <code className='font-mono text-[11px] text-gray-400 break-all'>{p.base_url}</code>
                  </td>
                  <td className='py-1.5 pr-3'>
                    <code className='font-mono text-[11px] text-gray-400'>{p.api_type}</code>
                  </td>
                  <td className='py-1.5 pr-3'>
                    <a
                      href={p.signup_url}
                      target='_blank'
                      rel='noopener noreferrer'
                      className='text-brand-400 hover:underline'
                    >
                      {p.signup_label}
                    </a>
                    {p.signup_note && (
                      <span className='text-gray-500 ml-1'>· {p.signup_note}</span>
                    )}
                  </td>
                  <td className='py-1.5'>
                    <button
                      type='button'
                      onClick={() => useProvider(p)}
                      className='text-[11px] px-2 py-0.5 rounded bg-gray-700 hover:bg-gray-600'
                    >
                      Use this
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </HelpDrawer>
      <AddEndpointForm
        onSaved={handleChange}
        prefill={prefill}
        onPrefillConsumed={() => setPrefill(null)}
      />
    </section>
  )
}
