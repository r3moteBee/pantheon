// frontend/src/components/help/mcpProviders.js
//
// Popular MCP presets matching the 10 highest value services.
// surfaces signup links and pre-fills settings inside the MCP Connections page.

export const MCP_PROVIDERS = [
  {
    name: 'Tavily Search',
    url: 'http://localhost:8123/sse',
    signup_url: 'https://tavily.com',
    signup_label: 'tavily.com',
    description: 'Search engine tailored for LLM agents.',
    request_interval_ms: 1000,
  },
  {
    name: 'Brave Search',
    url: 'http://localhost:8124/sse',
    signup_url: 'https://brave.com/search/api/',
    signup_label: 'brave.com/search/api',
    description: 'Web search and local places API with private endpoints.',
    request_interval_ms: 1000,
  },
  {
    name: 'Slack',
    url: 'http://localhost:8125/sse',
    signup_url: 'https://api.slack.com/apps',
    signup_label: 'api.slack.com/apps',
    description: 'Post messages, read channels, and manage team communication.',
    request_interval_ms: 500,
  },
  {
    name: 'Notion',
    url: 'http://localhost:8126/sse',
    signup_url: 'https://www.notion.so/my-integrations',
    signup_label: 'notion.so/my-integrations',
    description: 'Access workspace pages, databases, and search Notion documents.',
    request_interval_ms: 500,
  },
  {
    name: 'Jira',
    url: 'http://localhost:8127/sse',
    signup_url: 'https://id.atlassian.com/manage-profile/security/api-tokens',
    signup_label: 'id.atlassian.com',
    description: 'Manage projects, view and edit issues, and track tickets in Jira.',
    request_interval_ms: 500,
  },
  {
    name: 'Sequential Thinking',
    url: 'http://localhost:8128/sse',
    description: 'Reasoning tool to run sequential analysis on complex problems.',
    request_interval_ms: 0,
  },
  {
    name: 'Puppeteer',
    url: 'http://localhost:8129/sse',
    description: 'Local web scraping and automated browser control.',
    request_interval_ms: 1000,
  },
  {
    name: 'PostgreSQL',
    url: 'http://localhost:8130/sse',
    description: 'Inspect schemas and run read/write queries on SQL databases.',
    request_interval_ms: 0,
  },
  {
    name: 'SubDownload YouTube Parser',
    url: 'http://localhost:8131/sse',
    signup_url: 'https://glama.ai/mcp/connectors/com.subdownload.api/sub-download',
    signup_label: 'glama.ai',
    description: 'Interact with YouTube as a native data source, search channels/playlists, and extract transcripts.',
    request_interval_ms: 500,
  },
  {
    name: 'Zapier',
    url: 'http://localhost:8132/sse',
    signup_url: 'https://zapier.com/l/developer',
    signup_label: 'zapier.com',
    description: 'Trigger actions and integrate workflows across 7,000+ apps without custom integrations.',
    request_interval_ms: 1000,
  },
]
