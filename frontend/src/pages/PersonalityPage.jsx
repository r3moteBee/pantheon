import React, { useState } from 'react'
import { Pen, BookOpen } from 'lucide-react'
import PersonalityEditor from '../components/PersonalityEditor'
import PersonaLibrary from '../components/PersonaLibrary'

const TABS = [
  { id: 'editor', label: 'Editor', icon: Pen },
  { id: 'library', label: 'Persona Library', icon: BookOpen },
]

export default function PersonalityPage() {
  const [activeTab, setActiveTab] = useState('editor')

  return (
    <div className="flex flex-col h-full">
      {/* Tab bar */}
      <div className="flex border-b border-gray-800 bg-gray-900 px-4">
        {TABS.map((tab) => {
          const Icon = tab.icon
          return (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`flex items-center gap-2 px-4 py-3 text-sm font-medium border-b-2 transition-colors ${
                activeTab === tab.id
                  ? 'border-brand-500 text-brand-400'
                  : 'border-transparent text-gray-400 hover:text-gray-300'
              }`}
            >
              <Icon className="w-4 h-4" />
              {tab.label}
            </button>
          )
        })}
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto">
        {activeTab === 'editor' && <PersonalityEditor />}
        {activeTab === 'library' && <PersonaLibrary />}
      </div>
    </div>
  )
}
