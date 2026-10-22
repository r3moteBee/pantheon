/**
 * CoreEditor — shared CodeMirror primitive used by SkillEditor,
 * PersonalityEditor and the artifact editors.
 *
 * Props:
 *   value       — string (controlled)
 *   onChange    — (nextValue: string) => void
 *   language    — optional explicit language id ('markdown' | 'json' | 'python' | 'javascript' |
 *                 'swift' | ... see artifacts/codeLanguages.js; 'text' = none)
 *   filename    — optional; language is auto-detected from the extension
 *   contentType — optional MIME type, the fallback when the extension is unknown
 *   editable    — default true
 *   height      — default '100%'
 *   onSaveHotkey — optional callback for Cmd/Ctrl+S (prevents default)
 *   className   — wrapper class
 *   basicSetup  — override CodeMirror basicSetup props
 */
import React, { useMemo, useCallback, useEffect, useState } from 'react'
import CodeMirror from '@uiw/react-codemirror'
import { EditorView } from '@codemirror/view'
import { detectLanguage, loadLanguage } from './artifacts/codeLanguages'

export default function CoreEditor({
  value,
  onChange,
  language,
  filename,
  contentType,
  editable = true,
  height = '100%',
  onSaveHotkey,
  className,
  basicSetup,
}) {
  const resolvedLang = language || detectLanguage(filename, contentType)

  // Language support loads lazily (code-split per language); plain text until it arrives.
  const [langExt, setLangExt] = useState({ lang: null, ext: [] })
  useEffect(() => {
    let live = true
    loadLanguage(resolvedLang).then((ext) => { if (live) setLangExt({ lang: resolvedLang, ext }) })
    return () => { live = false }
  }, [resolvedLang])

  const extensions = useMemo(
    () => [EditorView.lineWrapping, ...(langExt.lang === resolvedLang ? langExt.ext : [])],
    [langExt, resolvedLang],
  )
  const handleChange = useCallback((val) => { onChange?.(val) }, [onChange])

  const handleKeyDown = useCallback((e) => {
    if (onSaveHotkey && (e.ctrlKey || e.metaKey) && e.key === 's') {
      e.preventDefault()
      onSaveHotkey()
    }
  }, [onSaveHotkey])

  return (
    <div
      className={className}
      onKeyDown={handleKeyDown}
      style={{ height, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}
    >
      <CodeMirror
        value={value ?? ''}
        height="100%"
        style={{ flex: 1, overflow: 'auto' }}
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
