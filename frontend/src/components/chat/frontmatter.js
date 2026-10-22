// YAML-frontmatter detection for the shared <Markdown> (UI review N3).
//
// A block such as
//     ---
//     type: task-ledger
//     status: active
//     ---
// is not markdown: CommonMark reads the closing `---` as a setext underline
// and renders every `key: value` line as one giant H2. splitFrontmatter()
// cuts those blocks out of the text (leading OR embedded, indented up to
// 3 spaces like a setext underline would be) so the renderer can show them
// as a compact metadata card and feed only the real markdown to
// react-markdown.
//
// A block is: an opening `---` line, one or more `key: value` lines (plus
// indented continuation lines, e.g. nested lists), no blank lines, and a
// closing `---` within MAX_LINES. A lone `---` with prose or a blank line
// after it never matches, so horizontal rules keep working.
//
// While a reply streams the closing `---` arrives character by character
// and `--` (any run of dashes) would already be a setext underline for the
// key lines above it. With `streaming: true` an unclosed block that runs to
// the end of the text is returned as a partial card instead, so the keys
// never flash as a heading.

const OPEN = /^[ \t]{0,3}-{3,}[ \t]*$/
const CLOSE = OPEN
const KEY = /^[ \t]{0,3}([A-Za-z_][\w.-]*)[ \t]*:(?:[ \t]+(.*)|[ \t]*)$/
const CONT = /^[ \t]+\S/
const PARTIAL_DASHES = /^[ \t]{0,3}-{1,2}[ \t]*$/
const MAX_LINES = 40

function parseFields(lines) {
  const fields = []
  for (const line of lines) {
    const m = line.match(KEY)
    if (m) {
      fields.push([m[1], (m[2] || '').trim()])
    } else if (fields.length) {
      const last = fields[fields.length - 1]
      last[1] = last[1] ? `${last[1]}\n${line.trim()}` : line.trim()
    }
  }
  return fields
}

// Returns the block starting at lines[start] or null:
// { end (index after the closing line), partial }.
function matchBlock(lines, start, streaming) {
  if (!OPEN.test(lines[start])) return null
  let keys = 0
  for (let i = start + 1; i < lines.length && i <= start + MAX_LINES; i++) {
    const line = lines[i]
    if (CLOSE.test(line)) return keys ? { end: i + 1, partial: false } : null
    if (KEY.test(line)) { keys++; continue }
    if (keys && CONT.test(line)) continue
    // Streaming: the last line may be a half-typed key or the closing dashes.
    if (streaming && i === lines.length - 1 && keys && (PARTIAL_DASHES.test(line) || line.trim() === '' || !/\s/.test(line.trim()))) {
      return { end: i + 1, partial: true }
    }
    return null
  }
  if (streaming && keys && lines.length - 1 <= start + MAX_LINES) return { end: lines.length, partial: true }
  return null
}

/**
 * Split markdown into segments:
 *   { type: 'markdown', text }
 *   { type: 'frontmatter', fields: [[key, value], ...], raw, partial }
 * Plain markdown comes back as a single markdown segment.
 */
export function splitFrontmatter(text, { streaming = false } = {}) {
  const src = typeof text === 'string' ? text : ''
  if (!src.includes('---')) return [{ type: 'markdown', text: src }]
  const lines = src.split('\n')
  const segments = []
  let mdStart = 0
  let i = 0
  while (i < lines.length) {
    const block = matchBlock(lines, i, streaming)
    if (!block) { i++; continue }
    const before = lines.slice(mdStart, i).join('\n')
    if (before.trim()) segments.push({ type: 'markdown', text: before })
    const bodyEnd = block.partial ? block.end : block.end - 1
    const body = lines.slice(i + 1, bodyEnd).filter((l) => !(block.partial && PARTIAL_DASHES.test(l)))
    segments.push({
      type: 'frontmatter',
      fields: parseFields(body),
      raw: lines.slice(i, block.end).join('\n'),
      partial: block.partial,
    })
    i = block.end
    mdStart = i
  }
  const rest = lines.slice(mdStart).join('\n')
  if (!segments.length) return [{ type: 'markdown', text: src }]
  if (rest.trim()) segments.push({ type: 'markdown', text: rest })
  return segments
}
