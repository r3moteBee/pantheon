import React from 'react'
import { Zap, ShieldCheck, Globe } from 'lucide-react'
import Skills from '../components/Skills'
import SkillScanDashboard from '../components/SkillScanDashboard'
import SkillHubsSettings from '../components/settings/SkillHubsSettings'
import TabStrip from '../components/TabStrip'
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
      <TabStrip
        tabs={TABS}
        value={tab}
        onChange={setTab}
        label="Skills sections"
        className="border-b border-gray-800 px-2 sm:px-6 pt-4"
      />
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
