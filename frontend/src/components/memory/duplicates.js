// Likely-duplicate names (graph entity labels, artifact tags): the same name
// once case, spaces and punctuation are ignored ("Marble Maze" / "MarbleMaze"),
// or one name extending another ("enhance-marble-maze" /
// "enhance-marblemaze-game"). Short names (< 8 chars) only match exactly, so
// "Swift" is not a duplicate of "SwiftUI". UI review #23/#27.

export function normalizeName(s) {
  return String(s || '').toLowerCase().replace(/[^a-z0-9]+/g, '')
}

const MIN_PREFIX = 8

/**
 * items: array of { id, label }. Returns Map<id, Array<{ id, label, exact }>>
 * listing, for every item that has one, its likely duplicates.
 */
export function findLikelyDuplicates(items) {
  const norm = items
    .map((it) => ({ ...it, n: normalizeName(it.label) }))
    .filter((it) => it.n)
    .sort((a, b) => (a.n < b.n ? -1 : a.n > b.n ? 1 : 0))
  const out = new Map()
  const add = (a, b, exact) => {
    if (!out.has(a.id)) out.set(a.id, [])
    out.get(a.id).push({ id: b.id, label: b.label, exact })
  }
  // Sorted order puts every name right before the names it prefixes.
  for (let i = 0; i < norm.length; i++) {
    const a = norm[i]
    for (let j = i + 1; j < norm.length; j++) {
      const b = norm[j]
      if (!b.n.startsWith(a.n)) break
      const exact = a.n === b.n
      if (!exact && a.n.length < MIN_PREFIX) continue
      add(a, b, exact); add(b, a, exact)
    }
  }
  return out
}
