import React from 'react'
import { User as UserIcon } from 'lucide-react'
import PersonalityEditor from '../PersonalityEditor'

export default function PersonalitySettings() {
  return (
    <div>
      <h3 className="text-sm font-semibold text-gray-200 flex items-center gap-2 mb-2">
        <UserIcon className="w-4 h-4 text-brand-400" />
        Personality
      </h3>
      <p className="text-[11px] text-gray-500 mb-4">
        The agent's identity (soul.md) and working rules (agent.md). Projects
        follow the global personality unless you give one its own: apply a
        preset or edit it with that project as the scope. Presets keep the
        global Key Commitments. Tone and focus live in each project's settings tab.
      </p>
      <PersonalityEditor />
    </div>
  )
}
