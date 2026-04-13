import { useState, useEffect, useCallback } from 'react'
import {
  MessageCircle, Save, RotateCw, Eye, EyeOff, RefreshCw,
  Trash2, ChevronDown, ChevronUp, Wifi, WifiOff, Hash
} from 'lucide-react'
import { settingsApi, messagingApi, projectsApi } from '../api/client'
import useStore from '../store'

// ── Adapter credential card ────────────────────────────────────────────────

function AdapterCard({ adapter, settings: appSettings, onSave, onRestart }) {
  const addNotification = useStore((s) => s.addNotification)
  const [expanded, setExpanded] = useState(false)
  const [saving, setSaving] = useState(false)
  const [restarting, setRestarting] = useState(false)

  // Telegram fields
  const [tgToken, setTgToken] = useState('')
  const [tgChatIds, setTgChatIds] = useState('')

  // Discord fields
  const [dcToken, setDcToken] = useState('')
  const [dcGuildIds, setDcGuildIds] = useState('')
  const [dcScope, setDcScope] = useState('guild')

  useEffect(() => {
    if (adapter.name === 'telegram') {
      setTgChatIds(appSettings?.telegram_allowed_chat_ids || '')
    } else if (adapter.name === 'discord') {
      setDcGuildIds(appSettings?.discord_allowed_guild_ids || '')
      setDcScope(appSettings?.discord_command_scope || 'guild')
    }
  }, [adapter.name, appSettings])

  const inputClass = 'w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500'

  const save = async () => {
    setSaving(true)
    try {
      const payload = {}
      if (adapter.name === 'telegram') {
        payload.telegram_allowed_chat_ids = tgChatIds
        if (tgToken) payload.telegram_bot_token = tgToken
      } else if (adapter.name === 'discord') {
        payload.discord_allowed_guild_ids = dcGuildIds
        payload.discord_command_scope = dcScope
        if (dcToken) payload.discord_bot_token = dcToken
      }
      await settingsApi.update(payload)
      setTgToken('')
      setDcToken('')
      addNotification({ type: 'success', message: `${adapter.display_name} settings saved.` })
      if (onSave) onSave()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setSaving(false)
  }

  const restart = async () => {
    setRestarting(true)
    try {
      const res = await messagingApi.restartAdapter(adapter.name)
      const d = res.data || res
      if (d.status === 'ok') {
        addNotification({ type: 'success', message: d.message || `${adapter.display_name} restarted.` })
      } else {
        addNotification({ type: 'error', message: d.message || `Failed to restart ${adapter.display_name}.` })
      }
      if (onRestart) onRestart()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setRestarting(false)
  }

  const statusColor = adapter.running ? 'text-green-400' : adapter.configured ? 'text-yellow-400' : 'text-gray-500'
  const statusText = adapter.running ? 'Running' : adapter.configured ? 'Stopped' : 'Not configured'
  const StatusIcon = adapter.running ? Wifi : WifiOff

  const tokenSet = adapter.name === 'telegram'
    ? appSettings?.telegram_bot_token_set
    : appSettings?.discord_bot_token_set

  return (
    <div className="bg-gray-900/50 border border-gray-800 rounded-lg p-4">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center justify-between"
      >
        <div className="flex items-center gap-3">
          <StatusIcon className={`w-4 h-4 ${statusColor}`} />
          <span className="text-sm font-semibold text-gray-200">{adapter.display_name}</span>
          <span className={`text-xs ${statusColor}`}>{statusText}</span>
          {adapter.channel_count > 0 && (
            <span className="text-xs text-gray-500">{adapter.channel_count} channels</span>
          )}
        </div>
        {expanded ? <ChevronUp className="w-4 h-4 text-gray-500" /> : <ChevronDown className="w-4 h-4 text-gray-500" />}
      </button>

      {expanded && (
        <div className="mt-4 space-y-4">
          {adapter.name === 'telegram' && (
            <>
              <p className="text-xs text-gray-500">
                Connect a Telegram bot to chat with the agent.
                Create a bot via{' '}
                <a href="https://t.me/BotFather" target="_blank" rel="noreferrer" className="text-brand-400 hover:underline">@BotFather</a>.
              </p>
              <TokenField
                label="Bot Token"
                value={tgToken}
                onChange={setTgToken}
                tokenSet={tokenSet}
              />
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Allowed Chat IDs</label>
                <input
                  type="text"
                  value={tgChatIds}
                  onChange={(e) => setTgChatIds(e.target.value)}
                  placeholder="123456789, 987654321"
                  className={inputClass}
                />
                <p className="text-xs text-gray-600 mt-1">Comma-separated. Leave blank to allow all.</p>
              </div>
            </>
          )}

          {adapter.name === 'discord' && (
            <>
              <p className="text-xs text-gray-500">
                Connect a Discord bot.
                Create one at{' '}
                <a href="https://discord.com/developers/applications" target="_blank" rel="noreferrer" className="text-brand-400 hover:underline">Discord Developer Portal</a>.
                Enable <strong>Message Content Intent</strong> under Privileged Gateway Intents.
              </p>
              <TokenField
                label="Bot Token"
                value={dcToken}
                onChange={setDcToken}
                tokenSet={tokenSet}
              />
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Allowed Guild IDs</label>
                <input
                  type="text"
                  value={dcGuildIds}
                  onChange={(e) => setDcGuildIds(e.target.value)}
                  placeholder="123456789012345678"
                  className={inputClass}
                />
                <p className="text-xs text-gray-600 mt-1">Comma-separated server IDs. Leave blank to allow all.</p>
              </div>
              <div>
                <label className="block text-xs font-medium text-gray-400 mb-1.5">Slash Command Scope</label>
                <select
                  value={dcScope}
                  onChange={(e) => setDcScope(e.target.value)}
                  className={inputClass}
                >
                  <option value="guild">Guild (instant, per-server)</option>
                  <option value="global">Global (all servers, ~1hr delay)</option>
                </select>
              </div>
            </>
          )}

          <div className="flex items-center gap-3">
            <button
              onClick={save}
              disabled={saving}
              className="flex items-center gap-2 px-4 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded-lg disabled:opacity-50"
            >
              <Save className="w-4 h-4" />
              Save
            </button>
            <button
              onClick={restart}
              disabled={restarting}
              className="flex items-center gap-2 px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-200 text-sm rounded-lg disabled:opacity-50"
            >
              <RotateCw className={`w-4 h-4 ${restarting ? 'animate-spin' : ''}`} />
              {restarting ? 'Restarting…' : 'Restart'}
            </button>
          </div>

          {adapter.error && (
            <p className="text-xs text-red-400 mt-2">Error: {adapter.error}</p>
          )}
        </div>
      )}
    </div>
  )
}

// ── Token input with show/hide toggle ──────────────────────────────────────

function TokenField({ label, value, onChange, tokenSet }) {
  const [show, setShow] = useState(false)
  return (
    <div>
      <label className="block text-xs font-medium text-gray-400 mb-1.5">
        {label}
        {tokenSet && !value && (
          <span className="ml-2 text-green-500 font-normal">✓ saved</span>
        )}
      </label>
      <div className="flex gap-2">
        <input
          type={show ? 'text' : 'password'}
          value={value}
          onChange={(e) => onChange(e.target.value)}
          placeholder={tokenSet ? '(leave blank to keep existing)' : 'Paste token here…'}
          className="flex-1 bg-gray-800 border border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 focus:ring-1 focus:ring-brand-500"
        />
        <button onClick={() => setShow(!show)} className="px-3 py-2 bg-gray-800 hover:bg-gray-700 rounded-lg text-gray-400">
          {show ? <EyeOff className="w-4 h-4" /> : <Eye className="w-4 h-4" />}
        </button>
      </div>
    </div>
  )
}

// ── Channel mapping table ──────────────────────────────────────────────────

function ChannelMappingTable({ channels, mappings, projects, defaultProject, onSetMapping, onRemoveMapping, onSetDefault }) {
  // Merge channels + mappings into a single view
  const rows = []
  const mappedIds = new Set(mappings.map((m) => m.channel_id))

  // Channels from running adapters
  for (const ch of channels) {
    const mapping = mappings.find((m) => m.channel_id === ch.channel_id)
    rows.push({
      channel_id: ch.channel_id,
      raw_id: ch.raw_id,
      name: ch.name,
      platform: ch.platform,
      project_id: mapping?.project_id || null,
      mapped: !!mapping,
    })
  }

  // Mappings for channels not currently visible (adapter offline, etc.)
  for (const m of mappings) {
    if (!channels.find((c) => c.channel_id === m.channel_id)) {
      rows.push({
        channel_id: m.channel_id,
        raw_id: m.channel_id.split(':').slice(1).join(':'),
        name: m.channel_name || m.channel_id,
        platform: m.platform,
        project_id: m.project_id,
        mapped: true,
      })
    }
  }

  if (rows.length === 0) {
    return (
      <p className="text-xs text-gray-500 italic">
        No channels discovered yet. Start an adapter and refresh.
      </p>
    )
  }

  const platformIcon = (p) => {
    if (p === 'discord') return '#'
    if (p === 'telegram') return '💬'
    return '•'
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead>
          <tr className="text-xs text-gray-500 border-b border-gray-800">
            <th className="text-left py-2 px-2 font-medium">Channel</th>
            <th className="text-left py-2 px-2 font-medium">Platform</th>
            <th className="text-left py-2 px-2 font-medium">Project</th>
            <th className="py-2 px-2 w-10"></th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.channel_id} className="border-b border-gray-800/50 hover:bg-gray-800/30">
              <td className="py-2 px-2">
                <div className="flex items-center gap-2">
                  <span className="text-gray-500">{platformIcon(row.platform)}</span>
                  <span className="text-gray-200">{row.name}</span>
                </div>
                <span className="text-xs text-gray-600">{row.channel_id}</span>
              </td>
              <td className="py-2 px-2 text-gray-400 capitalize">{row.platform}</td>
              <td className="py-2 px-2">
                <select
                  value={row.project_id || ''}
                  onChange={(e) => {
                    const val = e.target.value
                    if (val) {
                      onSetMapping(row.channel_id, val)
                    } else {
                      onRemoveMapping(row.channel_id)
                    }
                  }}
                  className="bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
                >
                  <option value="">Default ({defaultProject})</option>
                  {projects.map((p) => (
                    <option key={p.id} value={p.id}>{p.name} ({p.id})</option>
                  ))}
                </select>
              </td>
              <td className="py-2 px-2">
                {row.mapped && (
                  <button
                    onClick={() => onRemoveMapping(row.channel_id)}
                    className="text-gray-600 hover:text-red-400"
                    title="Remove mapping"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

// ── Main export ────────────────────────────────────────────────────────────

export default function MessagingSettings() {
  const addNotification = useStore((s) => s.addNotification)
  const [adapters, setAdapters] = useState([])
  const [channels, setChannels] = useState([])
  const [mappings, setMappings] = useState([])
  const [projects, setProjects] = useState([])
  const [defaultProject, setDefaultProject] = useState('default')
  const [appSettings, setAppSettings] = useState({})
  const [refreshing, setRefreshing] = useState(false)

  const load = useCallback(async () => {
    try {
      const [statusRes, channelsRes, mappingsRes, projectsRes, defaultRes, settingsRes] = await Promise.all([
        messagingApi.status(),
        messagingApi.getChannels().catch(() => ({ data: { channels: [] } })),
        messagingApi.getMappings(),
        projectsApi.list(),
        messagingApi.getDefaultProject(),
        settingsApi.get(),
      ])
      setAdapters((statusRes.data || statusRes).adapters || [])
      setChannels((channelsRes.data || channelsRes).channels || [])
      setMappings((mappingsRes.data || mappingsRes).mappings || [])
      const pList = (projectsRes.data || projectsRes).projects || (projectsRes.data || projectsRes)
      setProjects(Array.isArray(pList) ? pList : Object.values(pList))
      setDefaultProject((defaultRes.data || defaultRes).project_id || 'default')
      setAppSettings((settingsRes.data || settingsRes))
    } catch (err) {
      console.error('Failed to load messaging settings:', err)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const refresh = async () => {
    setRefreshing(true)
    await load()
    setRefreshing(false)
  }

  const setMapping = async (channelId, projectId) => {
    try {
      await messagingApi.setMapping(channelId, projectId)
      await load()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const removeMapping = async (channelId) => {
    try {
      await messagingApi.removeMapping(channelId)
      await load()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const updateDefault = async (projectId) => {
    try {
      await messagingApi.setDefaultProject(projectId)
      setDefaultProject(projectId)
      addNotification({ type: 'success', message: `Default project set to ${projectId}.` })
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const inputClass = 'bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-brand-500'

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <MessageCircle className="w-4 h-4 text-gray-400" />
          <h3 className="text-sm font-semibold text-gray-200">Messaging Integrations</h3>
        </div>
        <button
          onClick={refresh}
          disabled={refreshing}
          className="flex items-center gap-1.5 px-3 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-300 text-xs rounded-lg disabled:opacity-50"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${refreshing ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>

      {/* Adapter cards */}
      <div className="space-y-3">
        {adapters.map((a) => (
          <AdapterCard
            key={a.name}
            adapter={a}
            settings={appSettings}
            onSave={load}
            onRestart={load}
          />
        ))}
        {adapters.length === 0 && (
          <p className="text-xs text-gray-500 italic">Loading adapters…</p>
        )}
      </div>

      {/* Channel mapping */}
      <div className="space-y-3">
        <div className="flex items-center gap-2">
          <Hash className="w-4 h-4 text-gray-400" />
          <h3 className="text-sm font-semibold text-gray-200">Channel → Project Mapping</h3>
        </div>

        <div className="flex items-center gap-3">
          <label className="text-xs text-gray-400">Default project for unmapped channels:</label>
          <select
            value={defaultProject}
            onChange={(e) => updateDefault(e.target.value)}
            className={inputClass}
          >
            <option value="default">default</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>{p.name} ({p.id})</option>
            ))}
          </select>
        </div>

        <ChannelMappingTable
          channels={channels}
          mappings={mappings}
          projects={projects}
          defaultProject={defaultProject}
          onSetMapping={setMapping}
          onRemoveMapping={removeMapping}
          onSetDefault={updateDefault}
        />
      </div>
    </div>
  )
}
