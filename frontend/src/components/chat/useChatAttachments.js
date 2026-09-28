import { useState } from 'react'
import { useStore } from '../../store'
import { chatApi } from '../../api/client'

// Pending chat attachments: picked, pasted or dropped files, uploaded as
// artifacts when the message is sent.
export default function useChatAttachments() {
  const [attachments, setAttachments] = useState([])
  const [uploading, setUploading] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const activeProject = useStore((s) => s.activeProject)
  const sessionId = useStore((s) => s.sessionId)
  const addNotification = useStore((s) => s.addNotification)

  const addFiles = (files) => {
    setAttachments((prev) => [...prev, ...files])
  }

  const removeAttachment = (index) => {
    setAttachments((prev) => prev.filter((_, i) => i !== index))
  }

  const uploadAttachments = async (files) => {
    if (files.length === 0) return []
    const projectId = activeProject?.id || 'default'
    const results = []
    for (const file of files) {
      try {
        const res = await chatApi.attachFile(file, projectId, sessionId)
        results.push({
          name: res.data.filename || file.name,
          path: res.data.path,
          size: res.data.size,
          artifactId: res.data.artifact_id,
          contentType: res.data.content_type,
          indexing: res.data.indexing || false,
          extractionJobId: res.data.extraction_job_id || null,
        })
      } catch (err) {
        addNotification({ type: 'error', message: `Failed to upload ${file.name}: ${err.message}` })
      }
    }
    return results
  }

  // Upload everything pending and clear the tray; returns the uploaded files.
  const uploadPending = async () => {
    if (attachments.length === 0) return []
    setUploading(true)
    const uploaded = await uploadAttachments(attachments)
    setUploading(false)
    setAttachments([])
    return uploaded
  }

  // Handle paste events for images
  const handlePaste = (e) => {
    const items = e.clipboardData?.items
    if (!items) return
    const pastedFiles = []
    for (const item of items) {
      if (item.kind === 'file') {
        const file = item.getAsFile()
        if (file) pastedFiles.push(file)
      }
    }
    if (pastedFiles.length > 0) {
      e.preventDefault()
      setAttachments((prev) => [...prev, ...pastedFiles])
    }
  }

  // Handle drag and drop
  const dropHandlers = {
    onDragOver: (e) => {
      e.preventDefault()
      setIsDragging(true)
    },
    onDragLeave: (e) => {
      e.preventDefault()
      setIsDragging(false)
    },
    onDrop: (e) => {
      e.preventDefault()
      setIsDragging(false)
      const files = Array.from(e.dataTransfer.files || [])
      if (files.length > 0) {
        setAttachments((prev) => [...prev, ...files])
      }
    },
  }

  return {
    attachments,
    uploading,
    isDragging,
    addFiles,
    removeAttachment,
    uploadPending,
    handlePaste,
    dropHandlers,
  }
}
