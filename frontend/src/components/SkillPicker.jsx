import React, { useState, useEffect, useRef } from 'react'
import { Zap } from 'lucide-react'
import { skillsApi } from '../api/client'

/**
 * SkillPicker — autocomplete dropdown that appears when the user types `/` in chat input.
 *
 * Props:
 *   query      - The text after `/` (e.g., typing "/code" → query="code")
 *   projectId  - Current project ID for filtering
 *   onSelect   - Called with skill name when user picks one
 *   onClose    - Called when the picker should be dismissed
 *   visible    - Whether the picker is shown
 */
export default function SkillPicker({ query, projectId, onSelect, onClose, visible }) {
  const [skills, setSkills] = useState([])
  const [filtered, setFiltered] = useState([])
  const [selectedIndex, setSelectedIndex] = useState(0)
  const listRef = useRef(null)

  // Load skills once
  useEffect(() => {
    if (visible) {
      skillsApi.list(projectId, { enabledOnly: true }).then((res) => {
        setSkills(res.data.skills || [])
      }).catch(() => {})
    }
  }, [visible, projectId])

  // Filter by query
  useEffect(() => {
    if (!query) {
      setFiltered(skills)
    } else {
      const q = query.toLowerCase()
      setFiltered(
        skills.filter(
          (s) =>
            s.name.toLowerCase().includes(q) ||
            s.description?.toLowerCase().includes(q) ||
            s.tags?.some((t) => t.toLowerCase().includes(q))
        )
      )
    }
    setSelectedIndex(0)
  }, [query, skills])

  // Keyboard navigation
  useEffect(() => {
    if (!visible) return

    const handler = (e) => {
      if (e.key === 'ArrowDown') {
        e.preventDefault()
        setSelectedIndex((i) => Math.min(i + 1, filtered.length - 1))
      } else if (e.key === 'ArrowUp') {
        e.preventDefault()
        setSelectedIndex((i) => Math.max(i - 1, 0))
      } else if (e.key === 'Tab' || e.key === 'Enter') {
        if (filtered.length > 0) {
          e.preventDefault()
          onSelect(filtered[selectedIndex].name)
        }
      } else if (e.key === 'Escape') {
        e.preventDefault()
        onClose()
      }
    }

    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [visible, filtered, selectedIndex, onSelect, onClose])

  // Scroll selected item into view
  useEffect(() => {
    if (listRef.current) {
      const el = listRef.current.children[selectedIndex]
      el?.scrollIntoView({ block: 'nearest' })
    }
  }, [selectedIndex])

  if (!visible || filtered.length === 0) return null

  return (
    <div className="absolute bottom-full left-0 right-0 mb-1 z-50">
      <div
        ref={listRef}
        className="bg-gray-800 border border-gray-700 rounded-lg shadow-xl max-h-64 overflow-y-auto scrollbar-thin"
      >
        <div className="px-3 py-1.5 text-[10px] text-gray-500 border-b border-gray-700 sticky top-0 bg-gray-800">
          Skills — type to filter, ↑↓ to navigate, Enter to select, Esc to dismiss
        </div>
        {filtered.map((skill, i) => (
          <button
            key={skill.name}
            onClick={() => onSelect(skill.name)}
            className={`w-full flex items-start gap-2.5 px-3 py-2 text-left transition-colors ${
              i === selectedIndex ? 'bg-brand-900/50' : 'hover:bg-gray-750'
            }`}
          >
            <Zap className={`w-3.5 h-3.5 mt-0.5 flex-shrink-0 ${i === selectedIndex ? 'text-brand-400' : 'text-gray-500'}`} />
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <span className={`text-xs font-medium font-mono ${i === selectedIndex ? 'text-brand-300' : 'text-gray-300'}`}>
                  /{skill.name}
                </span>
                {skill.tags?.slice(0, 2).map((tag) => (
                  <span key={tag} className="text-[9px] px-1 py-0 rounded bg-gray-700 text-gray-500">{tag}</span>
                ))}
              </div>
              <p className="text-[10px] text-gray-500 truncate mt-0.5">{skill.description}</p>
            </div>
          </button>
        ))}
      </div>
    </div>
  )
}
