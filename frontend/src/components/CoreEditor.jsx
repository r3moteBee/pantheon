/**
 * CoreEditor — shared CodeMirror primitive used by SkillEditor,
 * PersonalityEditor, and FileRepository.
 *
 * Props:
 *   value       — string (controlled)
 *   onChange    — (nextValue: string) => void
 *   language    — optional explicit language id ('markdown' | 'json' | 'python' | 'javascript' | 'text')
 *   filename    — optional; language is auto-detected from the extension
 *   editable    — default true
 *   height      — default '100%'
 *   onSaveHotkey — optional callback for Cmd/Ctrl+S (prevents default)
 *   className   — wrapper class
 *   basicSetup  — override CodeMirror basicSetup props
 */
import React, { useMemo, useCallback } from 'react'
import CodeMirror from '@uiw/react-codemirror'
import { markdown } from '@codemirror/lang-markdown'
import { json as jsonLang } from '@codemirror/lang-json'
import { python } from '@codemirror/lang-python'
import { javascript } from '@codemirror/lang-javascript'

// Filename extension → language id
const EXT_TO_LANG = {
  md: 'markdown', markdown: 'markdown',
  json: 'json',
  py: 'python', pyi: 'python',
  js: 'javascript', mjs: 'javascript', cjs: 'javascript',
  jsx: 'javascript', ts: 'javascript', tsx: 'javascript',
}

function detectLanguage(filename) {
  if (!filename) return 'text'
  const dot = filename.lastIndexOf('.')
  if (dot < 0) return 'text'
  const ext = filename.slice(dot + 1).toLowerCase()
  return EXT_TO_LANG[ext] || 'text'
}

function extensionsFor(lang) {
  switch (lang) {
    case 'markdown':  return [markdown()]
    case 'json':      return [jsonLang()]
    case 'python':    return [python()]
    case 'javascript':return [javascript({ jsx: true, typescript: true })]
    default:          return []
  }
}

export default function CoreEditor({
  value,
  onChange,
  language,
  filename,
  editable = true,
  height = '100%',
  onSaveHotkey,
  className,
  basicSetup,
}) {
  const resolvedLang = language || detectLanguage(filename)

  const extensions = useMemo(() => extensionsFor(resolvedLang), [resolvedLang])
  const handleChange = useCallback((val) => { onChange?.(val) }, [onChange])

  const handleKeyDown = useCallback((e) => {
    if (onSaveHotkey && (e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault()
      onSaveHotkey()
    }
  }, [onSaveHotkey])

  return (
    <div className={className} onKeyDown={handleKeyDown} style={{ height }}>
      <CodeMirror
        value={value ?? ''}
        height={height}
        theme="dark"
        editable={editable}
        extensions={extensions}
        onChange={handleChange}
        basicSetup={{
          lineNumbers: true,
          highlightActiveLine: true,
          foldGutter: true,
          bracketMatching: true,
          autocompletion: false,
          ...basicSetup,
        }}
      />
    </div>
  )
}
