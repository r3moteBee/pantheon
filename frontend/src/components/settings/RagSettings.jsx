import React, { useState, useEffect } from 'react'
import { Save, Server } from 'lucide-react'
import { useStore } from '../../store'
import { settingsApi } from '../../api/client'

export default function RagSettings() {
  const [enabled, setEnabled] = useState(true)
  const [chunkSize, setChunkSize] = useState(500)
  const [chunkOverlap, setChunkOverlap] = useState(50)
  const [strategy, setStrategy] = useState('headings')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  useEffect(() => {
    settingsApi.get().then((res) => {
      setEnabled(res.data.memory_recall_enabled !== false)
      setChunkSize(res.data.file_chunk_size || 500)
      setChunkOverlap(res.data.file_chunk_overlap || 50)
      setStrategy(res.data.file_chunk_strategy || 'headings')
      setLoading(false)
    }).catch(() => {
      setLoading(false)
    })
  }, [])

  const save = async () => {
    setSaving(true)
    try {
      await settingsApi.update({
        memory_recall_enabled: enabled,
        file_chunk_size: parseInt(chunkSize) || 500,
        file_chunk_overlap: parseInt(chunkOverlap) || 50,
        file_chunk_strategy: strategy,
      })
      addNotification({ type: 'success', message: 'RAG Settings saved.' })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setSaving(false)
  }

  if (loading) {
    return <div className="text-xs text-gray-500">Loading RAG Settings...</div>
  }

  const inputClass = 'w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500'

  return (
    <div className="space-y-6">
      <div>
        <h3 className="text-sm font-semibold text-gray-200 mb-1 flex items-center gap-1.5">
          <Server className="w-4 h-4 text-brand-400" /> RAG & Retrieval Settings
        </h3>
        <p className="text-xs text-gray-500">
          Configure ChromaDB indexing parameters to fine-tune how workspace files and artifacts are chunked and recalled in conversation.
        </p>
      </div>

      <div className="bg-gray-800/40 border border-gray-700/50 rounded-lg p-4 space-y-4">
        {/* Toggle recall */}
        <div className="flex items-center justify-between">
          <div>
            <label className="block text-xs font-semibold text-gray-200">Memory Recall</label>
            <span className="text-[11px] text-gray-500">Automatically query and attach relevant vector memory to LLM context.</span>
          </div>
          <button
            type="button"
            onClick={() => setEnabled(!enabled)}
            className={`relative inline-flex h-5 w-9 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
              enabled ? 'bg-brand-600' : 'bg-gray-700'
            }`}
          >
            <span
              className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow ring-0 transition duration-200 ease-in-out ${
                enabled ? 'translate-x-4' : 'translate-x-0'
              }`}
            />
          </button>
        </div>

        <div className="border-t border-gray-700/50 my-4" />

        {/* Chunk Strategy */}
        <div>
          <label className="block text-xs font-semibold text-gray-300 mb-1">Chunking Strategy</label>
          <select
            value={strategy}
            onChange={(e) => setStrategy(e.target.value)}
            className={inputClass}
          >
            <option value="headings">Markdown Headings (respect section dividers)</option>
            <option value="paragraphs">Paragraphs (respect line breaks)</option>
            <option value="fixed">Fixed-size (split strictly by characters)</option>
          </select>
          <span className="text-[10px] text-gray-500 block mt-1">
            "headings" splits Markdown by headers then sub-splits paragraphs. "paragraphs" uses empty lines. "fixed" uses exact character counts.
          </span>
        </div>

        {/* Chunk Size */}
        <div className="grid grid-cols-2 gap-4">
          <div>
            <label className="block text-xs font-semibold text-gray-300 mb-1">Chunk Size (Tokens)</label>
            <input
              type="number"
              min="10"
              max="5000"
              value={chunkSize}
              onChange={(e) => setChunkSize(e.target.value)}
              className={inputClass}
            />
            <span className="text-[10px] text-gray-500 block mt-1">Target token length for each individual vector chunk (default: 500).</span>
          </div>

          {/* Chunk Overlap */}
          <div>
            <label className="block text-xs font-semibold text-gray-300 mb-1">Chunk Overlap (Tokens)</label>
            <input
              type="number"
              min="0"
              max="2000"
              value={chunkOverlap}
              onChange={(e) => setChunkOverlap(e.target.value)}
              className={inputClass}
            />
            <span className="text-[10px] text-gray-500 block mt-1">Token count overlap between consecutive text chunks (default: 50).</span>
          </div>
        </div>
      </div>

      <div>
        <button
          onClick={save}
          disabled={saving || !chunkSize || chunkOverlap === ''}
          className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-lg disabled:opacity-50 transition-colors"
        >
          <Save className="w-4 h-4" />
          {saving ? 'Saving...' : 'Save RAG Settings'}
        </button>
      </div>
    </div>
  )
}
