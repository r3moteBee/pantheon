import React from 'react'
import { Zap, ShieldCheck, Globe } from 'lucide-react'
import Skills from '../components/Skills'
import SkillScanDashboard from '../components/SkillScanDashboard'
import SkillHubsSettings from '../components/settings/SkillHubsSettings'
import { useUrlParam } from '../hooks/useUrlState'
import usePageTitle from '../hooks/usePageTitle'

const TABS = [
  { id: 'library', label: 'Library', icon: Zap },
  { id: 'security', label: 'Security', icon: ShieldCheck },
  // Skill registry hubs (was Settings -> Skills).
  { id: 'sources', label: 'Sources', icon: Globe },
]

export default function SkillsPage() {
  const [tab, setTab] = useUrlParam('section', 'library', { allowed: TABS.map((t) => t.id) })
  usePageTitle(tab === 'library' ? null : TABS.find((t) => t.id === tab)?.label, 'Skills')

  return (
    <div className="h-full flex flex-col">
      <div
        role="tablist"
        aria-label="Skills sections"
        className="flex items-center border-b border-gray-800 px-2 sm:px-6 pt-4 overflow-x-auto scrollbar-thin"
      >
        {TABS.map((t) => {
          const Icon = t.icon
          const active = tab === t.id
          return (
            <button
              key={t.id}
              role="tab"
              aria-selected={active}
              onClick={() => setTab(t.id)}
              className={`flex flex-shrink-0 items-center gap-1.5 px-4 py-2 text-xs font-medium whitespace-nowrap border-b-2 transition-colors ${
                active
                  ? 'border-brand-400 text-brand-300'
                  : 'border-transparent text-gray-400 hover:text-gray-200'
              }`}
            >
              <Icon className="w-3.5 h-3.5" aria-hidden="true" />
              {t.label}
            </button>
          )
        })}
      </div>
      <div className="flex-1 overflow-hidden">
        {tab === 'library' && <Skills />}
        {tab === 'security' && <SkillScanDashboard />}
        {tab === 'sources' && (
          <div className="h-full overflow-y-auto scrollbar-thin">
            <div className="max-w-[860px] mx-auto p-4 sm:p-6">
              <SkillHubsSettings />
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
