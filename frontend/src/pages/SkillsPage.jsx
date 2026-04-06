import React, { useState } from 'react'
import { Zap, ShieldCheck } from 'lucide-react'
import Skills from '../components/Skills'
import SkillScanDashboard from '../components/SkillScanDashboard'

export default function SkillsPage() {
  const [tab, setTab] = useState('library')

  const tabs = [
    { id: 'library', label: 'Library', icon: Zap },
    { id: 'security', label: 'Security', icon: ShieldCheck },
  ]

  return (
    <div className="h-full flex flex-col">
      <div className="flex items-center border-b border-gray-800 px-6 pt-4">
        {tabs.map((t) => {
          const Icon = t.icon
          return (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-4 py-2 text-xs font-medium border-b-2 transition-colors ${
                tab === t.id
                  ? 'border-brand-400 text-brand-300'
                  : 'border-transparent text-gray-500 hover:text-gray-300'
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          )
        })}
      </div>
      <div className="flex-1 overflow-hidden">
        {tab === 'library' && <Skills />}
        {tab === 'security' && <SkillScanDashboard />}
      </div>
    </div>
  )
}
