import React from 'react'
import { Shield } from 'lucide-react'
import SecurityLog from '../SecurityLog'
import SandboxStatus from './SandboxStatus'
import SkillOverridePassword from './SkillOverridePassword'

function AuditLog() {
  return (
    <div>
      <div className="flex items-center gap-2 mb-4">
        <Shield className="w-5 h-5 text-gray-400" />
        <h2 className="text-lg font-semibold text-gray-200">Security Audit Log</h2>
      </div>
      <p className="text-xs text-gray-500 mb-4">
        All security-relevant events across authentication, skills, vault, and settings.
      </p>
      <div className="rounded-lg border border-gray-800 overflow-hidden" style={{ height: '480px' }}>
        <SecurityLog embedded />
      </div>
    </div>
  )
}

// Security tab: sandbox health, skill-scan override password, audit log.
// Returns a fragment so the tab wrapper's space-y-8 spacing applies as before.
export default function SecuritySettings() {
  return (
    <>
      <SandboxStatus />
      <div className="border-t border-gray-800" />
      <SkillOverridePassword />
      <div className="border-t border-gray-800" />
      <AuditLog />
    </>
  )
}
