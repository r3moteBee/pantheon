import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, ChevronDown, Folder, FolderOpen, Box } from 'lucide-react'

/**
 * Project-aware folder tree.
 *
 * Props:
 *  - nodes: Array<{ project_id, project_name, folders: string[], artifact_count?: number }>
 *  - selected: { project_id?: string, folder?: string }
 *  - onSelect: ({ project_id, folder }) => void
 *  - onDrop?: ({ project_id, folder }, event) => void   // optional DnD target
 *  - collapsedKey: string                                // localStorage key
 *  - showProjects: 'always' | 'multi-only'              // hide project headers when only one
 */
export default function FolderTree({
  nodes,
  selected = {},
  onSelect,
  onDrop,
  collapsedKey,
  showProjects = 'multi-only',
}) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      const raw = localStorage.getItem(collapsedKey)
      if (raw) return new Set(JSON.parse(raw))
    } catch {}
    // Default: everything collapsed.
    const init = new Set()
    for (const node of nodes) {
      init.add(`project:${node.project_id}`)
      for (const folder of node.folders) init.add(`folder:${node.project_id}:${folder}`)
    }
    return init
  })

  useEffect(() => {
    try { localStorage.setItem(collapsedKey, JSON.stringify(Array.from(collapsed))) } catch {}
  }, [collapsed, collapsedKey])

  const [dropHover, setDropHover] = useState(null)
  const hideProjectHeader = showProjects === 'multi-only' && nodes.length <= 1

  const toggle = (key) => {
    setCollapsed((s) => {
      const n = new Set(s)
      n.has(key) ? n.delete(key) : n.add(key)
      return n
    })
  }

  // Build per-project nested-tree structure for rendering.
  const trees = useMemo(() => nodes.map((node) => ({
    ...node,
    tree: buildTree(node.folders),
  })), [nodes])

  const handleDragOver = (target) => (e) => {
    if (!onDrop) return
    e.preventDefault()
    setDropHover(JSON.stringify(target))
  }
  const handleDragLeave = () => setDropHover(null)
  const handleDrop = (target) => (e) => {
    if (!onDrop) return
    e.preventDefault()
    setDropHover(null)
    onDrop(target, e)
  }

  const renderFolder = (project_id, folderPath, displayName, depth) => {
    const key = `folder:${project_id}:${folderPath}`
    const isSelected = selected.project_id === project_id && selected.folder === folderPath
    const hover = dropHover === JSON.stringify({ project_id, folder: folderPath })
    return (
      <button
        key={key}
        type="button"
        onClick={() => onSelect({ project_id, folder: folderPath })}
        onDragOver={handleDragOver({ project_id, folder: folderPath })}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop({ project_id, folder: folderPath })}
        className={`w-full text-left text-xs px-2 py-1 rounded flex items-center gap-1 ${
          isSelected ? 'bg-brand-600 text-white' : hover ? 'bg-brand-600/30 text-white' : 'hover:bg-gray-900 text-gray-400'
        }`}
        style={{ paddingLeft: 12 + depth * 12 }}
      >
        <Folder className="w-3 h-3" />
        <span>{displayName}</span>
      </button>
    )
  }

  return (
    <div className="space-y-0.5">
      {trees.map((node) => {
        const projKey = `project:${node.project_id}`
        const projCollapsed = collapsed.has(projKey)
        const projSelected = selected.project_id === node.project_id && !selected.folder
        const projHover = dropHover === JSON.stringify({ project_id: node.project_id, folder: '' })
        return (
          <div key={node.project_id}>
            {!hideProjectHeader && (
              <div className="flex items-center">
                <button
                  type="button"
                  onClick={() => toggle(projKey)}
                  className="p-0.5 text-gray-500 hover:text-gray-300"
                  aria-label={projCollapsed ? 'Expand project' : 'Collapse project'}
                >
                  {projCollapsed ? <ChevronRight className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
                </button>
                <button
                  type="button"
                  onClick={() => onSelect({ project_id: node.project_id, folder: '' })}
                  onDragOver={handleDragOver({ project_id: node.project_id, folder: '' })}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop({ project_id: node.project_id, folder: '' })}
                  className={`flex-1 text-left text-xs px-1 py-1 rounded flex items-center gap-1 font-semibold ${
                    projSelected ? 'bg-brand-600 text-white' : projHover ? 'bg-brand-600/30 text-white' : 'hover:bg-gray-900 text-gray-200'
                  }`}
                >
                  <Box className="w-3 h-3" />
                  <span>{node.project_name}</span>
                  {typeof node.artifact_count === 'number' && (
                    <span className="ml-auto text-gray-500">{node.artifact_count}</span>
                  )}
                </button>
              </div>
            )}
            {!projCollapsed && (
              <div>
                {renderTreeRows(node.tree, node.project_id, collapsed, toggle, renderFolder, hideProjectHeader ? 0 : 1)}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

// Turn a flat list of folder paths into a nested-row render order.
function buildTree(folders) {
  // folders are like ["p1", "p1/sub", "p1/sub/deep", "p1/other"]; produce
  // { name, path, depth, children: [...] } recursively.
  const root = { children: new Map(), path: '' }
  for (const path of folders) {
    const parts = path.split('/')
    let cursor = root
    let acc = ''
    for (const part of parts) {
      acc = acc ? `${acc}/${part}` : part
      if (!cursor.children.has(acc)) {
        cursor.children.set(acc, { name: part, path: acc, children: new Map() })
      }
      cursor = cursor.children.get(acc)
    }
  }
  return root
}

function renderTreeRows(treeNode, project_id, collapsed, toggle, renderFolder, baseDepth) {
  const rows = []
  const walk = (node, depth) => {
    for (const child of node.children.values()) {
      const key = `folder:${project_id}:${child.path}`
      const isCollapsed = collapsed.has(key)
      const hasChildren = child.children.size > 0
      rows.push(
        <div key={`row:${key}`} className="flex items-center">
          {hasChildren ? (
            <button
              type="button"
              onClick={(e) => { e.stopPropagation(); toggle(key) }}
              className="p-0.5 text-gray-500 hover:text-gray-300"
              style={{ marginLeft: 4 + depth * 12 }}
              aria-label={isCollapsed ? 'Expand folder' : 'Collapse folder'}
            >
              {isCollapsed ? <ChevronRight className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
            </button>
          ) : (
            <span style={{ marginLeft: 4 + depth * 12, width: 16 }} />
          )}
          <div className="flex-1 min-w-0">
            {renderFolder(project_id, child.path, child.name, 0)}
          </div>
        </div>
      )
      if (!isCollapsed) walk(child, depth + 1)
    }
  }
  walk(treeNode, baseDepth)
  return rows
}
