import React from 'react'
import HelpDrawer from '../help/HelpDrawer'

export default function ChannelsHelp() {
  return (
    <HelpDrawer title='About channels' storageKey='help.channels'>
      <p className='text-xs text-gray-400 mb-2'>
        <strong>Channels</strong> are messaging surfaces that let you reach the
        agent from outside the web UI. Configure credentials here for any
        platform the agent should listen on.
      </p>
      <p className='text-xs text-gray-400 mb-2'>
        Currently supported platforms include <strong>Telegram, Discord, Slack, Matrix, and Mattermost</strong>. 
        Additional messengers will land in this tab as adapters are added.
      </p>
      <p className='text-xs text-gray-400'>
        Channels are <em>inbound</em> — users send messages <em>to</em> Pantheon
        through them. For <em>outbound</em> services the agent calls (GitHub,
        MCP servers, web search), see the <strong>Connections</strong> page in
        the side nav.
      </p>
    </HelpDrawer>
  )
}
