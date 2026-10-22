import React, { useState, useEffect } from 'react'
import { Github, Plug, Trash2, Plus, RefreshCw, Eye, EyeOff, AlertTriangle, Search } from 'lucide-react'
import { connectionsApi } from '../api/client'
import MCPConnections from '../components/MCPConnections'
import HelpDrawer from '../components/help/HelpDrawer'
import SearchProvidersTab from '../components/connections/SearchProvidersTab'
import PageHeader from '../components/PageHeader'
import TabStrip from '../components/TabStrip'
import Time from '../components/Time'
import { useUrlParam } from '../hooks/useUrlState'
import usePageTitle from '../hooks/usePageTitle'

const TABS = [
  { id: 'mcp',    label: 'MCP servers', icon: Plug },
  { id: 'github', label: 'GitHub', icon: Github },
  { id: 'search', label: 'Web search', icon: Search },
]

export default function ConnectionsPage() {
  const [tab, setTab] = useUrlParam('section', 'mcp', { allowed: TABS.map((t) => t.id) })
  usePageTitle(TABS.find((t) => t.id === tab)?.label, 'Connections')

  return (
    <div className="h-full flex flex-col">
      <TabStrip
        tabs={TABS}
        value={tab}
        onChange={setTab}
        label="Connection types"
        className="border-b border-gray-800 px-2 sm:px-6 pt-4"
      />
      <div className="flex-1 overflow-hidden">
        {tab === 'github' && <GitHubAccountsTab />}
        {tab === 'mcp'    && <MCPTab />}
        {tab === 'search' && (
          <ConnectionsFrame title="Web search providers">
            <SearchProvidersTab />
          </ConnectionsFrame>
        )}
      </div>
    </div>
  )
}

/**
 * The one frame every Connections tab shares (UI review N1/#17): the scroll
 * container, the shared PageHeader (title, optional description, right-hand
 * actions) and the max-w-3xl column with the page gutter. The tab bodies
 * (MCPConnections, GitHub accounts, SearchProvidersTab) render no heading,
 * scroll area or gutter of their own.
 */
export function ConnectionsFrame({ title, description, actions, children }) {
  return (
    // Header and body share ONE column, so the title's left edge is the body's
    // left edge on every tab (round 3 #2: the title sat at the page gutter while
    // the body sat in a centered column - a "double indent").
    <div className="h-full overflow-y-auto scrollbar-thin">
      <div className="max-w-3xl mx-auto px-4 sm:px-6">
        <PageHeader title={title} description={description} actions={actions} className="!px-0" />
        <div className="py-4 sm:py-6 space-y-4">
          {children}
        </div>
      </div>
    </div>
  )
}


