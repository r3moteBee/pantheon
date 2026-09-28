import React from 'react'
import { User as UserIcon } from 'lucide-react'
import PersonalityEditor from '../PersonalityEditor'

export default function PersonalitySettings() {
  return (
    <div>
      <h3 className="text-sm font-semibold text-gray-200 flex items-center gap-2 mb-2">
        <UserIcon className="w-4 h-4 text-brand-400" />
        Global agent identity
      </h3>
      <p className="text-[11px] text-gray-500 mb-4">
        This is the agent's stable identity across every project (the
        soul.md / agent.md files). Per-project tone overrides live in
        each project's settings tab.
      </p>
      <PersonalityEditor />
    </div>
  )
}
