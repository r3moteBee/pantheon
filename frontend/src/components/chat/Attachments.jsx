import React from 'react'
import { X, FileText, Image, File, Paperclip } from 'lucide-react'

function AttachmentIcon({ filename }) {
  const ext = (filename || '').split('.').pop().toLowerCase()
  const imageExts = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp']
  const docExts = ['pdf', 'doc', 'docx', 'txt', 'md', 'csv', 'json', 'yaml', 'yml']
  if (imageExts.includes(ext)) return <Image className="w-3.5 h-3.5 text-purple-400" />
  if (docExts.includes(ext)) return <FileText className="w-3.5 h-3.5 text-blue-400" />
  return <File className="w-3.5 h-3.5 text-gray-400" />
}

export function AttachmentPill({ file, onRemove }) {
  return (
    <div className="flex items-center gap-1.5 bg-gray-700 rounded-lg px-2 py-1 text-xs text-gray-200 max-w-[200px]">
      <AttachmentIcon filename={file.name} />
      <span className="truncate">{file.name}</span>
      {onRemove && (
        <button onClick={onRemove} className="ml-auto flex-shrink-0 hover:text-red-400 transition-colors">
          <X className="w-3 h-3" />
        </button>
      )}
    </div>
  )
}

export function DropOverlay() {
  return (
    <div className="absolute inset-0 z-50 bg-brand-900/60 backdrop-blur-sm flex items-center justify-center pointer-events-none">
      <div className="bg-gray-800 border-2 border-dashed border-brand-400 rounded-2xl px-8 py-6 text-center">
        <Paperclip className="w-8 h-8 text-brand-400 mx-auto mb-2" />
        <p className="text-brand-300 font-medium">Drop files to attach</p>
        <p className="text-xs text-gray-400 mt-1">Files will be uploaded and indexed into memory</p>
      </div>
    </div>
  )
}