function GitHubAccountsTab() {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [showAdd, setShowAdd] = useState(false)
  const [token, setToken] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [adding, setAdding] = useState(false)

  const refresh = async () => {
    setLoading(true); setError(null)
    try {
      const res = await connectionsApi.list()
      setItems(res.data.connections || [])
    } catch (e) {
      setError(e.message)
    } finally { setLoading(false) }
  }
  useEffect(() => { refresh() }, [])

  const addConnection = async () => {
    if (!token.trim()) return
    setAdding(true); setError(null)
    try {
      // System-wide GitHub connection — no repo binding here. Repo selection
      // happens per-project on the chat Repository tab.
      await connectionsApi.create({ token: token.trim() })
      setShowAdd(false); setToken('')
      await refresh()
    } catch (e) {
      setError(e.message)
    } finally { setAdding(false) }
  }

  const remove = async (id) => {
    if (!confirm('Remove this account? Any project bindings using it will be cleared.')) return
    await connectionsApi.delete(id)
    await refresh()
  }

  return (
    <ConnectionsFrame
      title="GitHub accounts"
      description={<>System-wide PATs. Bind a specific repo to a project on its
        <span className="text-gray-300"> Workspace → Repository </span>tab.</>}
      actions={<>
        <button onClick={refresh} className="text-xs text-gray-400 hover:text-gray-200 flex items-center gap-1 whitespace-nowrap">
          <RefreshCw className="w-3 h-3" aria-hidden="true" /> Refresh
        </button>
        {!showAdd && (
          <button
            onClick={() => setShowAdd(true)}
            className="text-xs px-3 py-1.5 rounded-md bg-brand-700 hover:bg-brand-600 text-white flex items-center gap-1 whitespace-nowrap"
          >
            <Plus className="w-3 h-3" aria-hidden="true" /> Add account
          </button>
        )}
      </>}
    >
      <div>
        <HelpDrawer title='About GitHub Personal Access Tokens' storageKey='help.github-pat'>
          <p className='text-xs text-gray-400 mb-3'>
            Pantheon uses a PAT to read repositories and open pull requests on
            your behalf when the agent runs coding tasks. Generate either type
            below — fine-grained is preferred but classic works too.
          </p>
          <table className='w-full text-xs mb-3'>
            <thead className='text-gray-400'>
              <tr>
                <th className='text-left font-normal pb-1 pr-3'>Type</th>
                <th className='text-left font-normal pb-1 pr-3'>Required scope</th>
                <th className='text-left font-normal pb-1'>Generate at</th>
              </tr>
            </thead>
            <tbody className='text-gray-300'>
              <tr className='border-t border-amber-900/30'>
                <td className='py-1.5 pr-3 align-top'>Fine-grained <span className='text-gray-400'>(recommended)</span></td>
                <td className='py-1.5 pr-3 align-top'>
                  Contents: read &amp; write<br/>
                  Pull requests: read &amp; write
                </td>
                <td className='py-1.5 align-top'>
                  <a href='https://github.com/settings/personal-access-tokens'
                     target='_blank' rel='noopener noreferrer'
                     className='text-brand-400 hover:underline'>
                    github.com/settings/personal-access-tokens
                  </a>
                </td>
              </tr>
              <tr className='border-t border-amber-900/30'>
                <td className='py-1.5 pr-3 align-top'>Classic</td>
                <td className='py-1.5 pr-3 align-top'>
                  <code className='text-amber-200/80'>repo</code>
                </td>
                <td className='py-1.5 align-top'>
                  <a href='https://github.com/settings/tokens'
                     target='_blank' rel='noopener noreferrer'
                     className='text-brand-400 hover:underline'>
                    github.com/settings/tokens
                  </a>
                </td>
              </tr>
            </tbody>
          </table>
          <p className='text-xs text-gray-400'>
            Tokens are encrypted in the local secrets vault and never written
            to logs. Removing an account clears any project bindings using it.
          </p>
        </HelpDrawer>
      </div>

      {error && (
        <div className="p-3 rounded-md border border-red-700 bg-red-950 text-red-300 text-sm flex items-center gap-2">
          <AlertTriangle className="w-4 h-4" aria-hidden="true" /> {error}
        </div>
      )}

      {showAdd && (
        <div className="p-4 rounded-lg border border-gray-700 bg-gray-900 space-y-3">
          <div className="text-sm font-semibold">Add GitHub account</div>
          <p className="text-xs text-gray-400">
            Personal Access Token (classic or fine-grained) with{' '}
            <code className="text-brand-300">repo</code> scope. Encrypted in
            the local secrets vault.
          </p>
          <div className="flex gap-2">
            <input
              type={showToken ? 'text' : 'password'}
              value={token}
              onChange={(e) => setToken(e.target.value)}
              placeholder="github_pat_…"
              className="flex-1 px-3 py-2 rounded-md bg-gray-800 border border-gray-700 text-sm font-mono"
            />
            <button
              onClick={() => setShowToken(!showToken)}
              className="px-2 text-gray-400 hover:text-gray-200"
              type="button"
              aria-label={showToken ? 'Hide token' : 'Show token'}
              title={showToken ? 'Hide token' : 'Show token'}
            >
              {showToken ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
            </button>
          </div>
          <div className="flex justify-end gap-2 pt-2">
            <button
              onClick={() => { setShowAdd(false); setToken('') }}
              className="px-3 py-1.5 text-xs rounded-md text-gray-400 hover:text-gray-200 whitespace-nowrap"
            >
              Cancel
            </button>
            <button
              onClick={addConnection}
              disabled={!token.trim() || adding}
              className="px-3 py-1.5 text-xs rounded-md bg-brand-700 hover:bg-brand-600 text-white disabled:opacity-50 whitespace-nowrap"
            >
              {adding ? 'Verifying…' : 'Add account'}
            </button>
          </div>
        </div>
      )}

      {loading && <div className="text-xs text-gray-400">Loading…</div>}
      {!loading && items.length === 0 && !showAdd && (
        <div className="text-sm text-gray-400 italic">
          No GitHub accounts connected. Add one to enable repo binding on projects.
        </div>
      )}

      {!loading && items.length > 0 && (
        <div className="space-y-2">
          {items.map((c) => (
            <div key={c.id} className="p-3 rounded-md border border-gray-700 bg-gray-900 flex items-center justify-between gap-3">
              <div className="text-sm min-w-0">
                <div className="font-medium text-gray-200 flex items-center gap-2">
                  <Github className="w-4 h-4 text-brand-400" />
                  {c.account_login ? `@${c.account_login}` : 'GitHub'}
                  {c.full_name && <span className="text-gray-400">· default repo: {c.full_name}</span>}
                  {c.status === 'error' && <AlertTriangle className="w-4 h-4 text-amber-400" />}
                </div>
                <div className="text-xs text-gray-400">
                  added <Time value={c.created_at} />
                  {c.last_used_at && <> · last used <Time value={c.last_used_at} /></>}
                  {c.project_id && ` · legacy per-project (${c.project_id})`}
                </div>
                {c.status === 'error' && c.error_message && (
                  <div className="text-xs text-amber-400 mt-1">{c.error_message}</div>
                )}
              </div>
              <button
                onClick={() => remove(c.id)}
                className="flex-shrink-0 text-gray-400 hover:text-red-400"
                title="Remove account"
                aria-label={`Remove GitHub account ${c.account_login || ''}`.trim()}
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          ))}
        </div>
      )}
    </ConnectionsFrame>
  )
}


function MCPTab() {
  // MCPConnections renders only its toolbar, description and list; the page
  // title, scroll container and gutter come from the shared frame, the same
  // one the GitHub and Web search tabs use (UI review N1: the MCP tab had
  // lost both after round 1).
  return (
    <ConnectionsFrame title="MCP servers">
      <MCPConnections />
    </ConnectionsFrame>
  )
}
