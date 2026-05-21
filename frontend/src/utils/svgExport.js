import { jsPDF } from 'jspdf'
import { svg2pdf } from 'svg2pdf.js'

function serializeSvg(svgEl) {
  const clone = svgEl.cloneNode(true)
  if (!clone.getAttribute('xmlns')) {
    clone.setAttribute('xmlns', 'http://www.w3.org/2000/svg')
  }
  if (!clone.getAttribute('xmlns:xlink')) {
    clone.setAttribute('xmlns:xlink', 'http://www.w3.org/1999/xlink')
  }
  return new XMLSerializer().serializeToString(clone)
}

function triggerDownload(blob, filename) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 0)
}

function getSvgSize(svgEl) {
  const vb = svgEl.viewBox?.baseVal
  if (vb && vb.width && vb.height) return { w: vb.width, h: vb.height }
  const r = svgEl.getBoundingClientRect()
  return { w: r.width || 800, h: r.height || 600 }
}

export function downloadSvgFile(svgEl, basename) {
  const svg = serializeSvg(svgEl)
  const blob = new Blob([svg], { type: 'image/svg+xml;charset=utf-8' })
  triggerDownload(blob, `${basename}.svg`)
}

export async function downloadPngFile(svgEl, basename, scale = 2) {
  const { w, h } = getSvgSize(svgEl)
  const svgText = serializeSvg(svgEl)
  const svgBlob = new Blob([svgText], { type: 'image/svg+xml;charset=utf-8' })
  const svgUrl = URL.createObjectURL(svgBlob)
  try {
    const img = new Image()
    await new Promise((resolve, reject) => {
      img.onload = resolve
      img.onerror = () => reject(new Error('Failed to load SVG into <img> — check for external resources'))
      img.src = svgUrl
    })
    const canvas = document.createElement('canvas')
    canvas.width = Math.max(1, Math.ceil(w * scale))
    canvas.height = Math.max(1, Math.ceil(h * scale))
    const ctx = canvas.getContext('2d')
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, canvas.width, canvas.height)
    ctx.drawImage(img, 0, 0, canvas.width, canvas.height)
    const blob = await new Promise((resolve, reject) =>
      canvas.toBlob((b) => (b ? resolve(b) : reject(new Error('toBlob returned null'))), 'image/png')
    )
    triggerDownload(blob, `${basename}.png`)
  } finally {
    URL.revokeObjectURL(svgUrl)
  }
}

export async function downloadPdfFile(svgEl, basename) {
  const { w, h } = getSvgSize(svgEl)
  const orientation = w >= h ? 'l' : 'p'
  const pdf = new jsPDF({ orientation, unit: 'pt', format: [w, h] })
  pdf.setFillColor(255, 255, 255)
  pdf.rect(0, 0, w, h, 'F')
  await svg2pdf(svgEl, pdf, { x: 0, y: 0, width: w, height: h })
  pdf.save(`${basename}.pdf`)
}
