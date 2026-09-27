import React, { useState } from 'react'
import EndpointList from './EndpointList'
import ModelRouting from './ModelRouting'
import RoutingUsage from './RoutingUsage'
import ChatRouter from './ChatRouter'
import RouterTuning from './RouterTuning'

export default function LlmSettings() {
  const [refreshKey, setRefreshKey] = useState(0)
  return (
    <div className='space-y-6'>
      <EndpointList onChange={() => setRefreshKey((k) => k + 1)} />
      <ModelRouting refreshKey={refreshKey} onSaved={() => setRefreshKey((k) => k + 1)} />
      <ChatRouter refreshKey={refreshKey} />
      <RoutingUsage />
      <RouterTuning onApplied={() => setRefreshKey((k) => k + 1)} />
    </div>
  )
}
