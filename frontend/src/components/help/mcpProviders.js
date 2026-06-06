// frontend/src/components/help/mcpProviders.js
//
// Popular hosted MCP presets — surfaces signup links and pre-fills settings
// inside the MCP Connections page.
//
// Only remote/hosted servers belong here (official Streamable HTTP endpoints,
// the transport Pantheon's client speaks). Locally-run MCP servers are
// discovered via the "Scan local ports" option instead.
//
// `auth_type` ('api_key' | 'oauth2') pre-selects the auth radio in the form.

export const MCP_PROVIDERS = [
  {
    name: 'Tavily Search',
    url: 'https://mcp.tavily.com/mcp/',
    auth_type: 'oauth2',
    signup_url: 'https://tavily.com',
    signup_label: 'tavily.com',
    description:
      'Search engine tailored for LLM agents. Hosted remote server with OAuth; alternatively append ?tavilyApiKey=<key> to the URL and use no auth.',
    request_interval_ms: 1000,
  },
  {
    name: 'Notion',
    url: 'https://mcp.notion.com/mcp',
    auth_type: 'oauth2',
    signup_url: 'https://www.notion.so/my-integrations',
    signup_label: 'notion.so/my-integrations',
    description:
      'Access workspace pages, databases, and search Notion documents. Official hosted server — OAuth only (sign in to your workspace when prompted).',
    request_interval_ms: 500,
  },
  {
    name: 'Jira',
    url: 'https://mcp.atlassian.com/v1/mcp/authv2',
    auth_type: 'oauth2',
    signup_url: 'https://id.atlassian.com/manage-profile/security/api-tokens',
    signup_label: 'id.atlassian.com',
    description:
      'Manage projects, view and edit issues, and track tickets in Jira/Confluence Cloud. Official Atlassian hosted server — OAuth 2.1.',
    request_interval_ms: 500,
  },
  {
    name: 'SubDownload YouTube Parser',
    url: 'https://api.subdownload.com/mcp',
    auth_type: 'api_key',
    signup_url: 'https://glama.ai/mcp/connectors/com.subdownload.api/sub-download',
    signup_label: 'glama.ai',
    description:
      'Interact with YouTube as a native data source, search channels/playlists, and extract transcripts. Hosted remote server (Streamable HTTP).',
    request_interval_ms: 500,
  },
  {
    name: 'Zapier',
    url: 'https://mcp.zapier.com/api/mcp/s/<server-id>/mcp',
    auth_type: 'api_key',
    signup_url: 'https://mcp.zapier.com',
    signup_label: 'mcp.zapier.com',
    description:
      'Trigger actions and integrate workflows across 7,000+ apps. Copy your personal server URL from mcp.zapier.com — the URL itself is the secret, so no separate key is needed.',
    request_interval_ms: 1000,
  },
]
