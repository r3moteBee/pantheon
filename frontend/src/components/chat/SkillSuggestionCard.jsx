import React from 'react'
import { Wand2, Check, XCircle } from 'lucide-react'

// Prompt shown when the backend suggests a skill for the last message.
export default function SkillSuggestionCard({ suggestion, onAccept, onDecline }) {
  return (
    <div className="flex justify-start mb-4">
      <div className="max-w-3xl w-full">
        <div className="border border-amber-700/50 bg-amber-950/40 rounded-2xl px-4 py-3">
          <div className="flex items-center gap-2 mb-2">
            <Wand2 className="w-4 h-4 text-amber-400" />
            <span className="text-sm font-medium text-amber-300">Skill suggested</span>
          </div>
          <p className="text-sm text-gray-200 mb-1">
            <span className="font-mono text-amber-300">/{suggestion.skill}</span>
            {' — '}{suggestion.description}
          </p>
          {suggestion.reason && (
            <p className="text-xs text-gray-500 mb-3">Matched: {suggestion.reason}</p>
          )}
          <div className="flex gap-2">
            <button
              onClick={onAccept}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-amber-700 hover:bg-amber-600 text-white transition-colors"
            >
              <Check className="w-3 h-3" />
              Use skill
            </button>
            <button
              onClick={onDecline}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium bg-gray-700 hover:bg-gray-600 text-gray-300 transition-colors"
            >
              <XCircle className="w-3 h-3" />
              Skip
            </button>
          </div>
        </div>
      </div>
    </div>
  )
}
