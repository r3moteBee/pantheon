import React from 'react'
import GlobalTasks from './GlobalTasks'
import JobRuns from './JobRuns'

// Tasks tab: scheduled tasks across projects, then the cross-project job runs.
// Returns a fragment so the tab wrapper's space-y-8 spacing applies as before.
export default function TasksSettings() {
  return (
    <>
      <GlobalTasks />
      <div className="border-t border-gray-800" />
      <JobRuns />
    </>
  )
}
