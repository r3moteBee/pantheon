import React, { useState, useEffect } from 'react'
import { Play, Square, Trash2, ChevronDown, ChevronRight, Clock, RefreshCw } from 'lucide-react'
import { useStore } from '../store'
import { tasksApi } from '../api/client'
import InfoTooltip from './help/InfoTooltip'
import HelpDrawer from './help/HelpDrawer'

function CreateTaskForm({ onTaskCreated, activeProject }) {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [schedule, setSchedule] = useState('now')
  const [loading, setLoading] = useState(false)
  const addNotification = useStore((s) => s.addNotification)

  const createTask = async () => {
    if (!name.trim()) return
    setLoading(true)
    try {
      await tasksApi.create(name, description, schedule, activeProject?.id || 'default')
      setName('')
      setDescription('')
      setSchedule('now')
      addNotification({ type: 'success', message: 'Task created' })
      onTaskCreated()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  return (
    <div className="bg-gray-800 rounded-lg p-4 border border-gray-700 space-y-3">
      <h3 className="text-sm font-semibold text-gray-200">Create New Task</h3>
      <input
        type="text"
        value={name}
        onChange={(e) => setName(e.target.value)}
        placeholder="Task name..."
        className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
      />
      <textarea
        value={description}
        onChange={(e) => setDescription(e.target.value)}
        placeholder="Description (optional)..."
        rows={2}
        className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500 resize-none"
      />
      <div>
        <label className='block text-xs text-gray-400 mb-1'>
          Schedule
          <InfoTooltip text="Pick a preset to run once now, on a daily/weekly/monthly cadence, or every N hours. cron syntax is standard 5-field (min hour day month weekday) — e.g. 0 9 * * * = 9am daily." />
        </label>
        <select
          value={schedule}
          onChange={(e) => setSchedule(e.target.value)}
          className="w-full bg-gray-900 border border-gray-600 rounded px-3 py-2 text-sm text-gray-100 focus:outline-none focus:border-brand-500"
        >
          <option value="now">Run Now (once)</option>
          <option value="0 9 * * *">Daily at 9am</option>
          <option value="0 9 * * 1">Weekly (Monday 9am)</option>
          <option value="0 9 1 * *">Monthly (1st at 9am)</option>
          <option value="interval:60">Every Hour</option>
          <option value="interval:360">Every 6 Hours</option>
        </select>
      </div>
      <button
        onClick={createTask}
        disabled={loading || !name.trim()}
        className="w-full px-3 py-2 bg-brand-600 hover:bg-brand-700 text-white text-sm rounded disabled:opacity-50"
      >
        {loading ? 'Creating...' : 'Create Task'}
      </button>
    </div>
  )
}

function TaskItem({ task, onRefresh }) {
  const [expanded, setExpanded] = useState(false)
  const [logs, setLogs] = useState([])
  const [logsLoading, setLogsLoading] = useState(false)
  const activeProject = useStore((s) => s.activeProject)
  const addNotification = useStore((s) => s.addNotification)

  const loadLogs = async () => {
    setLogsLoading(true)
    try {
      const res = await tasksApi.getLogs(task.id, activeProject?.id || 'default')
      setLogs(res.data.logs || [])
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLogsLoading(false)
  }

  const deleteTask = async () => {
    if (!confirm('Delete this task?')) return
    try {
      await tasksApi.cancel(task.id)
      addNotification({ type: 'success', message: 'Task deleted' })
      onRefresh()
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
  }

  const formatTime = (ts) => {
    try {
      return new Date(ts).toLocaleString()
    } catch {
      return ts
    }
  }

  return (
    <div className="bg-gray-800 rounded-lg border border-gray-700 overflow-hidden">
      {/* Task header */}
      <button
        onClick={() => {
          setExpanded(!expanded)
          if (!expanded && logs.length === 0) {
            loadLogs()
          }
        }}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-gray-750 transition-colors text-left"
      >
        {expanded ? <ChevronDown className="w-4 h-4 text-gray-500" /> : <ChevronRight className="w-4 h-4 text-gray-500" />}
        <div className="flex-1 min-w-0">
          <p className="font-medium text-gray-200">{task.name}</p>
          <p className="text-xs text-gray-600 mt-1">
            <Clock className="w-3 h-3 inline mr-1" />
            {task.status || 'pending'}
          </p>
        </div>
        {task.next_run && (
          <span className="text-xs text-gray-500">{formatTime(task.next_run)}</span>
        )}
      </button>

      {/* Task details */}
      {expanded && (
        <div className="border-t border-gray-700 px-4 py-3 bg-gray-900 space-y-3">
          {task.description && (
            <div>
              <p className="text-xs text-gray-500 mb-1">Description</p>
              <p className="text-sm text-gray-400">{task.description}</p>
            </div>
          )}

          {task.schedule && (
            <div>
              <p className="text-xs text-gray-500 mb-1">Schedule</p>
              <p className="text-sm font-mono text-gray-400">{task.schedule}</p>
            </div>
          )}

          {/* Logs */}
          <div>
            <div className="flex items-center justify-between mb-2">
              <p className="text-xs font-semibold text-gray-400">Logs</p>
              <button
                onClick={loadLogs}
                disabled={logsLoading}
                className="text-xs text-gray-500 hover:text-gray-300 disabled:opacity-50"
              >
                <RefreshCw className="w-3 h-3" />
              </button>
            </div>
            <div className="bg-gray-950 rounded text-xs text-gray-400 p-2 max-h-32 overflow-y-auto font-mono scrollbar-thin">
              {logs.length === 0 ? (
                <p className="text-gray-600">No logs yet</p>
              ) : (
                logs.map((log, i) => (
                  <div key={log.id || i} className="py-0.5 whitespace-pre-wrap break-words">
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

          {/* Actions */}
          <div className="flex gap-2 pt-2">
            <button
              onClick={deleteTask}
              className="flex-1 flex items-center justify-center gap-2 px-3 py-2 bg-red-900 hover:bg-red-800 text-red-200 text-xs rounded"
            >
              <Trash2 className="w-3 h-3" />
              Delete Task
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

export default function TaskMonitor() {
  const [tasks, setTasks] = useState([])
  const [loading, setLoading] = useState(false)
  const activeProject = useStore((s) => s.activeProject)
  const addNotification = useStore((s) => s.addNotification)

  const loadTasks = async () => {
    setLoading(true)
    try {
      const res = await tasksApi.list(activeProject?.id || 'default')
      setTasks(res.data.tasks || [])
    } catch (err) {
      addNotification({ type: 'error', message: err.message })
    }
    setLoading(false)
  }

  useEffect(() => {
    loadTasks()
    const interval = setInterval(loadTasks, 30000)
    return () => clearInterval(interval)
  }, [activeProject])

  return (
    <div className="flex flex-col h-full bg-gray-950">
      {/* Header */}
      <div className="px-6 py-4 bg-gray-900 border-b border-gray-800 flex items-center justify-between">
        <h1 className="text-xl font-bold text-gray-100">Task Monitor</h1>
        <button
          onClick={loadTasks}
          disabled={loading}
          className="p-2 text-gray-400 hover:text-gray-300 disabled:opacity-50"
        >
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {/* Content */}
      <div className="flex-1 overflow-y-auto scrollbar-thin p-6">
        <div className="max-w-2xl mx-auto space-y-4">
          <HelpDrawer title='About task schedules' storageKey='help.task-schedules'>
            <p className='text-xs text-gray-400 mb-3'>
              Pick a preset for the common cases, or ask the agent to schedule
              a task with custom timing using the syntax below.
            </p>
            <table className='w-full text-xs mb-3'>
              <tbody className='text-gray-300'>
                <tr className='border-t border-amber-900/30'>
                  <td className='py-1.5 pr-3 font-mono text-amber-200/80 align-top'>now</td>
                  <td className='py-1.5'>Run once, immediately</td>
                </tr>
                <tr className='border-t border-amber-900/30'>
                  <td className='py-1.5 pr-3 font-mono text-amber-200/80 align-top'>delay:N</td>
                  <td className='py-1.5'>Run once after N minutes</td>
                </tr>
                <tr className='border-t border-amber-900/30'>
                  <td className='py-1.5 pr-3 font-mono text-amber-200/80 align-top'>interval:N</td>
                  <td className='py-1.5'>Recurring, every N minutes</td>
                </tr>
                <tr className='border-t border-amber-900/30'>
                  <td className='py-1.5 pr-3 font-mono text-amber-200/80 align-top'>m h dom mon dow</td>
                  <td className='py-1.5'>Standard 5-field cron — e.g. <code className='text-amber-200/80'>0 9 * * 1</code> = Monday 9am</td>
                </tr>
              </tbody>
            </table>
            <p className='text-xs text-gray-400'>
              A <strong>skill</strong> (<code className='text-amber-200/80'>/slug</code> in chat) is a reusable
              callable; a <strong>scheduled task</strong> is a one-shot or recurring autonomous run
              that may optionally invoke a skill. Don't confuse them.
            </p>
          </HelpDrawer>
          <CreateTaskForm onTaskCreated={loadTasks} activeProject={activeProject} />

          <div className="space-y-3">
            <h3 className="text-sm font-semibold text-gray-400">Active Tasks</h3>
            {tasks.length === 0 ? (
              <p className="text-center text-gray-600 py-8">No tasks scheduled</p>
            ) : (
              tasks.map((task) => (
                <TaskItem key={task.id} task={task} onRefresh={loadTasks} />
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
