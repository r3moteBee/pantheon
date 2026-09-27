import React, { useState, useEffect } from 'react'
import { Trash2, RefreshCw, Search, ArrowUpDown } from 'lucide-react'
import { useStore } from '../../store'
import { tasksApi } from '../../api/client'
import useProjectNames from './useProjectNames'

export default function GlobalTasks() {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(false)
  const [search, setSearch] = useState('')
  const [sortBy, setSortBy] = useState('next_run') // next_run | name | project_name
  const [sortDir, setSortDir] = useState('asc')
  const [expandedTask, setExpandedTask] = useState(null)
  const [logs, setLogs] = useState([])
  const [logsLoading, setLogsLoading] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  const projectName = useProjectNames()

  const loadTasks = async () => {
    setLoading(true)
    try {
      const res = await tasksApi.listAll()
      setTasks(res.data.tasks || [])
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  useEffect(() => { loadTasks() }, [])

  const loadLogs = async (taskId, projectId) => {
    setLogsLoading(true)
    try {
      const res = await tasksApi.getLogs(taskId, projectId)
      setLogs(res.data.logs || [])
    } catch {
      setLogs([])
    }
    setLogsLoading(false)
  }

  const cancelTask = async (taskId) => {
    try {
      await tasksApi.cancel(taskId)
      addNotification({ type: 'success', message: 'Task cancelled' })
      loadTasks()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const toggleSort = (col) => {
    if (sortBy === col) {
      setSortDir(sortDir === 'asc' ? 'desc' : 'asc')
    } else {
      setSortBy(col)
      setSortDir('asc')
    }
  }

  const filtered = tasks.filter((t) => {
    if (!search.trim()) return true
    const q = search.toLowerCase()
    const pName = projectName(t.project_id).toLowerCase()
    return (
      (t.name || '').toLowerCase().includes(q) ||
      pName.includes(q) ||
      (t.project_id || '').toLowerCase().includes(q) ||
      (t.description || '').toLowerCase().includes(q)
    )
  })

  const sorted = [...filtered].sort((a, b) => {
    const dir = sortDir === 'asc' ? 1 : -1
    if (sortBy === 'next_run') {
      const aVal = a.next_run || ''
      const bVal = b.next_run || ''
      return aVal.localeCompare(bVal) * dir
    }
    if (sortBy === 'project_name') {
      return projectName(a.project_id).localeCompare(projectName(b.project_id)) * dir
    }
    const aVal = (a[sortBy] || '').toLowerCase()
    const bVal = (b[sortBy] || '').toLowerCase()
    return aVal.localeCompare(bVal) * dir
  })

  const formatTime = (ts) => {
    try { return new Date(ts).toLocaleString() } catch { return ts || '—' }
  }

  const SortHeader = ({ col, label }) => (
    <button onClick={() => toggleSort(col)} className="flex items-center gap-1 text-xs font-semibold text-gray-400 hover:text-gray-200">
      {label}
      {sortBy === col && <ArrowUpDown className="w-3 h-3" />}
    </button>
  )

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-bold text-gray-200">All Tasks</h2>
        <button onClick={loadTasks} disabled={loading} className="p-2 text-gray-400 hover:text-gray-300 disabled:opacity-50">
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>
      <p className="text-xs text-gray-500">Tasks across all projects. Tasks always run on schedule; the per-project Task Monitor filters by active project.</p>

      {/* Search */}
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-500" />
        <input
          type="text"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Filter by project, task name, or description..."
          className="w-full bg-gray-900 border border-gray-700 rounded-lg pl-9 pr-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
        />
      </div>

      {/* Table */}
      <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
        {/* Header */}
        <div className="grid grid-cols-12 gap-2 px-4 py-2 bg-gray-850 border-b border-gray-700">
          <div className="col-span-3"><SortHeader col="name" label="Task" /></div>
          <div className="col-span-2"><SortHeader col="project_name" label="Project" /></div>
          <div className="col-span-2"><SortHeader col="next_run" label="Next Run" /></div>
          <div className="col-span-2 text-xs font-semibold text-gray-400">Schedule</div>
          <div className="col-span-1 text-xs font-semibold text-gray-400">Status</div>
          <div className="col-span-2" />
        </div>

        {/* Rows */}
        {sorted.length === 0 ? (
          <p className="text-center text-gray-600 text-sm py-8">
            {tasks.length === 0 ? 'No tasks scheduled' : 'No tasks match your search'}
          </p>
        ) : (
          sorted.map((task) => (
            <div key={task.id}>
              <div
                className="grid grid-cols-12 gap-2 px-4 py-2.5 hover:bg-gray-750 cursor-pointer items-center border-b border-gray-700/50"
                onClick={() => {
                  if (expandedTask === task.id) {
                    setExpandedTask(null)
                  } else {
                    setExpandedTask(task.id)
                    loadLogs(task.id, task.project_id)
                  }
                }}
              >
                <div className="col-span-3 text-sm text-gray-200 truncate">{task.name}</div>
                <div className="col-span-2 text-xs text-gray-500 truncate">{projectName(task.project_id)}</div>
                <div className="col-span-2 text-xs text-gray-500">{formatTime(task.next_run)}</div>
                <div className="col-span-2 text-xs text-gray-500 font-mono truncate">{task.schedule}</div>
                <div className="col-span-1">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${
                    task.status === 'scheduled' ? 'bg-emerald-900 text-emerald-300' : 'bg-gray-700 text-gray-400'
                  }`}>{task.status}</span>
                </div>
                <div className="col-span-2 flex justify-end">
                  <button
                    onClick={(e) => { e.stopPropagation(); cancelTask(task.id) }}
                    className="text-xs text-red-400 hover:text-red-300 px-2 py-1"
                    title="Cancel task"
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                </div>
              </div>

              {/* Expanded detail */}
              {expandedTask === task.id && (
                <div className="px-4 py-3 bg-gray-900 border-b border-gray-700 space-y-2">
                  {task.description && (
                    <p className="text-xs text-gray-400"><span className="text-gray-500">Description:</span> {task.description}</p>
                  )}
                  <div>
                    <p className="text-xs font-semibold text-gray-400 mb-1">Execution Log</p>
                    <div className="bg-gray-950 rounded text-xs text-gray-400 p-2 max-h-40 overflow-y-auto font-mono scrollbar-thin">
                      {logsLoading ? (
                        <p className="text-gray-600">Loading...</p>
                      ) : logs.length === 0 ? (
                        <p className="text-gray-600">No logs yet</p>
                      ) : (
                        logs.map((log, i) => (
                          <div key={log.id || i} className="whitespace-pre-wrap break-words py-0.5">
                            <span className="text-gray-600">{formatTime(log.timestamp)}</span>{' '}
                            <span className={log.event === 'failed' ? 'text-red-400' : log.event === 'completed' ? 'text-emerald-400' : 'text-gray-400'}>
                              [{log.event}]
                            </span>{' '}
                            {log.details || ''}
                          </div>
                        ))
                      )}
                    </div>
                  </div>
                </div>
              )}
            </div>
          ))
        )}
      </div>
    </div>
  )
}
