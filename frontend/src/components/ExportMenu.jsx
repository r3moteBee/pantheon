import React, { useState, useRef, useEffect } from 'react'
import { Download } from 'lucide-react'
import { downloadSvgFile, downloadPngFile, downloadPdfFile } from '../utils/svgExport'

export default function ExportMenu({ getSvgEl, basename = 'diagram', title = 'Export' }) {
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    function onDoc(e) {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  async function handle(fmt) {
    const svgEl = typeof getSvgEl === 'function' ? getSvgEl() : null
    if (!svgEl) {
      setOpen(false)
      return
    }
    setBusy(true)
    try {
      if (fmt === 'svg') downloadSvgFile(svgEl, basename)
      else if (fmt === 'png') await downloadPngFile(svgEl, basename)
      else if (fmt === 'pdf') await downloadPdfFile(svgEl, basename)
    } catch (err) {
      console.error(`Export to ${fmt} failed`, err)
      alert(`Export to ${fmt.toUpperCase()} failed: ${err?.message || err}`)
    } finally {
      setBusy(false)
      setOpen(false)
    }
  }

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        disabled={busy}
        className="p-1.5 rounded bg-gray-900/80 hover:bg-gray-800 text-gray-300 border border-gray-700 shadow disabled:opacity-50"
        title={title}
      >
        <Download className="w-3.5 h-3.5" />
      </button>
      {open && (
        <div className="absolute right-0 mt-1 z-20 bg-gray-900 border border-gray-700 rounded shadow-lg text-xs min-w-[80px]">
          {['svg', 'png', 'pdf'].map((fmt) => (
            <button
              key={fmt}
              type="button"
              onClick={() => handle(fmt)}
              className="block w-full text-left px-3 py-1.5 hover:bg-gray-800 text-gray-200 uppercase tracking-wide"
            >
              {fmt}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
