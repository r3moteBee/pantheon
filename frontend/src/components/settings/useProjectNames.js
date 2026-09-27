import { useState, useEffect } from 'react'
import { projectsApi } from '../../api/client'

// Project id -> display name lookup ('default' -> 'Default').
// Returns a function that falls back to the id when the name is unknown.
export default function useProjectNames() {
  const [projectNames, setProjectNames] = useState({})

  useEffect(() => {
    projectsApi.list().then((res) => {
      const map = {}
      for (const p of res.data.projects || []) {
        map[p.id] = p.name || p.id
      }
      map['default'] = 'Default'
      setProjectNames(map)
    }).catch(() => {})
  }, [])

  return (id) => projectNames[id] || id
}
